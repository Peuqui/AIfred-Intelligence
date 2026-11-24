"""Addresses a tool really read: the ``{"read": [...]}`` event of a tool executor (collected in ToolKit.read_urls,
never a result) and the research tools that report it (web_fetch on success, web_search for the pages it read)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from fake_web_search import FakeWebSearch

from aifred.lib.function_calling import Tool, ToolKit
from aifred.lib.tools import registry
from aifred.plugins.tools.research import get_research_tools


def _events(toolkit: ToolKit, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    async def collect() -> list[dict[str, Any]]:
        return [event async for event in toolkit.execute_streaming(name, arguments)]

    return asyncio.run(collect())


async def _reading(**_args: Any) -> AsyncGenerator[dict[str, Any], None]:
    yield {"read": ["https://www.Shop.test/a/", "https://r.jina.ai/https://other.test/b#x"]}
    yield {"result": "gelesen"}


def test_a_read_event_is_collected_normalized_and_is_no_result() -> None:
    toolkit = ToolKit(tools=[Tool(name="reader", description="", parameters={}, executor=_reading)])
    events = _events(toolkit, "reader", {})
    assert events == [{"type": "tool_result", "result": "gelesen"}]
    assert toolkit.read_urls == {"shop.test/a", "other.test/b"}


def test_a_read_event_alone_leaves_the_result_empty() -> None:
    async def only_read(**_args: Any) -> AsyncGenerator[dict[str, Any], None]:
        yield {"read": ["https://shop.test/a"]}

    toolkit = ToolKit(tools=[Tool(name="reader", description="", parameters={}, executor=only_read)])
    assert _events(toolkit, "reader", {}) == [{"type": "tool_result", "result": ""}]
    assert toolkit.read_urls == {"shop.test/a"}


def _tool(name: str) -> Tool:
    return next(tool for tool in get_research_tools("de", []) if tool.name == name)


def _fetch(monkeypatch: pytest.MonkeyPatch, url: str, scraped: dict[str, Any]) -> ToolKit:
    monkeypatch.setattr(registry, "scrape_webpage", lambda _url: scraped)
    monkeypatch.setattr("aifred.lib.security.validate_external_url", lambda url: None)
    toolkit = ToolKit(tools=[_tool("web_fetch")], _source="browser")
    _events(toolkit, "web_fetch", {"url": url})
    return toolkit


def test_a_successful_fetch_reports_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    toolkit = _fetch(monkeypatch, "https://www.shop.test/a/", {"success": True, "content": "Seite", "word_count": 1})
    assert toolkit.read_urls == {"shop.test/a"}


def test_a_failed_fetch_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    toolkit = _fetch(monkeypatch, "https://shop.test/a", {"success": False, "error": "404"})
    assert toolkit.read_urls == set()


def test_a_fetch_without_text_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    toolkit = _fetch(monkeypatch, "https://shop.test/a", {"success": True, "content": "", "word_count": 0})
    assert toolkit.read_urls == set()


def test_a_rejected_url_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib.security import UnsafeURLError

    def refuse(url: str) -> None:
        raise UnsafeURLError("intern")

    monkeypatch.setattr("aifred.lib.security.validate_external_url", refuse)
    toolkit = ToolKit(tools=[_tool("web_fetch")], _source="browser")
    events = _events(toolkit, "web_fetch", {"url": "http://localhost/a"})
    assert "error" in json.loads(events[-1]["result"])
    assert toolkit.read_urls == set()


def test_a_search_reports_the_pages_it_read(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = ["https://a.test/x", "https://b.test/y", "https://c.test/z"]
    pages = [{"success": True, "url": url, "title": "T", "content": "Text", "word_count": 1} for url in urls[:2]]
    FakeWebSearch(urls=urls, pages=pages).install(monkeypatch)
    toolkit = ToolKit(tools=[_tool("web_search")], _source="browser")
    from aifred.lib.debug_bus import session_scope  # the search reports its steps to the session's debug bus

    async def search() -> None:
        with session_scope("s"):
            async for _ in toolkit.execute_streaming("web_search", {"queries": ["Preis"]}):
                pass

    asyncio.run(search())
    assert toolkit.read_urls == {"a.test/x", "b.test/y"}   # the third was found, not read


def test_a_search_without_pages_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeWebSearch(urls=["https://a.test/x"], pages=[]).install(monkeypatch)
    toolkit = ToolKit(tools=[_tool("web_search")], _source="browser")
    _events(toolkit, "web_search", {"queries": ["Preis"]})
    assert toolkit.read_urls == set()
