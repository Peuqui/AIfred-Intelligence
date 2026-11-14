"""The three values of the nightly cleanup (retention in days, delete after it, describe at night) live in the vision
plugin's settings form (Settings, Plugins, gear) and nowhere else, also with the plugin off. The plugin's settings.json
is a stand-in in tmp_path; the form goes through lib/plugin_admin as the page does."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import config, plugin_admin, plugin_registry, vision_cleanup, vision_settings
from aifred.lib.user_error import Invalid

KEYS = (vision_cleanup.RETENTION_FORM_KEY, *vision_cleanup.GC_FORM_KEYS.values())


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    settings_file = tmp_path / "vision_settings.json"
    settings_file.write_text(json.dumps({"vision_mode": "live", "face_recognition": {"enabled": True, "continuous": True}}))
    monkeypatch.setattr(vision_settings, "VISION_SETTINGS_PATH", settings_file)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("")
    for key in KEYS:
        monkeypatch.setenv(key, "")      # recorded: whatever the form sets goes again after the test
        monkeypatch.delenv(key)
    yield settings_file


def _shown() -> dict[str, str]:
    return {field.key: field.value for field in plugin_admin.form("vision", "de").fields}


def test_the_form_offers_the_three_fields_with_their_defaults(world: Path) -> None:
    form = plugin_admin.form("vision", "de")
    assert [field.key for field in form.fields] == [
        "VISION_RETENTION_DAYS", "VISION_GC_PRUNE", "VISION_GC_DESCRIBE"]
    assert _shown() == {"VISION_RETENTION_DAYS": "14", "VISION_GC_PRUNE": "on", "VISION_GC_DESCRIBE": "on"}
    assert [field.label for field in form.fields] == ["Aufbewahrung (Tage)", "Alte Ereignisse nach Frist löschen", "Nachts nachbeschreiben"]
    assert [option for option in form.fields[1].options] == [("on", "an"), ("off", "aus")]
    assert all(field.tooltip for field in form.fields)


def test_saving_writes_into_the_vision_settings_and_keeps_the_rest(world: Path) -> None:
    asyncio.run(plugin_admin.save_form(
        "vision", {"VISION_RETENTION_DAYS": "30", "VISION_GC_PRUNE": "off", "VISION_GC_DESCRIBE": "on"}))
    stored = json.loads(world.read_text())
    assert stored["face_recognition"] == {"enabled": True, "continuous": True, "retention_days": 30,
                                          "gc_prune": False, "gc_describe": True}
    assert stored["vision_mode"] == "live"
    assert (vision_cleanup.retention_days(), vision_cleanup.gc_switch("gc_prune"), vision_cleanup.gc_switch("gc_describe")) == (30, False, True)
    assert _shown() == {"VISION_RETENTION_DAYS": "30", "VISION_GC_PRUNE": "off", "VISION_GC_DESCRIBE": "on"}


@pytest.mark.parametrize("days", ["0", "3651", "-5", "vierzehn", "", "1.5"])
def test_invalid_days_are_refused_and_nothing_is_written(world: Path, days: str) -> None:
    before = world.read_text()
    with pytest.raises(Invalid):
        asyncio.run(plugin_admin.save_form(
            "vision", {"VISION_RETENTION_DAYS": days, "VISION_GC_PRUNE": "off", "VISION_GC_DESCRIBE": "off"}))
    assert world.read_text() == before


def test_a_switch_that_is_neither_on_nor_off_is_refused(world: Path) -> None:
    with pytest.raises(Invalid):
        asyncio.run(plugin_admin.save_form(
            "vision", {"VISION_RETENTION_DAYS": "10", "VISION_GC_PRUNE": "vielleicht", "VISION_GC_DESCRIBE": "on"}))


def test_the_form_is_listed_and_saved_with_the_plugin_off(world: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    monkeypatch.setattr(plugin_registry, "_is_switched_off", lambda _folder: True)
    monkeypatch.setattr(plugin_registry, "_reload_tools", lambda: None)
    plugin_registry._tools = None
    try:
        assert plugin_registry.get_tool_plugin("vision") is None            # off: not loaded as a plugin
        with TestClient(api_app) as client:
            client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
            rows: dict[str, Any] = {row["name"]: row for row in client.get("/plugins").json()["tools"]}
            assert rows["vision"]["enabled"] is False and rows["vision"]["has_settings"] is True
            saved = client.put("/plugins/vision/form", json={"values": {
                "VISION_RETENTION_DAYS": "21", "VISION_GC_PRUNE": "on", "VISION_GC_DESCRIBE": "off"}})
            assert saved.status_code == 200
            assert [field["value"] for field in saved.json()["fields"]] == ["21", "on", "off"]
            assert client.put("/plugins/vision/form", json={"values": {
                "VISION_RETENTION_DAYS": "0", "VISION_GC_PRUNE": "on", "VISION_GC_DESCRIBE": "off"}}).status_code == 422
    finally:
        plugin_registry._tools = None
    assert vision_cleanup.retention_days() == 21 and vision_cleanup.gc_switch("gc_describe") is False
