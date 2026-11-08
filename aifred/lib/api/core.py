"""Health, Settings and Models endpoints."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config import (
    DEBATE_ROUNDS_MAX,
    DEBATE_ROUNDS_MIN,
    DICTATION_ENGINES,
)
from ..logging_utils import log_message
from ..settings import persisted_settings
from ..settings import update_settings as write_settings
from ..user_error import Invalid, NotDone
from .app import API_VERSION, api_app

# ============================================================
# Pydantic Models for API Request/Response
# ============================================================

class HealthResponse(BaseModel):
    """Health check response"""
    status: str = "ok"
    version: str = API_VERSION
    backend_type: str = ""


Language = Literal["de", "en"]
Gender = Literal["male", "female"]
ConsensusType = Literal["majority", "unanimous"]


class SettingsResponse(BaseModel):
    """AIfred's general settings (settings.json). The agents' models and switches are their quick
    choices (``/agents/choices``); the backend changes with its own switch (``PUT /backend``)."""

    backend_type: str
    ui_language: Language
    user_name: str
    user_gender: Gender
    enable_tts: bool = Field(description="Speech output is on")
    tts_autoplay: bool = Field(description="A reply is read aloud while it is written")
    dictation_engine: str = Field(description="Who recognizes a dictation: one of config.DICTATION_ENGINES")
    show_transcription: bool = Field(description="A dictation goes into the input field first")
    max_debate_rounds: int
    consensus_type: ConsensusType
    # Not settings: what the interface may offer (config.py).
    debate_rounds_min: int = DEBATE_ROUNDS_MIN
    debate_rounds_max: int = DEBATE_ROUNDS_MAX


class SettingsUpdate(BaseModel):
    """Only the fields sent change; every other key of settings.json stays as the file has it. A field
    this endpoint does not know is refused (422), not ignored."""

    model_config = ConfigDict(extra="forbid")

    ui_language: Language | None = None
    user_name: str | None = None
    user_gender: Gender | None = None
    enable_tts: bool | None = None
    tts_autoplay: bool | None = None
    dictation_engine: str | None = None
    show_transcription: bool | None = None
    max_debate_rounds: int | None = Field(None, ge=DEBATE_ROUNDS_MIN, le=DEBATE_ROUNDS_MAX)
    consensus_type: ConsensusType | None = None


class ModelsResponse(BaseModel):
    """Available models response"""
    backend_type: str
    models: dict[str, str]  # {model_id: display_label}
    vision_models: list[str]  # List of vision-capable model IDs


# ============================================================
# Health Endpoint
# ============================================================

@api_app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """The API answers; its version and the backend in use."""
    return HealthResponse(version=API_VERSION, backend_type=persisted_settings()["backend_type"])


# ============================================================
# Settings Endpoints
# ============================================================

@api_app.get("/settings", response_model=SettingsResponse, tags=["Settings"])
async def get_settings() -> SettingsResponse:
    """AIfred's general settings: settings.json over the defaults of config.py."""
    saved = persisted_settings()
    return SettingsResponse(backend_type=saved["backend_type"], **{key: saved[key] for key in SettingsUpdate.model_fields})


@api_app.patch("/settings", response_model=SettingsResponse, tags=["Settings"])
async def update_settings(update: SettingsUpdate) -> SettingsResponse:
    """Change the fields sent and answer with every setting. The UI language also sets the number
    format of the process."""
    from ..formatting import set_ui_locale

    changes = update.model_dump(exclude_none=True)
    if "user_name" in changes:
        changes["user_name"] = changes["user_name"].strip()
    if changes.get("dictation_engine", DICTATION_ENGINES[0]) not in DICTATION_ENGINES:
        raise Invalid("unknown_dictation_engine", engine=changes["dictation_engine"])
    if changes and not write_settings(changes):
        raise NotDone("settings_not_saved")
    if "ui_language" in changes:
        set_ui_locale(changes["ui_language"])
    log_message(f"📝 API: settings changed: {sorted(changes)}")
    return await get_settings()


# ============================================================
# Models Endpoints
# ============================================================

@api_app.get("/models", response_model=ModelsResponse, tags=["Models"])
async def get_available_models() -> ModelsResponse:
    """The models of the backend in use (id → label) and which of them see images."""
    from ..backend_switch import model_catalog
    from ..vision_utils import is_vision_model_sync

    saved = persisted_settings()
    models, _notes = await model_catalog(saved["backend_type"], saved["cloud_api_provider"])
    return ModelsResponse(
        backend_type=saved["backend_type"],
        models=models,
        vision_models=[model_id for model_id in models if is_vision_model_sync(model_id)],
    )
