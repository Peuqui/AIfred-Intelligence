"""Compression inside a turn: the tool loop summarizes when its messages reach HISTORY_COMPRESSION_TRIGGER and goes
on with its tools; only what stays over the trigger ends the tool phase (no tools, the context_guard note).

The summary itself is one function for both places (context_manager.summarize_messages). The model calls are
stand-ins: a summarizing client, and an OpenAI client that plays the server's stream round by round.
"""
import asyncio
import copy
import json
from collections.abc import AsyncGenerator, AsyncIterator, Coroutine
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from aifred.backends.base import LLMMessage, LLMOptions, LLMResponse
from aifred.backends.cloud_api import CloudAPIBackend
from aifred.lib.config import HISTORY_COMPRESSION_TARGET, HISTORY_COMPRESSION_TRIGGER, TOOL_LOOP_KEPT_ROUNDS
from aifred.lib.context_manager import LoopCompression, compress_tool_loop, estimate_tokens, summarize_messages
from aifred.lib.prompt_loader import load_prompt

if TYPE_CHECKING:
    from aifred.lib.function_calling import ToolKit
    _ToolKitBase = ToolKit
else:
    _ToolKitBase = object

NUM_CTX = 10_000
SUMMARY = "Früher ging es um das Wetter in Bonn; die Suche fand Regen für Montag."


class SummaryClient:
    """Answers every summary request with ``text`` and keeps the prompts it got."""

    def __init__(self, text: str = SUMMARY) -> None:
        self.text = text
        self.prompts: list[str] = []

    async def chat(self, model: str, messages: list[LLMMessage], options: LLMOptions) -> LLMResponse:
        content = messages[0].content
        assert isinstance(content, str)
        self.prompts.append(content)
        return LLMResponse(text=self.text, tokens_generated=20, tokens_per_second=10.0)


def _compression(client: SummaryClient) -> LoopCompression:
    return LoopCompression(client, "summary-model", "de")


def _round(number: int, result: str = "") -> list[dict[str, Any]]:
    call_id = f"call_{number}"
    return [
        {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": "lookup", "arguments": json.dumps({"q": f"Frage {number}"})}}],
        },
        {"role": "tool", "tool_call_id": call_id, "content": result or f"Ergebnis {number}"},
    ]


def _run[T](work: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(work)


async def _collect(events: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event async for event in events]


# ── The summary of a list of messages ────────────────────────────────────


def test_the_summary_is_the_text_of_the_client_without_its_thinking() -> None:
    client = SummaryClient(f"<think>Erst lesen.</think>{SUMMARY}")
    messages = [{"role": "user", "content": "Wie wird das Wetter?"}, {"role": "assistant", "content": "Regen."}]
    events = _run(_collect(summarize_messages(messages, client, "summary-model", NUM_CTX, "de")))
    assert events[-1] == {"type": "summary", "text": SUMMARY, "thinking": "Erst lesen."}
    assert all(event["type"] == "debug" for event in events[:-1])
    (prompt,) = client.prompts
    assert "User: Wie wird das Wetter?" in prompt and "Assistant: Regen." in prompt


def test_a_summary_too_short_counts_as_failed() -> None:
    events = _run(_collect(summarize_messages([{"role": "user", "content": "Hallo"}], SummaryClient("ok"), "m", NUM_CTX, "de")))
    assert events[-1]["text"] == ""


# ── What the loop compresses ─────────────────────────────────────────────


def _pairs_intact(messages: list[dict[str, Any]]) -> bool:
    """Every tool result answers a call of the assistant message right before its run of results."""
    open_calls: set[str] = set()
    for message in messages:
        if message["role"] == "assistant" and message.get("tool_calls"):
            open_calls = {call["id"] for call in message["tool_calls"]}
        elif message["role"] == "tool":
            if message["tool_call_id"] not in open_calls:
                return False
        else:
            open_calls = set()
    return True


def test_system_question_and_the_last_rounds_stay_older_ones_become_one_summary() -> None:
    system = {"role": "system", "content": "Du bist AIfred."}
    earlier = [{"role": "user", "content": "[USER]: Wetter gestern?"}, {"role": "assistant", "content": "Sonnig."}]
    question = {"role": "user", "content": "[USER]: Und morgen in Bonn?"}
    rounds = [_round(number) for number in range(1, 5)]
    messages = [system, *earlier, question, *[message for round_messages in rounds for message in round_messages]]
    original = copy.deepcopy(messages)
    client = SummaryClient()

    # Far over the target: everything but the kept rounds has to go.
    events = _run(_collect(compress_tool_loop(messages, NUM_CTX, NUM_CTX, _compression(client))))

    compressed = events[-1]
    assert compressed["type"] == "loop_compressed"
    kept = [message for round_messages in rounds[-TOOL_LOOP_KEPT_ROUNDS:] for message in round_messages]
    summary = compressed["messages"][1]
    assert compressed["messages"] == [system, summary, question, *kept]
    assert summary == {"role": "user", "content": f"[CONVERSATION CONTEXT]:\n[Summary]\n{SUMMARY}"}
    assert _pairs_intact(compressed["messages"])
    assert compressed["used_tokens"] < NUM_CTX
    # The summary saw the earlier conversation and the older rounds, calls and results, not the kept ones.
    (prompt,) = client.prompts
    assert "Sonnig." in prompt and "Ergebnis 1" in prompt and "Ergebnis 2" in prompt and "lookup(" in prompt
    assert "Ergebnis 3" not in prompt and "Ergebnis 4" not in prompt
    assert {"type": "progress", "phase": "compress"} in events and {"type": "progress", "phase": ""} in events
    assert messages == original     # the loop gets new messages; the list it handed in stays


def test_rounds_are_compressed_only_when_the_earlier_conversation_is_not_enough() -> None:
    earlier = [{"role": "user", "content": "[USER]: " + "Wortfüllung " * 400}]
    messages = [{"role": "system", "content": "System"}, *earlier, {"role": "user", "content": "Frage"},
                *_round(1), *_round(2), *_round(3)]
    target = int(NUM_CTX * HISTORY_COMPRESSION_TARGET)
    used = target + estimate_tokens(earlier)   # the earlier conversation alone reaches the target
    client = SummaryClient()
    events = _run(_collect(compress_tool_loop(messages, used, NUM_CTX, _compression(client))))
    assert events[-1]["messages"][2:] == messages[2:]     # question and all three rounds as they were
    assert "Ergebnis 1" not in client.prompts[0]


def test_nothing_to_compress_gives_no_new_messages() -> None:
    messages = [{"role": "system", "content": "System"}, {"role": "user", "content": "Frage"}, *_round(1), *_round(2)]
    client = SummaryClient()
    events = _run(_collect(compress_tool_loop(messages, NUM_CTX, NUM_CTX, _compression(client))))
    assert all(event["type"] != "loop_compressed" for event in events)
    assert client.prompts == []


def test_a_failed_summary_gives_no_new_messages() -> None:
    messages = [{"role": "system", "content": "System"}, {"role": "user", "content": "alt"},
                {"role": "user", "content": "Frage"}, *_round(1)]
    events = _run(_collect(compress_tool_loop(messages, NUM_CTX, NUM_CTX, _compression(SummaryClient("")))))
    assert all(event["type"] != "loop_compressed" for event in events)


# ── The loop of the backend ──────────────────────────────────────────────


class FakeToolkit(_ToolKitBase):
    """One tool, ``lookup``; each call answers with its number."""

    def __init__(self) -> None:
        self.calls = 0
        # Read by the search for tool calls written as text (function_calling.extract_text_tool_calls).
        self._by_name = {}

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": "lookup", "description": "Sucht.", "parameters": {}}}]

    @property
    def session_id(self) -> str:
        return ""

    async def execute_streaming(
        self, name: str, arguments: str | dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls += 1
        yield {"type": "tool_result", "result": f"Ergebnis {self.calls}"}


def _chunk(delta: Any = None, finish: str | None = None, usage: tuple[int, int] | None = None) -> SimpleNamespace:
    choices = [SimpleNamespace(delta=delta, finish_reason=finish)] if delta is not None else []
    counted = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1]) if usage else None
    return SimpleNamespace(choices=choices, usage=counted)


def _tool_call_round(number: int, prompt_tokens: int) -> list[SimpleNamespace]:
    call = SimpleNamespace(index=0, id=f"call_{number}",
                           function=SimpleNamespace(name="lookup", arguments=json.dumps({"q": number})))
    return [_chunk(SimpleNamespace(content=None, tool_calls=[call]), "tool_calls"), _chunk(usage=(prompt_tokens, 10))]


def _answer_round() -> list[SimpleNamespace]:
    return [_chunk(SimpleNamespace(content="Antwort.", tool_calls=None), "stop"), _chunk(usage=(100, 2))]


class FakeServer:
    """The OpenAI client of the backend: plays one scripted stream per request, keeps what each request sent."""

    def __init__(self, rounds: list[list[SimpleNamespace]]) -> None:
        self.rounds = rounds
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        self.requests.append({"messages": copy.deepcopy(kwargs["messages"]), "tools": kwargs.get("tools")})
        chunks = self.rounds[len(self.requests) - 1]

        async def stream() -> AsyncIterator[SimpleNamespace]:
            for chunk in chunks:
                yield chunk
        return stream()


def _loop(monkeypatch: pytest.MonkeyPatch, server: FakeServer, messages: list[LLMMessage],
          client: SummaryClient) -> list[dict[str, Any]]:
    backend = CloudAPIBackend(base_url="http://127.0.0.1:9/v1", api_key="x")
    monkeypatch.setattr(backend, "client", server)

    async def run() -> list[dict[str, Any]]:
        return [event async for event in backend.chat_stream(
            "agent-model", messages, LLMOptions(num_ctx=NUM_CTX), toolkit=FakeToolkit(),
            compression=_compression(client),
        )]
    return _run(run())


def _over_trigger() -> int:
    return int(NUM_CTX * HISTORY_COMPRESSION_TRIGGER) + 100


def test_below_the_trigger_nothing_happens(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeServer([_tool_call_round(1, 1000), _answer_round()])
    client = SummaryClient()
    messages = [LLMMessage("system", "System"), LLMMessage("user", "alt"), LLMMessage("user", "Frage")]
    _loop(monkeypatch, server, messages, client)
    first, second = server.requests
    assert second["tools"] is not None
    assert second["messages"][:3] == first["messages"]
    assert [message["role"] for message in second["messages"][3:]] == ["assistant", "tool"]
    assert client.prompts == []


def test_over_the_trigger_the_loop_compresses_and_keeps_its_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    earlier = "[USER]: " + "Wortfüllung " * 1500
    server = FakeServer([_tool_call_round(1, _over_trigger()), _answer_round()])
    client = SummaryClient()
    messages = [LLMMessage("system", "System"), LLMMessage("user", earlier), LLMMessage("user", "Frage")]
    events = _loop(monkeypatch, server, messages, client)
    second = server.requests[1]
    assert second["tools"] is not None
    assert [message["content"] for message in second["messages"][:3]] == [
        "System", f"[CONVERSATION CONTEXT]:\n[Summary]\n{SUMMARY}", "Frage",
    ]
    assert [message["role"] for message in second["messages"][3:]] == ["assistant", "tool"]
    assert len(client.prompts) == 1
    assert {"type": "progress", "phase": "compress"} in events
    assert not any("Context guard" in event.get("message", "") for event in events)
    assert messages[1].content == earlier     # the caller's messages (and the session) stay as they were


def test_with_nothing_left_to_compress_the_tool_phase_ends_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeServer([_tool_call_round(1, _over_trigger()), _answer_round()])
    client = SummaryClient()
    events = _loop(monkeypatch, server, [LLMMessage("system", "System"), LLMMessage("user", "Frage")], client)
    second = server.requests[1]
    assert second["tools"] is None
    assert second["messages"][-1] == {"role": "user", "content": load_prompt("utility/context_guard")}
    assert client.prompts == []
    assert any("Context guard" in event.get("message", "") for event in events)


def test_still_over_the_trigger_after_compressing_the_tool_phase_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeServer([_tool_call_round(1, NUM_CTX), _answer_round()])
    client = SummaryClient()
    messages = [LLMMessage("system", "System"), LLMMessage("user", "kurz"), LLMMessage("user", "Frage")]
    _loop(monkeypatch, server, messages, client)
    second = server.requests[1]
    assert len(client.prompts) == 1
    assert second["tools"] is None
    assert second["messages"][1]["content"].startswith("[CONVERSATION CONTEXT]:\n[Summary]")
    assert second["messages"][-1]["content"] == load_prompt("utility/context_guard")
