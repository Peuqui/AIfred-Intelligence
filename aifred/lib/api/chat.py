"""Chat endpoints: inject, status, clear, history, sessions, session config."""

from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

from ..logging_utils import log_message
from .app import api_app
from .schemas import SystemActionResponse


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
    from ..session_storage import set_pending_message

    # Auth: inject läuft die volle Agenten-Pipeline (inkl. Tools) auf der
    # Ziel-Session — also hinter ein Token klemmen, fail-closed. Ohne
    # konfiguriertes Token ist der Endpoint deaktiviert (kein offener
    # Remote-Control-Zugang). Zentrale, konstant-zeitige Prüfung in lib/auth.
    from ..auth import require_service_token
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
    from ..session_storage import load_session

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
    from ..session_storage import (
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
    from ..session_storage import list_sessions

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
    from ..session_storage import load_session, get_latest_session_file

    try:
        # Determine which session to load
        if session_id:
            session_data = load_session(session_id)
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
    from ..session_storage import update_session_config, get_session_config

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
