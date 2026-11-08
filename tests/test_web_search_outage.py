"""Multi-API web search: only a search that reached no server is reported as an outage.

An "offline" answer tells the agent the network is down; a broken search adapter is no outage and
must show as the error it is, with its traceback in the log. The hits keep their titles and snippets,
also for a single query (the URL ranking reads them; until 08.11.2026 a single query lost them).
"""

import pytest

from aifred.lib.tools import search_tools
from aifred.lib.tools.base import BaseTool
from aifred.lib.tools.search_tools import MultiAPISearchTool


class _Api(BaseTool):
    def __init__(self, name: str, outcome: dict | Exception) -> None:
        super().__init__()
        self.name = name
        self.description = name
        self.min_call_interval = 0.0
        self._outcome = outcome
        self.searched: list[str] = []

    def execute(self, query: str, **kwargs) -> dict:
        self.searched.append(query)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


NETWORK_DOWN = {"success": False, "error": "connection refused", "error_type": "network"}


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(search_tools, "log_exception", lambda message, error: lines.append(f"{message}: {error!r}"))
    return lines


def _search(*apis: _Api, queries: tuple[str, ...] = ("Wetter Berlin",)) -> dict:
    tool = MultiAPISearchTool()
    tool.apis = list(apis)
    return tool.execute_multi_query(list(queries))


def test_no_server_reached_is_an_outage(logged: list[str]) -> None:
    result = _search(_Api("A", NETWORK_DOWN), _Api("B", NETWORK_DOWN))
    assert result["success"] is False and result["error_type"] == "network"


def test_a_broken_adapter_is_its_error_not_an_outage(logged: list[str]) -> None:
    result = _search(_Api("A", RuntimeError("adapter bug")))
    assert result["success"] is False
    assert "error_type" not in result
    assert "adapter bug" in result["error"]
    assert logged == ["Web search: A failed: RuntimeError('adapter bug')"]


def test_an_adapter_timeout_is_an_outage(logged: list[str]) -> None:
    result = _search(_Api("A", TimeoutError()))
    assert result["error_type"] == "network"
    assert logged == []


def _hits(*pages: tuple[str, str, str]) -> dict:
    return {"success": True, "related_urls": [url for url, _, _ in pages],
            "titles": [title for _, title, _ in pages], "snippets": [snippet for _, _, snippet in pages]}


def test_a_single_query_goes_to_every_api_and_keeps_titles_and_snippets(logged: list[str]) -> None:
    first = _Api("A", _hits(("https://a.test/1", "Wetter Berlin heute", "Sonnig, 12 Grad")))
    second = _Api("B", _hits(("https://a.test/1/", "Doppelt", "Doppelt"), ("https://b.test", "Regenradar", "Ab Mittag Regen")))
    result = _search(first, second)
    assert first.searched == second.searched == ["Wetter Berlin"]
    found = dict(zip(result["related_urls"], zip(result["titles"], result["snippets"], strict=True), strict=True))
    assert len(found) == 2   # the same page with a trailing slash counts once
    assert found["https://b.test"] == ("Regenradar", "Ab Mittag Regen")


def test_several_queries_are_distributed_over_the_apis(logged: list[str]) -> None:
    first, second = _Api("A", _hits(("https://a.test", "a", "a"))), _Api("B", _hits(("https://b.test", "b", "b")))
    _search(first, second, queries=("eins", "zwei", "drei"))
    assert first.searched == ["eins", "drei"]
    assert second.searched == ["zwei"]
