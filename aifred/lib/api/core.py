"""Health, Settings and Models endpoints."""

from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List

from ..agent_settings import get_persisted_tuning as _tuning
from ..settings import load_settings, save_settings, get_default_settings
from ..formatting import format_number
from ..logging_utils import log_message
from ..config import DEFAULT_OLLAMA_URL
from .app import api_app, API_VERSION, get_global_backend_state


# ============================================================
# Pydantic Models for API Request/Response
# ============================================================

class HealthResponse(BaseModel):
    """Health check response"""
    status: str = "ok"
    version: str = API_VERSION
    backend_type: str = ""
    backend_healthy: bool = False


class SettingsResponse(BaseModel):
    """Current settings response"""
    # Backend
    backend_type: str = "ollama"
    backend_url: str = DEFAULT_OLLAMA_URL

    # Models
    aifred_model: str = ""
    sokrates_model: str = ""
    salomo_model: str = ""
    automatik_model: str = ""
    vision_model: str = ""

    # RoPE Factors
    aifred_rope_factor: float = 1.0
    sokrates_rope_factor: float = 1.0
    salomo_rope_factor: float = 1.0
    automatik_rope_factor: float = 1.0
    vision_rope_factor: float = 1.0

    # LLM Parameters
    temperature: float = 0.3
    temperature_mode: str = "auto"
    enable_thinking: bool = True

    # Research
    research_mode: str = "automatik"

    # Multi-Agent
    multi_agent_mode: str = "standard"
    max_debate_rounds: int = 3
    consensus_type: str = "majority"

    # TTS/STT
    enable_tts: bool = False
    tts_voice: str = "Deutsch (Katja)"
    tts_engine: str = "edge"
    whisper_model_key: str = "small"

    # UI
    ui_language: str = "de"
    user_name: str = ""


class SettingsUpdate(BaseModel):
    """Settings update request - all fields optional"""
    # Backend
    backend_type: Optional[str] = None

    # Models (by ID, not display name)
    aifred_model: Optional[str] = None
    sokrates_model: Optional[str] = None
    salomo_model: Optional[str] = None
    automatik_model: Optional[str] = None
    vision_model: Optional[str] = None

    # RoPE Factors
    aifred_rope_factor: Optional[float] = None
    sokrates_rope_factor: Optional[float] = None
    salomo_rope_factor: Optional[float] = None
    automatik_rope_factor: Optional[float] = None
    vision_rope_factor: Optional[float] = None

    # LLM Parameters
    temperature: Optional[float] = None
    temperature_mode: Optional[str] = None
    enable_thinking: Optional[bool] = None

    # Research
    research_mode: Optional[str] = None

    # Multi-Agent
    multi_agent_mode: Optional[str] = None
    max_debate_rounds: Optional[int] = None
    consensus_type: Optional[str] = None

    # TTS/STT
    enable_tts: Optional[bool] = None
    tts_voice: Optional[str] = None
    tts_engine: Optional[str] = None
    whisper_model_key: Optional[str] = None

    # UI
    ui_language: Optional[str] = None
    user_name: Optional[str] = None


class ModelsResponse(BaseModel):
    """Available models response"""
    backend_type: str
    models: Dict[str, str]  # {model_id: display_label}
    vision_models: List[str]  # List of vision-capable model IDs


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
async def get_settings():
    """
    Get all current settings.

    Returns the complete settings configuration including models,
    multi-agent settings, TTS/STT config, etc.
    """
    settings = load_settings()
    if not settings:
        settings = get_default_settings()

    global_state = get_global_backend_state()

    # Extract models from backend_models if available
    backend_type = settings.get("backend_type", "ollama")
    backend_models = settings.get("backend_models", {}).get(backend_type, {})

    # Get backend URL with proper fallback (global_state may have None values)
    backend_url = global_state.get("backend_url") or DEFAULT_OLLAMA_URL

    return SettingsResponse(
        backend_type=backend_type,
        backend_url=backend_url,
        # backend_models is the single source of truth for model fields
        aifred_model=backend_models.get("aifred", ""),
        sokrates_model=backend_models.get("sokrates", ""),
        salomo_model=backend_models.get("salomo", ""),
        automatik_model=backend_models.get("automatik", ""),
        vision_model=backend_models.get("vision", ""),
        aifred_rope_factor=_tuning(settings, "aifred", "rope_factor", 1.0),
        sokrates_rope_factor=_tuning(settings, "sokrates", "rope_factor", 1.0),
        salomo_rope_factor=_tuning(settings, "salomo", "rope_factor", 1.0),
        automatik_rope_factor=settings.get("automatik_rope_factor", 1.0),
        vision_rope_factor=_tuning(settings, "vision", "rope_factor", 1.0),
        temperature=settings.get("temperature", 0.3),
        temperature_mode=settings.get("temperature_mode", "auto"),
        enable_thinking=settings.get("enable_thinking", True),
        research_mode=settings.get("research_mode", "automatik"),
        multi_agent_mode=settings.get("multi_agent_mode", "standard"),
        max_debate_rounds=settings.get("max_debate_rounds", 3),
        consensus_type=settings.get("consensus_type", "majority"),
        enable_tts=settings.get("enable_tts", False),
        # Handle different field names in settings.json
        tts_voice=settings.get("voice", settings.get("tts_voice", "Deutsch (Katja)")),
        tts_engine=settings.get("tts_engine", "edge"),
        whisper_model_key=settings.get("whisper_model", settings.get("whisper_model_key", "small")),
        ui_language=settings.get("ui_language", "de"),
        user_name=settings.get("user_name", "")
    )


@api_app.patch("/settings", response_model=SettingsResponse, tags=["Settings"])
async def update_settings(update: SettingsUpdate):
    """
    Update settings.

    Only provided fields are updated, others remain unchanged.
    Changes are persisted to settings.json.

    NOTE: Model changes may require backend restart to take effect.
    Use /api/system/restart-ollama after changing models.
    """
    settings = load_settings()
    if not settings:
        settings = get_default_settings()

    # Apply updates (only non-None values)
    update_dict = update.model_dump(exclude_none=True)

    # Model fields live in backend_models[<backend_type>] — the SAME
    # structure the UI writes and the Message-Hub reads. A flat top-level
    # write here used to create a second truth that the hub never saw.
    # API field name -> backend_models key (agent id)
    _MODEL_FIELDS = {
        "aifred_model": "aifred", "sokrates_model": "sokrates",
        "salomo_model": "salomo", "automatik_model": "automatik",
        "vision_model": "vision",
    }
    # Per-agent tuning fields live in settings["agent_tuning"][<agent>]
    # (automatik_rope_factor stays flat — the Automatik is not an agent).
    _TUNING_FIELDS = {
        "aifred_rope_factor": ("aifred", "rope_factor"),
        "sokrates_rope_factor": ("sokrates", "rope_factor"),
        "salomo_rope_factor": ("salomo", "rope_factor"),
        "vision_rope_factor": ("vision", "rope_factor"),
    }
    # Map API field names to settings.json field names (non-model fields)
    field_mapping = {
        "tts_voice": "voice",
        "whisper_model_key": "whisper_model",
    }

    target_backend = update_dict.get("backend_type") or settings.get("backend_type", "llamacpp")
    for api_field, value in update_dict.items():
        if api_field in _MODEL_FIELDS:
            model_key = _MODEL_FIELDS[api_field]
            settings.setdefault("backend_models", {}).setdefault(target_backend, {})[model_key] = value
            log_message(f"📝 API: Updated backend_models.{target_backend}.{model_key} = {value}")
            continue
        if api_field in _TUNING_FIELDS:
            agent, tuning_field = _TUNING_FIELDS[api_field]
            settings.setdefault("agent_tuning", {}).setdefault(agent, {})[tuning_field] = value
            log_message(f"📝 API: Updated agent_tuning.{agent}.{tuning_field} = {value}")
            continue
        settings_field = field_mapping.get(api_field, api_field)
        settings[settings_field] = value
        log_message(f"📝 API: Updated {settings_field} = {value}")

    # Save to file
    if not save_settings(settings):
        raise HTTPException(status_code=500, detail="Failed to save settings")

    log_message(f"✅ API: Settings saved ({len(update_dict)} fields updated)")
    # Browser detects changes via settings.json mtime (no extra flag needed)

    # Return updated settings
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

    models_dict: Dict[str, str] = {}
    vision_models: List[str] = []
    backend_type = settings.get("backend_type", "ollama")

    # SSOT for vision detection: lib.vision_utils.is_vision_model_sync covers
    # both native --mmproj models (Qwen3.5/3.6) and name-based VLMs.
    from ..vision_utils import is_vision_model_sync as _is_vision_model

    # Get models from global state (populated by initialize_backend)
    available = global_state.get("available_models", [])

    # If global state is empty, try to fetch from backend
    if not available:
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
        except Exception as e:
            log_message(f"⚠️ API: Failed to fetch models: {e}")
    else:
        # Use cached models - need to reconstruct dict
        # Global state stores display labels, we need to extract IDs
        for display_label in available:
            # Extract model ID from display label (e.g., "qwen3:8b (2.3 GB)" -> "qwen3:8b")
            model_id = display_label.split(" (")[0] if " (" in display_label else display_label
            models_dict[model_id] = display_label
            if _is_vision_model(model_id):
                vision_models.append(model_id)

    return ModelsResponse(
        backend_type=backend_type,
        models=models_dict,
        vision_models=vision_models
    )
