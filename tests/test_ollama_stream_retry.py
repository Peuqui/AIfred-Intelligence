"""Ollama follows the retry rule of every backend (worth_retrying in backends/base.py): a request that
failed before its first chunk goes out again on a connection error or a 5xx, at once with a 4xx; the
try without think mode for a model that has none stays. Ollama itself is a fake behind MockTransport."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from aifred.backends import base
from aifred.backends.base import BackendInferenceError, BackendModelNotFoundError, LLMMessage, LLMOptions
from aifred.backends.ollama import OllamaBackend

ANSWER = "\n".join(json.dumps(line) for line in [
    {"message": {"content": "Antwort"}, "done": False},
    {"message": {"content": ""}, "done": True, "prompt_eval_count": 3, "eval_count": 1},
]).encode()


def run_turn(
    monkeypatch: pytest.MonkeyPatch, answers: list[Any], thinking: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[float]]:
    """Each request takes the next of ``answers``: an httpx.Response, or an exception to raise.
    Returns the items of the turn, the payloads sent and the waits."""
    sent: list[dict[str, Any]] = []
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def no_wait(seconds: float) -> None:
        waits.append(seconds)

    backend = OllamaBackend("http://ollama.test")
    backend.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def no_unload(model: str) -> None:
        return None

    monkeypatch.setattr(backend, "_handle_hybrid_model_unload", no_unload)
    monkeypatch.setattr(base.asyncio, "sleep", no_wait)
    monkeypatch.setattr("aifred.lib.logging_utils.log_message", lambda message, category="info": None)

    async def turn() -> list[dict[str, Any]]:
        options = LLMOptions(enable_thinking=thinking)
        return [item async for item in backend.chat_stream("qwen3:8b", [LLMMessage(role="user", content="Hallo")], options)]

    return asyncio.run(turn()), sent, waits


def text_of(items: list[dict[str, Any]]) -> str:
    return "".join(item.get("text", "") for item in items if item["type"] == "content")


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), httpx.Response(503, text="loading model"), httpx.Response(500, text="oom")],
)
def test_a_passing_error_is_retried(monkeypatch: pytest.MonkeyPatch, failure: Any) -> None:
    from aifred.lib.config import STREAM_RETRY_DELAY_S

    items, sent, waits = run_turn(monkeypatch, [failure, httpx.Response(200, content=ANSWER)])
    assert text_of(items) == "Antwort"
    assert (len(sent), waits) == (2, [STREAM_RETRY_DELAY_S])


def test_a_rejected_request_comes_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(BackendInferenceError):
        run_turn(monkeypatch, [httpx.Response(400, json={"error": "bad request"})])
    with pytest.raises(BackendModelNotFoundError):
        run_turn(monkeypatch, [httpx.Response(404, json={"error": "model not found"})])


def test_a_model_without_think_mode_is_asked_again_without_it(monkeypatch: pytest.MonkeyPatch) -> None:
    items, sent, waits = run_turn(monkeypatch, [
        httpx.Response(400, json={"error": "\"qwen3:8b\" does not support thinking"}),
        httpx.Response(200, content=ANSWER),
    ], thinking=True)
    assert text_of(items) == "Antwort"
    assert sent[0]["think"] is True and sent[1]["think"] is False
    assert waits == []
    assert any(item["type"] == "thinking_warning" for item in items)
