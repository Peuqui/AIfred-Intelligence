"""Off means off for a channel (14.11.2026): its main switch (``monitor`` in the settings) decides whether it offers
tools, is offered as a target and may send anything. Channel stand-ins; the settings in a temporary file."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from aifred.lib import message_processor, plugin_registry, settings
from aifred.lib.alert_bus import AlertEvent, AlertRule, _deliver_in_session
from aifred.lib.envelope import OutboundMessage
from aifred.lib.plugin_base import PluginContext
from aifred.lib.read_mark import BubbleRef
from aifred.lib.user_error import Conflict


class _Channel:
    always_reply = False

    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[str] = []

    def is_configured(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Any]:
        return [f"{self.name}_tool"]

    async def send_reply(self, outbound: OutboundMessage, original: Any) -> None:
        self.sent.append(outbound.text)


@pytest.fixture
def channels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, _Channel]:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"channel_toggles": {"mail": {"monitor": True}, "chat": {"monitor": False}}}))
    monkeypatch.setattr(settings, "SETTINGS_FILE", settings_file)
    stand_ins = {"mail": _Channel("mail"), "chat": _Channel("chat")}
    monkeypatch.setattr(plugin_registry, "all_channels", lambda: dict(stand_ins))
    monkeypatch.setattr(plugin_registry, "get_channel", lambda name: stand_ins.get(name))
    monkeypatch.setattr(plugin_registry, "discover_tools", list)
    monkeypatch.setattr(plugin_registry, "plugin_label", lambda plugin: str(plugin.name).title())
    return stand_ins


def test_the_main_switch_is_the_one_answer(channels: dict[str, _Channel]) -> None:
    assert plugin_registry.channel_is_on("mail") and not plugin_registry.channel_is_on("chat")
    assert not plugin_registry.channel_is_on("unknown")
    assert list(plugin_registry.on_channels()) == ["mail"]


def test_a_channel_that_is_off_offers_no_tools(channels: dict[str, _Channel]) -> None:
    ctx = PluginContext(agent_id="aifred", lang="en", session_id="s")
    offered = plugin_registry.collect_plugin_tools(ctx)
    assert [(owner.name, tools) for owner, tools in offered] == [("mail", ["mail_tool"])]


def test_nothing_is_sent_through_a_channel_that_is_off(channels: dict[str, _Channel], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(message_processor, "_resolve_channel_recipient", lambda channel, recipient: recipient)
    assert asyncio.run(message_processor.announce_to_channel("mail", "me", "hello", session_id=None)) is True
    with pytest.raises(Conflict) as refused:
        asyncio.run(message_processor.announce_to_channel("chat", "me", "hello", session_id=None))
    assert "Chat" in str(refused.value) and "off" in str(refused.value)
    assert channels["chat"].sent == [] and channels["mail"].sent == ["hello"]


def test_an_announcement_in_rooms_refuses_before_it_makes_a_bubble(channels: dict[str, _Channel], monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[str] = []

    def record(*_args: Any, **_kwargs: Any) -> BubbleRef:
        recorded.append("bubble")
        return BubbleRef("s", "t1")

    monkeypatch.setattr(message_processor, "record_autonomous_turn", record)
    with pytest.raises(Conflict):
        asyncio.run(message_processor.announce_into_rooms("chat", ["kitchen"], "hello", "Codine", {}))
    assert recorded == []


def test_an_alert_goes_on_to_its_other_sinks_when_one_channel_is_off(channels: dict[str, _Channel], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(message_processor, "_resolve_channel_recipient", lambda channel, recipient: recipient or "me")
    monkeypatch.setattr(message_processor, "resolve_announce_targets", lambda channel, target: [target or "me"])
    monkeypatch.setattr(message_processor, "record_autonomous_turn", lambda *args, **kwargs: BubbleRef("s", "t1"))
    event = AlertEvent(producer="vision", category="face_unknown", source_id="cam", severity="warning", title="t", body="b",
                       timestamp=datetime(2026, 11, 14, 12, 0))
    rule = AlertRule(producer="vision", sinks=["chat:me", "mail:me"], rule_id="r")
    assert asyncio.run(_deliver_in_session(event, rule)) is True
    assert channels["chat"].sent == [] and len(channels["mail"].sent) == 1


def test_a_channel_that_is_off_is_not_offered_as_a_target(channels: dict[str, _Channel], monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib.api.jobs import jobs_options
    from aifred.lib.api.vigilantia import _rules

    monkeypatch.setattr("aifred.lib.plugin_base.plugin_display_name", lambda plugin, lang: str(plugin.name))
    assert [option.id for option in asyncio.run(jobs_options()).channels] == ["mail"]
    assert [option.id for option in _rules().channels] == ["mail"]
