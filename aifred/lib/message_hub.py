"""Message Hub — Background worker management for channel listeners.

Manages the lifecycle of channel workers (IMAP listener, Discord bot, etc.).
Workers run in a **separate thread** with their own asyncio event loop,
completely independent of the Reflex UI event loop. This prevents UI
operations (session reloads, polling) from blocking channel processing.

Communication with the Reflex UI is via files (session JSON, notifications).

Workers auto-restart on crash with exponential backoff (max 5 retries).
"""

import asyncio
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Coroutine

from .logging_utils import log_message


def _hub_log(msg: str, level: str = "info") -> None:
    """Log to both debug-log file AND stdout (→ journalctl)."""
    log_message(msg, level)
    print(f"[MessageHub] {msg}", file=sys.stderr, flush=True)

# Auto-restart limits
_MAX_RESTART_ATTEMPTS = 5
_INITIAL_BACKOFF_SECONDS = 5
_MAX_BACKOFF_SECONDS = 300  # 5 minutes


@dataclass
class _Worker:
    """Internal bookkeeping for a registered worker."""

    name: str
    factory: Callable[[], Coroutine]  # async def that runs until cancelled
    task: asyncio.Task | None = None
    restart_count: int = 0


class MessageHub:
    """Central registry and lifecycle manager for channel workers.

    Workers run in a dedicated background thread with their own asyncio
    event loop, completely decoupled from the Reflex UI event loop.
    """

    def __init__(self) -> None:
        self._workers: dict[str, _Worker] = {}
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = threading.Event()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, factory: Callable[[], Coroutine]) -> None:
        """Register a worker coroutine factory.

        *factory* is called (without arguments) when the hub starts.
        The returned coroutine is wrapped in an asyncio.Task.  If the
        hub's event loop is already running (late registration, e.g.
        after the user toggles a channel on in the UI), the task is
        launched immediately on that loop in a thread-safe way.  Any
        existing task under the same name is cancelled first.
        """
        existing = self._workers.get(name)
        if existing and existing.task and not existing.task.done():
            _hub_log(
                f"Message Hub: worker '{name}' already registered, "
                f"cancelling previous task before replacing",
                "warning",
            )
            if self._loop is not None:
                self._loop.call_soon_threadsafe(existing.task.cancel)
            else:
                existing.task.cancel()

        worker = _Worker(name=name, factory=factory)
        self._workers[name] = worker
        _hub_log(f"Message Hub: registered worker '{name}'")

        # If the hub thread is already running, schedule the new worker
        # on its loop.  Without this, late-registered workers (e.g.
        # plugins enabled from the UI after startup) are silently
        # orphaned — their coroutine is never awaited, so no listener
        # binds its port.
        if (
            self._loop is not None
            and self._thread is not None
            and self._thread.is_alive()
        ):
            loop = self._loop

            def _schedule() -> None:
                worker.task = loop.create_task(
                    self._run_worker(worker),
                    name=f"message_hub:{worker.name}",
                )

            loop.call_soon_threadsafe(_schedule)
            _hub_log(f"Message Hub: scheduled '{name}' on running loop")

    def unregister(self, name: str) -> None:
        """Remove a worker (stops it first if running)."""
        worker = self._workers.pop(name, None)
        if worker and worker.task and not worker.task.done():
            worker.task.cancel()
            _hub_log(f"Message Hub: unregistered + cancelled worker '{name}'")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _thread_main(self) -> None:
        """Entry point for the worker thread. Creates its own event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        _hub_log("Message Hub: worker thread started (own event loop)")

        # Start all workers as tasks in this loop
        for worker in self._workers.values():
            if worker.task and not worker.task.done():
                continue
            worker.task = self._loop.create_task(
                self._run_worker(worker),
                name=f"message_hub:{worker.name}",
            )

        started = [w.name for w in self._workers.values() if w.task]
        if started:
            _hub_log(f"Message Hub: started workers: {', '.join(started)}")
        else:
            _hub_log("Message Hub: no workers registered")

        self._started.set()

        # Run the event loop until stop_all is called
        self._loop.run_forever()

        # Cleanup after loop stops
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.close()
        self._loop = None
        _hub_log("Message Hub: worker thread stopped")

    async def start_all(self) -> None:
        """Start all workers in a dedicated background thread.

        The thread gets its own asyncio event loop, completely independent
        of the Reflex UI loop. This prevents UI operations from blocking
        channel message processing.
        """
        if self._thread and self._thread.is_alive():
            _hub_log("Message Hub: worker thread already running")
            return

        self._started.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="message_hub_worker",
            daemon=True,
        )
        self._thread.start()

        # Wait for the thread to start its event loop
        self._started.wait(timeout=10)

    async def stop_all(self) -> None:
        """Stop all workers and shut down the worker thread."""
        if not self._loop or not self._thread:
            return

        # Cancel all tasks from the main thread (thread-safe)
        for worker in self._workers.values():
            if worker.task and not worker.task.done():
                self._loop.call_soon_threadsafe(worker.task.cancel)
                _hub_log(f"Message Hub: cancelling worker '{worker.name}'")

        # Stop the event loop (thread-safe)
        self._loop.call_soon_threadsafe(self._loop.stop)

        # Wait for thread to finish
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            _hub_log("Message Hub: WARNING — worker thread did not stop cleanly", "warning")

        # Clear task references
        for worker in self._workers.values():
            worker.task = None

        self._thread = None
        _hub_log("Message Hub: all workers stopped")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_running(self, name: str) -> bool:
        """Check whether a specific worker is currently running."""
        worker = self._workers.get(name)
        return worker is not None and worker.task is not None and not worker.task.done()

    def status(self) -> dict[str, bool]:
        """Return running status for all registered workers."""
        return {name: self.is_running(name) for name in self._workers}

    def health(self) -> dict[str, dict[str, object]]:
        """Return detailed health info for all workers."""
        return {
            name: {
                "running": self.is_running(name),
                "restart_count": w.restart_count,
            }
            for name, w in self._workers.items()
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_worker(self, worker: _Worker) -> None:
        """Wrapper with auto-restart on crash (exponential backoff)."""
        while True:
            try:
                worker.restart_count = 0  # Reset on successful start
                await worker.factory()
                # factory returned normally — don't restart
                _hub_log(f"Message Hub: worker '{worker.name}' exited normally")
                return
            except asyncio.CancelledError:
                _hub_log(f"Message Hub: worker '{worker.name}' stopped")
                return
            except Exception as exc:
                worker.restart_count += 1
                if worker.restart_count > _MAX_RESTART_ATTEMPTS:
                    error_msg = (
                        f"CRITICAL: worker '{worker.name}' crashed {worker.restart_count} times, "
                        f"permanently dead. Last error: {exc}"
                    )
                    _hub_log(f"Message Hub: {error_msg}", "error")
                    # Write notification so the UI can show it
                    from .message_processor import write_hub_notification
                    write_hub_notification(
                        session_id="",
                        session_title=f"Worker '{worker.name}' crashed",
                        channel=worker.name,
                        sender="system",
                        status="error",
                    )
                    return

                backoff = min(
                    _INITIAL_BACKOFF_SECONDS * (2 ** (worker.restart_count - 1)),
                    _MAX_BACKOFF_SECONDS,
                )
                _hub_log(
                    f"Message Hub: worker '{worker.name}' crashed ({worker.restart_count}/{_MAX_RESTART_ATTEMPTS}): "
                    f"{exc} — restarting in {backoff}s",
                    "error",
                )
                await asyncio.sleep(backoff)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
message_hub = MessageHub()


def register_channel_workers(hub: MessageHub | None = None) -> None:
    """Register all configured channel listeners with the hub.

    Auto-discovers channel plugins and registers those that are
    both configured (credentials present) and enabled in settings.
    """
    hub = hub or message_hub

    from .settings import load_settings
    from .plugin_registry import all_channels

    settings = load_settings() or {}
    channel_toggles = settings.get("channel_toggles", {})

    for name, plugin in all_channels().items():
        if hub.is_running(name):
            continue  # already running, don't re-register
        toggles = channel_toggles.get(name, {})
        plugin_on = toggles.get("monitor", False)
        listener_on = plugin_on if plugin.always_reply else toggles.get("listener", False)
        if plugin.is_configured() and plugin_on and listener_on:
            hub.register(name, plugin.listener_loop)
        else:
            _hub_log(f"Message Hub: channel '{name}' skipped "
                     f"(configured={plugin.is_configured()}, enabled={plugin_on}, listener={listener_on})")
