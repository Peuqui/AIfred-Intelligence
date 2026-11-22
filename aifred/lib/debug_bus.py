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
import threading
from datetime import datetime
from typing import Self

from .logging_utils import log_message

# Session ID for the current async task (set via session_scope)
_current_session: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "debug_bus_session", default=None
)

# Lines of the running scopes, per session, waiting for their one write to the session file. The pages get each
# line at once through the ``debug`` event; the file (what a page loads when it opens) is written when the scope
# ends, not once per line (a session file is rewritten as a whole, some are hundreds of KB). A crash loses only
# the lines of the unfinished scope in the file; the log has them (``debug`` writes it before).
_pending: dict[str, list[str]] = {}
# Guards the swap in ``_take_pending``: debug() is also called from worker threads (to_thread keeps the context).
_pending_lock = threading.Lock()


def current_session_id() -> str | None:
    """The session the current task works for (``session_scope``), ``None`` outside one."""
    return _current_session.get()


class session_scope:
    """Context manager that binds a session_id for debug message routing.

    Messages emitted via debug() inside this scope reach the session's open pages at once (``debug`` event) and
    the session file once, when the scope ends.
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
        if self._session_id:
            lines = _take_pending(self._session_id)
            if lines:
                _flush_to_session(self._session_id, lines)


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
        with _pending_lock:
            _pending.setdefault(sid, []).append(line)
        from . import session_events
        session_events.publish(sid, "debug", {"line": line})


def report_phase(
    phase: str, *, current: int = 0, total: int = 0, failed: int = 0, detail: dict[str, str | bool | None] | None = None,
) -> None:
    """Tell the pages of the current session what the turn is doing: ``intent`` (the Automatik call),
    ``images`` (their description), ``compress`` (the history), ``model_wait`` (another turn's models run),
    ``gpu_hold`` (somebody holds the cards, GPU-Orc /hold), ``scraping`` (``current`` of ``total``
    pages read, ``failed`` unreachable). ``detail``: what the page shows besides the name (``gpu_hold``: who holds
    which machine's cards, see ``gpu_hold._hold_detail``). An empty phase ends it; the caller ends what it began. The
    browser shows each phase by its name (``frontend/src/lib/turnProgress.ts``, tests/test_turn_phases.py).

    Outside a session_scope nobody listens (a search outside a turn).
    """
    sid = _current_session.get()
    if sid:
        from . import session_events
        session_events.publish(
            sid, "turn_phase", {"phase": phase, "current": current, "total": total, "failed": failed, "detail": detail},
            replaces=frozenset({"turn_phase"}),
        )


def _take_pending(session_id: str) -> list[str]:
    """The waiting lines of a session, removed from the buffer."""
    with _pending_lock:
        return _pending.pop(session_id, [])


def pending_lines(session_id: str) -> list[str]:
    """The lines of the session's running scopes not yet in its file: a page opened mid-turn shows them too."""
    with _pending_lock:
        return list(_pending.get(session_id, []))


def drop_pending(session_id: str) -> None:
    """Forget the session's waiting lines: its chat was emptied, they must not come back with the scope's end."""
    _take_pending(session_id)


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
