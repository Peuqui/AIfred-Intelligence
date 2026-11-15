"""A describer's refusal (HTTP 4xx) reaches the log with the server's reason, not only with the status line."""

from __future__ import annotations

import asyncio
from typing import Any, Self

import httpx
import pytest

from aifred.lib import vision_analyzer

URL = "http://describer.example/v1/chat/completions"
REASON = "This model's maximum context length is 24576 tokens, however you requested 31000 tokens"


class RefusingStream:
    def __init__(self) -> None:
        self._response = httpx.Response(400, text=REASON, request=httpx.Request("POST", URL))

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    @property
    def request(self) -> httpx.Request:
        return self._response.request

    @property
    def text(self) -> str:
        return self._response.text

    def raise_for_status(self) -> None:
        self._response.raise_for_status()

    async def aread(self) -> bytes:
        return await self._response.aread()


class RefusingClient:
    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    def stream(self, method: str, url: str, json: dict[str, Any] | None = None) -> RefusingStream:
        return RefusingStream()


def test_the_servers_reason_is_in_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", RefusingClient)
    with pytest.raises(httpx.HTTPStatusError, match="maximum context length is 24576"):
        asyncio.run(vision_analyzer._stream_completion(URL, {"model": "m"}))
