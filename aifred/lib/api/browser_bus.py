"""Browser Push Bus — reflex-independent server→browser channel (SSE).

One pipeline for everything the server needs to push to the browser
WITHOUT going through Reflex state deltas. A bare asyncio.create_task
mutates server state but Reflex never pushes that delta — so background
work (streaming-TTS finalize, session-title generation, …) announces its
results over this bus instead. The browser's EventSource consumes them
and updates the DOM directly.

Each event carries a ``kind`` field plus kind-specific metadata; the JS
client routes by kind. The events travel through the session event hub
(``lib/session_events.py``): one id counter per session, one queue per
listening tab, the replay buffer for reconnects.

Audio kinds keep the user-gesture inheritance: the EventSource opens in
the Send-button click chain, so audio.play() is allowed for tts/media.

Kinds:
  - "tts"          : ``{kind, url, version, playback_rate}``
  - "tts_speak"    : a reply part the browser's own voice speaks (``url`` = JSON of the utterance)
  - "tts_pause"    : silence in the playlist between two parts (``url`` = milliseconds)
  - "media"        : ``{kind, url, version, state_key, start_pos_sec,
                        is_stream, audio_type}``
  - "stop"/"pause"/"resume"/"seek"/"speed" : audio control events
  - "bubble_audio" : combined replay URL for a finished chat bubble
  - "bubble_tts_note" : who spoke that bubble (escalation list)
  - "session_title": ``{kind, url, version}`` — url carries the title text

To add a new kind see docs/de/architecture/browser-push-bus.md.
"""

import asyncio
import json
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from .. import session_events
from ..logging_utils import log_message
from .app import api_app

TTS_CHUNK_KINDS = frozenset({"tts", "tts_speak", "tts_pause"})
# Kinds that carry a state, not an event: the newest replaces its predecessors in the
# replay buffer (a page that reconnects needs the latest, not the history).
LATEST_WINS_KINDS = frozenset({"service_status"})
SSE_KEEPALIVE_S = 15.0


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
    """Push an event to the browser push bus.

    The ``url`` field is the generic payload slot: for audio kinds it is an
    audio URL, for ``session_title`` / ``service_status`` it carries the text.

    Kinds and their metadata:
      - ``"tts"``    : chunk-stream, gapless. {url, playback_rate}
      - ``"tts_speak"`` : a part the browser's own voice (Web Speech API) speaks, same
                       queue; ``url`` holds the JSON of the utterance.
      - ``"tts_pause"`` : silence between two parts of the TTS queue; ``url`` holds the
                       pause in milliseconds.
      - ``"media"``  : single-track, position-saved. {url, state_key,
                       start_pos_sec, is_stream, audio_type, playback_rate}
      - ``"stop"``   : halt + clear src + final-position-save. (no metadata)
      - ``"pause"``  : halt, keep src + position. (no metadata)
      - ``"resume"`` : continue from current position. (no metadata)
      - ``"seek"``   : jump to ``position_sec`` (or ±N sec when ``relative=True``).
      - ``"speed"``  : set ``audio.playbackRate`` to ``factor`` (0.25–4.0).
      - ``"bubble_audio"`` : streaming-TTS finalize announces the combined
                       replay URL so custom.js can attach it to the latest
                       bubble's speaker button. {url}
      - ``"bubble_tts_note"`` : follows ``bubble_audio`` — ``url`` holds who
                       spoke (escalation list). {url}
      - ``"service_status"`` : model / STT / TTS state for the status line; ``url``
                       holds the snapshot as JSON, the newest replaces the older ones.
      - ``"session_title"`` : a background task finished title generation —
                       ``url`` holds the title. {url}
    """
    data: dict[str, Any] = {"url": url, "playback_rate": playback_rate}
    if kind == "media":
        data.update(
            state_key=state_key,
            start_pos_sec=float(start_pos_sec),
            is_stream=bool(is_stream),
            audio_type=audio_type,
        )
    elif kind == "seek":
        data.update(position_sec=float(position_sec), relative=bool(relative))
    elif kind == "speed":
        data["factor"] = float(factor)
    # The combined replay file replaces the reply's chunks (their files are deleted at
    # finalize): a page opened later must not get them replayed into its queue.
    replaces = TTS_CHUNK_KINDS if kind == "bubble_audio" else frozenset()
    if kind in LATEST_WINS_KINDS:
        replaces = frozenset({kind})
    session_events.publish(session_id, kind, data, replaces=replaces)


def browser_queue_clear(session_id: str) -> None:
    """Forget the buffered events of the session (called at the start of a new message).

    The id counter keeps rising: clients dedupe by version, a reset would make
    the next message's events look already seen.
    """
    session_events.clear_replay(session_id)


def _sse_item(event: session_events.SessionEvent) -> str:
    """The event in the wire format custom.js reads: ``{kind, version, url, …}``."""
    item = {"kind": event.kind, "version": event.id, **event.data}
    return f"id: {event.id}\ndata: {json.dumps(item)}\n\n"


@api_app.get("/browser/stream/{session_id}", tags=["Browser"])
async def browser_stream(session_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events for the browser push bus.

    Every open tab gets every event. Reconnect-safe: each event carries
    ``id: <version>``; on reconnect the browser sends ``Last-Event-ID`` and
    gets only the buffered events after it.
    """
    last_event_id = request.headers.get("last-event-id", "0")
    after_id = int(last_event_id) if last_event_id.isdigit() else 0

    async def event_generator():
        async with session_events.subscribe(session_id, after_id) as (missed, queue):
            log_message(
                f"📡 Browser SSE: stream opened for session {session_id[:8]}… (last id {after_id}, "
                f"{len(missed)} replayed, {session_events.subscriber_count(session_id)} listening)"
            )
            # Flushes the headers at once; without it the proxy buffers until the first
            # data and EventSource stays in CONNECTING.
            yield ": connected\n\n"
            for event in missed:
                yield _sse_item(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse_item(event)
        log_message(f"📡 Browser SSE: stream closed for session {session_id[:8]}…")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
