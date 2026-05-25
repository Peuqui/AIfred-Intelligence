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


def active_subscriber_count(source_id: str) -> int:
    """How many SSE clients are currently subscribed to this source.
    Used by /api/vision/events/active for diagnostics + the future
    auto-stop logic (no subscribers = stop the watcher)."""
    return len(_subscribers.get(source_id, []))
