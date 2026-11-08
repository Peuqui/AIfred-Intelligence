"""Centralized Debug Bus: what a turn reports to its session, from any depth of the code.

One call for every context: ``debug("message")`` writes the log, and inside a ``session_scope`` also
the session's debug console (its file and its open pages). ``report_phase`` tells the session's pages
what the turn is doing while no text comes (the line in the chat, ``frontend/src/lib/turnProgress.ts``).

Usage:
    from aifred.lib.debug_bus import debug, session_scope

    with session_scope(session_id):      # once per turn (turn_service.run_turn) or request
        debug("🔍 Web search started")   # → log + session file + ``debug`` event
        report_phase("scraping", current=3, total=7)

    # Outside a session (startup): the log only
    debug("Server starting...")
"""

import contextvars
from datetime import datetime
from typing import Self

from .logging_utils import log_message

# Session ID for the current async task (set via session_scope)
_current_session: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "debug_bus_session", default=None
)


class session_scope:
    """Context manager that binds a session_id for debug message routing.

    Messages emitted via debug() inside this scope are written immediately
    to the session file (one write per message) so the UI polling timer
    can pick them up in near-realtime.
    Nest-safe: restores previous session_id on exit.
    """

    def __init__(self, session_id: str | None) -> None:
        self._session_id = session_id
        self._session_token: contextvars.Token | None = None

    def __enter__(self) -> Self:
        self._session_token = _current_session.set(self._session_id)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._session_token is not None:
            _current_session.reset(self._session_token)


def debug(msg: str) -> None:
    """Emit a debug message: the log always; inside a session_scope also the session file and a
    ``debug`` event to the session's pages.

    Args:
        msg: The debug message (without timestamp — added automatically).
    """
    log_message(msg)
    show_in_session(msg)


def show_in_session(msg: str) -> None:
    """The line in the debug console of the current session: its file and a ``debug`` event to its
    pages. Nothing outside a session_scope (a channel's startup and shutdown stay out of the console)."""
    sid = _current_session.get()
    if sid:
        line = f"{datetime.now().strftime('%H:%M:%S')} | {msg}"
        _flush_to_session(sid, [line])
        from . import session_events
        session_events.publish(sid, "debug", {"line": line})


def report_phase(phase: str, *, current: int = 0, total: int = 0, failed: int = 0) -> None:
    """Tell the pages of the current session what the turn is doing: ``intent`` (the Automatik call),
    ``images`` (their description), ``compress`` (the history), ``scraping`` (``current`` of ``total``
    pages read, ``failed`` unreachable). An empty phase ends it; the caller ends what it began. The
    browser shows each phase by its name (``frontend/src/lib/turnProgress.ts``, tests/test_turn_phases.py).

    Outside a session_scope nobody listens (a search outside a turn).
    """
    sid = _current_session.get()
    if sid:
        from . import session_events
        session_events.publish(
            sid, "turn_phase", {"phase": phase, "current": current, "total": total, "failed": failed},
            replaces=frozenset({"turn_phase"}),
        )


def _flush_to_session(session_id: str, messages: list[str]) -> None:
    """Write buffered debug messages to the session file."""
    from .config import MESSAGE_HUB_OWNER
    from .session_storage import load_session, session_rmw_lock, update_chat_data

    # M4: load→append→save as ONE unit — debug flushes run concurrently
    # with hub/browser chat writes on the same file (see session_rmw_lock).
    with session_rmw_lock:
        session = load_session(session_id)
        data = session.get("data", {}) if session else {}
        existing = data.get("debug_messages", [])
        existing.extend(messages)

        update_chat_data(
            session_id=session_id,
            chat_history=data.get("chat_history", []),
            debug_messages=existing,
            owner=MESSAGE_HUB_OWNER,
        )
