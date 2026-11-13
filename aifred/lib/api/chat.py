"""Chat endpoints: inject, status, clear, history, sessions, session config."""

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from ..logging_utils import log_message
from ..user_error import BadRequest, Missing
from .app import api_app
from .schemas import CallerName, SystemActionResponse


class ChatInjectRequest(BaseModel):
    """Chat inject request - injects message into browser session"""
    sender: CallerName
    message: str = Field(..., min_length=1, description="User message to inject")
    session_id: str = Field(..., description="Browser session session_id (required)")
    token: str = Field(
        "", description="Auth token (configured in INJECT_API_TOKEN env var)"
    )


class ChatHistoryResponse(BaseModel):
    """Chat history response"""
    chat_history: list[dict[str, str]]  # [{user: "...", assistant: "..."}]
    llm_history: list[dict[str, str]]  # [{role: "user/assistant/system", content: "..."}]
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
    A message into a session as the user's own input (Agent-Orc and other tools): the server runs the turn as if it
    had been written in the chat — intent, research, the discussion modes, compression — and every open tab of the
    session sees it live. With a turn running in the session the message waits behind it (the session's queue).

    Requires session_id to identify the target session (GET /api/sessions lists them).
    """
    # Auth: inject läuft die volle Agenten-Pipeline (inkl. Tools) auf der
    # Ziel-Session — also hinter ein Token klemmen, fail-closed. Ohne
    # konfiguriertes Token ist der Endpoint deaktiviert (kein offener
    # Remote-Control-Zugang). Zentrale, konstant-zeitige Prüfung in lib/auth.
    from ..auth import require_service_token
    from ..turn_service import TurnRequest, submit_turn
    from .sessions import stored_session
    require_service_token("inject", request.token)
    stored_session(request.session_id)

    log_message(f"📨 API: Injecting message to {request.session_id[:8]}...")

    # The injected text acts as the user's own input; the header names who sent it
    # (same "[Channel] Sender" header as every other channel message from the user).
    from datetime import datetime

    from ..envelope import InboundMessage
    from ..message_processor import build_user_chat_content
    headed = build_user_chat_content(InboundMessage(
        channel="inject", channel_id=request.session_id, sender=request.sender,
        text=request.message, timestamp=datetime.now(),
    ))
    queued = submit_turn(TurnRequest(request.session_id, headed))
    log_message(f"✅ API: {'queued behind the running turn' if queued else 'turn started'} for {request.session_id[:8]}...")
    return ChatInjectResponse(
        success=True,
        message="Message queued behind the running turn" if queued else "Turn started",
        session_id=request.session_id,
        queued=queued,
    )


class ChatStatusResponse(BaseModel):
    """Chat status response"""
    is_generating: bool = False
    message_count: int = 0
    session_id: str = ""


@api_app.get("/chat/status", response_model=ChatStatusResponse, tags=["Chat"])
async def get_chat_status(session_id: str):
    """
    Get current chat status for a session.

    Use this to poll for completion after injecting a message: is_generating is True while a turn of the
    session runs. A message waiting behind it starts right after, so is_generating can turn True again.

    Args:
        session_id: the session's id
    """
    from ..pipeline_registry import is_pipeline_running
    from .sessions import stored_session

    session = stored_session(session_id)
    return ChatStatusResponse(
        is_generating=is_pipeline_running(session_id),
        message_count=len(session.get("data", {}).get("chat_history", [])),
        session_id=session_id
    )


class ChatClearRequest(BaseModel):
    """Chat clear request"""
    session_id: str | None = Field(None, description="Browser session session_id to clear")


@api_app.post("/chat/clear", response_model=SystemActionResponse, tags=["Chat"])
async def clear_chat_endpoint(request: ChatClearRequest = ChatClearRequest(session_id=None)):
    """
    Empty a session's chat as the chat's own button does (POST /api/sessions/{id}/clear): the turn and the reading
    end, histories, debug lines and title go, so do the chat's images, audio and screenshots; open tabs follow.

    Without session_id it clears the most recently modified session.
    """
    from .. import session_events
    from ..session_storage import clear_chat, get_latest_session_file
    from ..turn_service import end_session_activity

    session_id = request.session_id
    if not session_id:
        session_file = get_latest_session_file()
        if not session_file:
            return SystemActionResponse(success=False, message="No sessions found to clear")
        session_id = session_file.stem
    end_session_activity(session_id)
    if not await asyncio.to_thread(clear_chat, session_id):
        raise Missing("no_session")
    session_events.publish(session_id, "chat_cleared", {})
    log_message(f"🗑️ API: Chat session {session_id[:8]}... cleared")
    return SystemActionResponse(success=True, message=f"Chat session {session_id[:8]}... cleared")


@api_app.get("/chat/history", response_model=ChatHistoryResponse, tags=["Chat"])
async def get_chat_history(session_id: str | None = None):
    """
    Get chat history for a session.

    If session_id is provided, returns that session's history.
    Otherwise returns the most recently modified session.

    Returns both the UI-friendly chat_history and the LLM-optimized llm_history.
    """
    from ..session_storage import get_latest_session_file, load_session

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

    # No session yet: an empty history, not an error.
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
    active_agent: str | None = Field(
        None,
        description="Agent that responds to messages (e.g. 'aifred', 'sokrates', custom IDs)"
    )
    multi_agent_mode: str | None = Field(
        None,
        description="Multi-agent mode: 'standard', 'critical_review', 'recensio', 'auto_consensus', 'tribunal', 'symposion'"
    )
    symposion_agents: list[str] | None = Field(
        None,
        description="Selected agents for Symposion mode (list of agent IDs)"
    )


class SessionConfigResponse(BaseModel):
    """Session config response (reflects the current state after update)."""
    success: bool
    session_id: str
    config: dict[str, Any]


@api_app.post("/session/config", response_model=SessionConfigResponse, tags=["Chat"])
async def update_session_config_endpoint(request: SessionConfigRequest):
    """
    Update the config block (agent, discussion mode) of a session.

    Only the fields you provide are updated — omit a field to leave it
    unchanged. Browser tabs viewing this session detect the change
    automatically via the session file mtime and reload their UI.

    Use this endpoint to:
    - Switch agents from external scripts/automation
    - Change discussion modes programmatically
    - Integrate voice assistants / external channels

    Returns the full current config after the update.
    """
    from ..session_storage import get_session_config, update_session_config

    updates = request.model_dump(exclude={"session_id"}, exclude_none=True)
    if not updates:
        raise BadRequest("no_config_fields", fields="active_agent, multi_agent_mode, symposion_agents")

    success = update_session_config(request.session_id, **updates)
    if not success:
        raise Missing("no_session")

    current = get_session_config(request.session_id)
    log_message(
        f"⚙️ API: Session {request.session_id[:8]}... config updated: {updates}"
    )
    return SessionConfigResponse(
        success=True,
        session_id=request.session_id,
        config=current,
    )
