"""Event stream per session: everything the server tells the clients of a session.

One hub for every client (browser tabs, devices): each subscriber gets its own queue, so two tabs
of one session both see every event. Ids rise per session and never fall; a client that
reconnects passes the last id it saw and gets the events after it from the replay buffer.

The replay buffer holds what a page opened late still needs: it is emptied when a new turn starts
(``clear_replay``), and an event can replace earlier ones of given kinds (a state that only the
newest value describes, or audio chunks a combined file supersedes).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SessionEvent:
    id: int
    kind: str
    data: dict[str, Any]


@dataclass
class _Channel:
    last_id: int = 0
    replay: list[SessionEvent] = field(default_factory=list)
    subscribers: set["asyncio.Queue[SessionEvent]"] = field(default_factory=set)


_channels: dict[str, _Channel] = {}


def _channel(session_id: str) -> _Channel:
    return _channels.setdefault(session_id, _Channel())


def publish(
    session_id: str,
    kind: str,
    data: dict[str, Any],
    *,
    replaces: frozenset[str] = frozenset(),
) -> SessionEvent:
    """Send an event to every subscriber of the session and keep it for late clients.

    ``replaces``: kinds whose earlier events leave the replay buffer when this one arrives.
    """
    channel = _channel(session_id)
    channel.last_id += 1
    event = SessionEvent(channel.last_id, kind, data)
    if replaces:
        channel.replay = [old for old in channel.replay if old.kind not in replaces]
    channel.replay.append(event)
    for queue in channel.subscribers:
        queue.put_nowait(event)
    return event


def clear_replay(session_id: str, *, keep: frozenset[str] = frozenset()) -> None:
    """Forget the buffered events of the session (ids keep rising), except the kinds in ``keep``."""
    channel = _channel(session_id)
    channel.replay = [event for event in channel.replay if event.kind in keep]


def replay_after(session_id: str, after_id: int) -> list[SessionEvent]:
    """The buffered events with an id above ``after_id``."""
    return [event for event in _channel(session_id).replay if event.id > after_id]


@asynccontextmanager
async def subscribe(session_id: str, after_id: int = 0) -> AsyncIterator[tuple[list[SessionEvent], "asyncio.Queue[SessionEvent]"]]:
    """Subscribe to a session: yields the missed events and the queue of everything that follows.

    Registering the queue and taking the replay happen without an ``await`` in between, and
    ``publish`` runs on the same event loop: every event lands in exactly one of the two.
    """
    channel = _channel(session_id)
    queue: asyncio.Queue[SessionEvent] = asyncio.Queue()
    channel.subscribers.add(queue)
    try:
        yield replay_after(session_id, after_id), queue
    finally:
        channel.subscribers.discard(queue)


def subscriber_count(session_id: str) -> int:
    return len(_channel(session_id).subscribers)


def sessions_with_subscribers() -> list[str]:
    """The sessions at least one client listens to right now."""
    return [session_id for session_id, channel in _channels.items() if channel.subscribers]
