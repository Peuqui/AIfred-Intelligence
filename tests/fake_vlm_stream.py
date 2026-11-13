"""An httpx.AsyncClient stand-in for the image describer's streamed call (vision_analyzer._stream_completion)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Self


def streaming_client(sent: list[dict[str, Any]], pieces: list[str], last: dict[str, Any] | None = None) -> type:
    """A client whose ``stream()`` answers with server-sent events: one chunk per piece of the answer, then
    the end (finish_reason, plus ``last`` such as usage and timings). Each request lands in ``sent`` as
    ``{"url": …, "payload": …}``."""
    events: list[dict[str, Any]] = [{"choices": [{"delta": {"content": piece}}]} for piece in pieces]
    events.append({"choices": [{"delta": {}, "finish_reason": "stop"}], **(last or {})})

    class FakeStream:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        def raise_for_status(self) -> None:
            pass

        async def aiter_lines(self) -> AsyncIterator[str]:
            for event in events:
                yield f"data: {json.dumps(event)}"
                yield ""
            yield "data: [DONE]"

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        def stream(self, method: str, url: str, json: dict[str, Any] | None = None) -> FakeStream:
            sent.append({"url": url, "payload": json})
            return FakeStream()

    return FakeAsyncClient
