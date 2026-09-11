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


def _tool_round_turn(monkeypatch, counter_states: list) -> Dict[str, Any]:
    """A two-round vLLM turn (tool call, then answer) against fake counters;
    returns the done metrics. ``counter_states`` are the successive
    /metrics readings: (port, (prefill_tok, prefill_s, requests, gen_tok, decode_s))."""
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
                "m", [LLMMessage(role="user", content="Psalm 91?")], toolkit=_toolkit()):
            if item.get("type") == "done":
                done = item["metrics"]
        return done

    return asyncio.run(run())


def test_vllm_rates_describe_the_last_request_of_a_tool_turn(monkeypatch) -> None:
    # Reads: before round 1, before round 2, after round 2 (done)
    metrics = _tool_round_turn(monkeypatch, [
        (5811, (0.0, 0.0, 0.0, 0.0, 0.0)),
        (5811, (30700.0, 58.0, 1.0, 60.0, 2.0)),
        (5811, (32000.0, 60.6, 2.0, 476.0, 14.6)),
    ])
    assert metrics["tokens_prompt_computed"] == 1300
    assert metrics["prompt_per_second"] == pytest.approx(1300 / 2.6)
    assert metrics["tokens_per_second"] == pytest.approx(416 / 12.6)


def test_vllm_foreign_request_in_the_window_yields_no_rate(monkeypatch) -> None:
    # A second request finished while ours ran: not attributable, no guess
    metrics = _tool_round_turn(monkeypatch, [
        (5811, (0.0, 0.0, 0.0, 0.0, 0.0)),
        (5811, (30700.0, 58.0, 1.0, 60.0, 2.0)),
        (5811, (32500.0, 61.5, 3.0, 500.0, 15.5)),
    ])
    assert "prompt_per_second" not in metrics
