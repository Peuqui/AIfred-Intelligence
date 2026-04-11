"""Centralized Debug Bus — single debug API for all contexts.

Replaces the dual debug paths (Browser State vs Hub debug_sink).
Plugin developers only need one call: debug("message").

Usage:
    from aifred.lib.debug_bus import debug, session_scope

    # Hub path: set session context once per request
    with session_scope(session_id):
        debug("🔍 Web search started")   # → log + session file (immediate)
        debug("✅ 7 sources scraped")     # → log + session file (immediate)

    # Browser path: state.add_debug() forwards to debug() internally
    # No session_scope needed — Reflex State handles live UI updates

    # Standalone (startup, no session): goes to logfile only
    debug("Server starting...")
"""

import contextvars
from datetime import datetime

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

    def __enter__(self) -> "session_scope":
        self._session_token = _current_session.set(self._session_id)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._session_token is not None:
            _current_session.reset(self._session_token)


def debug(msg: str) -> None:
    """Emit a debug message.

    - Always: writes to log_message() (logfile + console queue)
    - If session_scope active: writes immediately to session file
      so the UI timer (1s polling) can pick it up in near-realtime.

    Args:
        msg: The debug message (without timestamp — added automatically).
    """
    log_message(msg)

    sid = _current_session.get()
    if sid:
        ts = datetime.now().strftime("%H:%M:%S")
        _flush_to_session(sid, [f"{ts} | {msg}"])



def _flush_to_session(session_id: str, messages: list[str]) -> None:
    """Write buffered debug messages to the session file."""
    from .session_storage import load_session, update_chat_data
    from .config import MESSAGE_HUB_OWNER

    session = load_session(session_id)
    data = session.get("data", {}) if session else {}
    existing = data.get("debug_messages", [])
    existing.extend(messages)

    # Browser detects via session file mtime-watch (SSOT)
    update_chat_data(
        session_id=session_id,
        chat_history=data.get("chat_history", []),
        debug_messages=existing,
        owner=MESSAGE_HUB_OWNER,
    )
