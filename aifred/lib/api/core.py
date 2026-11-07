"""Health, Settings and Models endpoints."""

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..config import (
    DEBATE_ROUNDS_MAX,
    DEBATE_ROUNDS_MIN,
    DEFAULT_OLLAMA_URL,
    DICTATION_ENGINES,
)
from ..formatting import format_number
from ..logging_utils import log_message
from ..settings import load_settings, persisted_settings
from ..settings import update_settings as write_settings
from .app import API_VERSION, api_app, get_global_backend_state

# ============================================================
# Pydantic Models for API Request/Response
# ============================================================

class HealthResponse(BaseModel):
    """Health check response"""
    status: str = "ok"
    version: str = API_VERSION
    backend_type: str = ""
    backend_healthy: bool = False


Language = Literal["de", "en"]
Gender = Literal["male", "female"]
ConsensusType = Literal["majority", "unanimous"]


class SettingsResponse(BaseModel):
    """AIfred's general settings (settings.json). The agents' models and switches are their quick
    choices (``/agents/choices``); the backend changes with its own switch (Reflex UI)."""

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
async def health_check():
    """
    Health check endpoint.

    Returns API status and backend connection info.
    """
    global_state = get_global_backend_state()
    settings = load_settings() or {}

    return HealthResponse(
        status="ok",
        version=API_VERSION,
        backend_type=settings.get("backend_type", "ollama"),
        backend_healthy=global_state.get("backend_type") is not None
    )


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
    format of the process (the Reflex UI's language switch does the same)."""
    from ..formatting import set_ui_locale

    changes = update.model_dump(exclude_none=True)
    if "user_name" in changes:
        changes["user_name"] = changes["user_name"].strip()
    if changes.get("dictation_engine", DICTATION_ENGINES[0]) not in DICTATION_ENGINES:
        raise HTTPException(status_code=422, detail=f"Unknown dictation engine: {changes['dictation_engine']}")
    if changes and not write_settings(changes):
        raise HTTPException(status_code=500, detail="settings.json could not be read or written")
    if "ui_language" in changes:
        set_ui_locale(changes["ui_language"])
    log_message(f"📝 API: settings changed: {sorted(changes)}")
    return await get_settings()


# ============================================================
# Models Endpoints
# ============================================================

@api_app.get("/models", response_model=ModelsResponse, tags=["Models"])
async def get_available_models():
    """
    Get list of available models.

    Returns all models from the current backend with their display labels
    and a separate list of vision-capable models.
    """
    global_state = get_global_backend_state()
    settings = load_settings() or {}

    models_dict: dict[str, str] = {}
    vision_models: list[str] = []
    backend_type = settings.get("backend_type", "ollama")

    # SSOT for vision detection: lib.vision_utils.is_vision_model_sync covers
    # both native --mmproj models (Qwen3.5/3.6) and name-based VLMs.
    from ..vision_utils import is_vision_model_sync as _is_vision_model

    # Get models from global state (populated by initialize_backend). The
    # id -> label mapping is the source; labels are a view and are never
    # parsed back into an id (they carry size and runtime badges).
    cached_models: dict = global_state.get("available_models_dict", {})

    # If global state is empty, try to fetch from backend
    if not cached_models:
        try:
            import httpx
            if backend_type == "ollama":
                backend_url = settings.get("backend_url", DEFAULT_OLLAMA_URL)
                # Async client — a sync httpx.get here would stall the one
                # granian event loop (all sessions, SSE, WS heartbeats).
                async with httpx.AsyncClient(timeout=5.0) as _hc:
                    response = await _hc.get(f"{backend_url}/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    for m in data.get("models", []):
                        model_id = m['name']
                        size_gb = m['size'] / (1024**3)
                        models_dict[model_id] = f"{model_id} ({format_number(size_gb, 1)} GB)"
                        # Check if vision model
                        if _is_vision_model(model_id):
                            vision_models.append(model_id)
        except httpx.HTTPError as e:  # Ollama not reachable: no models to list
            log_message(f"⚠️ API: Failed to fetch models: {e}")
    else:
        for model_id, display_label in cached_models.items():
            models_dict[model_id] = display_label
            if _is_vision_model(model_id):
                vision_models.append(model_id)

    return ModelsResponse(
        backend_type=backend_type,
        models=models_dict,
        vision_models=vision_models
    )
