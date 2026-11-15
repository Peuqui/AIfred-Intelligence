"""The gate for the models a turn calls.

llama-swap's group ``main`` is exclusive: a call to another model unloads the loaded one. Two sessions that work
with different big models would swap them with every request (a cold start of minutes each time), and neither would
get on. The gate lets turns that call the SAME models run side by side and makes a turn with other models wait until
every running turn is done. Strictly first come, first served: nothing overtakes a waiting turn, so none starves.

A key is the set of model ids a turn can call (``turn_service``); equal keys are compatible. The turns run on several
event loops (the server's, the message hub's, the scheduler's), the state is guarded by a lock and every waiting turn
is woken on its own loop.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

ModelKey = frozenset[str]


@dataclass(eq=False)
class _Ticket:
    key: ModelKey
    label: str
    granted: bool = False
    loop: asyncio.AbstractEventLoop | None = None
    wake: asyncio.Future[None] | None = None


class ModelGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: list[_Ticket] = []
        self._waiting: deque[_Ticket] = deque()

    def _compatible(self, key: ModelKey) -> bool:
        return all(ticket.key == key for ticket in self._running)

    def _grant_waiting(self) -> None:
        """Start the waiting turns from the front while they fit; the first that does not stops the line."""
        while self._waiting and self._compatible(self._waiting[0].key):
            ticket = self._waiting.popleft()
            ticket.granted = True
            self._running.append(ticket)
            assert ticket.loop is not None and ticket.wake is not None
            ticket.loop.call_soon_threadsafe(_wake, ticket.wake)

    def _release(self, ticket: _Ticket) -> None:
        with self._lock:
            if ticket.granted:
                self._running.remove(ticket)
            else:
                self._waiting.remove(ticket)
            self._grant_waiting()

    @asynccontextmanager
    async def hold(
        self, key: ModelKey, *, label: str, on_wait: Callable[[tuple[str, ...]], None] | None = None,
    ) -> AsyncIterator[None]:
        """Run in the block as one of the turns that call ``key``. When turns with other models run or wait, the turn
        waits for its place first; ``on_wait`` gets the labels of the turns it waits behind (the running ones, then
        the waiting). A cancelled turn leaves the line."""
        ticket = _Ticket(key, label)
        with self._lock:
            if not self._waiting and self._compatible(key):
                ticket.granted = True
                self._running.append(ticket)
                blockers: tuple[str, ...] = ()
            else:
                ticket.loop = asyncio.get_running_loop()
                ticket.wake = ticket.loop.create_future()
                self._waiting.append(ticket)
                blockers = tuple(other.label for other in (*self._running, *list(self._waiting)[:-1]))
        try:
            if not ticket.granted:
                if on_wait is not None:
                    on_wait(blockers)
                assert ticket.wake is not None
                await ticket.wake
            yield
        finally:
            self._release(ticket)


def _wake(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


_gate = ModelGate()


def model_gate() -> ModelGate:
    return _gate
