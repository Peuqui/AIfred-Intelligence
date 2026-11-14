"""Unregistering a worker cancels its task on the hub thread's loop, not from the caller's thread: a task belongs to
the loop it runs on (register and stop_all always did it that way; unregister did not until 14.11.2026)."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from aifred.lib.message_hub import MessageHub

if TYPE_CHECKING:
    _TaskBase = asyncio.Task[None]
else:
    _TaskBase = object


class _ProbeTask(_TaskBase):
    """A task stand-in that notes the thread its cancel arrives on."""

    def __init__(self) -> None:
        self.cancelled_on: int | None = None

    def done(self) -> bool:
        return False

    def cancel(self, msg: object = None) -> bool:
        self.cancelled_on = threading.get_ident()
        return True


def test_unregister_cancels_on_the_hub_thread() -> None:
    hub = MessageHub()

    async def wait_forever() -> None:
        await asyncio.Event().wait()

    async def scenario() -> None:
        hub.register("probe", wait_forever)
        await hub.start_all()
        for _ in range(100):
            if hub.is_running("probe"):
                break
            await asyncio.sleep(0.01)
        assert hub.is_running("probe")
        probe = _ProbeTask()
        hub._workers["probe"].task = probe
        hub.unregister("probe")
        for _ in range(100):
            if probe.cancelled_on is not None:
                break
            await asyncio.sleep(0.01)
        assert hub._thread is not None
        assert probe.cancelled_on == hub._thread.ident != threading.get_ident()
        assert hub.registered() == set()
        await hub.stop_all()

    asyncio.run(scenario())
