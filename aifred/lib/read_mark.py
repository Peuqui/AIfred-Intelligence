"""The one place that moves a session's read mark and tells the open tabs.

Two ways lead here: the browser says it has read a bubble (``POST /api/sessions/{id}/read``), and a
speaking device has played an announcement to its end (FreeEcho.2 alert queue): what was heard is read.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from . import session_events
from .logging_utils import log_exception
from .session_storage import mark_read_until
from .user_error import UserError


@dataclass(frozen=True)
class BubbleRef:
    """A bubble of a session: the session and the bubble's timestamp."""

    session_id: str
    timestamp: str


def mark_read(session_id: str, timestamp: str) -> bool:
    """Move the read mark of ``session_id`` forward to the bubble ``timestamp`` (``mark_read_until``: Missing
    and Invalid for an unknown session or bubble) and tell the open tabs when it moved. Returns whether it
    moved. Blocking (session file): off the event loop with ``asyncio.to_thread``."""
    if not mark_read_until(session_id, timestamp):
        return False
    # A state, not an event: a tab that reconnects needs the newest mark only.
    session_events.publish(session_id, "read_until", {"timestamp": timestamp}, replaces=frozenset({"read_until"}))
    return True


async def mark_heard(bubble: BubbleRef | None, label: str) -> None:
    """A device played what belongs to ``bubble`` to its end: the bubble counts as read. Nothing without a bubble.
    A failure here (the session or the bubble is gone meanwhile, the file) must not stop the device's playback:
    it is logged with its traceback."""
    if bubble is None:
        return
    try:
        await asyncio.to_thread(mark_read, bubble.session_id, bubble.timestamp)
    except (OSError, UserError) as error:  # session file, or the session / bubble is gone meanwhile
        log_exception(f"[{label}] heard to the end, but its bubble could not be marked read", error)
