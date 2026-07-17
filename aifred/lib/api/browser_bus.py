"""Browser Push Bus — reflex-independent server→browser channel (SSE).

One pipeline for everything the server needs to push to the browser
WITHOUT going through Reflex state deltas. A bare asyncio.create_task
mutates server state but Reflex never pushes that delta — so background
work (streaming-TTS finalize, session-title generation, …) announces its
results over this bus instead. The browser's EventSource consumes them
and updates the DOM directly.

Each event carries a ``kind`` field plus kind-specific metadata; the JS
client routes by kind. Server-side all kinds share one queue, one
monotonic version counter and the SSE replay logic.

Audio kinds keep the user-gesture inheritance: the EventSource opens in
the Send-button click chain, so audio.play() is allowed for tts/media.

Kinds:
  - "tts"          : ``{kind, url, version, playback_rate}``
  - "media"        : ``{kind, url, version, state_key, start_pos_sec,
                        is_stream, audio_type}``
  - "stop"/"pause"/"resume"/"seek"/"speed" : audio control events
  - "bubble_audio" : combined replay URL for a finished chat bubble
  - "session_title": ``{kind, url, version}`` — url carries the title text

To add a new kind see docs/de/architecture/browser-push-bus.md.
"""

import asyncio
from typing import Dict, Any, List

from fastapi import Request
from pydantic import BaseModel, Field

from ..logging_utils import log_message
from .app import api_app

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
