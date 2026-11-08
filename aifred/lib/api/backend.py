"""Backend and calibration as the settings page reads them: the backend in use with the ones these GPUs can run,
the cloud providers, the GPUs (lib/backend_overview.py), and the calibration of the models the agents run on
with the matrix of AIfred's model (lib/calibration/matrix.py, model_info.py). Reading only; switching the
backend and starting a calibration follow."""

import asyncio
from dataclasses import dataclass

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


@api_app.get("/backend", response_model=BackendOverview, tags=["Backend"])
async def backend() -> BackendOverview:
    from ..backend_overview import backend_overview

    return await asyncio.to_thread(backend_overview)


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
