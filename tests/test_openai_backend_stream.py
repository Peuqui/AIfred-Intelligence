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
from typing import Any, Dict, List

import pytest
from openai.types.chat import ChatCompletionChunk

# aifred.lib zuerst: aifred.backends allein laeuft in einen Zirkelimport
from aifred.lib.function_calling import Tool, ToolKit
from aifred.backends.base import LLMMessage
from aifred.backends.llamacpp import LlamaCppBackend
from aifred.backends.vllm import vLLMBackend


def _chunk(delta: Dict[str, Any], finish_reason: str | None = None) -> ChatCompletionChunk:
    return ChatCompletionChunk.model_validate({
        "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    })


async def _stream(chunks: List[ChatCompletionChunk]):
    for chunk in chunks:
        yield chunk


def _consume(backend: Any, chunks: List[ChatCompletionChunk]) -> tuple[str, Dict[str, Any]]:
    state: Dict[str, Any] = {}
    counters: Dict[str, Any] = {"prompt_tokens": 0, "total_tokens": 0,
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


def test_swallowed_tool_call_is_reported(monkeypatch) -> None:
    """Round 1 as vLLM with a mismatched parser answers: finish_reason
    tool_calls, no call, no text. The loop must say so before it forces the
    final round without tools."""
    backend = vLLMBackend()
    monkeypatch.setattr(backend, "_build_stream_metrics", lambda *a, **k: {})
    rounds = [
        [_chunk({"content": ""}), _chunk({}, finish_reason="tool_calls")],
        [_chunk({"content": "Ohne Werkzeug."}), _chunk({}, finish_reason="stop")],
    ]
    requests: List[Dict[str, Any]] = []

    async def create(**kwargs: Any):
        requests.append(kwargs)
        return _stream(rounds.pop(0))

    backend.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def run() -> List[Dict[str, Any]]:
        return [item async for item in backend.chat_stream(
            "m", [LLMMessage(role="user", content="Psalm 91?")], toolkit=_toolkit())]

    items = asyncio.run(run())
    debug = [i["message"] for i in items if i.get("type") == "debug"]
    assert any("delivered no tool call" in m for m in debug)
    # Forced final round without tools still produces the answer
    assert requests[1]["tools"] is None
    assert "Ohne Werkzeug." in "".join(i.get("text", "") for i in items)


def _tool_round_turn(monkeypatch, counter_states: list, toolkit: ToolKit | None = None) -> Dict[str, Any]:
    """A two-round vLLM turn (tool call, then answer) against fake counters;
    returns the done metrics. ``counter_states`` are the successive
    /metrics readings, one before and one after each request:
    (port, (prefill_tok, prefill_s, requests, gen_tok, decode_s))."""
    backend = vLLMBackend()
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

    async def run() -> Dict[str, Any]:
        done: Dict[str, Any] = {}
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
    assert metrics["tokens_prompt_computed"] == 32000
    assert metrics["prompt_per_second"] == pytest.approx(32000 / 60.6)
    assert metrics["tokens_generated"] == 476
    assert metrics["tokens_per_second"] == pytest.approx(476 / 14.6)


def test_vllm_foreign_request_in_the_window_makes_prefill_unknown(monkeypatch) -> None:
    # A second request finished while round 2 ran: not attributable, and the
    # server reported no cache hits to measure from outside, so no guess.
    metrics = _tool_round_turn(monkeypatch, [
        ROUND_1_BEFORE, ROUND_1_AFTER, ROUND_1_AFTER, (5811, (32500.0, 61.5, 3.0, 500.0, 15.5)),
    ])
    assert metrics["prompt_per_second"] is None


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
    assert metrics["tokens_prompt_computed"] == 32000 + 8000
    assert metrics["prompt_per_second"] == pytest.approx(40000 / 70.0)
    assert metrics["tokens_generated"] == 476 + 1024
    assert metrics["tokens_per_second"] == pytest.approx(1500 / 40.0)


def test_thinking_of_every_round_is_summed(monkeypatch) -> None:
    """The model thinks before the tool call and again before the answer;
    both blocks count, not only the first."""
    backend = vLLMBackend()
    monkeypatch.setattr(backend, "_read_counters", lambda: None)
    call = {"index": 0, "id": "c1", "type": "function",
            "function": {"name": "search_bible", "arguments": '{"query": "Psalm 91"}'}}

    async def slow(chunks: List[ChatCompletionChunk]):
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

    async def run() -> Dict[str, Any]:
        done: Dict[str, Any] = {}
        async for item in backend.chat_stream(
                "m", [LLMMessage(role="user", content="Psalm 91?")], toolkit=_toolkit()):
            if item.get("type") == "done":
                done = item["metrics"]
        return done

    thinking_s = asyncio.run(run())["work"]["thinking_s"]
    # Round 1 thinks from its first chunk to the stream end (~0.15 s), round 2
    # until its answer starts (~0.10 s). The first block alone would be ~0.15 s.
    assert thinking_s >= 0.22
