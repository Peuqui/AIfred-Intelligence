"""The tools on offer in the agent editor follow the plugins while the service runs."""
from __future__ import annotations

from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from aifred.lib import agent_config, plugin_registry
from aifred.lib.plugin_base import BaseChannel, PluginContext
from aifred.state._agent_editor_mixin import AgentEditorMixin as M

CTX = PluginContext(agent_id="hal", lang="de", session_id="", llm_history=[])


def _tools(*names: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(name=name, tier=2) for name in names]


def _plugin(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


@pytest.fixture
def plugins(monkeypatch: pytest.MonkeyPatch) -> list:
    found: list = []
    monkeypatch.setattr(plugin_registry, "collect_plugin_tools", lambda ctx: list(found))
    monkeypatch.setattr(plugin_registry, "plugin_display_name", lambda plugin, lang: f"{plugin.name} {lang}")
    return found


def test_memory_first_one_group_per_plugin_channels_together_at_the_end(plugins: list) -> None:
    channel = Mock(spec=BaseChannel)
    plugins.extend([(channel, _tools("mail_send")), (_plugin("Nextcloud"), _tools("a", "b")), (channel, _tools("chat_send"))])
    groups = plugin_registry.tool_groups_on_offer(CTX)
    assert [group["name_de"] for group in groups] == ["Memory", "Nextcloud de", "Channels"]
    assert groups[1] == {"name_de": "Nextcloud de", "name_en": "Nextcloud en", "names": ["a", "b"],
                         "tools": [{"name": "a", "tier": 2}, {"name": "b", "tier": 2}]}
    assert groups[2]["names"] == ["mail_send", "chat_send"]


class _Editor:
    def __init__(self, tools: dict[str, bool]) -> None:
        self.editor_agent_id = "hal"
        self.editor_tools = tools
        self.editor_tool_groups: list = []
        self._offer_editor_tools = MethodType(M._offer_editor_tools, self)
        self.refresh = MethodType(M._refresh_editor_tools, self)


def _names(editor: _Editor) -> set[str]:
    return {name for group in editor.editor_tool_groups for name in group["names"] if name in ("google", "cloud")}


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(tools=["google"])
    monkeypatch.setattr(agent_config, "get_agent_config", lambda agent_id: config)


def test_a_plugin_switched_off_leaves_the_offer_and_the_choices(plugins: list, agent: None) -> None:
    plugins.extend([(_plugin("Google"), _tools("google")), (_plugin("Nextcloud"), _tools("cloud"))])
    editor = _Editor({"google": True, "cloud": False})
    plugins.pop(0)
    editor.refresh()
    assert _names(editor) == {"cloud"}
    assert "google" not in editor.editor_tools


def test_a_plugin_switched_on_starts_from_the_saved_list(plugins: list, agent: None) -> None:
    plugins.append((_plugin("Google"), _tools("google")))
    editor = _Editor({})
    editor.refresh()
    assert editor.editor_tools["google"] is True


def test_a_choice_not_saved_yet_survives_the_switch(plugins: list, agent: None) -> None:
    plugins.extend([(_plugin("Google"), _tools("google")), (_plugin("Nextcloud"), _tools("cloud"))])
    editor = _Editor({"google": False, "cloud": True})  # the saved list says the opposite
    editor.refresh()
    assert (editor.editor_tools["google"], editor.editor_tools["cloud"]) == (False, True)


def test_no_whitelist_grants_everything_on_offer(plugins: list) -> None:
    plugins.append((_plugin("Nextcloud"), _tools("cloud")))
    editor = _Editor({})
    editor._offer_editor_tools("hal", None)
    assert all(editor.editor_tools.values()) and "cloud" in editor.editor_tools
