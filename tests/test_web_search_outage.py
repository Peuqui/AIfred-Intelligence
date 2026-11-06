"""Multi-API web search: only a search that reached no server is reported as an outage.

An "offline" answer tells the agent the network is down; a broken search adapter is no outage and
must show as the error it is, with its traceback in the log.
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

    def execute(self, query: str, **kwargs) -> dict:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


NETWORK_DOWN = {"success": False, "error": "connection refused", "error_type": "network"}


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(search_tools, "log_exception", lambda message, error: lines.append(f"{message}: {error!r}"))
    return lines


def _search(*apis: _Api) -> dict:
    tool = MultiAPISearchTool()
    tool.apis = list(apis)
    return tool.execute("Wetter Berlin")


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
