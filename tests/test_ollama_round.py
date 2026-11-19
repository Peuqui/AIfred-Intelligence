"""Ollama as a transport of the tool loop: one /api/chat request in, pieces out.

Ollama is a fake behind httpx.MockTransport; the lines are the documented NDJSON format of /api/chat
(streamed ``message.content`` / ``message.thinking`` / ``message.tool_calls``, the counters and
``done_reason`` in the final chunk). Tool calls come complete in one chunk, arguments as an object, without
an id. The tool loop over them is tested in test_tool_loop.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from aifred.lib.function_calling import Tool
from aifred.lib.perf_metrics import InferenceWork  # aifred.lib zuerst (Zirkelimport)
from aifred.backends.base import BackendInferenceError, LLMMessage, LLMOptions
from aifred.backends.ollama import OllamaBackend
from aifred.backends.round_pieces import RoundEnd, TextDelta, ThinkingDelta, ToolCallStarted

if TYPE_CHECKING:
    from aifred.lib.function_calling import ToolKit
    _ToolKitBase = ToolKit
else:
    _ToolKitBase = object


def ndjson(*lines: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content="\n".join(json.dumps(line) for line in lines).encode())


def chunk(message: dict[str, Any], **final: Any) -> dict[str, Any]:
    return {"model": "qwen3:8b", "created_at": "2026-11-19T10:00:00Z",
            "message": {"role": "assistant", "content": "", **message}, "done": bool(final), **final}


FINAL = {"done_reason": "stop", "total_duration": 4_000_000_000, "load_duration": 500_000_000,
         "prompt_eval_count": 26, "prompt_eval_duration": 300_000_000,
         "eval_count": 12, "eval_duration": 2_000_000_000}

TEXT_AND_THINKING = ndjson(
    chunk({"thinking": "Ich "}),
    chunk({"thinking": "suche."}),
    chunk({"content": "Hallo"}),
    chunk({}, **FINAL),
)

LOOKUP_CALL = {"function": {"name": "lookup", "arguments": {"q": "Psalm 91"}}}
TOOL_ROUND = ndjson(
    chunk({"tool_calls": [LOOKUP_CALL]}),
    chunk({}, done_reason="stop", prompt_eval_count=120, prompt_eval_duration=400_000_000,
          eval_count=30, eval_duration=1_500_000_000),
)


def backend_with(
    monkeypatch: pytest.MonkeyPatch, answers: list[httpx.Response],
) -> tuple[OllamaBackend, list[dict[str, Any]], list[str]]:
    """The backend, the payloads it sent, and the models its hybrid check ran for."""
    sent: list[dict[str, Any]] = []
    unloads: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return answers.pop(0)

    async def count_unload(model: str) -> None:
        unloads.append(model)

    backend = OllamaBackend("http://ollama.test")
    backend.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(backend, "_handle_hybrid_model_unload", count_unload)
    return backend, sent, unloads


def round_pieces(
    backend: OllamaBackend, messages: list[dict[str, Any]], options: LLMOptions | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> list[Any]:
    async def run() -> list[Any]:
        return [piece async for piece in backend.stream_round("qwen3:8b", messages, options or LLMOptions(), tools)]
    return asyncio.run(run())


QUESTION: list[dict[str, Any]] = [{"role": "user", "content": "Frage"}]
TOOLS = [{"type": "function", "function": {"name": "lookup", "description": "Sucht.", "parameters": {}}}]


# ---- Stream parsing ----------------------------------------------------------------------------


def test_thinking_and_content_become_pieces_and_the_final_chunk_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _, _ = backend_with(monkeypatch, [TEXT_AND_THINKING])
    *pieces, last = round_pieces(backend, QUESTION)
    assert pieces == [ThinkingDelta("Ich "), ThinkingDelta("suche."), TextDelta("Hallo")]
    assert isinstance(last, RoundEnd)
    assert (last.finish_reason, last.prompt_tokens, last.completion_tokens, last.tool_calls) == ("stop", 26, 12, [])
    assert last.work == InferenceWork(prefill_tokens=26, prefill_s=0.3, decode_tokens=12, decode_s=2.0)


def test_a_tool_call_comes_complete_with_an_id_made_here(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _, _ = backend_with(monkeypatch, [TOOL_ROUND])
    started, last = round_pieces(backend, QUESTION, tools=TOOLS)
    assert started == ToolCallStarted("lookup")
    (call,) = last.tool_calls
    assert (call.name, call.arguments) == ("lookup", '{"q": "Psalm 91"}') and call.id.startswith("ollama-")


def test_an_id_never_repeats_one_a_compressed_loop_still_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    # After the loop compression summarized the first rounds, only a later one is kept: its id must not come again,
    # or the result of the new call would be matched to the kept call's tool name.
    backend, _, _ = backend_with(monkeypatch, [TOOL_ROUND, TOOL_ROUND])
    kept_round: list[dict[str, Any]] = [
        {"role": "user", "content": "[summary of the earlier rounds]"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "ollama-1-0", "type": "function", "function": {"name": "search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "ollama-1-0", "content": "Ergebnis"},
    ]
    *_, first = round_pieces(backend, QUESTION + kept_round, tools=TOOLS)
    *_, second = round_pieces(backend, QUESTION + kept_round, tools=TOOLS)
    ids = {first.tool_calls[0].id, second.tool_calls[0].id}
    assert "ollama-1-0" not in ids and len(ids) == 2


def test_a_round_cut_at_the_limit_says_length(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _, _ = backend_with(monkeypatch, [ndjson(chunk({}, done_reason="length", prompt_eval_count=4096, eval_count=512))])
    (last,) = round_pieces(backend, QUESTION)
    assert last.finish_reason == "length"


def test_a_line_that_is_not_json_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = httpx.Response(200, content=b'not json\n' + json.dumps(chunk({}, **FINAL)).encode())
    backend, _, _ = backend_with(monkeypatch, [answer])
    (last,) = round_pieces(backend, QUESTION)
    assert isinstance(last, RoundEnd)


def test_a_stream_without_its_final_chunk_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _, _ = backend_with(monkeypatch, [ndjson(chunk({"content": "abgerissen"}))])
    with pytest.raises(BackendInferenceError, match="final chunk"):
        round_pieces(backend, QUESTION)


# ---- The request -------------------------------------------------------------------------------


def test_the_request_carries_tools_context_and_think(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, sent, _ = backend_with(monkeypatch, [TEXT_AND_THINKING, TEXT_AND_THINKING])
    options = LLMOptions(num_ctx=32768, enable_thinking=True)
    round_pieces(backend, QUESTION, options, TOOLS)
    round_pieces(backend, QUESTION, options, None)
    with_tools, without_tools = sent
    assert with_tools["tools"] == TOOLS and "tools" not in without_tools
    assert with_tools["options"]["num_ctx"] == 32768 and with_tools["think"] is True
    assert with_tools["stream"] is True and "keep_alive" not in with_tools
    assert with_tools["messages"] == [{"role": "user", "content": "Frage"}]


def test_the_loop_messages_are_translated_to_ollamas_format(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": [
            {"type": "text", "text": "Was ist das?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
        ]},
        {"role": "assistant", "content": None, "reasoning_content": "erst suchen", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": '{"q": "Psalm 91"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "Ergebnis"},
    ]
    backend, sent, _ = backend_with(monkeypatch, [TEXT_AND_THINKING])
    round_pieces(backend, messages, tools=TOOLS)
    assert sent[0]["messages"] == [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Was ist das?", "images": ["QUJD"]},
        {"role": "assistant", "content": "", "thinking": "erst suchen",
         "tool_calls": [{"function": {"name": "lookup", "arguments": {"q": "Psalm 91"}}}]},
        {"role": "tool", "content": "Ergebnis", "tool_name": "lookup"},
    ]


def test_a_result_that_answers_no_call_is_an_error() -> None:
    with pytest.raises(KeyError):
        OllamaBackend._convert_messages([{"role": "tool", "tool_call_id": "nirgends", "content": "x"}])


def test_arguments_that_are_not_json_are_an_error() -> None:
    call = {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": "{kaputt"}}
    with pytest.raises(ValueError):
        OllamaBackend._convert_messages([{"role": "assistant", "content": None, "tool_calls": [call]}])


# ---- A whole turn over the shared loop -------------------------------------------------------------


class LookupToolkit(_ToolKitBase):
    def __init__(self) -> None:
        self.calls: list[str | dict[str, Any]] = []
        self._by_name = {"lookup": Tool(name="lookup", description="x", parameters={}, executor=lambda **kw: "ok", tier=2)}

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return TOOLS

    @property
    def session_id(self) -> str:
        return ""

    async def execute_streaming(
        self, name: str, arguments: str | dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls.append(arguments)
        yield {"type": "tool_result", "result": "Der Herr ist mein Hirte."}


def test_a_tool_turn_over_ollama_runs_the_tool_and_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the tool loop was shared, a streamed Ollama chat with a toolkit raised a TypeError."""
    backend, sent, unloads = backend_with(monkeypatch, [TOOL_ROUND, ndjson(
        chunk({"content": "Psalm 23."}), chunk({}, **FINAL))])
    toolkit = LookupToolkit()

    async def run() -> list[dict[str, Any]]:
        return [item async for item in backend.chat_stream(
            "qwen3:8b", [LLMMessage(role="user", content="Psalm?")], LLMOptions(num_ctx=8192), toolkit=toolkit)]

    items = asyncio.run(run())
    assert [item["type"] for item in items] == ["tool_call_start", "tool_call", "tool_result", "content", "done"]
    assert toolkit.calls == ['{"q": "Psalm 91"}']
    assert sent[1]["messages"][1:] == [
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "lookup", "arguments": {"q": "Psalm 91"}}}]},
        {"role": "tool", "content": "Der Herr ist mein Hirte.", "tool_name": "lookup"},
    ]
    done = items[-1]["metrics"]
    assert InferenceWork.from_dict(done["work"]).prefill_tokens == 120 + 26  # both requests
    assert unloads == ["qwen3:8b"]  # the hybrid check once per turn, not once per round
