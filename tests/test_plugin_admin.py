"""The plugins as the settings page manages them (lib/plugin_admin.py, /api/plugins), with stand-ins for the plugins.

The Reflex page held the logic in its state and sent every stored credential, passwords too, into the browser's
state. Now one place serves both pages, and a password never leaves the server: the form says whether one is set,
and an empty password field keeps it.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import config, message_hub, plugin_admin, plugin_registry, settings
from aifred.lib.plugin_base import CredentialField
from aifred.lib.user_error import Conflict, Invalid, Missing


class _Hub:
    def __init__(self) -> None:
        self.running: set[str] = set()

    def is_running(self, name: str) -> bool:
        return name in self.running

    def register(self, name: str, _factory: Any) -> None:
        self.running.add(name)

    async def start_all(self) -> None:
        return None

    def unregister(self, name: str) -> None:
        self.running.discard(name)


class _Mail:
    """A channel that answers only when told to, with a password and a choice."""

    name = "mail"
    always_reply = False
    credential_fields = [
        CredentialField(env_key="MAIL_USER", label_key="Benutzer", is_secret=True),
        CredentialField(env_key="MAIL_PASSWORD", label_key="Passwort", is_password=True),
        CredentialField(env_key="MAIL_MODE", label_key="Modus", options=[("imap", "IMAP"), ("pop", "POP")]),
    ]

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.applied: dict[str, str] = {}

    def is_configured(self) -> bool:
        return bool(os.environ.get("MAIL_PASSWORD"))

    def load_settings(self) -> dict[str, str]:
        path = self.folder / "mail.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def save_settings(self, values: dict[str, str]) -> None:
        (self.folder / "mail.json").write_text(json.dumps(values))

    def apply_credentials(self, values: dict[str, str]) -> None:
        self.applied = dict(values)

    async def listener_loop(self) -> None:
        return None


class _Chat(_Mail):
    """A channel that always answers: its listener goes with its switch."""

    name = "chat"
    always_reply = True


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"ui_language": "de"}))
    monkeypatch.setattr(settings, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("")
    hub = _Hub()
    monkeypatch.setattr(message_hub, "message_hub", hub)
    # The stand-ins have no folder with an i18n.json: their names come from here.
    monkeypatch.setattr(plugin_admin, "plugin_display_name", lambda plugin, lang: str(plugin.name).title())
    monkeypatch.setattr(plugin_admin, "plugin_description", lambda plugin, lang: "")
    monkeypatch.setattr(plugin_admin, "load_plugin_i18n", lambda folder: {})
    channels = {"mail": _Mail(tmp_path), "chat": _Chat(tmp_path)}
    switched_off: set[str] = {"wetter"}
    monkeypatch.setattr(plugin_registry, "all_channels", lambda: dict(channels))
    monkeypatch.setattr(plugin_registry, "get_channel", lambda name: channels.get(name))
    monkeypatch.setattr(plugin_registry, "get_tool_plugin", lambda name: None)
    monkeypatch.setattr(plugin_registry, "list_all_plugins", lambda: [
        {"name": name, "display_de": name.title(), "display_en": name.title(), "file": name, "type": "tool",
         "enabled": "" if name in switched_off else "1", "has_credentials": ""} for name in ("rechner", "wetter")
    ])
    monkeypatch.setattr(plugin_registry, "is_plugin_enabled", lambda name, plugin_type="tool": name not in switched_off)
    def enable(name: str) -> bool:
        switched_off.discard(name)
        return True

    def disable(name: str) -> bool:
        switched_off.add(name)
        return True

    monkeypatch.setattr(plugin_registry, "enable_plugin", enable)
    monkeypatch.setattr(plugin_registry, "disable_plugin", disable)
    for key in ("MAIL_USER", "MAIL_PASSWORD", "MAIL_MODE", "MAIL_ENABLED", "CHAT_ENABLED"):
        monkeypatch.setenv(key, "")      # recorded: whatever the code sets goes again after the test
        monkeypatch.delenv(key)
    yield {"hub": hub, "channels": channels, "off": switched_off, "env": tmp_path / ".env", "settings": settings_file}


def test_a_tool_plugin_switches_by_its_marker(world: dict[str, Any]) -> None:
    asyncio.run(plugin_admin.set_tool("wetter", True))
    asyncio.run(plugin_admin.set_tool("rechner", False))
    assert world["off"] == {"rechner"}
    assert [(row.name, row.enabled) for row in plugin_admin.tools("de")] == [("rechner", False), ("wetter", True)]
    with pytest.raises(Missing):
        asyncio.run(plugin_admin.set_tool("gibt-es-nicht", True))


def test_a_channel_listens_by_its_own_switch_or_with_its_main_switch(world: dict[str, Any]) -> None:
    with pytest.raises(Conflict):                          # not set up yet: the form first
        asyncio.run(plugin_admin.set_channel("mail", monitor=True))
    os.environ["MAIL_PASSWORD"] = "geheim"
    asyncio.run(plugin_admin.set_channel("mail", monitor=True))
    assert world["hub"].running == set()                   # on, but its listener has a switch of its own
    asyncio.run(plugin_admin.set_channel("mail", listener=True, auto_reply=True, tier=2))
    assert world["hub"].running == {"mail"}
    asyncio.run(plugin_admin.set_channel("chat", monitor=True))
    assert world["hub"].running == {"mail", "chat"}        # always answers: listens with the main switch
    asyncio.run(plugin_admin.set_channel("chat", monitor=False))
    assert world["hub"].running == {"mail"}
    rows = {row.name: row for row in plugin_admin.channels("de")}
    assert (rows["mail"].monitor, rows["mail"].listener, rows["mail"].auto_reply, rows["mail"].tier) == (True, True, True, 2)
    with pytest.raises(Invalid):
        asyncio.run(plugin_admin.set_channel("mail", tier=9))


def test_the_form_never_sends_a_password_and_an_empty_one_keeps_it(world: dict[str, Any]) -> None:
    asyncio.run(plugin_admin.save_form("mail", {"MAIL_USER": "aifred", "MAIL_PASSWORD": "geheim", "MAIL_MODE": "pop"}))
    shown = {field.key: field for field in plugin_admin.form("mail", "de").fields}
    assert (shown["MAIL_USER"].value, shown["MAIL_PASSWORD"].value, shown["MAIL_PASSWORD"].is_set) == ("aifred", "", True)
    assert shown["MAIL_MODE"].value == "pop" and shown["MAIL_MODE"].options == [("imap", "IMAP"), ("pop", "POP")]
    assert "MAIL_PASSWORD=" in world["env"].read_text() and json.loads((world["env"].parent / "mail.json").read_text()) == {"MAIL_MODE": "pop"}
    asyncio.run(plugin_admin.save_form("mail", {"MAIL_USER": "aifred", "MAIL_PASSWORD": "", "MAIL_MODE": "imap"}))
    assert os.environ["MAIL_PASSWORD"] == "geheim" and world["channels"]["mail"].applied["MAIL_PASSWORD"] == "geheim"
    assert world["hub"].running == {"mail"} and os.environ["MAIL_ENABLED"] == "true"      # a saved channel is on
    with pytest.raises(Invalid):
        asyncio.run(plugin_admin.save_form("mail", {"MAIL_MODE": "smtp"}))
    with pytest.raises(Invalid):
        asyncio.run(plugin_admin.save_form("mail", {"FREMD": "x"}))
    with pytest.raises(Missing):
        plugin_admin.form("wetter", "de")


def test_the_page_lists_switches_and_fills_the_form(world: dict[str, Any]) -> None:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    with TestClient(api_app) as client:
        client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        listed = client.get("/plugins").json()
        assert [tool["name"] for tool in listed["tools"]] == ["rechner", "wetter"]
        assert [channel["tier"] for channel in listed["channels"]] == [1, 1] and listed["tiers"][0]["label"].startswith("T0 — ")
        assert client.put("/plugins/tools/wetter", json={"enabled": True}).json()["tools"][1]["enabled"] is True
        assert client.patch("/plugins/channels/mail", json={"monitor": True}).status_code == 409
        saved = client.put("/plugins/mail/form", json={"values": {"MAIL_USER": "a", "MAIL_PASSWORD": "b", "MAIL_MODE": "imap"}}).json()
        assert [field["value"] for field in saved["fields"]] == ["a", "", "imap"] and saved["fields"][1]["is_set"]
        assert client.get("/plugins/wetter/form").status_code == 404
