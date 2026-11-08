"""Deleting an agent and clearing its memory in the agent editor.

Both second clicks crashed: delete called _select_agent_for_editor, which never existed, and
clear imported get_agent_config from multi_agent, which does not define it.
"""

from types import SimpleNamespace

import pytest

from aifred.lib import agent_config, agent_memory
from aifred.lib.user_error import Invalid
from aifred.state import AIState


class _EditorFake:
    delete_agent_editor = AIState.event_handlers["delete_agent_editor"].fn
    clear_agent_memory = AIState.event_handlers["clear_agent_memory"].fn

    def __init__(self) -> None:
        self.editor_delete_confirm = ""
        self.editor_memory_confirm = ""
        self.debug: list[str] = []
        self.loaded: list[str] = []
        self.dropdown_refreshes = 0

    def add_debug(self, message: str) -> None:
        self.debug.append(message)

    def _refresh_agent_dropdown(self) -> None:
        self.dropdown_refreshes += 1

    def _load_agent_into_state(self, agent_id: str) -> None:
        self.loaded.append(agent_id)

    def _push_editor_dom(self) -> str:
        return "dom-push"


@pytest.fixture
def deleted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_delete(agent_id: str) -> None:
        if agent_id == "aifred":
            raise Invalid("base_agent_stays", agent="AIfred")
        calls.append(agent_id)

    monkeypatch.setattr(agent_config, "delete_agent", fake_delete)
    monkeypatch.setattr(agent_config, "load_agents_raw", lambda: {"aifred": {}, "sokrates": {}})
    return calls


def test_first_click_only_asks(deleted: list[str]) -> None:
    editor = _EditorFake()
    assert editor.delete_agent_editor("custom") is None
    assert editor.editor_delete_confirm == "custom"
    assert deleted == [] and editor.loaded == []


def test_second_click_deletes_and_shows_first_remaining_agent(deleted: list[str]) -> None:
    editor = _EditorFake()
    editor.editor_delete_confirm = "custom"
    assert editor.delete_agent_editor("custom") == "dom-push"
    assert deleted == ["custom"]
    assert editor.loaded == ["aifred"]
    assert editor.dropdown_refreshes == 1
    assert editor.editor_delete_confirm == ""


def test_failed_delete_stays_on_the_agent(deleted: list[str]) -> None:
    editor = _EditorFake()
    editor.editor_delete_confirm = "aifred"
    assert editor.delete_agent_editor("aifred") is None
    assert editor.loaded == [] and editor.dropdown_refreshes == 0
    assert editor.editor_delete_confirm == ""
    assert "AIfred is the base agent" in editor.debug[-1]           # the log reads in English


def test_second_click_clears_the_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    cleared: list[str] = []

    class _Memory:
        def clear(self, agent_id: str) -> int:
            cleared.append(agent_id)
            return 3

    monkeypatch.setattr(agent_memory, "get_agent_memory", lambda: _Memory())
    monkeypatch.setattr(agent_config, "get_agent_config", lambda agent_id: SimpleNamespace(display_name="Sokrates"))
    editor = _EditorFake()
    editor.editor_memory_confirm = "sokrates"
    assert editor.clear_agent_memory("sokrates") is not None
    assert cleared == ["sokrates"]
    assert editor.debug[-1].endswith("Sokrates: 3 memories cleared")
