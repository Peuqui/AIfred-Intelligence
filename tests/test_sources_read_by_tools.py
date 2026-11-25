"""The sources block lists every page the turn really read (25.11.2026): the ones ``web_fetch`` fetched and the
ones a tool reported with ``{"read": [...]}`` (``web_search`` reads its best hits itself), in the order they were
read, each page once, each entry saying through which tool it was read.

At job 41 a value carried a source that "was never fetched": the search had read the page, but only the fetches
were listed."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest

import aifred.lib.subagent_run as run
from aifred.backends.base import LLMOptions
from aifred.lib.bubble import KIND_SOURCES, BubbleArtifact, render_artifact
from aifred.lib.function_calling import Tool, ToolKit
from aifred.lib.llm_pipeline import run_llm_stream
from aifred.lib.perf_metrics import InferenceWork
from aifred.lib.plugin_base import PluginContext
from aifred.lib.security import TIER_READONLY, TIER_WRITE_DATA

FETCH_ARGS = json.dumps({"url": "https://shop.example/a"})


def _tool_read(name: str, *urls: str) -> dict[str, Any]:
    return {"type": "tool_read", "name": name, "urls": list(urls)}


class _Script:
    """A model whose requests play exactly ``events``."""

    backend_type = "vllm"

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def chat_stream(self, model, messages, options, toolkit=None, compression=None, interjections=None):
        for event in self.events:
            yield event
        yield {"type": "content", "text": "Fertig."}
        yield {"type": "done", "metrics": {"work": InferenceWork().to_dict()}}


def _sources(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    async def play() -> Any:
        async for event in run_llm_stream(
            _Script(events), "m", [], SimpleNamespace(enable_thinking=False), "AIfred", source="browser",  # type: ignore[arg-type]
        ):
            if event["type"] == "pipeline_result":
                return event["result"]

    result = asyncio.run(play())
    blocks = [artifact for artifact in result.artifacts if artifact.kind == KIND_SOURCES]
    assert len(blocks) <= 1
    return [entry for block in blocks for entry in block.data["urls"]]


def _fetch(url: str, ok: bool = True, read: bool = True) -> list[dict[str, Any]]:
    """The events of one web_fetch: its call, the read report of a success, its result."""
    result = "Inhalt" if ok else json.dumps({"error": "nicht erreichbar"})
    return [
        {"type": "tool_call", "name": "web_fetch", "arguments": json.dumps({"url": url})},
        *([_tool_read("web_fetch", url)] if ok and read else []),
        {"type": "tool_result", "name": "web_fetch", "result": result},
    ]


def _search(*urls: str) -> list[dict[str, Any]]:
    return [
        {"type": "tool_call", "name": "web_search", "arguments": '{"queries": ["x"]}'},
        _tool_read("web_search", *urls),
        {"type": "tool_result", "name": "web_search", "result": "Treffer"},
    ]


class TestPipelineCollectsReadPages:
    def test_the_pages_a_search_read_are_listed_as_read(self):
        entries = _sources(_search("https://shop.example/a", "https://shop.example/b"))
        assert entries == [
            {"url": "https://shop.example/a", "success": True, "tool": "web_search"},
            {"url": "https://shop.example/b", "success": True, "tool": "web_search"},
        ]

    def test_fetches_and_search_reads_come_in_the_order_they_were_read(self):
        entries = _sources([
            *_fetch("https://a.example/1"),
            *_search("https://b.example/2", "https://c.example/3"),
            *_fetch("https://d.example/4"),
        ])
        assert [(e["url"], e["tool"]) for e in entries] == [
            ("https://a.example/1", "web_fetch"), ("https://b.example/2", "web_search"),
            ("https://c.example/3", "web_search"), ("https://d.example/4", "web_fetch"),
        ]
        assert all(e["success"] is True for e in entries)

    def test_a_page_read_by_search_and_fetch_is_listed_once_where_it_was_first_read(self):
        entries = _sources([
            *_search("https://www.shop.example/a/", "https://b.example/2"),
            *_fetch("https://shop.example/a#preis"),
        ])
        assert [(e["url"], e["tool"]) for e in entries] == [
            ("https://www.shop.example/a/", "web_search"), ("https://b.example/2", "web_search"),
        ]

    def test_a_fetch_first_and_the_search_reading_the_same_page_later_stay_one_entry(self):
        entries = _sources([*_fetch("https://shop.example/a"), *_search("https://www.shop.example/a/")])
        assert [(e["url"], e["tool"]) for e in entries] == [("https://shop.example/a", "web_fetch")]

    def test_the_read_report_of_a_fetch_is_no_second_entry(self):
        entries = _sources(_fetch("https://shop.example/a"))
        assert entries == [{"url": "https://shop.example/a", "success": True, "tool": "web_fetch"}]

    def test_a_failed_fetch_stays_a_failure(self):
        entries = _sources(_fetch("https://shop.example/a", ok=False))
        assert entries == [{"url": "https://shop.example/a", "success": False, "tool": "web_fetch"}]

    def test_a_search_reading_a_page_whose_fetch_failed_makes_it_read(self):
        entries = _sources([*_fetch("https://shop.example/a", ok=False), *_search("https://shop.example/a")])
        assert entries == [{"url": "https://shop.example/a", "success": True, "tool": "web_fetch"}]

    def test_a_search_reading_nothing_makes_no_block(self):
        assert _sources(_search()) == []

    def test_a_fetch_of_an_address_that_cannot_be_split_is_listed_as_it_is(self):
        entries = _sources(_fetch("http://[broken", ok=False))
        assert [e["url"] for e in entries] == ["http://[broken"]


class TestBlockMarkup:
    ENTRIES = [
        {"url": "https://a.example/1", "success": True, "tool": "web_fetch"},
        {"url": "https://b.example/2", "success": True, "tool": "web_search"},
        {"url": "https://c.example/3", "success": False, "tool": "web_fetch"},
    ]

    def test_every_entry_names_the_tool_it_was_read_through(self):
        html = render_artifact(BubbleArtifact(KIND_SOURCES, {"urls": self.ENTRIES}), None)
        assert html.count("(über web_fetch)") == 2 and html.count("(über web_search)") == 1
        assert html.index("a.example") < html.index("b.example") < html.index("c.example")
        assert "🔗 3 Web-Quellen (1 fehlgeschlagen)" in html

    def test_the_note_follows_the_ui_language(self):
        from aifred.lib.formatting import build_sources_collapsible
        html = build_sources_collapsible(self.ENTRIES, lang="en")
        assert "(via web_search)" in html and "über" not in html


class TestEventChain:
    def test_the_toolkit_reports_the_read_addresses_as_they_were_given(self):
        async def reading(**_args: Any) -> AsyncGenerator[dict[str, Any], None]:
            yield {"read": ["https://www.Shop.test/a/"]}
            yield {"result": "gelesen"}

        toolkit = ToolKit(tools=[Tool(name="reader", description="", parameters={}, executor=reading)])

        async def collect() -> list[dict[str, Any]]:
            return [event async for event in toolkit.execute_streaming("reader", {})]

        events = asyncio.run(collect())
        assert events == [
            {"type": "tool_read", "urls": ["https://www.Shop.test/a/"]},
            {"type": "tool_result", "result": "gelesen"},
        ]

    def test_the_tool_loop_forwards_it_with_the_tool_name(self):
        from aifred.backends.round_pieces import TextDelta
        from test_tool_loop import FakeBackend, FakeToolkit, end, lookup_call, play

        class ReadingToolkit(FakeToolkit):
            async def execute_streaming(self, name, arguments):
                yield {"type": "tool_read", "urls": ["https://shop.test/a"]}
                yield {"type": "tool_result", "result": "ok"}

        backend = FakeBackend([[end([lookup_call()], finish="tool_calls")], [TextDelta("Antwort."), end()]])
        items = play(backend, ReadingToolkit())
        assert [i for i in items if i["type"] == "tool_read"] == [
            {"type": "tool_read", "name": "lookup", "urls": ["https://shop.test/a"]},
        ]


class TestSubAgentHandsItsSourcesOn:
    def test_the_block_of_the_sub_agent_reaches_the_caller_with_the_search_reads(self, monkeypatch: pytest.MonkeyPatch):
        async def prepare(agent_id, user_query, **kwargs):
            return "", ToolKit(tools=[Tool(name="web_search", description="", parameters={}, executor=lambda: "", tier=TIER_READONLY)])

        class FakeClient(_Script):
            def __init__(self, **kwargs):
                super().__init__([*_search("https://shop.example/a"), *_fetch("https://shop.example/b")])

            async def close(self):
                pass

        monkeypatch.setattr(run, "prepare_agent_toolkit", prepare)
        monkeypatch.setattr(run, "resolve_run_params", lambda agent_id: run.RunParams("llamacpp", "http://x", "m", 8192, 0.5))
        monkeypatch.setattr(run, "build_llm_options", lambda state, agent, model, t, n: LLMOptions(temperature=t, num_ctx=n))
        monkeypatch.setattr(run, "LLMClient", FakeClient)
        ctx = PluginContext(agent_id="aifred", lang="de", session_id="s", max_tier=TIER_WRITE_DATA, source="browser")
        session = run.SubagentSession(
            ctx, allowed_tiers=frozenset({TIER_READONLY}), depth=0, target_agent="aifred", task="Suche", expected_result="Liste",
        )

        async def collect() -> list[dict[str, Any]]:
            return [item async for item in session.run()]

        events = asyncio.run(collect())
        artifacts = next(e for e in events if "artifacts" in e)["artifacts"]
        (sources,) = [a for a in artifacts if a["kind"] == KIND_SOURCES]
        assert [(e["url"], e["tool"]) for e in sources["data"]["urls"]] == [
            ("https://shop.example/a", "web_search"), ("https://shop.example/b", "web_fetch"),
        ]
