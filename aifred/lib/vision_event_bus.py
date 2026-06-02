"""In-process pub/sub for VLM events from the watcher to SSE consumers.

The frame_bus.py already handles raw frames, but VLM-analysis events
are structured JSON dicts (description + metadata) — different shape,
different consumer (the live-preview popup's teleprompter), so we
keep them on a separate bus.

API:
    publish_vlm_event(source_id, event)   — called by the watcher
    subscribe(source_id) -> async iter    — consumed by the SSE endpoint

Each subscriber has its own asyncio.Queue, so a slow consumer can't
block the publisher (we drop messages with a fixed maxsize instead).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

# Per source_id list of subscriber queues. Multiple browser tabs on
# the same camera each get their own queue.
_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

# Cap per queue — drop oldest events if a subscriber falls behind so
# the publisher path stays non-blocking. 32 events at 5s cooldown ≈
# 2.6 minutes of backlog before drops.
_QUEUE_MAXSIZE = 32

# Grace period after the last SSE viewer for a source disconnects before
# its live-preview watcher is torn down. Survives transient EventSource
# reconnects (network blips, popup re-render) without killing the VLM
# observation mid-stream. Matches frame_hub.GRACE_SEC.
_IDLE_GRACE_SEC = 5.0

# Pending teardown tasks per source — cancelled if a new viewer arrives
# inside the grace window.
_pending_teardown: dict[str, asyncio.Task[None]] = {}


async def _teardown_after_grace(source_id: str) -> None:
    """Wait out the grace window, then — if still nobody is watching —
    return the source to its baseline (restore armed surveillance or
    stop it). Cancelled by a new subscriber before the window elapses."""
    try:
        await asyncio.sleep(_IDLE_GRACE_SEC)
    except asyncio.CancelledError:
        return
    if _subscribers.get(source_id):
        return  # a viewer came back during the grace window
    from .logging_utils import log_message
    log_message(f"🛑 vision_event_bus idle teardown source={source_id}")
    try:
        from .vision_autostart import restore_or_stop_after_preview
        await restore_or_stop_after_preview(source_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("event-bus: preview teardown failed for %s: %s", source_id, e)
    finally:
        _pending_teardown.pop(source_id, None)


def publish_vlm_event(source_id: str, event: dict[str, Any]) -> None:
    """Push an event to all subscribers of this source. Non-blocking —
    if a subscriber's queue is full we drop the oldest message rather
    than wait. Safe to call from any async context."""
    queues = _subscribers.get(source_id, [])
    from .logging_utils import log_message
    log_message(
        f"🔔 publish_vlm_event source={source_id} subscribers={len(queues)} "
        f"desc_len={len(event.get('description', ''))}"
    )
    for q in queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Drop oldest, make room for the new one.
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("event-bus: dropping event for %s (queue still full)", source_id)


async def subscribe(source_id: str) -> AsyncIterator[dict[str, Any]]:
    """Async iterator of events for this source. Loop terminates when
    the caller stops consuming (CancelledError on the next __anext__).
    """
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    _subscribers.setdefault(source_id, []).append(q)
    # A viewer is (back) — cancel any pending idle teardown for this
    # source so a reconnect inside the grace window keeps the watcher.
    pending = _pending_teardown.pop(source_id, None)
    if pending is not None:
        pending.cancel()
    from .logging_utils import log_message
    log_message(
        f"🔗 vision_event_bus subscribe source={source_id} "
        f"total_subscribers={len(_subscribers[source_id])}"
    )
    try:
        while True:
            event = await q.get()
            yield event
    finally:
        # Cleanup — remove this subscriber's queue from the registry.
        lst = _subscribers.get(source_id, [])
        if q in lst:
            lst.remove(q)
        if not lst and source_id in _subscribers:
            del _subscribers[source_id]
        # Last viewer gone → schedule a grace-delayed teardown of the
        # popup's on-demand watcher (restore armed surveillance or stop).
        if not lst:
            _pending_teardown[source_id] = asyncio.create_task(
                _teardown_after_grace(source_id),
                name=f"vlm_preview_teardown|{source_id}",
            )


def active_subscriber_count(source_id: str) -> int:
    """How many SSE clients are currently subscribed to this source.
    Used by /api/vision/events/active for diagnostics; the idle-teardown
    in ``subscribe()`` drives the auto-stop (no viewers = tear down)."""
    return len(_subscribers.get(source_id, []))
