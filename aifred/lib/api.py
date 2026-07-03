"""
AIfred REST API Module

Provides HTTP API endpoints for remote control of AIfred.
Uses FastAPI and is mounted at /api by Reflex's app.api.mount().

API Documentation: http://localhost:8002/api/docs

Endpoints (all prefixed with /api):
- GET  /health              - Health check
- GET  /settings            - Get all settings
- PATCH /settings           - Update settings
- GET  /models              - List available models
- GET  /sessions            - List all sessions
- POST /chat/inject         - Inject message into browser session
- GET  /chat/status         - Get chat/generation status
- POST /chat/clear          - Clear chat history
- GET  /chat/history        - Get chat history
- POST /system/restart-ollama   - Restart Ollama service
- POST /system/restart-aifred   - Restart AIfred service
- POST /system/clear-vectordb   - Clear Vector DB
- POST /system/reset-defaults   - Reset to default settings
- POST /calibrate           - Run context calibration
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from pathlib import Path
import asyncio
import html as _html
import subprocess
import time

from .settings import load_settings, save_settings, get_default_settings
from .formatting import format_number
from .logging_utils import log_message
from .config import DEFAULT_OLLAMA_URL

API_VERSION = "3.1.0"


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


class ChatInjectRequest(BaseModel):
    """Chat inject request - injects message into browser session"""
    message: str = Field(..., min_length=1, description="User message to inject")
    session_id: str = Field(..., description="Browser session session_id (required)")
    token: str = Field(
        "", description="Auth token (configured in INJECT_API_TOKEN env var)"
    )


class ChatHistoryResponse(BaseModel):
    """Chat history response"""
    chat_history: List[Dict[str, str]]  # [{user: "...", assistant: "..."}]
    llm_history: List[Dict[str, str]]  # [{role: "user/assistant/system", content: "..."}]
    session_id: str = ""


class SystemActionResponse(BaseModel):
    """Response for system actions"""
    success: bool
    message: str
    details: Optional[str] = None


# ============================================================
# FastAPI App
# ============================================================

api_app = FastAPI(
    title="AIfred Intelligence API",
    description="REST API for remote control of AIfred Intelligence",
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# NOTE: CORS middleware is NOT added here.
# Reflex handles CORS via its own middleware when using api_transformer.
# Adding CORSMiddleware here causes "ASGI flow error: Connection already upgraded"
# because it conflicts with Reflex's WebSocket upgrade handling.


# ============================================================
# Global State Access
# ============================================================

def get_global_backend_state() -> Dict[str, Any]:
    """Get reference to global backend state from state.py"""
    try:
        from ..state import _global_backend_state
        return _global_backend_state
    except ImportError:
        return {}


def get_active_session_state():
    """
    Get the active AIState session if one exists.

    NOTE: This is tricky because Reflex state is session-bound.
    We use a module-level reference that gets set when a session is active.
    """
    try:
        from .. import state as _state_module
        return getattr(_state_module, "_active_api_state", None)
    except ImportError:
        return None


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
        # Models can be in backend_models or directly in settings
        aifred_model=settings.get("model", backend_models.get("aifred_model", "")),
        sokrates_model=settings.get("sokrates_model", ""),
        salomo_model=settings.get("salomo_model", ""),
        automatik_model=settings.get("automatik_model", backend_models.get("automatik_model", "")),
        vision_model=settings.get("vision_model", backend_models.get("vision_model", "")),
        aifred_rope_factor=settings.get("aifred_rope_factor", 1.0),
        sokrates_rope_factor=settings.get("sokrates_rope_factor", 1.0),
        salomo_rope_factor=settings.get("salomo_rope_factor", 1.0),
        automatik_rope_factor=settings.get("automatik_rope_factor", 1.0),
        vision_rope_factor=settings.get("vision_rope_factor", 1.0),
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

    # Map API field names to settings.json field names
    field_mapping = {
        "aifred_model": "model",
        "tts_voice": "voice",
        "whisper_model_key": "whisper_model",
    }

    for api_field, value in update_dict.items():
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
    from .vision_utils import is_vision_model_sync as _is_vision_model

    # Get models from global state (populated by initialize_backend)
    available = global_state.get("available_models", [])

    # If global state is empty, try to fetch from backend
    if not available:
        try:
            import httpx
            if backend_type == "ollama":
                backend_url = settings.get("backend_url", DEFAULT_OLLAMA_URL)
                response = httpx.get(f"{backend_url}/api/tags", timeout=5.0)
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


# ============================================================
# Chat Endpoints
# ============================================================

class ChatInjectResponse(BaseModel):
    """Chat inject response"""
    success: bool
    message: str
    session_id: str = ""
    queued: bool = True


@api_app.post("/chat/inject", response_model=ChatInjectResponse, tags=["Chat"])
async def inject_message(request: ChatInjectRequest):
    """
    Inject a message into a browser session.

    The message is queued for the browser to process. The browser will
    automatically pick up the message and run the full pipeline:
    - Intent Detection
    - Research/Automatik Mode
    - Multi-Agent (Sokrates/Tribunal)
    - History Compression

    This ensures the API uses the exact same code path as manual browser input.
    The user sees everything live in the browser - streaming, debug messages, etc.

    Requires session_id to identify the target browser session.
    Use GET /api/sessions to list available sessions.
    """
    from .session_storage import set_pending_message

    # Auth: inject läuft die volle Agenten-Pipeline (inkl. Tools) auf der
    # Ziel-Session — also hinter ein Token klemmen, fail-closed. Ohne
    # konfiguriertes Token ist der Endpoint deaktiviert (kein offener
    # Remote-Control-Zugang). Zentrale, konstant-zeitige Prüfung in lib/auth.
    from .auth import require_service_token
    require_service_token("inject", request.token)

    log_message(f"📨 API: Injecting message to {request.session_id[:8]}...")

    success = set_pending_message(request.session_id, request.message)

    if success:
        log_message(f"✅ API: Message queued for {request.session_id[:8]}...")
        return ChatInjectResponse(
            success=True,
            message="Message queued for browser processing",
            session_id=request.session_id,
            queued=True
        )
    else:
        log_message(f"❌ API: Failed to queue message for {request.session_id[:8]}...")
        raise HTTPException(status_code=500, detail="Failed to queue message")


class ChatStatusResponse(BaseModel):
    """Chat status response"""
    is_generating: bool = False
    message_count: int = 0
    session_id: str = ""


@api_app.get("/chat/status", response_model=ChatStatusResponse, tags=["Chat"])
async def get_chat_status(session_id: str):
    """
    Get current chat status for a session.

    Use this to poll for completion after injecting a message.
    When is_generating becomes False, the response is complete.

    Args:
        session_id: Browser session session_id
    """
    from .session_storage import load_session

    session = load_session(session_id)
    if not session or "data" not in session:
        raise HTTPException(status_code=404, detail="Session not found")

    data = session["data"]
    chat_history = data.get("chat_history", [])

    return ChatStatusResponse(
        is_generating=data.get("is_generating", False),
        message_count=len(chat_history),
        session_id=session_id
    )


class ChatClearRequest(BaseModel):
    """Chat clear request"""
    session_id: Optional[str] = Field(None, description="Browser session session_id to clear")


@api_app.post("/chat/clear", response_model=SystemActionResponse, tags=["Chat"])
async def clear_chat(request: ChatClearRequest = ChatClearRequest(session_id=None)):
    """
    Clear chat history for a browser session.

    If session_id is provided, clears that session's chat history.
    Otherwise clears the most recently modified session.
    The browser will auto-reload and show empty chat.
    """
    from .session_storage import (
        update_chat_data, get_latest_session_file
    )

    # Determine which session to clear
    session_id = request.session_id
    if not session_id:
        session_file = get_latest_session_file()
        if session_file:
            session_id = session_file.stem
        else:
            return SystemActionResponse(
                success=False,
                message="No sessions found to clear"
            )

    # Clear the session's chat data (including debug console, like browser button)
    success = update_chat_data(
        session_id=session_id,
        chat_history=[],
        llm_history=[],
        debug_messages=[]  # Clear debug console too!
    )

    if success:
        # Browser detects the change automatically via session file mtime-watch (SSOT)
        log_message(f"🗑️ API: Chat session {session_id[:8]}... cleared")
        return SystemActionResponse(
            success=True,
            message=f"Chat session {session_id[:8]}... cleared"
        )
    else:
        return SystemActionResponse(
            success=False,
            message=f"Failed to clear session {session_id[:8]}..."
        )


class SessionInfo(BaseModel):
    """Session info for listing"""
    session_id: str
    last_seen: str
    message_count: int


class SessionsListResponse(BaseModel):
    """List of all sessions"""
    sessions: List[SessionInfo]


@api_app.get("/sessions", response_model=SessionsListResponse, tags=["Chat"])
async def list_all_sessions():
    """
    List all available sessions.

    Returns session_id, last_seen, and message_count for each session.
    Use session_id with /api/chat/send to write to a specific browser session.
    """
    from .session_storage import list_sessions

    sessions = list_sessions()
    return SessionsListResponse(
        sessions=[SessionInfo(**s) for s in sessions]
    )


@api_app.get("/chat/history", response_model=ChatHistoryResponse, tags=["Chat"])
async def get_chat_history(session_id: Optional[str] = None):
    """
    Get chat history for a session.

    If session_id is provided, returns that session's history.
    Otherwise returns the most recently modified session.

    Returns both the UI-friendly chat_history and the LLM-optimized llm_history.
    """
    from .session_storage import load_session, get_latest_session_file

    try:
        # Determine which session to load
        if session_id:
            session_data = load_session(session_id)
            session_id = session_id
        else:
            session_file = get_latest_session_file()
            if session_file:
                session_data = load_session(session_file.stem)
                session_id = session_file.stem
            else:
                session_data = None
                session_id = ""

        if session_data and "data" in session_data:
            # Convert chat_history from internal format (role/content dicts) to API format
            chat_history = []
            stored_history = session_data["data"].get("chat_history", [])
            # Pair consecutive user/assistant messages
            i = 0
            while i < len(stored_history):
                msg = stored_history[i]
                if isinstance(msg, dict) and msg.get("role") == "user":
                    user_text = msg.get("content", "")
                    assistant_text = ""
                    if i + 1 < len(stored_history):
                        next_msg = stored_history[i + 1]
                        if isinstance(next_msg, dict) and next_msg.get("role") == "assistant":
                            assistant_text = next_msg.get("content", "")
                            i += 1
                    chat_history.append({"user": user_text, "assistant": assistant_text})
                i += 1

            return ChatHistoryResponse(
                chat_history=chat_history,
                llm_history=session_data["data"].get("llm_history", []),
                session_id=session_id
            )
    except Exception as e:
        log_message(f"⚠️ API: Failed to load session: {e}")

    return ChatHistoryResponse(
        chat_history=[],
        llm_history=[],
        session_id=""
    )


# ============================================================
# Session Config Endpoint (agent/mode per session - SSOT)
# ============================================================


class SessionConfigRequest(BaseModel):
    """Session config update request.

    Only fields that are provided (non-None) will be updated.
    Browser tabs viewing this session detect the change automatically
    via mtime-watching on the session file.
    """
    session_id: str = Field(..., description="Session identifier")
    active_agent: Optional[str] = Field(
        None,
        description="Agent that responds to messages (e.g. 'aifred', 'sokrates', custom IDs)"
    )
    multi_agent_mode: Optional[str] = Field(
        None,
        description="Multi-agent mode: 'standard', 'sokrates', 'tribunal', 'symposion', 'critical_review', 'auto_consensus'"
    )
    symposion_agents: Optional[List[str]] = Field(
        None,
        description="Selected agents for Symposion mode (list of agent IDs)"
    )
    research_mode: Optional[str] = Field(
        None,
        description="Research mode: 'none', 'quick', 'deep', 'automatik'"
    )


class SessionConfigResponse(BaseModel):
    """Session config response (reflects the current state after update)."""
    success: bool
    session_id: str
    config: Dict[str, Any]


@api_app.post("/session/config", response_model=SessionConfigResponse, tags=["Chat"])
async def update_session_config_endpoint(request: SessionConfigRequest):
    """
    Update the config block (agent, mode, research mode) of a session.

    Only the fields you provide are updated — omit a field to leave it
    unchanged. Browser tabs viewing this session detect the change
    automatically via the session file mtime and reload their UI.

    Use this endpoint to:
    - Switch agents from external scripts/automation
    - Change discussion modes programmatically
    - Integrate voice assistants / external channels

    Returns the full current config after the update.
    """
    from .session_storage import update_session_config, get_session_config

    updates = request.model_dump(exclude={"session_id"}, exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=400,
            detail="No config fields provided (at least one of: active_agent, multi_agent_mode, symposion_agents, research_mode)"
        )

    success = update_session_config(request.session_id, **updates)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Session {request.session_id[:8]}... not found"
        )

    current = get_session_config(request.session_id)
    log_message(
        f"⚙️ API: Session {request.session_id[:8]}... config updated: {updates}"
    )
    return SessionConfigResponse(
        success=True,
        session_id=request.session_id,
        config=current,
    )


# ============================================================
# System Endpoints
# ============================================================

@api_app.post("/system/restart-ollama", response_model=SystemActionResponse, tags=["System"])
async def restart_ollama():
    """
    Restart Ollama service.

    Uses systemctl to restart the ollama service.
    Waits for the service to be ready before returning.
    """
    log_message("🔄 API: Restarting Ollama service...")

    try:
        # Restart via systemctl
        result = subprocess.run(
            ["systemctl", "restart", "ollama"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"systemctl restart failed: {result.stderr}"
            )

        # Wait for Ollama to be ready
        import httpx
        settings = load_settings() or {}
        backend_url = settings.get("backend_url", DEFAULT_OLLAMA_URL)

        for attempt in range(20):  # 10 seconds max
            await asyncio.sleep(0.5)
            try:
                response = httpx.get(f"{backend_url}/api/tags", timeout=2.0)
                if response.status_code == 200:
                    log_message(f"✅ API: Ollama ready after {(attempt+1)*0.5:.1f}s")
                    return SystemActionResponse(
                        success=True,
                        message="Ollama restarted successfully",
                        details=f"Ready after {(attempt+1)*0.5:.1f}s"
                    )
            except httpx.RequestError:
                continue

        return SystemActionResponse(
            success=True,
            message="Ollama restart initiated",
            details="Service may still be starting"
        )

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Restart timed out")
    except Exception as e:
        log_message(f"❌ API: Ollama restart failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_app.post("/system/restart-aifred", response_model=SystemActionResponse, tags=["System"])
async def restart_aifred(background_tasks: BackgroundTasks):
    """
    Restart AIfred service.

    Schedules a restart and returns immediately.
    The service will restart after a short delay.
    """
    log_message("🔄 API: AIfred restart requested...")

    from .process_utils import restart_service

    def delayed_restart():
        import time
        time.sleep(1)
        restart_service("aifred-intelligence", check=False)

    background_tasks.add_task(delayed_restart)

    return SystemActionResponse(
        success=True,
        message="AIfred restart scheduled",
        details="Service will restart in ~1 second"
    )


@api_app.post("/system/clear-vectordb", response_model=SystemActionResponse, tags=["System"])
async def clear_vector_db():
    """
    Clear Vector DB (ChromaDB).

    Deletes all cached research entries from ChromaDB.
    The collection structure remains intact.
    """
    log_message("🗑️ API: Clearing Vector DB...")

    try:
        import chromadb
        client = chromadb.HttpClient(host='localhost', port=8000)
        collection = client.get_collection('research_cache')

        # Get all IDs
        all_ids = collection.get(include=[])["ids"]
        count = len(all_ids)

        if all_ids:
            collection.delete(ids=all_ids)
            log_message(f"✅ API: Deleted {count} entries from Vector DB")
            return SystemActionResponse(
                success=True,
                message=f"Vector DB cleared ({count} entries deleted)"
            )
        else:
            return SystemActionResponse(
                success=True,
                message="Vector DB is already empty"
            )

    except Exception as e:
        log_message(f"❌ API: Vector DB clear failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_app.post("/system/reset-defaults", response_model=SystemActionResponse, tags=["System"])
async def reset_to_defaults():
    """
    Reset all settings to defaults.

    Loads default values from config.py and saves them to settings.json.
    Backend restart may be required for changes to take effect.
    """
    from .settings import reset_to_defaults as do_reset

    log_message("💾 API: Resetting to default settings...")

    if do_reset():
        log_message("✅ API: Settings reset to defaults")
        return SystemActionResponse(
            success=True,
            message="Settings reset to defaults",
            details="Restart backend for changes to take effect"
        )
    else:
        raise HTTPException(status_code=500, detail="Failed to reset settings")


@api_app.post("/calibrate", response_model=SystemActionResponse, tags=["System"])
async def run_calibration():
    """
    Run context window calibration.

    Tests maximum context size for each model configuration.
    Results are cached for faster subsequent use.

    NOTE: This can take several minutes depending on number of models.
    """
    log_message("🔧 API: Starting context calibration...")

    try:
        from .model_vram_cache import calibrate_all_models  # type: ignore[attr-defined]

        settings = load_settings() or {}
        backend_type = settings.get("backend_type", "ollama")

        if backend_type != "ollama":
            return SystemActionResponse(
                success=False,
                message="Calibration only supported for Ollama backend"
            )

        # Run calibration (this can take a while)
        results = await calibrate_all_models()

        calibrated_count = len([r for r in results if r.get("success")])

        log_message(f"✅ API: Calibration complete ({calibrated_count} models)")

        return SystemActionResponse(
            success=True,
            message=f"Calibration complete ({calibrated_count} models calibrated)",
            details=str(results)
        )

    except Exception as e:
        log_message(f"❌ API: Calibration failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Browser Push Bus — reflex-independent server→browser channel (SSE)
# ============================================================
#
# One pipeline for everything the server needs to push to the browser
# WITHOUT going through Reflex state deltas. A bare asyncio.create_task
# mutates server state but Reflex never pushes that delta — so background
# work (streaming-TTS finalize, session-title generation, …) announces its
# results over this bus instead. The browser's EventSource consumes them
# and updates the DOM directly.
#
# Each event carries a ``kind`` field plus kind-specific metadata; the JS
# client routes by kind. Server-side all kinds share one queue, one
# monotonic version counter and the SSE replay logic.
#
# Audio kinds keep the user-gesture inheritance: the EventSource opens in
# the Send-button click chain, so audio.play() is allowed for tts/media.
#
# Kinds:
#   - "tts"          : ``{kind, url, version, playback_rate}``
#   - "media"        : ``{kind, url, version, state_key, start_pos_sec,
#                         is_stream, audio_type}``
#   - "stop"/"pause"/"resume"/"seek"/"speed" : audio control events
#   - "bubble_audio" : combined replay URL for a finished chat bubble
#   - "session_title": ``{kind, url, version}`` — url carries the title text
#
# To add a new kind see docs/de/architecture/browser-push-bus.md.

# Per-session storage: {session_id: {"queue": [...items...], "version": int,
#                                    "playback_rate": str}}
_browser_event_storage: Dict[str, Dict[str, Any]] = {}

# Per-session asyncio.Queue for SSE listeners. Pushed alongside _browser_event_storage.
_browser_sse_queues: Dict[str, asyncio.Queue] = {}


class BrowserEventQueueResponse(BaseModel):
    """Response for browser-event queue polling (fallback when SSE unavailable)."""
    queue: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Audio items {kind, url, ...metadata} to play",
    )
    version: int = Field(default=0, description="Queue version for change detection")
    playback_rate: str = Field(default="1.0x", description="Playback speed")


def browser_push(
    session_id: str,
    kind: str,
    url: str,
    *,
    playback_rate: str = "1.0x",
    state_key: str = "",
    start_pos_sec: float = 0.0,
    is_stream: bool = False,
    audio_type: str = "music",
    position_sec: float = 0.0,
    relative: bool = False,
    factor: float = 1.0,
) -> None:
    """Push an event to the reflex-independent browser push bus.

    The ``url`` field is the generic payload slot: for audio kinds it is an
    audio URL, for ``session_title`` / ``debug`` it carries the text.

    Kinds and their metadata:
      - ``"tts"``    : chunk-stream, gapless. {url, playback_rate}
      - ``"media"``  : single-track, position-saved. {url, state_key,
                       start_pos_sec, is_stream, audio_type, playback_rate}
      - ``"stop"``   : halt + clear src + final-position-save. (no metadata)
      - ``"pause"``  : halt, keep src + position. (no metadata)
      - ``"resume"`` : continue from current position. (no metadata)
      - ``"seek"``   : jump to ``position_sec`` (or ±N sec when ``relative=True``).
      - ``"speed"``  : set ``audio.playbackRate`` to ``factor`` (0.25–4.0).
      - ``"bubble_audio"`` : streaming-TTS finalize announces the combined
                       replay URL so custom.js can attach it to the latest
                       bubble's speaker button (Reflex pushes no delta from
                       the background create_task). {url}
      - ``"session_title"`` : a background task finished title generation —
                       ``url`` holds the title; custom.js updates the
                       session-list entry. {url}

    Versions are monotonic per session — they NEVER decrease, even after
    a clear at the start of a new message. The client dedupes by version
    on SSE events; if the counter wrapped back to 0 the new response
    would be silently skipped (v1 <= queueVersion=3 → "already-known").
    """
    if session_id not in _browser_event_storage:
        _browser_event_storage[session_id] = {
            "queue": [],
            "version": 0,
            "playback_rate": "1.0x",
        }

    storage = _browser_event_storage[session_id]
    storage["version"] += 1
    new_version = storage["version"]

    item: Dict[str, Any] = {
        "kind": kind,
        "url": url,
        "version": new_version,
        "playback_rate": playback_rate,
    }
    if kind == "media":
        item["state_key"] = state_key
        item["start_pos_sec"] = float(start_pos_sec)
        item["is_stream"] = bool(is_stream)
        item["audio_type"] = audio_type
    elif kind == "seek":
        item["position_sec"] = float(position_sec)
        item["relative"] = bool(relative)
    elif kind == "speed":
        item["factor"] = float(factor)

    storage["queue"].append(item)
    storage["playback_rate"] = playback_rate
    log_message(
        f"📡 Browser Bus: Pushed {kind} v{new_version} "
        f"{url.split('/')[-1] if url else '(no url)'} for session {session_id[:8]}..."
    )

    # Also push to SSE queue if listener is connected
    if session_id in _browser_sse_queues:
        try:
            _browser_sse_queues[session_id].put_nowait(item)
            log_message(f"📡 Browser SSE: Queued {kind} v{new_version} (session {session_id[:8]}...)")
        except asyncio.QueueFull:
            log_message("⚠️ Browser SSE: Queue full, skipping")
    else:
        active_sessions = list(_browser_sse_queues.keys())
        if active_sessions:
            active_short = [s[:8] for s in active_sessions]
            log_message(
                f"⚠️ Browser SSE: No queue for session {session_id[:8]}... "
                f"(active SSE sessions: {active_short})"
            )
        else:
            log_message(
                f"⚠️ Browser SSE: No queue for session {session_id[:8]}... "
                f"(no SSE connections at all)"
            )


def browser_queue_clear(session_id: str) -> None:
    """Clear queued items for session (called at start of new message).

    The monotonic version counter is INTENTIONALLY preserved — clients
    dedupe SSE events by version, and a counter reset would make the next
    message's v1, v2, ... look like already-seen items to the client.
    """
    if session_id in _browser_event_storage:
        _browser_event_storage[session_id]["queue"] = []
        log_message(
            f"📡 Browser Bus: Cleared queue for session {session_id[:8]}... "
            f"(version stays at {_browser_event_storage[session_id]['version']})"
        )


@api_app.get("/browser/queue/{session_id}", response_model=BrowserEventQueueResponse, tags=["Browser"])
async def get_browser_queue(session_id: str, since_version: int = 0):
    """Polling fallback for the browser push bus (use SSE for real-time)."""
    if session_id not in _browser_event_storage:
        return BrowserEventQueueResponse(queue=[], version=0, playback_rate="1.0x")

    storage = _browser_event_storage[session_id]

    if storage["version"] <= since_version:
        return BrowserEventQueueResponse(
            queue=[], version=storage["version"], playback_rate=storage["playback_rate"]
        )

    return BrowserEventQueueResponse(
        queue=list(storage["queue"]),
        version=storage["version"],
        playback_rate=storage["playback_rate"],
    )


@api_app.delete("/browser/queue/{session_id}", tags=["Browser"])
async def clear_browser_queue(session_id: str):
    """Clear the browser push bus queue for session."""
    browser_queue_clear(session_id)
    return {"status": "ok", "message": "Queue cleared"}


@api_app.get("/browser/stream/{session_id}", tags=["Browser"])
async def browser_stream(session_id: str, request: Request):
    """Server-Sent Events for the reflex-independent browser push bus.

    Browser opens this connection once. Server pushes audio items
    immediately when they become available — no polling needed.

    Reconnect-safe: each event carries ``id: <version>``. On reconnect
    the browser auto-sends ``Last-Event-ID``, so we only replay items
    with a higher version — no duplicates, no client-side reset.
    """
    from fastapi.responses import StreamingResponse
    import json

    last_event_id_raw = request.headers.get("last-event-id", "0")
    try:
        last_event_id = int(last_event_id_raw)
    except (TypeError, ValueError):
        last_event_id = 0

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        old_queue = _browser_sse_queues.get(session_id)
        if old_queue is not None:
            log_message(
                f"📡 Browser SSE: Replacing existing queue for session "
                f"{session_id[:8]}... (reconnect, last_id={last_event_id})"
            )
        _browser_sse_queues[session_id] = queue
        log_message(
            f"📡 Browser SSE: Stream opened for session {session_id[:8]}... "
            f"(last_id={last_event_id})"
        )

        # Flush HTTP headers immediately by yielding an SSE comment.
        # Without this, the proxy buffers until the first data (up to 15s
        # keepalive), keeping EventSource stuck in CONNECTING state.
        yield ": connected\n\n"

        # Replay items the client missed (version > last_event_id).
        if session_id in _browser_event_storage:
            storage = _browser_event_storage[session_id]
            if storage["queue"]:
                missed = [it for it in storage["queue"] if it["version"] > last_event_id]
                if missed:
                    log_message(
                        f"📡 Browser SSE: Replaying {len(missed)} missed item(s) "
                        f"(v{missed[0]['version']}..v{missed[-1]['version']})"
                    )
                    for it in missed:
                        data = json.dumps(it)
                        yield f"id: {it['version']}\ndata: {data}\n\n"

        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    data = json.dumps(item)
                    yield f"id: {item['version']}\ndata: {data}\n\n"
                    log_message(
                        f"📡 Browser SSE: Sent {item.get('kind', '?')} "
                        f"v{item['version']} {item.get('url', '').split('/')[-1]}"
                    )

                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"

                except asyncio.CancelledError:
                    log_message(f"📡 Browser SSE: Stream cancelled for session {session_id[:8]}...")
                    break

        finally:
            # Only delete OUR queue — a reconnection may have already replaced it.
            if _browser_sse_queues.get(session_id) is queue:
                del _browser_sse_queues[session_id]
                log_message(
                    f"📡 Browser SSE: Stream closed for session {session_id[:8]}... "
                    f"(queue cleaned up)"
                )
            else:
                log_message(
                    f"📡 Browser SSE: Stream closed for session {session_id[:8]}... "
                    f"(queue already replaced by reconnect)"
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ============================================================
# ============================================================
# AGENT TRIGGER (Webhook API)
# ============================================================

class AgentTriggerRequest(BaseModel):
    """Request to trigger an agent action."""
    message: str = Field(..., description="Message/prompt for the agent")
    agent: str = Field(default="aifred", description="Agent to use (aifred, sokrates, salomo)")
    token: str = Field(..., description="Auth token (configured in WEBHOOK_API_TOKEN env var)")
    max_tier: int = Field(default=0, description="Max security tier (0=readonly, 1=communicate)")
    delivery: str = Field(default="log", description="Delivery mode: log, announce, review, webhook")
    channel: str = Field(default="", description="Target channel for announce delivery")
    recipient: str = Field(default="", description="Recipient for announce delivery")
    webhook_url: str = Field(default="", description="URL for webhook delivery")


class AgentTriggerResponse(BaseModel):
    """Response from agent trigger."""
    success: bool
    session_id: str = ""
    message: str = ""


@api_app.post("/agent/trigger", response_model=AgentTriggerResponse, tags=["Agent"])
async def trigger_agent(request: AgentTriggerRequest, background_tasks: BackgroundTasks):
    """
    Trigger an agent action from an external system.

    Runs in an isolated session with security tier enforcement.
    The result is delivered based on the delivery mode.
    Auth via token (WEBHOOK_API_TOKEN env var).
    """
    # Auth check (constant-time, fail-closed — zentrale Prüfung in lib/auth)
    from .auth import require_service_token
    require_service_token("webhook", request.token)

    # Tier cap: webhooks max tier 1 by default
    from .security import DEFAULT_TIER_BY_SOURCE
    max_allowed = DEFAULT_TIER_BY_SOURCE.get("webhook", 0)
    effective_tier = min(request.max_tier, max_allowed)

    log_message(f"API: agent/trigger — '{request.message[:50]}...' (agent={request.agent}, tier={effective_tier})")

    # Pre-allocate session + routing so the synchronous response can
    # already return the session_id while the engine call runs in the
    # background. process_inbound picks up the existing session via
    # routing_table.get_route().
    import secrets
    from .config import MESSAGE_HUB_OWNER
    from .session_storage import create_empty_session
    from .routing_table import routing_table

    session_id = secrets.token_hex(16)
    channel_id = secrets.token_hex(8)
    if not create_empty_session(session_id, owner=MESSAGE_HUB_OWNER):
        raise HTTPException(status_code=500, detail="Failed to create session")
    routing_table.set_route("webhook", channel_id, session_id)

    async def _run():
        from datetime import datetime
        from .envelope import InboundMessage
        from .message_processor import process_inbound
        from .scheduler import _deliver_result, Job

        msg = InboundMessage(
            channel="webhook",
            channel_id=channel_id,
            sender=MESSAGE_HUB_OWNER,
            text=request.message,
            timestamp=datetime.now(),
            metadata={
                "wake_agent": request.agent,
                # security.py honors max_tier on the webhook channel only.
                "max_tier": effective_tier,
            },
            target_agent=request.agent,
        )

        outbound = await process_inbound(msg)

        if outbound is None or not outbound.text:
            return

        # Create a minimal Job for delivery (delivery config comes from the
        # webhook request, not from the scheduler store).
        job = Job(
            job_id=0,
            name="webhook_trigger",
            schedule_type="once",
            schedule_expr="",
            payload={
                "delivery": request.delivery,
                "channel": request.channel,
                "recipient": request.recipient,
                "webhook_url": request.webhook_url,
            },
        )
        await _deliver_result(job, outbound.text, session_id)

    background_tasks.add_task(_run)

    return AgentTriggerResponse(
        success=True,
        session_id=session_id,
        message="Agent triggered, running in background",
    )


# ============================================================
# OAuth routes
# ============================================================

@api_app.get("/oauth/{provider}/callback", response_class=HTMLResponse, include_in_schema=False)
async def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    """OAuth 2.0 callback endpoint.  Google redirects here after user login."""
    from .oauth import oauth_broker

    if error:
        return HTMLResponse(
            f"<html><body><h2>❌ OAuth abgebrochen</h2><p>{_html.escape(error)}</p>"
            "<p>Du kannst dieses Fenster schließen.</p></body></html>",
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            "<html><body><h2>❌ Ungültiger Callback</h2>"
            "<p>Fehlende Parameter. Starte den Login-Flow neu.</p></body></html>",
            status_code=400,
        )

    try:
        await oauth_broker.handle_callback(state, code, expected_provider=provider)
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;padding:2rem'>"
            f"<h2>✅ {_html.escape(provider.capitalize())} verbunden!</h2>"
            "<p>Tokens gespeichert. Du kannst dieses Fenster schließen.</p>"
            "</body></html>"
        )
    except Exception as exc:
        return HTMLResponse(
            f"<html><body><h2>❌ Fehler</h2><p>{_html.escape(str(exc))}</p></body></html>",
            status_code=400,
        )


@api_app.get("/oauth/{provider}/status")
async def oauth_status(provider: str) -> dict:
    """Check whether a provider is connected (tokens stored)."""
    from .oauth import oauth_broker
    return {"provider": provider, "connected": oauth_broker.is_connected(provider)}


@api_app.get("/oauth/{provider}/auth-url")
async def oauth_auth_url(provider: str, redirect_uri: str, scopes: str = "") -> dict:
    """Generate an authorization URL to start the OAuth flow.

    ``scopes``: comma-separated list of OAuth scopes.
    ``redirect_uri``: the callback URL (must match Google Cloud Console registration).
    """
    from .oauth import oauth_broker
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    try:
        url = oauth_broker.get_auth_url(provider, scope_list, redirect_uri)
        return {"auth_url": url}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@api_app.delete("/oauth/{provider}")
async def oauth_disconnect(provider: str) -> dict:
    """Remove stored tokens for a provider."""
    from .oauth import oauth_broker
    await oauth_broker.disconnect(provider)
    return {"provider": provider, "disconnected": True}


# ============================================================
# Agent Bundle Export / Import
# ============================================================


@api_app.get("/agents/export", tags=["Agents"])
async def export_agents_endpoint(ids: str) -> Response:
    """Download a ZIP bundle containing one or more agents.

    ``ids`` is a comma-separated list of agent IDs (e.g. ``?ids=<id1>,<id2>``).
    A GET endpoint is used so the UI can trigger a plain browser download
    via ``<a href>`` without orchestrating a fetch+blob roundtrip.
    """
    from .agent_bundle import export_bundle

    agent_ids = [a.strip() for a in ids.split(",") if a.strip()]
    if not agent_ids:
        raise HTTPException(status_code=400, detail="No agents selected")

    try:
        data = export_bundle(agent_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log_message(f"export_bundle failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    if len(agent_ids) == 1:
        filename = f"{agent_ids[0]}.aifred-agent.zip"
    else:
        filename = f"aifred-agents-{int(time.time())}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_app.post("/agents/import/peek", tags=["Agents"])
async def peek_agent_bundle(file: UploadFile = File(...)) -> dict:
    """Inspect a bundle without writing — returns the list of agents inside
    plus a flag per agent indicating whether it already exists locally.
    """
    from .agent_bundle import peek_bundle

    try:
        zip_bytes = await file.read()
        return peek_bundle(zip_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Bundle nicht lesbar: {exc}")


@api_app.post("/agents/import", tags=["Agents"])
async def import_agents_endpoint(
    file: UploadFile = File(...),
    ids: str = Form(""),
    conflict: str = Form("abort"),
) -> dict:
    """Import selected agents from a bundle ZIP.

    ``ids`` (comma-separated) selects which agents to import; empty means
    all agents in the bundle. ``conflict`` is one of abort|overwrite|rename.
    """
    from .agent_bundle import import_bundle

    if conflict not in ("abort", "overwrite", "rename"):
        raise HTTPException(status_code=400, detail=f"Invalid conflict strategy: {conflict}")

    selected = [a.strip() for a in ids.split(",") if a.strip()] or None

    try:
        zip_bytes = await file.read()
        effective_ids, warnings = import_bundle(
            zip_bytes, selected_ids=selected, conflict=conflict,  # type: ignore[arg-type]
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log_message(f"import_bundle failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"success": True, "agent_ids": effective_ids, "warnings": warnings}


# ============================================================
# Audio Player API (file streaming + position sync)
# ============================================================

# In-memory map: session_id -> last requested state_key (for re-resolve from JS)
_audio_active: Dict[str, str] = {}


def _audio_resolver():  # type: ignore[no-untyped-def]
    """Build a fresh SourceResolver: filesystem-discovery + http_streams.

    Mirrors the plugin's _make_resolver() — local sources come from the
    data/media/audio/ filesystem (folders + symlinks), HTTP streams from
    plugin settings.json. Without this, the file endpoint would only see
    http_stream entries and 404 on every NAS-mounted source.
    """
    import json as _json
    from pathlib import Path as _Path
    from .audio_sources import SourceResolver, build_source_map
    from .config import MEDIA_AUDIO_DIR

    settings_path = (
        _Path(__file__).parent.parent
        / "plugins" / "tools" / "audio_player" / "settings.json"
    )
    streams: Dict[str, Dict[str, str]] = {}
    if settings_path.exists():
        try:
            with open(settings_path, encoding="utf-8") as f:
                data = _json.load(f)
            if isinstance(data, dict):
                streams = {
                    label: src
                    for label, src in data.get("sources", {}).items()
                    if src.get("type") == "http_stream"
                }
        except (OSError, _json.JSONDecodeError):
            pass
    sources = build_source_map(MEDIA_AUDIO_DIR, streams)
    return SourceResolver(sources)


@api_app.get("/audio/file", tags=["Audio"])
async def audio_file(request: Request, key: str):
    """Stream an audio file by state_key. Supports HTTP Range for seeking.

    The key is resolved against the audio_player plugin's source map, so
    the LLM never sees raw paths. Path-traversal is rejected by the resolver.
    """
    from fastapi.responses import StreamingResponse, RedirectResponse
    from .audio_sources import ALLOWED_EXTENSIONS

    try:
        src = _audio_resolver().resolve(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # HTTP streams: redirect the browser to the upstream URL — the browser
    # opens the connection itself, no proxying needed.
    if src.is_stream:
        return RedirectResponse(src.uri, status_code=302)

    file_path = Path(src.uri)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="unsupported audio extension")

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")

    # Map extension → MIME type for HTML5 <audio>
    mime_map = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".flac": "audio/flac", ".m4a": "audio/mp4", ".opus": "audio/ogg",
        ".aac": "audio/aac", ".mp4": "audio/mp4", ".webm": "audio/webm",
    }
    media_type = mime_map.get(file_path.suffix.lower(), "application/octet-stream")

    chunk_size = 64 * 1024

    if range_header:
        # Parse "bytes=start-end"
        try:
            unit, _, ranges = range_header.partition("=")
            if unit.strip().lower() != "bytes":
                raise ValueError("only bytes ranges supported")
            start_str, _, end_str = ranges.partition("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
            if start >= file_size or end >= file_size or start > end:
                raise HTTPException(status_code=416, detail="range not satisfiable")
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="invalid range header")

        length = end - start + 1

        async def iter_range():  # type: ignore[no-untyped-def]
            # File I/O via to_thread — a stalling medium (NFS, USB) must not
            # block the whole event loop.
            def _open_seeked():  # type: ignore[no-untyped-def]
                f = open(file_path, "rb")
                f.seek(start)
                return f

            f = await asyncio.to_thread(_open_seeked)
            try:
                remaining = length
                while remaining > 0:
                    data = await asyncio.to_thread(f.read, min(chunk_size, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
            finally:
                await asyncio.to_thread(f.close)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Cache-Control": "no-cache",
        }
        return StreamingResponse(iter_range(), status_code=206, media_type=media_type, headers=headers)

    # Full file response (with Accept-Ranges so the browser can seek later)
    async def iter_full():  # type: ignore[no-untyped-def]
        f = await asyncio.to_thread(open, file_path, "rb")
        try:
            while True:
                data = await asyncio.to_thread(f.read, chunk_size)
                if not data:
                    break
                yield data
        finally:
            await asyncio.to_thread(f.close)

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(iter_full(), media_type=media_type, headers=headers)


class AudioPositionRequest(BaseModel):
    """Browser → server: persist current playback position for resume."""
    state_key: str
    pos_sec: float
    duration_sec: Optional[float] = None
    completed: bool = False


@api_app.get("/audio/test", tags=["Audio"], response_class=HTMLResponse)
async def audio_test_page(key: str = "music/05-Ausgefressen.mp3"):
    """Standalone test page — verifies endpoint + browser playback without LLM/State."""
    import html as _html
    from urllib.parse import quote
    safe_key = _html.escape(key)
    audio_src = f"/api/audio/file?key={quote(key)}"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>AIfred Audio Test</title>
<style>body{{font-family:system-ui;max-width:720px;margin:40px auto;padding:0 16px;background:#1a1a1a;color:#ddd}}
h1{{font-size:18px;color:#4287f5}} code{{background:#333;padding:2px 6px;border-radius:3px}}
audio{{width:100%;margin:16px 0}} .ok{{color:#4ade80}} .err{{color:#f87171}}</style>
</head><body>
<h1>Audio-Endpoint Test</h1>
<p>State-Key: <code>{safe_key}</code></p>
<p>Source URL: <code>{audio_src}</code></p>
<audio id="testplayer" controls preload="metadata" src="{audio_src}"></audio>
<p>Status: <span id="status">loading…</span></p>
<pre id="log" style="background:#222;padding:10px;border-radius:4px;font-size:12px;max-height:300px;overflow:auto"></pre>
<script>
const a = document.getElementById('testplayer');
const s = document.getElementById('status');
const L = document.getElementById('log');
function log(msg, cls) {{
  const t = new Date().toLocaleTimeString();
  L.textContent += `[${{t}}] ${{msg}}\\n`;
  if (cls) {{ s.textContent = msg; s.className = cls; }}
}}
['loadstart','loadedmetadata','canplay','play','playing','pause','ended','error','stalled','waiting'].forEach(ev => {{
  a.addEventListener(ev, e => log(`event: ${{ev}} | currentTime=${{a.currentTime.toFixed(2)}} | duration=${{isFinite(a.duration)?a.duration.toFixed(1):'?'}} | networkState=${{a.networkState}} | readyState=${{a.readyState}}`));
}});
a.addEventListener('error', () => {{
  const e = a.error;
  log(`ERROR code=${{e?e.code:'?'}} message=${{e?e.message:'?'}}`, 'err');
}});
a.addEventListener('canplay', () => log('✅ canplay — pressing play', 'ok'));
a.addEventListener('play', () => log('▶ playing', 'ok'));
fetch("{audio_src}", {{method:'HEAD'}}).then(r => log(`HEAD response: HTTP ${{r.status}} | content-type=${{r.headers.get('content-type')}} | content-length=${{r.headers.get('content-length')}}`));
</script>
</body></html>"""


@api_app.post("/audio/position", tags=["Audio"])
async def audio_position(req: AudioPositionRequest):
    """Update audio_state.json from the browser's currentTime."""
    from .audio_state import audio_state
    if req.completed:
        audio_state.mark_completed(req.state_key)
        return {"status": "ok", "completed": True}
    # Resolve URI for the state_key (best-effort; failures are non-fatal —
    # the URI is informational, not authoritative)
    try:
        src = _audio_resolver().resolve(req.state_key)
        uri = src.uri
    except ValueError:
        uri = ""
    audio_state.update(
        key=req.state_key,
        uri=uri,
        pos_sec=float(req.pos_sec),
        duration_sec=float(req.duration_sec) if req.duration_sec else None,
    )
    return {"status": "ok"}


# ============================================================
# Vision: live JPEG snapshot + MJPEG stream endpoints
# ============================================================

# resolve_source_resolution lives in vision_utils.py — shared with the
# plugin tools so popup-UI resolution and tool-call resolution match.
# (Import here, mid-module, is intentional: api.py orders imports by
# feature section and ruff has E402 silenced via per-line noqa where
# this pattern recurs.)
from .vision_utils import resolve_source_resolution as _resolve_resolution  # noqa: E402


@api_app.get("/vision/snapshot/{source_id:path}", tags=["Vision"])
async def vision_snapshot_endpoint(
    source_id: str, width: int = 0, height: int = 0
) -> Response:
    """Liefert einen frischen JPEG-Snapshot der genannten Frame-Source.

    Geht durch den FrameHub: wenn schon ein Stream / Watcher läuft,
    bekommt der Snapshot den nächsten Frame aus dem laufenden Loop —
    kein zweiter V4L2-Open. Wenn niemand sonst zugreift, startet der
    Hub einen kurzen Reader und beendet ihn nach Grace-Period.
    """
    from .frame_hub import get_default_hub
    from .frame_sources import get as get_source

    src = get_source(source_id)
    if src is None:
        raise HTTPException(status_code=404, detail=f"unknown source: {source_id}")
    w, h = _resolve_resolution(source_id, width, height)
    hub = get_default_hub()
    try:
        frame = await hub.snapshot(src, width=w, height=h)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"snapshot failed: {e}") from e
    return Response(
        content=frame.image_bytes,
        media_type=f"image/{frame.format}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


# Boundary string used in the multipart MJPEG response. Must match the
# `boundary=` parameter in the Content-Type header exactly.
_MJPEG_BOUNDARY = b"--frame"


def _encode_mjpeg_chunk(frame: Any) -> bytes:
    return bytes(
        _MJPEG_BOUNDARY
        + b"\r\nContent-Type: image/"
        + frame.format.encode()
        + b"\r\nContent-Length: "
        + str(len(frame.image_bytes)).encode()
        + b"\r\n\r\n"
        + frame.image_bytes
        + b"\r\n"
    )


class _MotionOverlay:
    """Brennt die rohe MOG2-Foreground-Maske halbtransparent blau in jeden
    Frame — fürs Bewegungs-Tuning im Zonen-Editor.

    Eigener Detector-State pro Stream (kein globaler Zustand über Requests),
    mit denselben MOG2-Params wie der Watcher, damit die sichtbaren Pixel
    repräsentativ sind. BEWUSST ohne zone_mask: der Nutzer soll auch das
    Rauschen sehen, das er gerade wegmaskieren will. Die Prozent-Auswertung
    (gegen die gemalte Maske) macht der Browser anhand der blauen Pixel —
    der Server bleibt zustandslos.
    """

    # Gesättigtes Blau (BGR), das nach dem Blend immer B >> R und B >> G hat;
    # der Browser erkennt die Foreground-Pixel daran zuverlässig wieder.
    _BLUE = (255.0, 40.0, 0.0)
    _ALPHA = 0.6  # Anteil Blau im Blend (Rest: Originalbild)

    def __init__(self) -> None:
        import numpy as np

        from .vision_filters import MotionDetector
        from .vision_watcher import WatchConfig

        cfg = WatchConfig()
        self._det = MotionDetector(
            history=cfg.motion_history_frames,
            var_threshold=cfg.motion_var_threshold,
            warmup_frames=cfg.motion_warmup_frames,
            return_mask=True,
        )
        self._np = np

    def apply(self, frame: Any) -> Any:
        import dataclasses

        import cv2

        np = self._np
        mask = self._det.process(frame).foreground_mask
        if mask is None or not mask.any():
            return frame
        img = cv2.imdecode(np.frombuffer(frame.image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return frame
        if mask.shape[:2] != img.shape[:2]:
            mask = cv2.resize(
                mask, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST
            )
        sel = mask > 0
        blue = np.array(self._BLUE, dtype=np.float32)
        img[sel] = (
            (1.0 - self._ALPHA) * img[sel].astype(np.float32) + self._ALPHA * blue
        ).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return frame
        return dataclasses.replace(frame, image_bytes=buf.tobytes(), format="jpeg")


async def _mjpeg_stream(
    source: Any, fps: float, width: int = 0, height: int = 0,
    motion_overlay: bool = False,
) -> Any:
    """Async generator yielding MJPEG frames (multipart/x-mixed-replace).

    Geht durch den FrameHub — egal wie viele Browser-Tabs, Watcher
    oder Snapshots gerade aktiv sind, der Hub hält genau einen
    V4L2-Reader pro Source offen. Vorher gab es zwei Pfade (direkter
    source.stream vs. bus.subscribe), die je nach Watcher-Zustand
    umgeschaltet wurden und beim Toggle blackouts produzierten — der
    Hub löst das strukturell.

    ``fps`` ist die vom Browser gewünschte Anzeige-Rate. Der Hub
    selbst läuft mit ``max(fps aller Subscriber)``; dieser Stream
    drosselt clientseitig auf das vom User angeforderte Tempo —
    sonst wirkt das Bildrate-Dropdown nicht, weil der Watcher den
    Hub auf 10 fps zwingt und alle Frames an den Browser durch-
    gereicht werden.
    """
    import time
    from .frame_hub import get_default_hub

    hub = get_default_hub()
    # Drossel-Intervall in Sekunden. Bei fps <= 0 (Manual-Modus) wird
    # dieser Pfad eh nicht genutzt — der Browser holt einzeln über
    # /vision/snapshot.
    interval = 1.0 / max(0.01, float(fps))
    last_emit = 0.0
    overlay = _MotionOverlay() if motion_overlay else None
    async for frame in hub.subscribe(
        source, name="mjpeg-live-preview", fps=fps, width=width, height=height,
    ):
        now = time.monotonic()
        if now - last_emit < interval:
            continue
        last_emit = now
        if overlay is not None:
            frame = overlay.apply(frame)
        yield _encode_mjpeg_chunk(frame)


@api_app.get("/vision/stream/{source_id:path}", tags=["Vision"])
async def vision_stream_endpoint(
    source_id: str, fps: float = 1.0, width: int = 0, height: int = 0,
    overlay: str = "",
) -> Any:
    """MJPEG-Live-Stream der genannten Frame-Source.

    Browser-side: einfach ``<img src="/api/vision/stream/cam/v4l2_0?fps=2">``.
    Der Server hält die Connection offen und liefert kontinuierlich
    JPEGs als multipart/x-mixed-replace. Bei ``fps=0`` setzen wir
    intern auf 1.0 als Minimum — wer wirklich manuelle Frames will,
    nutzt /vision/snapshot.

    Akzeptiert ``fps`` zwischen 0.1 und 30. Werte außerhalb werden
    geklammert. ``width``/``height`` überschreiben den persistierten
    Per-Source-Default; ``0/0`` fällt auf vision_store zurück (gleiche
    Resolve-Logik wie beim Snapshot-Endpoint).

    ``overlay=motion`` brennt die rohe Bewegungsmaske halbtransparent blau
    ein (Zonen-Editor-Tuning) — eigener MOG2-State pro Stream, zustandslos.
    """
    from starlette.responses import StreamingResponse
    from .frame_sources import get as get_source

    src = get_source(source_id)
    if src is None:
        raise HTTPException(status_code=404, detail=f"unknown source: {source_id}")
    # NOTE: deliberately NOT calling src.is_available() here. That
    # method opens cv2.VideoCapture to probe, which races against the
    # previous stream's release() during a stream-switch — the probe
    # fails with "can't open camera by index", we'd return 503, the
    # browser sees an error and shows black. The actual open in
    # source.stream() has a retry loop and handles the cleanup latency
    # properly. Letting the stream attempt the open is the right move.
    # Clamp + sanitize
    fps = max(0.1, min(30.0, float(fps) if fps else 1.0))
    w, h = _resolve_resolution(source_id, width, height)
    return StreamingResponse(
        _mjpeg_stream(src, fps, w, h, motion_overlay=(overlay == "motion")),
        media_type=f"multipart/x-mixed-replace; boundary={_MJPEG_BOUNDARY.decode().lstrip('-')}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            # Tell nginx (and any other reverse proxy that honours this
            # header) to NOT buffer the response. Without it, nginx's
            # default 4 KB proxy_buffer holds back frames until the
            # buffer fills or times out — which is what makes 2-second
            # streams take 4 seconds to deliver and fresh streams
            # show black for a beat.
            "X-Accel-Buffering": "no",
            "Connection": "close",
        },
    )


@api_app.get("/vision/events/{source_id:path}", tags=["Vision"])
async def vision_events_endpoint(source_id: str) -> Any:
    """Server-Sent-Events stream of VLM analysis events for a source.

    Each line is a JSON object emitted by the watcher's continuous-VLM
    path:

        data: {"type":"vlm_analysis","timestamp":"…","description":"…", …}

    The browser opens this with ``new EventSource(...)`` and the
    teleprompter overlay appends each event as it arrives. Stream
    runs until the client disconnects; the watcher itself is
    started/stopped separately via the start/stop endpoints (or via
    the tool plugin's vision_start_watch / vision_stop_watch).
    """
    from starlette.responses import StreamingResponse
    from .vision_event_bus import subscribe

    import json as _json

    async def _gen() -> Any:
        # Initial comment-line so the EventSource sees a successful
        # 200 and any reverse proxy flushes its first response chunk
        # (some proxies wait for the first byte before forwarding).
        yield b":\n\n"
        async for event in subscribe(source_id):
            payload = _json.dumps(event, ensure_ascii=False)
            yield f"data: {payload}\n\n".encode()

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================
# Face Enrollment
# ============================================================


class FaceEnrollRequest(BaseModel):
    """Body fürs Inline-Enroll aus dem Live-Vorschau-Popup. Embedding
    kommt als base64-encoded float32-Array (512 dim für InsightFace
    buffalo_l), wie es im SSE-Event mitgegeben wird."""
    name: str = Field(..., min_length=1, description="Name der Person")
    source_id: str = Field(..., description="Frame-Source-ID, aus der das Embedding stammt")
    embedding_b64: str = Field(..., description="Base64-encodierter float32 numpy-array")


class FaceEnrollResponse(BaseModel):
    success: bool
    face_id: int
    name: str
    is_new: bool


@api_app.post("/vision/face/enroll", response_model=FaceEnrollResponse, tags=["Vision"])
async def vision_face_enroll(request: FaceEnrollRequest) -> FaceEnrollResponse:
    """Enrollen einer neuen Identity (oder weiteres Sample für eine
    bestehende). Wird vom Inline-Button bei ``face_unknown``-Zeilen im
    Live-Vorschau-Popup aufgerufen.

    Idempotent zur Name-Dedup: wenn schon eine ``face_id`` mit dem
    Namen existiert, wird das Embedding als zusätzliches Sample
    angefügt — kein zweiter Datensatz. So kann der User beim
    erneuten ``+ taggen`` einer bekannten Person das Modell mit
    weiteren Posen anreichern.
    """
    import base64 as _b64
    import numpy as _np
    from .vision_store import VisionStore

    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="empty name")

    # Embedding dekodieren
    try:
        emb_bytes = _b64.b64decode(request.embedding_b64, validate=True)
        embedding = _np.frombuffer(emb_bytes, dtype=_np.float32)
        if embedding.size == 0:
            raise ValueError("empty embedding")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"invalid embedding: {e}"
        ) from e

    store = VisionStore()
    is_new = store.get_face_by_name(name) is None
    face_id = store.get_or_create_face(name, enrolled_by="popup")

    try:
        store.add_embedding(face_id, embedding, quality_score=1.0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"add_embedding failed: {e}") from e

    log_message(
        f"🧑 Face enrolled: name='{name}' face_id={face_id} is_new={is_new} "
        f"source={request.source_id}"
    )

    # Alle lebenden Recognizer im Prozess informieren, damit der nächste
    # Frame die neue Identity sofort erkennt (eine frische Instanz zu
    # invalidieren wäre wirkungslos — siehe bump_enrollment_epoch).
    from .vision_filters.face_recognize import bump_enrollment_epoch
    bump_enrollment_epoch()

    return FaceEnrollResponse(
        success=True, face_id=face_id, name=name, is_new=is_new,
    )


# ============================================================
# Personarium — Identity-Verwaltung
# ============================================================


class FaceSummary(BaseModel):
    """Eine Identity-Zeile fürs Personarium."""
    id: int
    name: str
    embedding_count: int
    last_seen: str
    crop_url: str
    notes: str = ""


class FaceSummaryList(BaseModel):
    faces: List[FaceSummary]


class FaceEvent(BaseModel):
    id: int
    timestamp: str
    source_id: str
    crop_url: str
    confidence: float
    confidence_band: str


class FaceDetailResponse(BaseModel):
    id: int
    name: str
    notes: str
    embedding_count: int
    events: List[FaceEvent]


class FaceRenameRequest(BaseModel):
    name: str = Field(..., min_length=1)


@api_app.get("/vision/face/list", response_model=FaceSummaryList, tags=["Vision"])
async def vision_face_list() -> FaceSummaryList:
    """Liste aller enrolled Identitäten mit Avatar (letzter Crop),
    Anzahl Embeddings und letzter Sichtung."""
    from .vision_store import VisionStore
    store = VisionStore()
    rows = store.list_faces_with_summary()
    return FaceSummaryList(faces=[FaceSummary(**r) for r in rows])


@api_app.get(
    "/vision/face/{face_id}/details",
    response_model=FaceDetailResponse,
    tags=["Vision"],
)
async def vision_face_details(face_id: int) -> FaceDetailResponse:
    """Detail-View einer Identity: alle face-Events mit Crops."""
    from .vision_store import VisionStore
    store = VisionStore()
    face = store.get_face_by_id(face_id)
    if not face:
        raise HTTPException(status_code=404, detail=f"face {face_id} not found")
    events = store.list_face_events(face_id, limit=50)
    emb_count = len(store.list_embeddings(face_id))
    return FaceDetailResponse(
        id=face_id,
        name=str(face["name"]),
        notes=str(face.get("notes") or ""),
        embedding_count=emb_count,
        events=[FaceEvent(**e) for e in events],
    )


@api_app.post(
    "/vision/face/{face_id}/rename",
    response_model=SystemActionResponse,
    tags=["Vision"],
)
async def vision_face_rename(face_id: int, request: FaceRenameRequest) -> SystemActionResponse:
    """Identity umbenennen. 409 wenn der neue Name schon vergeben ist."""
    from .vision_store import VisionStore
    store = VisionStore()
    try:
        ok = store.rename_face(face_id, request.name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"face {face_id} not found")
    return SystemActionResponse(success=True, message=f"renamed to '{request.name}'")


@api_app.delete(
    "/vision/face/{face_id}",
    response_model=SystemActionResponse,
    tags=["Vision"],
)
async def vision_face_delete(face_id: int) -> SystemActionResponse:
    """Komplette Identity löschen: faces-Row + alle Embeddings + face_id-
    Refs in events auf NULL. Crops auf Disk bleiben — werden vom
    Cleanup-Task per TTL aufgeräumt."""
    from .vision_store import VisionStore
    store = VisionStore()
    face = store.get_face_by_id(face_id)
    if not face:
        raise HTTPException(status_code=404, detail=f"face {face_id} not found")
    info = store.delete_face_with_assets(face_id)
    # Alle lebenden Recognizer neu laden lassen, damit die Identity verschwindet
    from .vision_filters.face_recognize import bump_enrollment_epoch
    bump_enrollment_epoch()
    return SystemActionResponse(
        success=True,
        message=f"deleted face {face_id} ({info['embeddings_deleted']} embeddings)",
    )


@api_app.delete(
    "/vision/face/embedding/{embedding_id}",
    response_model=SystemActionResponse,
    tags=["Vision"],
)
async def vision_embedding_delete(embedding_id: int) -> SystemActionResponse:
    """Einzelnes Embedding löschen — nützlich um schlechte
    Enrollment-Samples (falsche Pose, schlechtes Licht) rauszuwerfen
    ohne die ganze Identity zu kippen."""
    from .vision_store import VisionStore
    store = VisionStore()
    ok = store.delete_embedding(embedding_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"embedding {embedding_id} not found")
    from .vision_filters.face_recognize import bump_enrollment_epoch
    bump_enrollment_epoch()
    return SystemActionResponse(success=True, message="embedding deleted")


@api_app.get("/vision/frame", tags=["Vision"])
async def vision_frame(id: int, w: int = 0) -> Response:
    """Gespeichertes Event-Vollbild als JPEG ausliefern.

    ``id`` ist die Event-ID, ``w`` ein optionaler Ziel-Breiten-Parameter:
    >0 skaliert serverseitig herunter (Casus-Thumbnail nutzt w=80, das
    Bild-Modal lädt ohne ``w`` in Vollauflösung). Kein extra Auth-Gate —
    Zugriff ist von außen ohnehin durch den Basic-Auth-Reverse-Proxy und
    lokal durch Maschinenzugang geschützt (gleiches Niveau wie die
    face-crop-Auslieferung unter /_upload)."""
    from .vision_store import VisionStore
    path_str = VisionStore().get_event_frame_path(id)
    if not path_str:
        raise HTTPException(status_code=404, detail=f"no frame for event {id}")
    frame_file = Path(path_str)
    if not frame_file.is_file():
        raise HTTPException(status_code=404, detail="frame file missing on disk")
    data = frame_file.read_bytes()
    if w and w > 0:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is not None and arr.shape[1] > w:
            scale = w / float(arr.shape[1])
            arr = cv2.resize(
                arr, (w, max(1, int(arr.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
            ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                data = buf.tobytes()
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# Pfad der vision-settings.json (Heimat der zone_masks).
_VISION_SETTINGS_PATH = (
    Path(__file__).resolve().parents[1] / "plugins/tools/vision/settings.json"
)


class ZoneMaskPayload(BaseModel):
    """Speicher-Payload des Zonen-Editors."""
    source_id: str
    cols: int = 0
    rows: int = 0
    cells: str = ""          # cols*rows Ziffern aus {0,1,2,3}
    enabled: bool = True     # Schnell-Toggle: aus = Maske bleibt, wirkt nicht


@api_app.get("/vision/zone-mask", tags=["Vision"])
async def get_zone_mask(source_id: str) -> Dict[str, Any]:
    """Gespeicherte Zonen-Maske einer Quelle (für den Editor zum Laden)."""
    import json
    try:
        data = json.loads(_VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    entry = (data.get("zone_masks") or {}).get(source_id)
    if not isinstance(entry, dict):
        return {"exists": False, "source_id": source_id}
    return {
        "exists": True,
        "source_id": source_id,
        "cols": entry.get("cols", 0),
        "rows": entry.get("rows", 0),
        "cells": entry.get("cells", ""),
        "enabled": entry.get("enabled", True),
    }


@api_app.post(
    "/vision/zone-mask", response_model=SystemActionResponse, tags=["Vision"]
)
async def save_zone_mask(payload: ZoneMaskPayload) -> SystemActionResponse:
    """Zonen-Maske einer Quelle speichern (oder löschen bei mode=off /
    leerem Raster). Schreibt in zone_masks der vision-settings.json.

    Hinweis: Der Watcher lädt die Maske beim (Neu-)Start einer Quelle —
    eine geänderte Maske greift erst nach Re-Arm/Neustart der Quelle."""
    import json
    try:
        data = json.loads(_VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    masks = data.get("zone_masks")
    if not isinstance(masks, dict):
        masks = {}
    painted = any(ch != "0" for ch in payload.cells)
    if not painted:
        # Nichts gemalt (alles 0) → Eintrag löschen.
        masks.pop(payload.source_id, None)
        msg = "zone mask cleared"
    else:
        if (
            payload.cols <= 0
            or payload.rows <= 0
            or len(payload.cells) != payload.cols * payload.rows
            or any(ch not in "0123" for ch in payload.cells)
        ):
            raise HTTPException(status_code=400, detail="invalid grid")
        masks[payload.source_id] = {
            "cols": payload.cols,
            "rows": payload.rows,
            "cells": payload.cells,
            "enabled": payload.enabled,
        }
        msg = "zone mask saved" if payload.enabled else "zone mask saved (disabled)"
    data["zone_masks"] = masks
    _VISION_SETTINGS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # Live in den laufenden Watcher übernehmen — greift sofort, kein
    # Re-Arm/Neustart der Quelle nötig. No-op wenn die Quelle nicht läuft.
    try:
        from .vision_watcher import get_default_watcher
        get_default_watcher().reload_zone_mask(payload.source_id)
    except Exception as e:  # noqa: BLE001
        log_message(f"⚠️ zone mask live-reload failed: {e}")
    return SystemActionResponse(success=True, message=msg)


class MotionMinPayload(BaseModel):
    """Speicher-Payload des Bewegungs-Schwellwert-Sliders im Zonen-Editor."""
    source_id: str
    motion_min_area_ratio: float    # 0.001–0.5 (Anteil bewegter Pixel)


@api_app.get("/vision/motion-min", tags=["Vision"])
async def get_motion_min(source_id: str) -> Dict[str, Any]:
    """Bewegungs-Schwellwert einer Quelle (für den Editor-Slider zum Laden).

    Fällt auf den globalen Default (0.02) zurück, wenn die Quelle (noch)
    keinen eigenen Wert hat."""
    from .vision_store import VisionStore
    stored = VisionStore().get_source(source_id) or {}
    mma = (stored.get("settings") or {}).get("motion_min_area_ratio")
    value = (
        float(mma)
        if isinstance(mma, (int, float)) and 0.001 <= mma <= 0.5
        else 0.02
    )
    return {"source_id": source_id, "motion_min_area_ratio": value}


@api_app.post(
    "/vision/motion-min", response_model=SystemActionResponse, tags=["Vision"]
)
async def save_motion_min(payload: MotionMinPayload) -> SystemActionResponse:
    """Bewegungs-Schwellwert einer Quelle speichern (Editor-Slider beim
    Loslassen). Greift live im laufenden Watcher — kein Re-Arm nötig."""
    from .vision_store import VisionStore
    mma = max(0.001, min(0.5, float(payload.motion_min_area_ratio)))
    VisionStore().patch_source_settings(
        payload.source_id, {"motion_min_area_ratio": mma}
    )
    try:
        from .vision_watcher import get_default_watcher
        get_default_watcher().reload_motion_min(payload.source_id, mma)
    except Exception as e:  # noqa: BLE001
        log_message(f"⚠️ motion_min live-reload failed: {e}")
    return SystemActionResponse(success=True, message="motion min saved")


@api_app.get("/vision/zone-editor", tags=["Vision"])
async def zone_editor_page(source_id: str = "") -> HTMLResponse:
    """Standalone JS-Canvas-Zonen-Editor (HTML). Über /api ausgeliefert,
    damit er unabhängig vom frontend_path-Prefix erreichbar ist; die
    source_id kommt als Query-Param (das JS liest sie aus location.search)."""
    import json
    from .formatting import format_number
    from .i18n import TranslationManager, t
    editor = (
        Path(__file__).resolve().parents[2] / "assets" / "zone_editor.html"
    )
    try:
        html = editor.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"editor missing: {e}") from e
    # Kamera-Anzeigename (Alias > display_name > source_id) auflösen, damit
    # der Header den Standort zeigt statt der rohen source_id.
    src_name = ""
    if source_id:
        try:
            from .vision_store import VisionStore
            src_name = VisionStore().source_labels().get(source_id, "")
        except Exception:  # noqa: BLE001
            src_name = ""
    # Übersetzungen in der aktuellen UI-Sprache als window.T injizieren —
    # zentral aus i18n.py, kein Duplikat im JS. Scannt alle zone_editor_*-Keys.
    keys = [
        k for k in TranslationManager._translations["de"]
        if k.startswith("zone_editor_")
    ]
    # Dezimaltrenner aus demselben format_number-Locale wie die App ableiten
    # (DE „1,5" → Komma, EN „1.5" → Punkt) — der Editor formatiert Prozente
    # damit konsistent zur restlichen UI.
    decimal_sep = format_number(1.1, 1)[1]
    inject = (
        "<script>window.T="
        + json.dumps({k: t(k) for k in keys}, ensure_ascii=False)
        + ";window.DEC="
        + json.dumps(decimal_sep)
        + ";window.SRC_NAME="
        + json.dumps(src_name, ensure_ascii=False)
        + ";</script>"
    )
    return HTMLResponse(
        html.replace("<!--I18N-->", inject),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


# ============================================================
# Export for api_transformer
# ============================================================

def get_api_app() -> FastAPI:
    """
    Get the FastAPI app for use with Reflex api_transformer.

    Usage in rxconfig.py or aifred.py:
        from aifred.lib.api import get_api_app
        app = rx.App(api_transformer=get_api_app())
    """
    return api_app
