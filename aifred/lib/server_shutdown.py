"""The server's shutdown as an event the endless streams end on.

uvicorn waits for every open response before it leaves (and before the lifespans end), and an event stream or a
camera's picture stream never ends by itself: one open tab kept the service in its stop until systemd killed it after
90 s (09.11.2026). ``serve`` runs uvicorn so that its shutdown first sets the event; every endless stream goes through
``until_shutdown``.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncGenerator, AsyncIterator

import uvicorn
from starlette.types import ASGIApp

_shutting_down = asyncio.Event()


class _Server(uvicorn.Server):
    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        _shutting_down.set()
        await super().shutdown(sockets)


def serve(app: ASGIApp, host: str, port: int) -> None:
    """Run ``app`` under uvicorn until SIGTERM/SIGINT; the endless streams end at the start of the shutdown."""
    _Server(uvicorn.Config(app, host=host, port=port, log_level="warning")).run()


async def until_shutdown[T](stream: AsyncGenerator[T, None]) -> AsyncIterator[T]:
    """The items of ``stream`` until it ends or the server shuts down; then ``stream`` is closed. Also when the client
    goes away: the step still waiting on ``stream`` is cancelled first, a running generator cannot be closed."""
    stop = asyncio.ensure_future(_shutting_down.wait())
    step: asyncio.Future[T] | None = None
    try:
        while True:
            step = asyncio.ensure_future(anext(stream))
            await asyncio.wait({step, stop}, return_when=asyncio.FIRST_COMPLETED)
            if not step.done():
                return
            try:
                value = step.result()
            except StopAsyncIteration:
                return
            yield value
    finally:
        stop.cancel()
        if step is not None and not step.done():
            step.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await step
        await stream.aclose()
