"""Watchdog for the server's event loop.

The server runs every request, every camera and every model stream on ONE loop thread. A call there that waits
(a file, a socket, a lock) without ``await`` stops the whole page: nothing is answered until it returns, then
everything comes at once. The log shows the freeze but not the line that causes it.

The watchdog is a thread of its own, so it keeps running while the loop stands. It hands the loop a tiny callback
every ``interval_s``; when that does not run within ``stall_s``, it writes the current stack of the loop's thread to
the debug log (once per freeze: the line the loop is stuck in) and, when the loop runs again, how long it stood.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback

from .logging_utils import log_message


class LoopWatchdog:
    def __init__(self, loop: asyncio.AbstractEventLoop, loop_thread_id: int, *, interval_s: float, stall_s: float) -> None:
        self._loop = loop
        self._loop_thread_id = loop_thread_id
        self._interval_s = interval_s
        self._stall_s = stall_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="loop-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            answered = threading.Event()
            asked_at = time.monotonic()
            self._loop.call_soon_threadsafe(answered.set)
            if not answered.wait(self._stall_s):
                self._report_stall(answered, asked_at)
            self._stop.wait(self._interval_s)

    def _report_stall(self, answered: threading.Event, asked_at: float) -> None:
        stack = "".join(traceback.format_stack(sys._current_frames()[self._loop_thread_id])).rstrip()
        log_message(f"🧊 Event loop does not answer for {self._stall_s:.1f} s — it is stuck here:\n{stack}", "warning")
        while not answered.wait(self._interval_s):
            if self._stop.is_set():
                return
        log_message(f"🧊 Event loop answers again after {time.monotonic() - asked_at:.1f} s", "warning")
