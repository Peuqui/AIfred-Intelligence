"""Tool rounds as native llm_history entries (history_entries): the readers of the history keep a round
and its results together, the model sees its own rounds as tool calls and other agents' rounds as text."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agent_fixtures import raw_test_agents
from aifred.lib import agent_config, context_manager, message_builder
from aifred.lib.history_entries import (
    history_units,
    is_round,
    is_tool_result,
    new_call_id,
    round_entry,
    tool_result_entry,
)
from aifred.lib.message_builder import build_messages_from_llm_history
from aifred.lib.session_title import first_exchange


def _call(call_id: str, name: str = "web_search", arguments: str = '{"q": "Bonn"}') -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _round_with_results(agent: str, text: str, number: int) -> list[dict[str, Any]]:
    first, second = f"call_{number}a", f"call_{number}b"
    return [
        round_entry(agent, text, [_call(first), _call(second, "web_fetch", '{"url": "x"}')]),
        tool_result_entry(first, f"Ergebnis {number}a"),
        tool_result_entry(second, f"Ergebnis {number}b"),
    ]


@pytest.fixture(autouse=True)
def agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps({"agents": raw_test_agents()}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    monkeypatch.setattr(message_builder, "get_user_name", lambda: "Peuqui")


# ── The entries ──────────────────────────────────────────────────────────


def test_the_entries_have_the_format() -> None:
    call = _call("call_1")
    assert round_entry("aifred", "Ich suche.", [call]) == {
        "role": "assistant", "content": "[AIFRED]: Ich suche.", "tool_calls": [call],
    }
    assert tool_result_entry("call_1", "3 Quellen") == {"role": "tool", "tool_call_id": "call_1", "content": "3 Quellen"}
    assert is_round(round_entry("aifred", "", [call])) and not is_round({"role": "assistant", "content": "[AIFRED]: x"})
    assert is_tool_result(tool_result_entry("call_1", "")) and not is_tool_result({"role": "user", "content": "x"})
    assert new_call_id().startswith("call_") and new_call_id() != new_call_id()


def test_units_hold_a_round_with_its_results_and_every_other_entry_alone() -> None:
    question = {"role": "user", "content": "Frage"}
    reply = {"role": "assistant", "content": "[AIFRED]: Antwort"}
    first, second = _round_with_results("aifred", "a", 1), _round_with_results("aifred", "b", 2)
    assert history_units([question, *first, {"role": "user", "content": "Einwurf"}, *second, reply]) == [
        [question], first, [{"role": "user", "content": "Einwurf"}], second, [reply],
    ]


def test_a_result_without_its_round_is_an_error() -> None:
    with pytest.raises(ValueError):
        history_units([{"role": "user", "content": "Frage"}, tool_result_entry("call_1", "x")])
    with pytest.raises(ValueError):
        history_units([tool_result_entry("call_1", "x")])
    with pytest.raises(ValueError):    # the result follows another round's call
        history_units([round_entry("aifred", "", [_call("call_1")]), tool_result_entry("call_9", "x")])
    with pytest.raises(ValueError):    # an entry between the round and its result
        history_units([round_entry("aifred", "", [_call("call_1")]), {"role": "user", "content": "x"},
                       tool_result_entry("call_1", "x")])


# ── The messages the model gets ──────────────────────────────────────────


def _history() -> list[dict[str, Any]]:
    return [{"role": "user", "content": "Wetter in Bonn?"}, *_round_with_results("aifred", "Ich suche.", 1),
            {"role": "assistant", "content": "[AIFRED]: Regen."}]


def test_an_agent_sees_its_own_round_as_tool_calls() -> None:
    messages = build_messages_from_llm_history(_history(), perspective="aifred", detected_language="de")
    assert messages[1] == {
        "role": "assistant", "content": "Ich suche.",
        "tool_calls": [_call("call_1a"), _call("call_1b", "web_fetch", '{"url": "x"}')],
    }
    assert messages[2:4] == [tool_result_entry("call_1a", "Ergebnis 1a"), tool_result_entry("call_1b", "Ergebnis 1b")]
    assert messages[4] == {"role": "assistant", "content": "Regen."}
    assert len(messages) == 5


def test_another_agent_sees_the_round_as_one_message_with_its_tools_and_results() -> None:
    messages = build_messages_from_llm_history(_history(), perspective="sokrates", detected_language="de")
    assert messages[1] == {"role": "user", "content": (
        "[AIfred]: Ich suche.\n"
        '(ausgeführte Werkzeuge: web_search({"q": "Bonn"}) → Ergebnis 1a; web_fetch({"url": "x"}) → Ergebnis 1b)'
    )}
    assert [m["role"] for m in messages] == ["user", "user", "user"]


def test_the_observer_sees_even_its_own_round_as_text_and_an_empty_text_leaves_the_line_alone() -> None:
    history = [round_entry("aifred", "", [_call("call_1")]), tool_result_entry("call_1", "ok")]
    messages = build_messages_from_llm_history(history, perspective="aifred", observer=True, detected_language="en")
    assert messages == [{"role": "user", "content": '[AIfred]: (tools run: web_search({"q": "Bonn"}) → ok)'}]


# ── Compression before the message ───────────────────────────────────────


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens_generated = 20
        self.tokens_per_second = 10.0


class _Client:
    async def chat(self, model: str, messages: Any, options: Any) -> _Response:
        return _Response("Zusammenfassung des bisherigen Gesprächs. " * 30)


def test_the_fifo_never_cuts_a_round_from_its_results() -> None:
    big_call = _call("call_1", arguments=json.dumps({"q": "Wortfüllung " * 400}))
    history = [
        round_entry("aifred", "", [big_call]), tool_result_entry("call_1", "kurz"),
        {"role": "user", "content": "Frage"}, {"role": "assistant", "content": "[AIFRED]: Antwort"},
    ]
    total = context_manager.estimate_tokens(history)
    without_round = context_manager.estimate_tokens(history[1:])
    limit = int(total / 0.9)
    # The target lies where dropping the round alone would be enough: the old one-message FIFO cut exactly there.
    assert without_round <= int(limit * 0.3)

    async def run() -> list[dict[str, Any]]:
        events = [event async for event in context_manager.summarize_history_if_needed(
            history=list(history), llm_client=_Client(), model_name="m", context_limit=limit,
            llm_history=list(history), system_prompt_tokens=0, detected_language="de",
        )]
        return [event for event in events if event["type"] == "history_update"]

    (update,) = asyncio.run(run())
    kept = update["llm_history"][1:]    # behind the summary
    assert not any(is_tool_result(entry) for entry in kept)
    assert [entry["content"] for entry in kept] == ["Frage", "[AIFRED]: Antwort"]


def test_the_summary_text_of_a_round_and_a_result() -> None:
    class Recording(_Client):
        prompt = ""

        async def chat(self, model: str, messages: Any, options: Any) -> _Response:
            Recording.prompt = messages[0].content
            return await super().chat(model, messages, options)

    async def run() -> None:
        async for _ in context_manager.summarize_messages(
            [round_entry("aifred", "", [_call("call_1")]), tool_result_entry("call_1", "ok")], Recording(), "m", 10_000, "de",
        ):
            pass

    asyncio.run(run())
    assert "Tool: ok" in Recording.prompt and "web_search(" in Recording.prompt


# ── Session title ────────────────────────────────────────────────────────


def test_the_title_takes_the_first_reply_not_a_round() -> None:
    history = [{"role": "user", "content": "Wetter?"}, *_round_with_results("aifred", "Ich suche.", 1),
               {"role": "assistant", "content": "[AIFRED]: Regen."}]
    assert first_exchange(history) == ("Wetter?", "Regen.")


def test_the_title_waits_while_only_a_round_exists() -> None:
    assert first_exchange([{"role": "user", "content": "Wetter?"}, *_round_with_results("aifred", "x", 1)]) is None


# ── URL ranking context ──────────────────────────────────────────────────


class _Ranker:
    prompt = ""

    async def chat(self, model: str, messages: list[dict[str, str]], options: dict[str, Any]) -> _Response:
        _Ranker.prompt = messages[0]["content"]
        return _Response("1")


def test_the_url_ranking_context_leaves_the_rounds_out() -> None:
    from aifred.lib.research.url_ranker import rank_urls_by_relevance

    history = [{"role": "user", "content": "Wetter?"}, *_round_with_results("aifred", "Ich suche.", 1),
               {"role": "assistant", "content": "[AIFRED]: Regen."}, {"role": "assistant", "content": None}]
    asyncio.run(rank_urls_by_relevance("Wetter?", ["http://a", "http://b"], ["a", "b"], ["", ""], _Ranker(), "m", history, top_n=1))
    assert "Regen." in _Ranker.prompt
    assert "Ich suche" not in _Ranker.prompt and "web_search" not in _Ranker.prompt and "None" not in _Ranker.prompt
