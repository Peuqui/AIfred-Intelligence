"""Stopping asyncio tasks."""

from __future__ import annotations

import asyncio
from typing import Any

from .logging_utils import log_message


async def cancel_and_wait(task: asyncio.Task[Any] | None, what: str, timeout: float | None = None) -> None:
    """Cancel ``task`` and wait until it has ended.

    A task that fails while it winds down, or is still running ``timeout`` seconds after the cancel,
    is logged and not raised: the caller stops it either way, but the failure must stay visible.
    A cancel of the caller itself is passed on.
    """
    if task is None or task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout)
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
    except TimeoutError:
        log_message(f"⚠️ {what}: still running {timeout} s after the cancel", "warning")
    except Exception as exc:  # noqa: BLE001 — logged: the task is stopped either way, its failure must not vanish
        log_message(f"⚠️ {what}: failed while stopping: {exc!r}", "error")
