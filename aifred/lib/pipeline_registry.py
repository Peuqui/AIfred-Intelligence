"""Pipeline registry — single source of truth for active inference pipelines.

Tracks the currently running ``asyncio.Task`` per session so that an external
stop command (FreeEcho.2 wake-word ``_stop``, browser stop button, future Discord
``/stop``-slash, …) can cancel the pipeline cleanly.

Design notes
------------
* One task per session — if a new pipeline starts while an old one is still
  registered, the old one is cancelled (last-write-wins; the user's newest
  request takes priority).
* The registry only stores ``asyncio.Task`` references — cancellation
  propagates naturally through ``await`` points to LLM streaming, tool calls,
  TTS generation, web-research, etc.

Limitations
-----------
* Audio buffers already streamed to the FreeEcho.2/Browser cannot be revoked from
  here — that's the device's job. The FreeEcho.2 plugin instance handles its local
  buffer drain on its side; this server-side function only stops generating
  *new* output.
"""

from __future__ import annotations

import asyncio
import threading

from .logging_utils import log_message
from typing import Self


# Per-session task registry. Access guarded by lock because pipelines can be
# started/cancelled from different threads (e.g. background hub workers vs.
# main server event loop).
_active_pipelines: dict[str, asyncio.Task] = {}
_lock = threading.Lock()


def register_pipeline(session_id: str, task: asyncio.Task) -> None:
    """Mark ``task`` as the active pipeline for ``session_id``.

    If another pipeline is already registered for the same session, that one
    is cancelled first (newest wins). Caller is typically::

        task = asyncio.current_task()
        if task is not None:
            register_pipeline(session_id, task)

    inside ``process_inbound`` / ``send_message``.
    """
    if not session_id or task is None:
        return
    with _lock:
        previous = _active_pipelines.get(session_id)
        _active_pipelines[session_id] = task
    # Cancel outside the lock — task.cancel() may schedule callbacks that
    # try to re-acquire the registry.
    if previous is not None and previous is not task and not previous.done():
        log_message(f"Pipeline registry: superseding old task for session {session_id[:8]}")
        _cancel(previous)


def unregister_pipeline(session_id: str, task: asyncio.Task | None = None) -> None:
    """Remove the entry for ``session_id`` (idempotent).

    If ``task`` is given, only removes when that task is still the registered
    one — protects against a stale ``finally`` removing a newer pipeline's
    registration after a supersession.
    """
    if not session_id:
        return
    with _lock:
        current = _active_pipelines.get(session_id)
        if task is None or current is task:
            _active_pipelines.pop(session_id, None)


def is_pipeline_running(session_id: str) -> bool:
    """Whether a turn of the session runs right now."""
    with _lock:
        task = _active_pipelines.get(session_id)
    return task is not None and not task.done()


def running_pipeline_count() -> int:
    """How many turns run right now, over every session and channel."""
    with _lock:
        return sum(1 for task in _active_pipelines.values() if not task.done())


def cancel_pipeline(session_id: str) -> bool:
    """Cancel the registered pipeline for ``session_id``.

    Returns True if a live task was cancelled, False if nothing was running.
    The cancellation propagates via ``asyncio.CancelledError`` through every
    ``await`` point in the pipeline (LLM stream, tool calls, TTS, …).
    """
    if not session_id:
        return False
    with _lock:
        task = _active_pipelines.get(session_id)
    if task is None or task.done():
        return False
    _cancel(task)
    return True


def _cancel(task: asyncio.Task) -> None:
    """Cancel on the task's own loop: a channel's turn runs on the message hub's thread, the stop
    comes from the server's (Task.cancel is not thread-safe)."""
    task.get_loop().call_soon_threadsafe(task.cancel)


class pipeline_scope:
    """Context manager that pairs ``register_pipeline`` with ``unregister_pipeline``.

    Ensures cleanup runs even on exceptions or cancellation::

        with pipeline_scope(session_id, asyncio.current_task()):
            ...  # pipeline body

    A ``None`` task or empty ``session_id`` make the scope a no-op so callers
    don't need to special-case those situations.
    """

    def __init__(self, session_id: str, task: asyncio.Task | None) -> None:
        self._session_id = session_id
        self._task = task

    def __enter__(self) -> Self:
        if self._task is not None and self._session_id:
            register_pipeline(self._session_id, self._task)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._task is not None and self._session_id:
            unregister_pipeline(self._session_id, self._task)
