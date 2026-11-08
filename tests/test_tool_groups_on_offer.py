"""The tools an agent can be granted (plugin_registry.tool_groups_on_offer): memory first, one group per plugin,
the channels together at the end."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aifred.lib import plugin_registry
from aifred.lib.plugin_base import BaseChannel, PluginContext

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
