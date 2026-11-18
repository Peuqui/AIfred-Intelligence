"""Chat endpoints for callers outside the app: inject a message, poll the status."""

from pydantic import BaseModel, Field

from ..logging_utils import log_message
from .app import api_app
from .schemas import CallerName


class ChatInjectRequest(BaseModel):
    """Chat inject request - injects message into browser session"""
    sender: CallerName
    message: str = Field(..., min_length=1, description="User message to inject")
    session_id: str = Field(..., description="Browser session session_id (required)")
    token: str = Field(
        "", description="Auth token (configured in INJECT_API_TOKEN env var)"
    )


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
