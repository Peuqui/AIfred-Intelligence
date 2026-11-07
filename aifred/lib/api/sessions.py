"""Sessions for the Vue app: list, open, create, delete, configure, send, stop, follow.

Every endpoint works on the logged-in user's own sessions: a session of another account answers
404 as if it did not exist. A message to a session with a running turn waits in the session's
queue; the turn service starts it when the running one ends, with or without an open tab.
"""

import asyncio
import json
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import session_events
from ..config import MULTI_AGENT_MODES
from ..session_storage import DEFAULT_SESSION_CONFIG
from .app import api_app

SSE_KEEPALIVE_S = 15.0


def current_user(request: Request) -> str:
    """The logged-in user (the middleware let only a valid login cookie through)."""
    from ..auth import verify_signed_username
    from ..browser_storage import USERNAME_COOKIE_NAME

    user = verify_signed_username(request.cookies.get(USERNAME_COOKIE_NAME, ""))
    if user is None:
        raise HTTPException(status_code=403, detail="Login cookie required")
    return user


def owned_session(request: Request, session_id: str) -> dict[str, Any]:
    """The session, if it exists and belongs to the logged-in user; otherwise 404."""
    from ..session_storage import load_session

    user = current_user(request)
    try:
        session = load_session(session_id)
    except ValueError:  # not a session id (32 hex characters)
        session = None
    if session is None or str(session.get("owner", "")).lower() != user:
        raise HTTPException(status_code=404, detail="No such session")
    return session


# ── List, create, open, delete ───────────────────────────────────────────


class SessionSummary(BaseModel):
    session_id: str
    title: str | None
    last_message_at: str
    last_message_display: str
    message_count: int
    channel: str


class Utterance(BaseModel):
    """A part of a reply the browser's own voice speaks (lib/browser_reading.py); voice "Auto" or
    empty = a voice of the device for the language."""

    text: str
    language: str
    voice: str = ""
    rate: float | None = None
    pitch: float | None = None


class EntryMetadata(BaseModel):
    """What a bubble keeps besides its text: what was spoken of it (lib/session_storage.py
    ``keep_reply_audio``), and more the client does not read yet."""

    model_config = ConfigDict(extra="allow")

    audio_urls: list[str] = Field(default_factory=list)
    browser_speech: list[Utterance] = Field(default_factory=list)


class ChatEntry(BaseModel):
    """One message of a session's chat history (lib/formatting.py builds them). Older session
    files lack some fields; fields the client does not read pass through."""

    model_config = ConfigDict(extra="allow")

    role: str
    content: str
    agent: str = ""
    agent_display_name: str = ""
    agent_emoji: str = ""
    mode: str = ""
    timestamp: str = ""
    time_display: str = ""
    metadata: EntryMetadata = Field(default_factory=EntryMetadata)
    has_audio: bool = False
    tts_note: str = Field("", description="Who spoke the reply (the engines of the escalation list)")


class SessionDetail(BaseModel):
    session_id: str
    title: str | None
    config: dict[str, Any]
    chat_history: list[ChatEntry]
    debug_messages: list[str]
    running: bool
    last_event_id: int = Field(description="Open the event stream after this id: nothing is missed")


class SessionCreated(BaseModel):
    session_id: str


@api_app.get("/sessions", response_model=list[SessionSummary], tags=["Sessions"])
async def sessions_list(request: Request) -> list[SessionSummary]:
    from ..session_storage import list_sessions

    return [SessionSummary(**entry) for entry in list_sessions(owner=current_user(request))]


@api_app.post("/sessions", response_model=SessionCreated, status_code=201, tags=["Sessions"])
async def sessions_create(request: Request) -> SessionCreated:
    from ..session_storage import create_empty_session, generate_session_id

    session_id = generate_session_id()
    create_empty_session(session_id, owner=current_user(request))
    return SessionCreated(session_id=session_id)


@api_app.get("/sessions/{session_id}", response_model=SessionDetail, tags=["Sessions"])
async def sessions_open(session_id: str, request: Request) -> SessionDetail:
    from ..pipeline_registry import is_pipeline_running
    from ..session_storage import get_session_config, touch_session

    session = owned_session(request, session_id)
    touch_session(session_id)
    data = session.get("data", {})
    return SessionDetail(
        session_id=session_id,
        title=data.get("title"),
        config=get_session_config(session_id),
        chat_history=data.get("chat_history", []),
        debug_messages=data.get("debug_messages", []),
        running=is_pipeline_running(session_id),
        last_event_id=session_events.last_event_id(session_id),
    )


@api_app.delete("/sessions/{session_id}", status_code=204, tags=["Sessions"])
async def sessions_delete(session_id: str, request: Request) -> Response:
    from .. import reply_speech
    from ..pipeline_registry import cancel_pipeline
    from ..session_storage import delete_session

    owned_session(request, session_id)
    cancel_pipeline(session_id)
    reply_speech.discard(session_id)
    delete_session(session_id, expected_owner=current_user(request))
    return Response(status_code=204)


# ── Configure ────────────────────────────────────────────────────────────


class SessionConfigUpdate(BaseModel):
    active_agent: str | None = None
    multi_agent_mode: str | None = None
    symposion_agents: list[str] | None = None


@api_app.patch("/sessions/{session_id}/config", tags=["Sessions"])
async def sessions_configure(session_id: str, update: SessionConfigUpdate, request: Request) -> dict[str, Any]:
    from ..agent_config import get_agent_config
    from ..session_storage import get_session_config, update_session_config

    owned_session(request, session_id)
    changes = update.model_dump(exclude_none=True)
    agents = [*([changes["active_agent"]] if "active_agent" in changes else []), *changes.get("symposion_agents", [])]
    unknown = [agent for agent in agents if get_agent_config(agent) is None]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown agent: {', '.join(unknown)}")
    if changes.get("multi_agent_mode", "standard") not in MULTI_AGENT_MODES:
        raise HTTPException(status_code=422, detail=f"Unknown multi-agent mode: {changes['multi_agent_mode']}")
    if changes:
        update_session_config(session_id, **changes)
    config = get_session_config(session_id)
    public = {key: config[key] for key in DEFAULT_SESSION_CONFIG}
    session_events.publish(session_id, "session_config", public)
    return public


# ── Send, stop ───────────────────────────────────────────────────────────


class MessageIn(BaseModel):
    text: str


class MessageAccepted(BaseModel):
    queued: bool = Field(description="True: a turn runs; this message starts when it ends")


@api_app.post("/sessions/{session_id}/messages", response_model=MessageAccepted, status_code=202, tags=["Sessions"])
async def sessions_send(session_id: str, message: MessageIn, request: Request) -> MessageAccepted:
    from ..turn_service import TurnRequest, submit_turn

    owned_session(request, session_id)
    if not message.text.strip():
        raise HTTPException(status_code=422, detail="Empty message")
    return MessageAccepted(queued=submit_turn(TurnRequest(session_id, message.text)))


@api_app.post("/sessions/{session_id}/stop", tags=["Sessions"])
async def sessions_stop(session_id: str, request: Request) -> dict[str, bool]:
    """End the running turn and the reading aloud, also one that outlasts its turn;
    ``cancelled`` says whether a turn was running."""
    from .. import reply_speech
    from ..pipeline_registry import cancel_pipeline

    owned_session(request, session_id)
    cancelled = cancel_pipeline(session_id)
    reply_speech.stop(session_id)
    return {"cancelled": cancelled}


# ── Follow ───────────────────────────────────────────────────────────────


def _sse(event: session_events.SessionEvent) -> str:
    return f"id: {event.id}\nevent: {event.kind}\ndata: {json.dumps(event.data)}\n\n"


@api_app.get("/sessions/{session_id}/events", tags=["Sessions"])
async def sessions_events(session_id: str, request: Request, after: int = 0) -> StreamingResponse:
    """Server-Sent Events of the session. Each event is named by its kind and carries its id.

    ``after``: the first connection starts behind the id the session's detail reported (an
    EventSource cannot set headers); on a reconnect the browser's Last-Event-ID wins.
    """
    owned_session(request, session_id)
    last_event_id = request.headers.get("last-event-id", "")
    after_id = int(last_event_id) if last_event_id.isdigit() else after

    async def stream():
        async with session_events.subscribe(session_id, after_id) as (missed, queue):
            yield ": connected\n\n"   # flushes the headers: a proxy holds them until the first data
            for event in missed:
                yield _sse(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
