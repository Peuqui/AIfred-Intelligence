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
import asyncio
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
                        if any(v in model_id.lower() for v in ['vision', 'vl', 'llava']):
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
            if any(v in model_id.lower() for v in ['vision', 'vl', 'llava']):
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
# TTS Queue for Streaming Audio (SSE + Polling Endpoints)
# ============================================================

# Global TTS queue storage: {session_id: {"queue": [...], "version": int}}
_tts_queue_storage: Dict[str, Dict[str, Any]] = {}

# Global asyncio.Queue per session for SSE streaming
# When audio is pushed, it goes to both storage (for polling) and queue (for SSE)
_tts_sse_queues: Dict[str, asyncio.Queue] = {}


class TTSQueueResponse(BaseModel):
    """Response for TTS queue polling"""
    queue: List[str] = Field(default_factory=list, description="Audio URLs to play")
    version: int = Field(default=0, description="Queue version for change detection")
    playback_rate: str = Field(default="1.0x", description="Playback speed")


def tts_queue_push(session_id: str, audio_url: str, playback_rate: str = "1.0x") -> None:
    """
    Push audio URL to TTS queue (called from state.py create_task).

    This is the bridge between asyncio.create_task (which can't update Reflex state)
    and the frontend (via SSE stream or polling).
    """
    if session_id not in _tts_queue_storage:
        _tts_queue_storage[session_id] = {"queue": [], "version": 0, "playback_rate": "1.0x"}

    storage = _tts_queue_storage[session_id]
    storage["queue"].append(audio_url)
    storage["version"] += 1
    storage["playback_rate"] = playback_rate
    log_message(f"🔊 TTS API: Pushed {audio_url.split('/')[-1]} to session {session_id[:8]}... (v{storage['version']})")

    # Also push to SSE queue if listener is connected
    if session_id in _tts_sse_queues:
        try:
            _tts_sse_queues[session_id].put_nowait({
                "audio_url": audio_url,
                "version": storage["version"],
                "playback_rate": playback_rate
            })
            log_message(f"🔊 TTS SSE: Queued for stream (session {session_id[:8]}...)")
        except asyncio.QueueFull:
            log_message("⚠️ TTS SSE: Queue full, skipping")
    else:
        # Diagnostic: Show why SSE push failed
        active_sessions = list(_tts_sse_queues.keys())
        if active_sessions:
            active_short = [s[:8] for s in active_sessions]
            log_message(f"⚠️ TTS SSE: No queue for session {session_id[:8]}... (active SSE sessions: {active_short})")
        else:
            log_message(f"⚠️ TTS SSE: No queue for session {session_id[:8]}... (no SSE connections at all)")


def tts_queue_clear(session_id: str) -> None:
    """Clear TTS queue for session (called at start of new message)."""
    if session_id in _tts_queue_storage:
        _tts_queue_storage[session_id] = {"queue": [], "version": 0, "playback_rate": "1.0x"}
        log_message(f"🔊 TTS API: Cleared queue for session {session_id[:8]}...")


@api_app.get("/tts/queue/{session_id}", response_model=TTSQueueResponse, tags=["TTS"])
async def get_tts_queue(session_id: str, since_version: int = 0):
    """
    Get TTS audio queue for streaming playback.

    The frontend polls this endpoint to get new audio URLs.
    Use since_version to only get updates since last poll.
    """
    if session_id not in _tts_queue_storage:
        return TTSQueueResponse(queue=[], version=0, playback_rate="1.0x")

    storage = _tts_queue_storage[session_id]

    # Only return if there are new items
    if storage["version"] <= since_version:
        return TTSQueueResponse(queue=[], version=storage["version"], playback_rate=storage["playback_rate"])

    return TTSQueueResponse(
        queue=storage["queue"],
        version=storage["version"],
        playback_rate=storage["playback_rate"]
    )


@api_app.delete("/tts/queue/{session_id}", tags=["TTS"])
async def clear_tts_queue(session_id: str):
    """Clear TTS queue for session."""
    tts_queue_clear(session_id)
    return {"status": "ok", "message": "Queue cleared"}


@api_app.get("/tts/stream/{session_id}", tags=["TTS"])
async def tts_stream(session_id: str):
    """
    Server-Sent Events (SSE) endpoint for real-time TTS audio streaming.

    Browser opens this connection once. Server pushes audio URLs immediately
    when they become available - no polling needed.
    """
    from fastapi.responses import StreamingResponse
    import json

    async def event_generator():
        # Create queue for this session
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        old_queue = _tts_sse_queues.get(session_id)
        if old_queue is not None:
            log_message(f"🔊 TTS SSE: Replacing existing queue for session {session_id[:8]}... (reconnect)")
        _tts_sse_queues[session_id] = queue
        log_message(f"🔊 TTS SSE: Stream opened for session {session_id[:8]}...")

        # Flush HTTP headers immediately by yielding an SSE comment.
        # Without this, the proxy buffers until the first data (up to 15s keepalive),
        # keeping EventSource stuck in CONNECTING state.
        yield ": connected\n\n"

        # IMPORTANT: Send any already-generated audio URLs immediately
        # This handles the race condition where TTS generation starts before SSE connects
        if session_id in _tts_queue_storage:
            storage = _tts_queue_storage[session_id]
            if storage["queue"]:
                log_message(f"🔊 TTS SSE: Sending {len(storage['queue'])} existing items (v{storage['version']})")
                for i, audio_url in enumerate(storage["queue"]):
                    item = {
                        "audio_url": audio_url,
                        "version": i + 1,  # Reconstruct version sequence
                        "playback_rate": storage["playback_rate"]
                    }
                    data = json.dumps(item)
                    yield f"data: {data}\n\n"
                    log_message(f"🔊 TTS SSE: Sent existing {audio_url.split('/')[-1]}")

        try:
            while True:
                try:
                    # Wait for next audio item (with timeout for keepalive)
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)

                    # Send audio URL as SSE event
                    data = json.dumps(item)
                    yield f"data: {data}\n\n"
                    log_message(f"🔊 TTS SSE: Sent {item['audio_url'].split('/')[-1]}")

                except asyncio.TimeoutError:
                    # Send keepalive comment (SSE spec: lines starting with : are comments)
                    yield ": keepalive\n\n"

                except asyncio.CancelledError:
                    # Client disconnected
                    log_message(f"🔊 TTS SSE: Stream cancelled for session {session_id[:8]}...")
                    break

        finally:
            # Only delete OUR queue - a reconnection may have already replaced it.
            # Without this identity check, a reconnecting SSE client would:
            # 1. New generator creates queue2, overwrites _tts_sse_queues[id]
            # 2. Old generator's finally deletes _tts_sse_queues[id] (queue2!)
            # 3. tts_queue_push() can never find the queue again
            if _tts_sse_queues.get(session_id) is queue:
                del _tts_sse_queues[session_id]
                log_message(f"🔊 TTS SSE: Stream closed for session {session_id[:8]}... (queue cleaned up)")
            else:
                log_message(f"🔊 TTS SSE: Stream closed for session {session_id[:8]}... (queue already replaced by reconnect)")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
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
    from .credential_broker import broker

    # Auth check
    expected_token = broker.get("webhook", "api_token")
    if not expected_token:
        raise HTTPException(status_code=503, detail="Webhook API not configured (WEBHOOK_API_TOKEN not set)")
    if request.token != expected_token:
        log_message("API: agent/trigger — invalid token", "warning")
        raise HTTPException(status_code=403, detail="Invalid token")

    # Tier cap: webhooks max tier 1 by default
    from .security import DEFAULT_TIER_BY_SOURCE
    max_allowed = DEFAULT_TIER_BY_SOURCE.get("webhook", 0)
    effective_tier = min(request.max_tier, max_allowed)

    log_message(f"API: agent/trigger — '{request.message[:50]}...' (agent={request.agent}, tier={effective_tier})")

    # Build job payload and execute async
    import secrets
    session_id = f"webhook_{secrets.token_hex(8)}"

    async def _run():
        from .session_storage import create_empty_session
        from .config import MESSAGE_HUB_OWNER
        from .message_processor import _call_engine
        from .scheduler import _deliver_result, Job

        create_empty_session(session_id, owner=MESSAGE_HUB_OWNER)

        response_text, _ = await _call_engine(
            user_text=request.message,
            session_id=session_id,
            agent=request.agent,
            max_tier=effective_tier,
            source="webhook",
        )

        if response_text:
            # Create a minimal Job for delivery
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
            await _deliver_result(job, response_text, session_id)

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
            f"<html><body><h2>❌ OAuth abgebrochen</h2><p>{error}</p>"
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
        await oauth_broker.handle_callback(state, code)
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;padding:2rem'>"
            f"<h2>✅ {provider.capitalize()} verbunden!</h2>"
            "<p>Tokens gespeichert. Du kannst dieses Fenster schließen.</p>"
            "</body></html>"
        )
    except Exception as exc:
        return HTMLResponse(
            f"<html><body><h2>❌ Fehler</h2><p>{exc}</p></body></html>",
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
    oauth_broker.disconnect(provider)
    return {"provider": provider, "disconnected": True}


# ============================================================
# Agent Bundle Export / Import
# ============================================================


@api_app.get("/agents/export", tags=["Agents"])
async def export_agents_endpoint(ids: str) -> Response:
    """Download a ZIP bundle containing one or more agents.

    ``ids`` is a comma-separated list of agent IDs (e.g. ``?ids=codi,hal``).
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
