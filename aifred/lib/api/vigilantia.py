"""The vision page's settings: the eye (armed or not), how the image model stays and which one, face and person
detection, how long events are kept, the image model loaded or not. Thin over ``lib/vigilantia.py``; the cameras'
own settings are ``PATCH /vision/cameras/{id}`` (``api/cameras.py``)."""

import asyncio
from dataclasses import asdict

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from .app import api_app
from .schemas import ChoiceOption


class VigilantiaRow(BaseModel):
    plugin_enabled: bool = Field(description="The eye can only be armed with the vision plugin on")
    armed: bool
    mode: str
    model: str = Field(description="The chosen image model, as the list names it ('' = not in the list)")
    face_enabled: bool
    face_continuous: bool
    person_detect: bool
    retention_days: int
    modes: list[ChoiceOption]
    models: list[str] = Field(description="The image models (llama-swap describer profiles, then Ollama's)")
    alert_types: list[ChoiceOption] = Field(description="The event types a camera can alert on")
    event_intervals: list[ChoiceOption]


class VigilantiaChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str | None = None
    model: str | None = None
    face_enabled: bool | None = None
    face_continuous: bool | None = None
    person_detect: bool | None = None
    retention_days: int | None = None


class ArmedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    armed: bool


class ModelState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loaded: bool


def _row() -> VigilantiaRow:
    from ..config import VISION_ALERT_TYPES, VISION_EVENT_INTERVAL_CHOICES
    from ..formatting import format_number
    from ..i18n import t
    from ..prompt_loader import get_language
    from ..vigilantia import VISION_MODES, vigilantia
    from ..vision_routing import image_models, listed_name

    lang = get_language()
    current = vigilantia()
    models = image_models()
    fields = asdict(current)
    fields["model"] = listed_name(models, current.model) if current.model else ""
    mode_labels = {"on-demand": "vision_settings_mode_ondemand", "live": "vision_settings_mode_live"}
    return VigilantiaRow(
        **fields,
        modes=[ChoiceOption(id=mode, label=t(mode_labels[mode], lang=lang)) for mode in VISION_MODES],
        models=models,
        alert_types=[ChoiceOption(id=kind, label=t(f"vision_settings_alert_type_{kind}", lang=lang)) for kind in VISION_ALERT_TYPES],
        event_intervals=[ChoiceOption(id=f"{seconds:g}", label=f"{format_number(seconds, 0)} s") for seconds in VISION_EVENT_INTERVAL_CHOICES],
    )


@api_app.get("/vision/settings", response_model=VigilantiaRow, tags=["Vision"])
async def vision_settings() -> VigilantiaRow:
    """The eye and the general settings of the vision, with their choices."""
    return await asyncio.to_thread(_row)


@api_app.patch("/vision/settings", response_model=VigilantiaRow, tags=["Vision"])
async def vision_settings_change(change: VigilantiaChange) -> VigilantiaRow:
    """Change some of the general settings: a mode switched to live loads the model at once, a new model unloads
    the old one; 422 for a mode or retention the eye does not take."""
    from ..vigilantia import change as change_settings

    changes = change.model_dump(exclude_none=True)
    try:
        await change_settings(**changes)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    log_message(f"👁️ Vision settings: {sorted(changes)} changed")
    return await asyncio.to_thread(_row)


@api_app.put("/vision/armed", response_model=VigilantiaRow, tags=["Vision"])
async def vision_armed(change: ArmedChange) -> VigilantiaRow:
    """Arm or disarm the eye: every camera with its background switch on starts (inside its schedule) or stops.
    409 when the vision plugin is off."""
    from ..vigilantia import set_armed

    try:
        changed = await set_armed(change.armed)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    log_message(f"👁️ Eye {'armed' if change.armed else 'disarmed'}: {changed} watcher(s)")
    return await asyncio.to_thread(_row)


@api_app.get("/vision/model", response_model=ModelState, tags=["Vision"])
async def vision_model() -> ModelState:
    """Whether the image model is loaded, as its backend reports it."""
    from ..vigilantia import model_loaded

    return ModelState(loaded=await model_loaded())


@api_app.put("/vision/model", response_model=ModelState, tags=["Vision"])
async def vision_model_load(state: ModelState) -> ModelState:
    """Load the image model (also in on-demand mode) or unload it; answers with what the backend reports then.
    409 without a model chosen."""
    from ..vigilantia import load_model

    try:
        loaded = await load_model(state.loaded)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    log_message(f"👁️ Image model {'loaded' if loaded else 'not loaded'} (asked: {'load' if state.loaded else 'unload'})")
    return ModelState(loaded=loaded)
