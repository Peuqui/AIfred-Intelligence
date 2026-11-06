"""Stream handling of the OpenAI-compatible backends (vLLM, llama.cpp).

- vLLM (``--reasoning-parser``) streams the thinking part as ``reasoning``,
  llama.cpp (``--reasoning-format deepseek``) as ``reasoning_content``. Until
  2026-09-11 only the llama.cpp field was read, so every Flash-Next thought
  was dropped.
- A server whose tool-call parser does not match the model's chat template
  reports ``finish_reason=tool_calls`` without delivering a call — that went
  unnoticed for two weeks and must now be loud.
- vLLM's own per-request measurement (counter delta around exactly one
  request) must survive tool rounds: the footer showed 22 tok/s prefill and
  6.5 tok/s decode for a turn vLLM itself measured at 500 and 33.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from openai.types.chat import ChatCompletionChunk

from aifred.backends.base import LLMMessage
from aifred.backends.llamacpp import LlamaCppBackend
from aifred.backends.vllm import vLLMBackend

# aifred.lib zuerst: aifred.backends allein laeuft in einen Zirkelimport
from aifred.lib.function_calling import Tool, ToolKit
from aifred.lib.perf_metrics import InferenceWork


def _chunk(delta: dict[str, Any], finish_reason: str | None = None) -> ChatCompletionChunk:
    return ChatCompletionChunk.model_validate({
        "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    })


async def _stream(chunks: list[ChatCompletionChunk]):
    for chunk in chunks:
        yield chunk


def _consume(backend: Any, chunks: list[ChatCompletionChunk]) -> tuple[str, dict[str, Any]]:
    state: dict[str, Any] = {}
    counters: dict[str, Any] = {"prompt_tokens": 0, "total_tokens": 0,
                                "server_timings": {}, "last_finish_reason": None}

    async def run() -> str:
        texts = [item["text"] async for item in backend._consume_stream(
            _stream(chunks), state, counters, [])]
        return "".join(texts)

    return asyncio.run(run()), state


def test_vllm_reasoning_field_becomes_a_think_block() -> None:
    text, state = _consume(vLLMBackend(), [
        _chunk({"reasoning": "Psalm 91 "}),
        _chunk({"reasoning": "passt."}),
        _chunk({"content": "Der Herr ist meine Zuflucht."}),
    ])
    assert text == "<think>Psalm 91 passt.</think>\n\nDer Herr ist meine Zuflucht."
    assert state["_reasoning_acc"] == "Psalm 91 passt."
    assert state["_visible_acc"] == "Der Herr ist meine Zuflucht."


def test_llamacpp_keeps_reading_reasoning_content() -> None:
    text, _ = _consume(LlamaCppBackend(), [
        _chunk({"reasoning_content": "denke"}),
        _chunk({"content": "Antwort"}),
    ])
    assert text == "<think>denke</think>\n\nAntwort"


def _toolkit() -> ToolKit:
    return ToolKit(
        tools=[Tool(name="search_bible", description="x", parameters={},
                    executor=lambda **kw: "ok", tier=2)],
        _source="browser",
        _max_tier=4,
    )


def _offline_vllm(monkeypatch) -> vLLMBackend:
    """vLLM backend whose pre-request check (llama-swap /running, model
    load) stays off the network — the model counts as running."""
    backend = vLLMBackend()

    async def running(model: str) -> float:
        return 0.0

    monkeypatch.setattr(backend, "_pre_request_check", running)
    return backend


def test_swallowed_tool_call_is_reported(monkeypatch) -> None:
    """Round 1 as vLLM with a mismatched parser answers: finish_reason
    tool_calls, no call, no text. The loop must say so before it forces the
    final round without tools."""
    backend = _offline_vllm(monkeypatch)
    monkeypatch.setattr(backend, "_read_counters", lambda: None)
    rounds = [
        [_chunk({"content": ""}), _chunk({}, finish_reason="tool_calls")],
        [_chunk({"content": "Ohne Werkzeug."}), _chunk({}, finish_reason="stop")],
    ]
    requests: list[dict[str, Any]] = []

    async def create(**kwargs: Any):
        requests.append(kwargs)
        return _stream(rounds.pop(0))

    backend.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def run() -> list[dict[str, Any]]:
        return [item async for item in backend.chat_stream(
            "m", [LLMMessage(role="user", content="Psalm 91?")], toolkit=_toolkit())]

    items = asyncio.run(run())
    debug = [i["message"] for i in items if i.get("type") == "debug"]
    assert any("delivered no tool call" in m for m in debug)
    # Forced final round without tools still produces the answer
    assert requests[1]["tools"] is None
    assert "Ohne Werkzeug." in "".join(i.get("text", "") for i in items)


def _tool_round_turn(monkeypatch, counter_states: list, toolkit: ToolKit | None = None) -> dict[str, Any]:
    """A two-round vLLM turn (tool call, then answer) against fake counters;
    returns the done metrics. ``counter_states`` are the successive
    /metrics readings, one before and one after each request:
    (port, (prefill_tok, prefill_s, requests, gen_tok, decode_s))."""
    backend = _offline_vllm(monkeypatch)
    readings = iter(counter_states)
    monkeypatch.setattr(backend, "_read_counters", lambda: next(readings))
    call = {"index": 0, "id": "c1", "type": "function",
            "function": {"name": "search_bible", "arguments": '{"query": "Psalm 91"}'}}
    rounds = [
        [_chunk({"tool_calls": [call]}), _chunk({}, finish_reason="tool_calls")],
        [_chunk({"content": "Der Herr ist meine Zuflucht."}), _chunk({}, finish_reason="stop")],
    ]

    async def create(**kwargs: Any):
        return _stream(rounds.pop(0))

    backend.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def run() -> dict[str, Any]:
        done: dict[str, Any] = {}
        async for item in backend.chat_stream(
                "m", [LLMMessage(role="user", content="Psalm 91?")], toolkit=toolkit or _toolkit()):
            if item.get("type") == "done":
                done = item["metrics"]
        return done

    return asyncio.run(run())


ROUND_1_BEFORE = (5811, (0.0, 0.0, 0.0, 0.0, 0.0))
ROUND_1_AFTER = (5811, (30700.0, 58.0, 1.0, 60.0, 2.0))
ROUND_2_AFTER = (5811, (32000.0, 60.6, 2.0, 476.0, 14.6))


def test_vllm_rates_sum_every_request_of_a_tool_turn(monkeypatch) -> None:
    metrics = _tool_round_turn(monkeypatch, [ROUND_1_BEFORE, ROUND_1_AFTER, ROUND_1_AFTER, ROUND_2_AFTER])
    # Total tokens over total phase time of both requests, from vLLM's own counters
    work = InferenceWork.from_dict(metrics["work"])
    assert work.prefill_tokens == 32000
    assert work.prefill_rate() == pytest.approx(32000 / 60.6)
    assert work.decode_tokens == 476
    assert work.decode_rate() == pytest.approx(476 / 14.6)


def test_vllm_foreign_request_in_the_window_makes_prefill_unknown(monkeypatch) -> None:
    # A second request finished while round 2 ran: not attributable, and the
    # server reported no cache hits to measure from outside, so no guess.
    metrics = _tool_round_turn(monkeypatch, [
        ROUND_1_BEFORE, ROUND_1_AFTER, ROUND_1_AFTER, (5811, (32500.0, 61.5, 3.0, 500.0, 15.5)),
    ])
    assert InferenceWork.from_dict(metrics["work"]).prefill_rate() is None


def test_subagent_work_reported_by_a_tool_joins_the_turn(monkeypatch) -> None:
    async def delegate(**kwargs: Any):
        yield {"work": {"prefill_tokens": 8000, "prefill_s": 9.4, "decode_tokens": 1024, "decode_s": 25.4,
                        "thinking_s": 6.0}}
        yield {"result": "Bericht"}

    toolkit = ToolKit(
        tools=[Tool(name="search_bible", description="x", parameters={}, executor=delegate, tier=2)],
        _source="browser",
        _max_tier=4,
    )
    metrics = _tool_round_turn(monkeypatch, [ROUND_1_BEFORE, ROUND_1_AFTER, ROUND_1_AFTER, ROUND_2_AFTER], toolkit)
    assert metrics["work"]["thinking_s"] == pytest.approx(6.0)  # the sub-agent's thinking
    work = InferenceWork.from_dict(metrics["work"])
    assert work.prefill_tokens == 32000 + 8000
    assert work.prefill_rate() == pytest.approx(40000 / 70.0)
    assert work.decode_tokens == 476 + 1024
    assert work.decode_rate() == pytest.approx(1500 / 40.0)


def test_thinking_of_every_round_is_summed(monkeypatch) -> None:
    """The model thinks before the tool call and again before the answer;
    both blocks count, not only the first."""
    backend = _offline_vllm(monkeypatch)
    monkeypatch.setattr(backend, "_read_counters", lambda: None)
    call = {"index": 0, "id": "c1", "type": "function",
            "function": {"name": "search_bible", "arguments": '{"query": "Psalm 91"}'}}

    async def slow(chunks: list[ChatCompletionChunk]):
        for chunk in chunks:
            await asyncio.sleep(0.05)
            yield chunk

    rounds = [
        [_chunk({"reasoning": "erst suchen"}), _chunk({"reasoning": "."}),
         _chunk({"tool_calls": [call]}), _chunk({}, finish_reason="tool_calls")],
        [_chunk({"reasoning": "passt"}), _chunk({"reasoning": "."}),
         _chunk({"content": "Der Herr ist meine Zuflucht."}), _chunk({}, finish_reason="stop")],
    ]

    async def create(**kwargs: Any):
        return slow(rounds.pop(0))

    backend.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def run() -> dict[str, Any]:
        done: dict[str, Any] = {}
        async for item in backend.chat_stream(
                "m", [LLMMessage(role="user", content="Psalm 91?")], toolkit=_toolkit()):
            if item.get("type") == "done":
                done = item["metrics"]
        return done

    thinking_s = asyncio.run(run())["work"]["thinking_s"]
    # Round 1 thinks from its first chunk to the stream end (~0.15 s), round 2
    # until its answer starts (~0.10 s). The first block alone would be ~0.15 s.
    assert thinking_s >= 0.22


# ---- When a failed request is sent again -------------------------------------------------
# Before its first piece reached the caller, a request is retried only when a new try can get past
# the error (connection, timeout, server 5xx); an error of AIfred's own code comes at once instead of
# after 15 minutes of silent retries.


def _request_url() -> httpx2.Request:
    # openai 3 has its own transport (httpx2); its errors carry httpx2 objects, not httpx ones.
    return httpx2.Request("POST", "http://swap/v1/chat/completions")


def _connection_error() -> Exception:
    import openai
    return openai.APIConnectionError(request=_request_url())


def _server_error(message: str, status: int = 502) -> Exception:
    import openai
    return openai.InternalServerError(message, response=httpx2.Response(status, request=_request_url()), body=None)


def _turn_with_failures(monkeypatch, failures: list[Exception]) -> tuple[list[dict[str, Any]], int, list[float], list[str]]:
    """A turn whose first requests fail with ``failures`` and whose next one answers. Returns the items,
    the number of requests, the waits and the logged lines."""
    from aifred.lib import logging_utils
    from aifred.backends import base

    backend = _offline_vllm(monkeypatch)
    monkeypatch.setattr(backend, "_read_counters", lambda: None)
    sent: list[int] = []
    waits: list[float] = []
    logged: list[str] = []

    async def create(**kwargs: Any):
        sent.append(1)
        if failures:
            raise failures.pop(0)
        return _stream([_chunk({"content": "Antwort"}), _chunk({}, finish_reason="stop")])

    async def no_wait(seconds: float) -> None:
        waits.append(seconds)

    backend.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(base.asyncio, "sleep", no_wait)
    monkeypatch.setattr(logging_utils, "log_message", lambda message, category="info": logged.append(message))

    async def run() -> list[dict[str, Any]]:
        return [item async for item in backend.chat_stream("m", [LLMMessage(role="user", content="Hallo")])]

    return asyncio.run(run()), len(sent), waits, logged


@pytest.mark.parametrize("failure", [_connection_error(), _server_error("Bad Gateway: swapping")])
def test_a_passing_error_is_retried_and_said_in_the_debug_console(monkeypatch, failure: Exception) -> None:
    from aifred.lib.config import STREAM_RETRY_DELAY_S

    items, sent, waits, logged = _turn_with_failures(monkeypatch, [failure])
    assert "Antwort" in "".join(i.get("text", "") for i in items)
    assert (sent, waits) == (2, [STREAM_RETRY_DELAY_S])
    retries = [line for line in logged if "retry in" in line]
    assert len(retries) == 1 and type(failure).__name__ in retries[0]


@pytest.mark.parametrize(
    "failure",
    [
        TypeError("an error in AIfred's own code"),
        KeyError("choices"),
        _server_error("upstream command exited prematurely"),
    ],
)
def test_an_error_a_new_try_cannot_get_past_comes_at_once(monkeypatch, failure: Exception) -> None:
    from aifred.backends.base import BackendError

    with pytest.raises(BackendError):
        _turn_with_failures(monkeypatch, [failure])


def test_no_retry_once_output_went_out(monkeypatch) -> None:
    from aifred.backends.base import BackendError

    backend = _offline_vllm(monkeypatch)
    monkeypatch.setattr(backend, "_read_counters", lambda: None)
    sent: list[int] = []

    async def broken_after_first_piece():
        yield _chunk({"content": "Ant"})
        raise _connection_error()

    async def create(**kwargs: Any):
        sent.append(1)
        return broken_after_first_piece()

    backend.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def run() -> list[dict[str, Any]]:
        return [item async for item in backend.chat_stream("m", [LLMMessage(role="user", content="Hallo")])]

    with pytest.raises(BackendError):
        asyncio.run(run())
    assert sent == [1]
