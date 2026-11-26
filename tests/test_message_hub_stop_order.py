"""stop_all lets the workers end before the hub loop stops.

A worker's shutdown may still need other tasks of its loop (Telegram's updater.stop() waits for its update fetcher).
Until 26.11.2026 the loop stopped right after the cancel, and its cleanup cancelled those tasks while the worker was
still shutting down.
"""

from __future__ import annotations

import asyncio

from aifred.lib.message_hub import MessageHub


def test_worker_shutdown_can_still_use_its_helper_task() -> None:
    hub = MessageHub()
    shutdown_result: list[str] = []

    async def worker_with_helper() -> None:
        release = asyncio.Event()

        async def helper() -> str:
            await release.wait()
            return "helper finished"

        helper_task = asyncio.create_task(helper())
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            release.set()
            try:
                shutdown_result.append(await helper_task)
            except asyncio.CancelledError:
                shutdown_result.append("helper was cancelled under the worker")
            raise

    async def scenario() -> None:
        hub.register("probe", worker_with_helper)
        await hub.start_all()
        for _ in range(100):
            if hub.is_running("probe"):
                break
            await asyncio.sleep(0.01)
        assert hub.is_running("probe")
        await hub.stop_all()

    asyncio.run(scenario())
    assert shutdown_result == ["helper finished"]
