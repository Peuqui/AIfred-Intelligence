"""start_all and stop_all wait for the hub thread, but not on the server's event loop: the loop stays usable meanwhile."""

from __future__ import annotations

import asyncio
import threading

from aifred.lib.message_hub import MessageHub


def test_loop_stays_responsive_while_start_all_waits() -> None:
    hub = MessageHub()
    release = threading.Event()
    original_thread_main = hub._thread_main

    def slow_thread_main() -> None:
        # The hub thread reports "started" only after the gate opens.
        release.wait(timeout=5)
        original_thread_main()

    hub._thread_main = slow_thread_main  # type: ignore[method-assign]

    async def scenario() -> int:
        ticks = 0
        starting = asyncio.create_task(hub.start_all())
        # The gate opens from another thread after 0.3 s; the loop has to tick until then.
        threading.Timer(0.3, release.set).start()
        while not release.is_set():
            await asyncio.sleep(0.02)
            ticks += 1
        await starting
        await hub.stop_all()
        return ticks

    assert asyncio.run(scenario()) >= 5
