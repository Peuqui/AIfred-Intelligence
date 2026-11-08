"""The web search of a turn (lib/research_tools.run_web_search) gives the model the pages it read, and only
them: the search's own answer is no source (until 08.11.2026 it joined the context as an empty source, and with
no page read the model got that instead of "no results"). The chain is a stand-in (fake_web_search.py), the
context is built for real."""
import asyncio
import json
from typing import Any

import pytest

from fake_web_search import FakeWebSearch

from aifred.lib import research_tools

URLS = ["https://a.test", "https://b.test"]


def _page(url: str, text: str) -> dict[str, Any]:
    return {"success": True, "url": url, "title": f"Seite {url[-6]}", "content": text, "word_count": len(text.split())}


def _search(monkeypatch: pytest.MonkeyPatch, pages: list[dict[str, Any]]) -> tuple[str, int]:
    FakeWebSearch(urls=URLS, pages=pages).install(monkeypatch)
    return asyncio.run(research_tools.run_web_search(["Wetter morgen"], []))


def test_the_context_holds_the_pages_read_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    context, sources = _search(monkeypatch, [_page(URLS[0], "Morgen scheint die Sonne."), _page(URLS[1], "Kein Regen.")])
    assert sources == 2
    assert "## Source 1:" in context and "## Source 2:" in context and "## Source 3:" not in context
    assert "Multi-API Search" not in context
    assert "Morgen scheint die Sonne." in context and "Kein Regen." in context


def test_no_page_read_is_no_result(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _search(monkeypatch, []) == (json.dumps({"error": "No results found"}), 0)


def test_a_page_without_text_is_no_source(monkeypatch: pytest.MonkeyPatch) -> None:
    context, sources = _search(monkeypatch, [_page(URLS[0], "Sonne."), _page(URLS[1], "")])
    assert sources == 1
    assert "## Source 2:" not in context
