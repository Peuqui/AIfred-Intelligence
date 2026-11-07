"""Sessions for the Vue app: list, open, create, delete, configure, send (with images), dictate, stop, follow.

Every endpoint works on the logged-in user's own sessions: a session of another account answers
404 as if it did not exist. A message to a session with a running turn waits in the session's
queue; the turn service starts it when the running one ends, with or without an open tab.
"""

import asyncio
import json
from typing import Any

from fastapi import Form, HTTPException, Request, UploadFile
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


class SessionConfig(BaseModel):
    """The per-session choices (session_storage.DEFAULT_SESSION_CONFIG)."""

    active_agent: str
    multi_agent_mode: str
    symposion_agents: list[str]
    incognito: bool = Field(description="The agents work without their long-term memory in this session")


def _config_of(session_id: str) -> SessionConfig:
    from ..session_storage import get_session_config

    config = get_session_config(session_id)
    return SessionConfig(**{key: config[key] for key in DEFAULT_SESSION_CONFIG})


class SessionDetail(BaseModel):
    session_id: str
    title: str | None
    config: SessionConfig
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
    from ..session_storage import touch_session

    session = owned_session(request, session_id)
    touch_session(session_id)
    data = session.get("data", {})
    return SessionDetail(
        session_id=session_id,
        title=data.get("title"),
        config=_config_of(session_id),
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
    incognito: bool | None = Field(None, description="The agents work without their long-term memory in this session")


@api_app.patch("/sessions/{session_id}/config", response_model=SessionConfig, tags=["Sessions"])
async def sessions_configure(session_id: str, update: SessionConfigUpdate, request: Request) -> SessionConfig:
    from ..agent_config import get_agent_config
    from ..session_storage import update_session_config

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
    public = _config_of(session_id)
    session_events.publish(session_id, "session_config", public.model_dump())
    return public


# ── Send, stop ───────────────────────────────────────────────────────────


class MessageIn(BaseModel):
    text: str
    images: list[str] = Field(default_factory=list, description="URLs of images uploaded for this message (POST …/images)")


class MessageAccepted(BaseModel):
    queued: bool = Field(description="True: a turn runs; this message starts when it ends")


@api_app.post("/sessions/{session_id}/messages", response_model=MessageAccepted, status_code=202, tags=["Sessions"])
async def sessions_send(session_id: str, message: MessageIn, request: Request) -> MessageAccepted:
    from ..config import MAX_IMAGES_PER_MESSAGE
    from ..turn_images import session_images
    from ..turn_service import TurnRequest, submit_turn

    owned_session(request, session_id)
    if not message.text.strip() and not message.images:
        raise HTTPException(status_code=422, detail="Empty message")
    if len(message.images) > MAX_IMAGES_PER_MESSAGE:
        raise HTTPException(status_code=422, detail=f"At most {MAX_IMAGES_PER_MESSAGE} images per message")
    try:
        session_images(session_id, message.images)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return MessageAccepted(queued=submit_turn(TurnRequest(session_id, message.text, images=tuple(message.images))))


# ── Images for the next message ──────────────────────────────────────────


class UploadedImage(BaseModel):
    name: str
    url: str
    size_kb: int


@api_app.post("/sessions/{session_id}/images", response_model=list[UploadedImage], tags=["Sessions"])
async def sessions_upload_images(session_id: str, files: list[UploadFile], request: Request) -> list[UploadedImage]:
    """Images for the next message: checked, made smaller like every upload, kept in the session's
    upload folder (deleted with the session). The message names them by URL."""
    from ..vision_utils import get_image_url, save_session_upload, validate_image_file

    owned_session(request, session_id)
    uploads = [(file.filename or "image.jpg", await file.read()) for file in files]
    for filename, content in uploads:
        valid, error = validate_image_file(filename, len(content))
        if not valid:
            raise HTTPException(status_code=422, detail=error)
    stored = []
    for filename, content in uploads:
        stem, _, ext = filename.rpartition(".")
        path = await asyncio.to_thread(save_session_upload, content, session_id, stem, ext.lower())
        stored.append(UploadedImage(name=path.name, url=get_image_url(path), size_kb=path.stat().st_size // 1024))
    return stored


@api_app.delete("/sessions/{session_id}/images/{name}", status_code=204, tags=["Sessions"])
async def sessions_delete_image(session_id: str, name: str, request: Request) -> Response:
    """An uploaded image the user took off the next message."""
    from ..turn_images import session_images

    owned_session(request, session_id)
    try:
        image = session_images(session_id, [f"/_upload/images/{session_id}/{name}"])[0]
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    image.path.unlink()
    return Response(status_code=204)


# ── Dictation and audio files ────────────────────────────────────────────


class DictationAccepted(BaseModel):
    job_id: str = Field(description="Its question (dictation_question) and its end (dictation_finished) carry this id")


class DictationAnswer(BaseModel):
    yes: bool


@api_app.post("/sessions/{session_id}/dictation", response_model=DictationAccepted, status_code=202, tags=["Sessions"])
async def sessions_dictation(
    session_id: str, file: UploadFile, request: Request, job_id: str = Form(), source: str = Form("mic"),
) -> DictationAccepted:
    """A recording (``source=mic``: dictation) or an audio file (``source=file``) becomes text on the
    server (lib/dictation.py); the rest arrives on the session's event stream. ``job_id`` (a UUID)
    comes from the client, which knows it before the first event of the job."""
    from ..dictation import DictationRejected, accept
    from ..prompt_loader import get_language

    owned_session(request, session_id)
    if source not in ("mic", "file"):
        raise HTTPException(status_code=422, detail=f"Unknown source: {source}")
    try:
        job = accept(job_id, session_id, file.filename or "audio", await file.read(), source, get_language())
    except DictationRejected as rejection:
        raise HTTPException(status_code=422, detail=rejection.message) from rejection
    return DictationAccepted(job_id=job.id)


@api_app.post("/sessions/{session_id}/dictation/{job_id}/answer", status_code=204, tags=["Sessions"])
async def sessions_dictation_answer(session_id: str, job_id: str, reply: DictationAnswer, request: Request) -> Response:
    """The user's answer to a dictation's question (a long transcription)."""
    from ..dictation import answer

    owned_session(request, session_id)
    if not answer(session_id, job_id, reply.yes):
        raise HTTPException(status_code=404, detail="No question of this dictation is open")
    return Response(status_code=204)


class DictatedText(BaseModel):
    text: str


class DictatedTextResult(BaseModel):
    sent: bool = Field(description="True: it went to the AI as a turn; False: it belongs in the input field")


@api_app.post("/sessions/{session_id}/dictation/text", response_model=DictatedTextResult, tags=["Sessions"])
async def sessions_dictation_text(session_id: str, dictated: DictatedText, request: Request) -> DictatedTextResult:
    """What the browser's own recognition heard: straight to the AI or into the input field, by the
    same rule as a transcription of the server (``dictation.hand_on``)."""
    from ..debug_bus import session_scope
    from ..dictation import hand_on

    owned_session(request, session_id)
    text = dictated.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Empty dictation")
    with session_scope(session_id):
        return DictatedTextResult(sent=hand_on(session_id, text))


class DictationSettings(BaseModel):
    engine: str = Field(description="Who recognizes a dictation: stt_suite or browser (config.DICTATION_ENGINES)")
    show_transcription: bool = Field(description="A dictation goes into the input field first instead of to the AI")


class DictationSettingsUpdate(BaseModel):
    engine: str | None = None
    show_transcription: bool | None = None


def _dictation_settings() -> DictationSettings:
    from ..settings import persisted_settings

    settings = persisted_settings()
    return DictationSettings(engine=settings["dictation_engine"], show_transcription=bool(settings["show_transcription"]))


@api_app.get("/dictation/settings", response_model=DictationSettings, tags=["Sessions"])
async def dictation_settings(request: Request) -> DictationSettings:
    current_user(request)
    return _dictation_settings()


@api_app.patch("/dictation/settings", response_model=DictationSettings, tags=["Sessions"])
async def dictation_settings_update(update: DictationSettingsUpdate, request: Request) -> DictationSettings:
    from ..config import DICTATION_ENGINES
    from ..settings import update_settings

    current_user(request)
    changes: dict[str, Any] = {}
    if update.engine is not None:
        if update.engine not in DICTATION_ENGINES:
            raise HTTPException(status_code=422, detail=f"Unknown dictation engine: {update.engine}")
        changes["dictation_engine"] = update.engine
    if update.show_transcription is not None:
        changes["show_transcription"] = update.show_transcription
    if changes and not update_settings(changes):
        raise HTTPException(status_code=500, detail="settings.json could not be written")
    return _dictation_settings()


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
