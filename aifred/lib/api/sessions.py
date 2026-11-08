"""Sessions for the Vue app: list, open, create, delete, configure, send (with images), dictate, stop, follow.

Every endpoint works on the logged-in user's own sessions: a session of another account answers
404 as if it did not exist. A message to a session with a running turn waits in the session's
queue; the turn service starts it when the running one ends, with or without an open tab.
"""

import asyncio
import json
from typing import Any, Literal

from fastapi import Form, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import session_events
from ..config import MULTI_AGENT_MODES
from ..session_storage import DEFAULT_SESSION_CONFIG
from ..turn_images import TurnImage
from ..user_error import Conflict, Invalid, Missing, Refused
from .app import api_app

SSE_KEEPALIVE_S = 15.0


def current_user(request: Request) -> str:
    """The logged-in user (the middleware let only a valid login cookie through)."""
    from ..auth import USERNAME_COOKIE_NAME, verify_signed_username

    user = verify_signed_username(request.cookies.get(USERNAME_COOKIE_NAME, ""))
    if user is None:
        raise Refused("login_required")
    return user


def stored_session(session_id: str) -> dict[str, Any]:
    """The session, if it exists; otherwise 404 (also for a string that is no session id)."""
    from ..session_storage import load_session

    try:
        session = load_session(session_id)
    except ValueError:  # not a session id (32 hex characters)
        session = None
    if session is None:
        raise Missing("no_session")
    return session


def owned_session(request: Request, session_id: str) -> dict[str, Any]:
    """The session, if it exists and belongs to the logged-in user; otherwise 404."""
    user = current_user(request)
    session = stored_session(session_id)
    if str(session.get("owner", "")).lower() != user:
        raise Missing("no_session")
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
    debate_seats: dict[str, str] = Field(
        description="Critic and judge of the debate modes (seat → agent): the session's choice over settings.json's",
    )


def _config_of(session_id: str) -> SessionConfig:
    from ..debate_seats import session_seats
    from ..session_storage import get_session_config

    config = get_session_config(session_id)
    return SessionConfig(**{**{key: config[key] for key in DEFAULT_SESSION_CONFIG}, "debate_seats": session_seats(config)})


class SessionDetail(BaseModel):
    session_id: str
    title: str | None
    config: SessionConfig
    chat_history: list[ChatEntry]
    debug_messages: list[str]
    running: bool
    speech_regenerating: bool = Field(description="Every reply is being read into new audio")
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
    from .. import reply_speech
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
        speech_regenerating=reply_speech.regenerating(session_id),
        last_event_id=session_events.last_event_id(session_id),
    )


@api_app.delete("/sessions/{session_id}", status_code=204, tags=["Sessions"])
async def sessions_delete(session_id: str, request: Request) -> Response:
    from ..session_storage import delete_session
    from ..turn_service import end_session_activity

    owned_session(request, session_id)
    end_session_activity(session_id)
    delete_session(session_id, expected_owner=current_user(request))
    return Response(status_code=204)


class SessionsToDelete(BaseModel):
    session_ids: list[str] = Field(min_length=1)


@api_app.post("/sessions/delete", status_code=204, tags=["Sessions"])
async def sessions_delete_many(request: Request, deleting: SessionsToDelete) -> Response:
    """Delete several of the user's sessions at once (the list's selection, or all); 404 and nothing deleted when
    one of them is not the user's."""
    from ..session_storage import delete_session
    from ..turn_service import end_session_activity

    for session_id in deleting.session_ids:
        owned_session(request, session_id)
    user = current_user(request)
    for session_id in deleting.session_ids:
        end_session_activity(session_id)
        delete_session(session_id, expected_owner=user)
    return Response(status_code=204)


@api_app.post("/sessions/{session_id}/clear", status_code=204, tags=["Sessions"])
async def sessions_clear(session_id: str, request: Request) -> Response:
    """Empty the chat: the running turn and the reading end, histories, debug lines and title go, so do the chat's
    images, spoken audio and screenshots (plots and pages stay); every open tab hears ``chat_cleared``."""
    from ..session_storage import clear_chat
    from ..turn_service import end_session_activity

    owned_session(request, session_id)
    end_session_activity(session_id)
    if not await asyncio.to_thread(clear_chat, session_id):
        raise Missing("no_session")
    session_events.publish(session_id, "chat_cleared", {})
    return Response(status_code=204)


# ── Configure ────────────────────────────────────────────────────────────


class SessionConfigUpdate(BaseModel):
    active_agent: str | None = None
    multi_agent_mode: str | None = None
    symposion_agents: list[str] | None = None
    incognito: bool | None = Field(None, description="The agents work without their long-term memory in this session")
    debate_seats: dict[str, str] | None = Field(None, description="Seats to change (critic, judge) → agent")


@api_app.patch("/sessions/{session_id}/config", response_model=SessionConfig, tags=["Sessions"])
async def sessions_configure(session_id: str, update: SessionConfigUpdate, request: Request) -> SessionConfig:
    from ..agent_config import get_agent_config
    from ..debate_seats import check_seat_agent
    from ..session_storage import get_session_config, update_session_config

    owned_session(request, session_id)
    changes = update.model_dump(exclude_none=True)
    if "debate_seats" in changes:
        for seat, agent in changes["debate_seats"].items():
            check_seat_agent(seat, agent)
        changes["debate_seats"] = {**get_session_config(session_id)["debate_seats"], **changes["debate_seats"]}
    agents = [*([changes["active_agent"]] if "active_agent" in changes else []), *changes.get("symposion_agents", [])]
    unknown = [agent for agent in agents if get_agent_config(agent) is None]
    if unknown:
        raise Invalid("unknown_agent", agent=", ".join(unknown))
    if changes.get("multi_agent_mode", "standard") not in MULTI_AGENT_MODES:
        raise Invalid("unknown_mode", value=changes["multi_agent_mode"])
    if changes:
        update_session_config(session_id, **changes)
    public = _config_of(session_id)
    session_events.publish(session_id, "session_config", public.model_dump())
    return public


class DebateMode(BaseModel):
    mode: str
    seats: list[str] = Field(description="The seats a session chooses for this mode (critic, judge)")


@api_app.get("/debate/modes", response_model=list[DebateMode], tags=["Sessions"])
async def debate_modes() -> list[DebateMode]:
    """The multi-agent modes a session can be in, each with the seats it chooses."""
    from ..turn_debate import chosen_seats

    return [DebateMode(mode=mode, seats=list(chosen_seats(mode))) for mode in MULTI_AGENT_MODES]


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
        raise Invalid("empty_message")
    if len(message.images) > MAX_IMAGES_PER_MESSAGE:
        raise Invalid("too_many_images", limit=MAX_IMAGES_PER_MESSAGE)
    session_images(session_id, message.images)
    return MessageAccepted(queued=submit_turn(TurnRequest(session_id, message.text, images=tuple(message.images))))


# ── Speech ───────────────────────────────────────────────────────────────


class BubbleRef(BaseModel):
    timestamp: str = Field(description="The bubble's timestamp (ChatEntry.timestamp)")


@api_app.post("/sessions/{session_id}/messages/speech", status_code=204, tags=["Sessions"])
async def sessions_read_bubble(session_id: str, bubble: BubbleRef, request: Request) -> Response:
    """Read a finished bubble aloud again: what plays now stops, the reading comes over the event stream
    (``tts``, …), and what was spoken replaces the bubble's audio (``message_updated``)."""
    from ..debug_bus import session_scope
    from ..reply_speech import bubble_reading_text, read_bubble
    from ..runtime_settings import load_runtime_settings

    session = owned_session(request, session_id)
    entry = next((m for m in session.get("data", {}).get("chat_history", []) if m.get("timestamp") == bubble.timestamp), None)
    if entry is None or entry.get("role") != "assistant":
        raise Missing("no_reply")
    view = load_runtime_settings()
    if not view.enable_tts:
        raise Conflict("speech_off")
    text = bubble_reading_text(entry)
    if not text:
        raise Invalid("nothing_to_read")
    with session_scope(session_id):
        await read_bubble(session_id, entry, text, view, view.ui_language)
    return Response(status_code=204)


@api_app.post("/sessions/{session_id}/speech/regenerate", status_code=204, tags=["Sessions"])
async def sessions_regenerate_speech(session_id: str, request: Request) -> Response:
    """Read every reply of the session into new audio, without playing it: what plays now stops, each
    bubble gets its audio as soon as it is made (``message_updated``), the run is announced by
    ``speech_regenerating`` (``running``), a failure of the TTS service by ``notice``."""
    from .. import reply_speech
    from ..debug_bus import session_scope
    from ..runtime_settings import load_runtime_settings

    owned_session(request, session_id)
    view = load_runtime_settings()
    if not view.enable_tts:
        raise Conflict("speech_off")
    with session_scope(session_id):
        if not reply_speech.regenerate_all(session_id, view, view.ui_language):
            raise Conflict("reading_all_running")
    return Response(status_code=204)


# ── Images for the next message ──────────────────────────────────────────


class UploadedImage(BaseModel):
    name: str
    url: str
    size_kb: int


def _uploaded(image: TurnImage) -> UploadedImage:
    return UploadedImage(name=image.name, url=image.url, size_kb=image.path.stat().st_size // 1024)


def _pending_image(request: Request, session_id: str, name: str) -> TurnImage:
    """An uploaded image of the user's own session, by its name; 404 for any other."""
    from ..turn_images import session_images

    owned_session(request, session_id)
    url = f"/_upload/images/{session_id}/{name}"
    try:
        return session_images(session_id, [url])[0]
    except Invalid as error:  # the image asked for by its address: not there is a 404
        raise Missing("not_session_image", url=url) from error


@api_app.post("/sessions/{session_id}/images", response_model=list[UploadedImage], tags=["Sessions"])
async def sessions_upload_images(session_id: str, files: list[UploadFile], request: Request) -> list[UploadedImage]:
    """Images for the next message: checked, made smaller like every upload, kept in the session's
    upload folder (deleted with the session). The message names them by URL."""
    from ..vision_utils import check_image_file, get_image_url, save_session_upload

    owned_session(request, session_id)
    uploads = [(file.filename or "image.jpg", await file.read()) for file in files]
    for filename, _content in uploads:
        check_image_file(filename)
    stored = []
    for filename, content in uploads:
        stem, _, ext = filename.rpartition(".")
        path = await asyncio.to_thread(save_session_upload, content, session_id, stem, ext.lower())
        stored.append(_uploaded(TurnImage(path.name, get_image_url(path), path)))
    return stored


@api_app.delete("/sessions/{session_id}/images/{name}", status_code=204, tags=["Sessions"])
async def sessions_delete_image(session_id: str, name: str, request: Request) -> Response:
    """An uploaded image the user took off the next message."""
    _pending_image(request, session_id, name).path.unlink()
    return Response(status_code=204)


class Rotation(BaseModel):
    clockwise: bool = Field(description="Turn right (true) or left (false), by 90 degrees")


@api_app.post("/sessions/{session_id}/images/{name}/rotate", response_model=UploadedImage, tags=["Sessions"])
async def sessions_rotate_image(session_id: str, name: str, rotation: Rotation, request: Request) -> UploadedImage:
    """An uploaded image turned by 90 degrees; it comes back under a new name, the old file is gone."""
    from ..turn_images import rotate_upload

    image = _pending_image(request, session_id, name)
    return _uploaded(await asyncio.to_thread(rotate_upload, session_id, image, rotation.clockwise))


class CropBox(BaseModel):
    x: float = Field(ge=0, lt=100, description="Left edge, percent of the image's width")
    y: float = Field(ge=0, lt=100, description="Top edge, percent of the image's height")
    width: float = Field(gt=0, le=100, description="Percent of the image's width")
    height: float = Field(gt=0, le=100, description="Percent of the image's height")


@api_app.post("/sessions/{session_id}/images/{name}/crop", response_model=UploadedImage, tags=["Sessions"])
async def sessions_crop_image(session_id: str, name: str, box: CropBox, request: Request) -> UploadedImage:
    """A part of an uploaded image; it comes back under a new name, the old file is gone. A box over
    the whole image leaves it as it is (same name)."""
    from ..turn_images import crop_upload

    image = _pending_image(request, session_id, name)
    return _uploaded(await asyncio.to_thread(crop_upload, session_id, image, box.model_dump()))


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
    from ..dictation import accept
    from ..prompt_loader import get_language

    owned_session(request, session_id)
    if source not in ("mic", "file"):
        raise Invalid("unknown_dictation_source", source=source)
    job = accept(job_id, session_id, file.filename or "audio", await file.read(), source, get_language())
    return DictationAccepted(job_id=job.id)


@api_app.post("/sessions/{session_id}/dictation/{job_id}/answer", status_code=204, tags=["Sessions"])
async def sessions_dictation_answer(session_id: str, job_id: str, reply: DictationAnswer, request: Request) -> Response:
    """The user's answer to a dictation's question (a long transcription)."""
    from ..dictation import answer

    owned_session(request, session_id)
    if not answer(session_id, job_id, reply.yes):
        raise Missing("no_dictation_question")
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
        raise Invalid("empty_dictation")
    with session_scope(session_id):
        return DictatedTextResult(sent=hand_on(session_id, text))


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


class MediaControl(BaseModel):
    action: Literal["pause", "resume", "stop", "seek", "ended"]
    position_sec: float = Field(0.0, description="seek: where to (relative: by how much)")
    relative: bool = False


@api_app.post("/sessions/{session_id}/media", tags=["Sessions"])
async def sessions_media(session_id: str, control: MediaControl, request: Request) -> dict[str, bool]:
    """The player's buttons on what the audio player plays in the session's tabs, the same way an agent's tool goes
    (lib/audio_channels/browser.py): every tab follows. ``ended``: the page played the last track to its end.
    ``done`` is False when nothing played."""
    from ..audio_channels import browser

    owned_session(request, session_id)
    target = f"browser:{session_id}"
    channel = browser.BrowserChannel()
    match control.action:
        case "pause":
            done = await channel.pause(target)
        case "resume":
            done = await channel.resume(target)
        case "stop":
            done = await channel.stop(target)
        case "seek":
            done = await channel.seek(target, control.position_sec, relative=control.relative)
        case "ended":
            browser.ended(session_id)
            done = True
    return {"done": done}


class SessionRemembered(BaseModel):
    agents: list[str] = Field(description="Display names of the agents that keep the summary")
    stored: int
    updated: int = Field(description="Earlier summaries of this session, replaced")
    summary: str


@api_app.post("/sessions/{session_id}/remember", response_model=SessionRemembered, tags=["Sessions"])
async def sessions_remember(session_id: str, request: Request) -> SessionRemembered:
    """„Merken“: the chat summarized by AIfred's model, kept in the memory of every agent that spoke in it."""
    from ..session_memory import remember_session

    owned_session(request, session_id)
    done = await remember_session(session_id)
    return SessionRemembered(agents=list(done.agents), stored=done.stored, updated=done.updated, summary=done.summary)


class ChatExported(BaseModel):
    url: str = Field(description="The standalone page, under /_upload/html_preview/")
    download_name: str = Field(description="A plain ASCII file name for saving it")
    messages: int


@api_app.post("/sessions/{session_id}/export", response_model=ChatExported, tags=["Sessions"])
async def sessions_export(session_id: str, request: Request) -> ChatExported:
    """The chat as one standalone HTML file (images, audio, sandbox pages and KaTeX embedded); 422 without a chat."""
    from ..chat_export import export_session

    owned_session(request, session_id)
    done = await asyncio.to_thread(export_session, session_id)
    return ChatExported(url=done.url, download_name=done.download_name, messages=done.messages)


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
