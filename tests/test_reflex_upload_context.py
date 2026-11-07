"""An upload request runs in an event context of its own.

Reflex 0.9.10.post1 built the upload's context with ``dataclasses.replace(root_context, …)`` and so
kept the root's txid. Every event enqueued from outside (websocket, API) is a ``fork()`` of the root
context, whose parent is that txid: while an upload ran, each of them became a child of the upload,
and the upload cancelled them when its request ended early. 06.11.2026: a dictation sent straight to
the AI is picked up by the poll during the recording's upload; closing the tab killed its turn.
AIfred patched this until Reflex fixed it itself (PR #7357, 0.10.0); the test now guards against
the fault coming back with a Reflex upgrade.
"""

import asyncio
from typing import NoReturn

import pytest
from reflex.event import Event
from reflex_base.event.context import EventContext
from reflex_base.event.processor.event_processor import EventProcessor


class _Recorded(Exception):
    """Stops the upload's enqueue once its context has been seen."""


def test_an_upload_does_not_take_the_root_txid(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = EventProcessor().configure()
    seen: dict[str, EventContext] = {}

    async def record(self: EventProcessor, token: str, event: Event, ev_ctx: EventContext | None = None) -> NoReturn:
        assert ev_ctx is not None
        seen["ctx"] = ev_ctx
        raise _Recorded

    monkeypatch.setattr(EventProcessor, "enqueue", record)

    async def upload() -> None:
        with pytest.raises(_Recorded):
            async for _delta in processor.enqueue_stream_delta("token", Event(name="state.upload", payload={})):
                pass

    asyncio.run(upload())
    root = processor._root_context
    assert root is not None
    outside = root.fork(token="token")   # how the websocket and the API enqueue an event
    assert seen["ctx"].txid != root.txid
    assert outside.parent_txid != seen["ctx"].txid   # an outside event is never the upload's child
