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
    )


@api_app.get("/calibration", response_model=CalibrationOverview, tags=["Backend"])
async def calibration() -> CalibrationOverview:
    """Reads the llama-swap config and the calibration caches and asks the TTS service for its engines."""
    return await asyncio.to_thread(_calibration_overview)
