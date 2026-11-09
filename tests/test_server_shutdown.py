"""The endless streams end when the server shuts down (09.11.2026: one open tab's event stream kept the service in its
stop until systemd killed it after 90 s), and close their generator when the client goes away."""

import asyncio
from collections.abc import AsyncGenerator

import pytest

from aifred.lib import server_shutdown
from aifred.lib.server_shutdown import until_shutdown


@pytest.fixture(autouse=True)
def fresh_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_shutdown, "_shutting_down", asyncio.Event())


def endless(closed: list[bool]) -> AsyncGenerator[int, None]:
    async def stream() -> AsyncGenerator[int, None]:
        try:
            number = 0
            while True:
                yield number
                number += 1
                await asyncio.Event().wait()  # an event that never comes, as a tab's quiet session
        finally:
            closed.append(True)

    return stream()


def test_shutdown_ends_a_waiting_stream_and_closes_it() -> None:
    closed: list[bool] = []

    async def run() -> list[int]:
        received: list[int] = []

        async def read() -> None:
            async for item in until_shutdown(endless(closed)):
                received.append(item)

        reader = asyncio.create_task(read())
        await asyncio.sleep(0.05)
        server_shutdown._shutting_down.set()
        await asyncio.wait_for(reader, timeout=2)
        return received

    assert asyncio.run(run()) == [0]
    assert closed == [True]


def test_a_finite_stream_runs_to_its_end() -> None:
    async def three() -> AsyncGenerator[int, None]:
        for number in range(3):
            yield number

    async def run() -> list[int]:
        return [item async for item in until_shutdown(three())]

    assert asyncio.run(run()) == [0, 1, 2]


def test_the_client_going_away_closes_the_stream() -> None:
    closed: list[bool] = []

    async def run() -> None:
        async def read() -> None:
            async for _ in until_shutdown(endless(closed)):
                pass

        reader = asyncio.create_task(read())
        await asyncio.sleep(0.05)
        reader.cancel()  # what Starlette does when the browser closes the connection
        with pytest.raises(asyncio.CancelledError):
            await reader

    asyncio.run(run())
    assert closed == [True]
