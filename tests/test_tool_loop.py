"""The tool loop (backends/tool_loop.py) against a fake transport: one loop for every backend.

``FakeBackend`` plays scripted rounds of pieces (what ``stream_round`` of a real backend yields) and keeps
what each request carried. What the loop does with them - tool calls and results, the <think> wrapping,
truncation, the forced final round, metrics, retries - is the same for vLLM, llama.cpp, cloud APIs and
Ollama; each backend's own translation has its own tests (test_openai_backend_stream, test_ollama_round).
"""

import asyncio
import copy
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx
import pytest

# aifred.lib zuerst: aifred.backends allein laeuft in einen Zirkelimport
from aifred.lib.function_calling import Tool
from aifred.lib.perf_metrics import InferenceWork
from aifred.backends import base, tool_loop
from aifred.backends.base import BackendError, BackendInferenceError, LLMBackend, LLMMessage, LLMOptions, MessageContent
from aifred.backends.round_pieces import (
    RoundEnd,
    RoundPiece,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallStarted,
    UiItem,
)

if TYPE_CHECKING:
    from aifred.lib.function_calling import ToolKit
    _ToolKitBase = ToolKit
else:
    _ToolKitBase = object


class FakeToolkit(_ToolKitBase):
    """One tool, ``lookup``; each call answers with its number and keeps its arguments."""

    def __init__(self, sub_agent_work: dict[str, Any] | None = None) -> None:
        self.calls: list[str | dict[str, Any]] = []
        self.sub_agent_work = sub_agent_work
        # Read by the search for tool calls written as text (function_calling.extract_text_tool_calls).
        self._by_name = {"lookup": Tool(name="lookup", description="x", parameters={}, executor=lambda **kw: "ok", tier=2)}

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": "lookup", "description": "Sucht.", "parameters": {}}}]

    @property
    def session_id(self) -> str:
        return ""

    async def execute_streaming(
        self, name: str, arguments: str | dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls.append(arguments)
        if self.sub_agent_work:
            yield {"type": "tool_work", "work": self.sub_agent_work}
        yield {"type": "tool_result", "result": f"Ergebnis {len(self.calls)}"}


class FakeBackend(LLMBackend):
    """A backend whose requests are scripted: round N plays ``rounds[N]`` (an exception in it is raised)."""

    def __init__(self, rounds: list[list[RoundPiece | Exception]], send_turn_reasoning: bool = False) -> None:
        super().__init__()
        self.rounds = rounds
        self.requests: list[dict[str, Any]] = []
        self.SEND_TURN_REASONING = send_turn_reasoning

    async def stream_round(
        self, model: str, messages: list[dict[str, Any]], options: LLMOptions, tools: list[dict[str, Any]] | None,
    ) -> AsyncIterator[RoundPiece]:
        self.requests.append({"messages": copy.deepcopy(messages), "tools": tools})
        for piece in self.rounds[len(self.requests) - 1]:
            if isinstance(piece, Exception):
                raise piece
            yield piece

    def _classify_error(self, error: Exception, model: str) -> BackendError:
        return BackendInferenceError(str(error))

    async def list_models(self) -> list[str]:
        return []

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True

    async def get_model_context_limit(self, model: str) -> tuple[int, int]:
        return (0, 0)

    async def is_model_loaded(self, model: str) -> bool:
        return True

    async def preload_model(self, model: str, num_ctx: int | None = None) -> tuple[bool, float]:
        return (True, 0.0)

    def get_capabilities(self) -> dict[str, bool]:
        return {}


def end(
    calls: list[ToolCall] | None = None, finish: str | None = "stop", prompt: int = 10, completion: int = 5,
    work: InferenceWork | None = None,
) -> RoundEnd:
    return RoundEnd(
        tool_calls=calls or [], finish_reason=finish, prompt_tokens=prompt, completion_tokens=completion,
        work=work or InferenceWork(),
    )


def lookup_call(number: int = 1) -> ToolCall:
    return ToolCall(id=f"call_{number}", name="lookup", arguments=json.dumps({"q": number}))


def play(
    backend: FakeBackend, toolkit: FakeToolkit | None = None, options: LLMOptions | None = None,
) -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        return [item async for item in backend.chat_stream(
            "m", [LLMMessage(role="user", content="Frage")], options, toolkit=toolkit)]
    return asyncio.run(run())


def text_of(items: list[dict[str, Any]]) -> str:
    return "".join(item["text"] for item in items if item["type"] == "content")


def debug_of(items: list[dict[str, Any]]) -> list[str]:
    return [item["message"] for item in items if item["type"] == "debug"]


# ---- A turn --------------------------------------------------------------------------------


def test_a_plain_answer_streams_and_ends_with_done() -> None:
    backend = FakeBackend([[TextDelta("Guten "), TextDelta("Tag"), end(prompt=7, completion=3)]])
    items = play(backend)
    assert [item["type"] for item in items] == ["content", "content", "done"]
    assert text_of(items) == "Guten Tag"
    assert items[-1]["metrics"]["tokens_prompt"] == 7
    assert backend.requests[0]["messages"] == [{"role": "user", "content": "Frage"}]
    assert backend.requests[0]["tools"] is None


def test_a_model_load_before_the_request_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend([[TextDelta("ok"), end()]])

    async def loaded(model: str) -> float:
        return 2.5

    monkeypatch.setattr(backend, "_pre_request_check", loaded)
    assert play(backend)[0] == {"type": "model_load", "seconds": 2.5}


def test_the_tool_results_go_back_to_the_model() -> None:
    backend = FakeBackend([
        [ToolCallStarted("lookup"), end([lookup_call()], finish="tool_calls")],
        [TextDelta("Antwort."), end()],
    ])
    toolkit = FakeToolkit()
    items = play(backend, toolkit)
    assert [item["type"] for item in items] == ["tool_call_start", "tool_call", "tool_result", "content", "done"]
    assert items[1] == {"type": "tool_call", "name": "lookup", "arguments": '{"q": 1}'}
    assert toolkit.calls == ['{"q": 1}']
    first, second = backend.requests
    assert first["tools"] == toolkit.definitions and second["tools"] == toolkit.definitions
    assert second["messages"][1:] == [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"q": 1}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "Ergebnis 1"},
    ]


def test_ui_items_of_the_backend_are_passed_on() -> None:
    backend = FakeBackend([[UiItem({"type": "debug", "message": "vom Backend"}), TextDelta("ok"), end()]])
    assert debug_of(play(backend)) == ["vom Backend"]


# ---- Reasoning ------------------------------------------------------------------------------


def test_reasoning_becomes_a_think_block_before_the_answer() -> None:
    backend = FakeBackend([[ThinkingDelta("Psalm 91 "), ThinkingDelta("passt."), TextDelta("Zuflucht."), end()]])
    assert text_of(play(backend)) == "<think>Psalm 91 passt.</think>\n\nZuflucht."


def test_a_think_block_the_round_ends_in_is_closed() -> None:
    backend = FakeBackend([[ThinkingDelta("nur Denken"), end()]])
    assert text_of(play(backend)) == "<think>nur Denken</think>\n\n"


def test_the_visible_text_and_the_reasoning_go_back_where_the_backend_sends_them() -> None:
    def rounds() -> list[list[RoundPiece | Exception]]:
        return [
            [ThinkingDelta("erst suchen"), TextDelta("Ich suche."), end([lookup_call()], finish="tool_calls")],
            [TextDelta("Fertig."), end()],
        ]
    plain, passing = FakeBackend(rounds()), FakeBackend(rounds(), send_turn_reasoning=True)
    play(plain, FakeToolkit())
    play(passing, FakeToolkit())
    assistant = plain.requests[1]["messages"][1]
    assert assistant["content"] == "Ich suche." and "reasoning_content" not in assistant
    assert passing.requests[1]["messages"][1]["reasoning_content"] == "erst suchen"


# ---- Truncation and tool calls the server did not deliver properly ---------------------------


def test_the_partial_tool_calls_of_a_truncated_round_are_discarded() -> None:
    backend = FakeBackend([
        [TextDelta("Plan"), end([lookup_call()], finish="length")],
        [TextDelta("Antwort."), end()],
    ])
    toolkit = FakeToolkit()
    items = play(backend, toolkit)
    assert toolkit.calls == []
    assert items[-1]["metrics"]["truncated"] is True
    assert any("Response truncated" in message for message in debug_of(items))
    assert "⚠️ Partial tool calls discarded" in debug_of(items)


def test_a_tool_call_written_as_text_is_run_and_not_echoed_back() -> None:
    written = 'Ich suche. <tool_call>{"name": "lookup", "arguments": {"q": 5}}</tool_call>'
    backend = FakeBackend([[TextDelta(written), end()], [TextDelta("Fertig."), end()]])
    toolkit = FakeToolkit()
    items = play(backend, toolkit)
    assert toolkit.calls == ['{"q": 5}']
    assert any("emitted as text" in message for message in debug_of(items))
    assistant = backend.requests[1]["messages"][1]
    assert "tool_call" not in assistant["content"] and assistant["tool_calls"][0]["id"].startswith("text-")


def test_a_swallowed_tool_call_is_reported() -> None:
    backend = FakeBackend([[end(finish="tool_calls")], [TextDelta("Ohne Werkzeug."), end()]])
    items = play(backend, FakeToolkit())
    assert any("delivered no tool call" in message for message in debug_of(items))
    assert backend.requests[1]["tools"] is None  # no content, so the final round comes without tools
    assert text_of(items) == "Ohne Werkzeug."


def test_a_round_without_its_end_marker_is_an_error() -> None:
    backend = FakeBackend([[TextDelta("abgerissen")]])
    with pytest.raises(BackendInferenceError, match="end marker"):
        play(backend)


# ---- Rounds, guard, final round ---------------------------------------------------------------


def test_exhausted_tool_rounds_force_a_final_round_without_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_loop, "MAX_TOOL_ROUNDS", 2)
    backend = FakeBackend([
        [end([lookup_call(1)], finish="tool_calls")],
        [end([lookup_call(2)], finish="tool_calls")],
        [TextDelta("Zusammenfassung."), end()],
    ])
    toolkit = FakeToolkit()
    items = play(backend, toolkit)
    assert len(toolkit.calls) == 2
    assert [request["tools"] is None for request in backend.requests] == [False, False, True]
    assert text_of(items) == "Zusammenfassung."
    # the results the model never saw are in the final request
    assert [m["role"] for m in backend.requests[2]["messages"]] == ["user", "assistant", "tool", "assistant", "tool"]


def test_the_context_guard_stops_the_tools_and_interjections_wait_for_it() -> None:
    backend = FakeBackend([
        [end([lookup_call()], finish="tool_calls", prompt=9_000, completion=100)],
        [TextDelta("Antwort."), end()],
    ])
    asked: list[str] = []

    async def interjections() -> list[MessageContent]:
        asked.append("asked")
        return ["Noch etwas"]

    async def run() -> list[dict[str, Any]]:
        return [item async for item in backend.chat_stream(
            "m", [LLMMessage(role="user", content="Frage")], LLMOptions(num_ctx=10_000),
            toolkit=FakeToolkit(), interjections=interjections)]

    items = asyncio.run(run())
    assert backend.requests[1]["tools"] is None
    assert any("Context guard" in message for message in debug_of(items))
    assert asked == []


def test_the_messages_sent_meanwhile_join_the_next_request() -> None:
    backend = FakeBackend([
        [end([lookup_call()], finish="tool_calls")],
        [TextDelta("Antwort."), end()],
    ])

    async def interjections() -> list[MessageContent]:
        return ["Noch etwas", "Und das"]

    async def run() -> list[dict[str, Any]]:
        return [item async for item in backend.chat_stream(
            "m", [LLMMessage(role="user", content="Frage")], None, toolkit=FakeToolkit(), interjections=interjections)]

    asyncio.run(run())
    assert backend.requests[1]["messages"][-2:] == [
        {"role": "user", "content": "Noch etwas"}, {"role": "user", "content": "Und das"},
    ]


# ---- Metrics ---------------------------------------------------------------------------------


def test_the_work_of_every_request_and_of_a_sub_agent_adds_up() -> None:
    backend = FakeBackend([
        [end([lookup_call()], finish="tool_calls", prompt=100, work=InferenceWork(
            prefill_tokens=100, prefill_s=1.0, decode_tokens=10, decode_s=0.5))],
        [TextDelta("Antwort."), end(prompt=150, work=InferenceWork(
            prefill_tokens=50, prefill_s=0.5, decode_tokens=20, decode_s=1.0))],
    ])
    sub_agent = {"prefill_tokens": 8000, "prefill_s": 9.0, "decode_tokens": 1000, "decode_s": 25.0}
    metrics = play(backend, FakeToolkit(sub_agent))[-1]["metrics"]
    work = InferenceWork.from_dict(metrics["work"])
    assert (work.prefill_tokens, work.decode_tokens) == (100 + 50 + 8000, 10 + 20 + 1000)
    assert metrics["tokens_prompt"] == 150  # the last request's prompt: the context the answer was written with


# ---- Retries: one rule for every backend ------------------------------------------------------


def _retry_turn(
    monkeypatch: pytest.MonkeyPatch, rounds: list[list[RoundPiece | Exception]],
) -> tuple[list[dict[str, Any]], list[float]]:
    waits: list[float] = []

    async def no_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(base.asyncio, "sleep", no_wait)
    monkeypatch.setattr("aifred.lib.logging_utils.log_message", lambda message, category="info": None)
    return play(FakeBackend(rounds)), waits


def test_an_error_before_any_output_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib.config import STREAM_RETRY_DELAY_S

    items, waits = _retry_turn(monkeypatch, [[httpx.ConnectError("refused")], [TextDelta("ok"), end()]])
    assert text_of(items) == "ok" and waits == [STREAM_RETRY_DELAY_S]


def test_an_error_after_output_is_not_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(BackendInferenceError):
        _retry_turn(monkeypatch, [[TextDelta("halb"), httpx.ConnectError("cut")], [TextDelta("ok"), end()]])


def test_a_rejected_request_comes_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "http://x/api/chat")
    rejected = httpx.HTTPStatusError("bad", request=request, response=httpx.Response(400, request=request))
    with pytest.raises(BackendInferenceError):
        _retry_turn(monkeypatch, [[rejected], [TextDelta("ok"), end()]])
