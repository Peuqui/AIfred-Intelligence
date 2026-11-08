"""Backend and calibration on the settings page: the backend in use with the ones these GPUs can run, the cloud
providers, the GPUs (lib/backend_overview.py); switching the backend and the cloud provider (lib/backend_switch.py);
the calibration of the models the agents run on with the matrix of AIfred's model (lib/calibration/matrix.py,
model_info.py). Restarting the backend's service is a system endpoint (api/system.py)."""

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from ..backend_overview import BackendOverview
from ..calibration.matrix import CalibrationMatrix
from ..calibration.model_info import ModelCalibration
from ..calibration.service import CalibrationOptions
from .app import api_app


@dataclass(frozen=True)
class ModelInUse:
    model_id: str
    agents: tuple[str, ...]  # who runs on it, by name
    calibration: ModelCalibration | None  # None: the backend keeps none (the cloud)


@dataclass(frozen=True)
class CalibrationOverview:
    backend_type: str
    model_id: str  # AIfred's: the model a calibration measures
    matrix: CalibrationMatrix | None  # None: the backend calibrates without variants (Ollama, the cloud)
    models: tuple[ModelInUse, ...]
    options: CalibrationOptions


@dataclass(frozen=True)
class CalibrationRunState:
    active: bool  # a calibration runs (also one started another way: the gate is process-wide)
    model_id: str  # of the server's latest run ("" = none yet)
    backend_type: str
    started_at: str
    running: bool
    outcome: str  # done, cancelled, failed — once it ended
    lines: tuple[str, ...]  # its console from ``after`` on
    next: int  # ``after`` for the next look


class CalibrationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cells: list[str]  # the ticked cells, "<vlm>|<tts>"
    vllm_resweep: bool = False
    vllm_dry_run: bool = False


class CalibrationOptionsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str | None = None
    allow_hybrid: bool | None = None


@dataclass(frozen=True)
class VlmCacheCleared:
    cleared: int


class BackendChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend_type: str


class CloudProviderChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str


@api_app.get("/backend", response_model=BackendOverview, tags=["Backend"])
async def backend() -> BackendOverview:
    from ..backend_overview import backend_overview

    return await asyncio.to_thread(backend_overview)


@api_app.put("/backend", response_model=BackendOverview, tags=["Backend"])
async def backend_switch(change: BackendChange) -> BackendOverview:
    """Switch the backend: the old one frees its models (llama-swap stops when the new one is none of its family),
    the new one's saved models are checked against its catalog. Answers when done (a start of llama-swap takes a
    minute). 422 for a backend these GPUs cannot run, 409 while a switch or a calibration runs."""
    from ..backend_switch import switch_backend

    await switch_backend(change.backend_type)
    return await backend()


@api_app.put("/backend/cloud-provider", response_model=BackendOverview, tags=["Backend"])
async def backend_cloud_provider(change: CloudProviderChange) -> BackendOverview:
    """The cloud's provider; while the cloud is the backend its models change with it. 422 for an unknown one."""
    from ..backend_switch import set_cloud_provider

    await set_cloud_provider(change.provider)
    return await backend()


def _calibration_overview() -> CalibrationOverview:
    from ..agent_choices import models_in_use
    from ..calibration.matrix import matrix
    from ..calibration.model_info import model_calibration
    from ..calibration.service import calibration_options
    from ..prompt_loader import get_language
    from ..runtime_settings import load_runtime_settings

    lang = get_language()
    view = load_runtime_settings()
    model_id = view.agent_tuning["aifred"].model_id
    return CalibrationOverview(
        backend_type=view.backend_type,
        model_id=model_id,
        matrix=matrix(view.backend_type, model_id, lang),
        models=tuple(
            ModelInUse(model, tuple(agents), model_calibration(view.backend_type, model))
            for model, agents in models_in_use(view, lang)
        ),
        options=calibration_options(),
    )


@api_app.get("/calibration", response_model=CalibrationOverview, tags=["Backend"])
async def calibration() -> CalibrationOverview:
    """Reads the llama-swap config and the calibration caches and asks the TTS service for its engines."""
    return await asyncio.to_thread(_calibration_overview)


def _run_state(after: int) -> CalibrationRunState:
    from ..calibration.service import latest_server_run
    from ..calibration_gate import is_calibration_active

    run = latest_server_run()
    if run is None:
        return CalibrationRunState(is_calibration_active(), "", "", "", False, "", (), 0)
    return CalibrationRunState(is_calibration_active(), run.model_id, run.backend_type, run.started_at, run.running,
                               run.outcome, tuple(run.lines[after:]), len(run.lines))


@api_app.get("/calibration/run", response_model=CalibrationRunState, tags=["Backend"])
async def calibration_run(after: int = 0) -> CalibrationRunState:
    """The server's latest run with its console from line ``after`` on (``next`` for the next look)."""
    return _run_state(after)


@api_app.post("/calibration/run", response_model=CalibrationRunState, status_code=202, tags=["Backend"])
async def calibration_start(start: CalibrationStart) -> CalibrationRunState:
    """Calibrate AIfred's model with the ticked cells (``GET /calibration`` names them); a ticked cell that failed
    before is tried again. Runs in the background for minutes to hours, stops llama-swap and takes every card;
    ``GET /calibration/run`` follows it. 409 while a calibration runs, 422 without a cell or for an unknown one, 404
    for a missing vLLM checkpoint."""
    from ..calibration.matrix import all_cell_keys, clear_failure
    from ..calibration.service import CalibrationRequest, start_server_run
    from ..config import BACKEND_URLS
    from ..runtime_settings import load_runtime_settings
    from ..user_error import Invalid

    view = load_runtime_settings()
    model_id = view.agent_tuning["aifred"].model_id
    offered = await asyncio.to_thread(all_cell_keys)
    unknown = [cell for cell in start.cells if cell not in offered]
    if unknown:
        raise Invalid("calibration_unknown_cell", cells=", ".join(unknown))
    request = CalibrationRequest(
        backend_type=view.backend_type, backend_url=BACKEND_URLS[view.backend_type], model_id=model_id,
        cells=dict.fromkeys(start.cells, True), vllm_resweep=start.vllm_resweep, vllm_dry_run=start.vllm_dry_run,
    )
    started = start_server_run(request)
    if view.backend_type == "llamacpp":
        for cell in start.cells:
            clear_failure(model_id, cell)
    return _run_state(len(started.lines) - 1)


@api_app.post("/calibration/run/cancel", response_model=CalibrationRunState, status_code=202, tags=["Backend"])
async def calibration_cancel() -> CalibrationRunState:
    """Ask the running calibration to stop: it ends at its next step (a loading test server is stopped at once) and
    restarts llama-swap."""
    from ..calibration_gate import request_cancel
    from ..logging_utils import log_message

    request_cancel()
    log_message("🛑 API: calibration cancel requested")
    return _run_state(0)


@api_app.get("/calibration/options", response_model=CalibrationOptions, tags=["Backend"])
async def calibration_options_get() -> CalibrationOptions:
    from ..calibration.service import calibration_options

    return await asyncio.to_thread(calibration_options)


@api_app.patch("/calibration/options", response_model=CalibrationOptions, tags=["Backend"])
async def calibration_options_change(change: CalibrationOptionsChange) -> CalibrationOptions:
    """The mode (``legacy`` or ``ai``) and whether a model may run partly on the CPU (hybrid); 422 for another mode."""
    from ..calibration.service import set_calibration_options

    return await asyncio.to_thread(set_calibration_options, mode=change.mode, allow_hybrid=change.allow_hybrid)


@api_app.delete("/calibration/vlm-cache", response_model=VlmCacheCleared, tags=["Backend"])
async def calibration_vlm_cache_clear() -> VlmCacheCleared:
    """Forget every measured VLM peak: the next calibration that needs a VLM reserve measures it again."""
    from .. import vlm_vram_cache
    from ..logging_utils import log_message

    cleared = await asyncio.to_thread(vlm_vram_cache.clear)
    log_message(f"🧹 API: VLM VRAM cache cleared ({cleared} entries)")
    return VlmCacheCleared(cleared)
