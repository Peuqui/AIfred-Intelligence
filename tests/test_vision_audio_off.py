"""Off means off for vision and the audio player (14.11.2026): their watchers, supervisor, cleanups and endpoints
belong to the plugin; the daily garbage collection of vision has a core part (pruning) that runs with the plugin off."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import audio_channels, plugin_registry, vision_autostart, vision_cleanup, vision_settings


@pytest.fixture
def plugin_on(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """Which tool plugins are on; everything else follows ``is_plugin_enabled``."""
    state = {"vision": True, "audio_player": True}
    monkeypatch.setattr(plugin_registry, "is_plugin_enabled", lambda name: state.get(name, True))
    return state


# ── Vision: watchers, supervisor ─────────────────────────────

class _Watcher:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def start(self, source_id: str, config: Any) -> None:
        self.started.append(source_id)


def test_no_watcher_starts_while_vision_is_off_however_armed(plugin_on: dict[str, bool], monkeypatch: pytest.MonkeyPatch) -> None:
    watcher = _Watcher()

    class Store:
        def list_sources(self) -> list[dict[str, Any]]:
            return [{"source_id": "cam/rtsp", "kind": "rtsp", "auto_start": True, "settings": {}}]

    monkeypatch.setattr("aifred.lib.vision_store.VisionStore", Store)
    monkeypatch.setattr("aifred.lib.vision_watcher.get_default_watcher", lambda: watcher)
    monkeypatch.setattr(vision_autostart, "load_vision_settings", lambda: {"vigilantia_armed": True})
    monkeypatch.setattr(vision_autostart, "_build_background_config", lambda record, plugin: None)

    plugin_on["vision"] = False
    assert asyncio.run(vision_autostart.start_all_background_watchers()) == 0 and watcher.started == []
    plugin_on["vision"] = True
    assert asyncio.run(vision_autostart.start_all_background_watchers()) == 1 and watcher.started == ["cam/rtsp"]


def test_the_supervisor_does_nothing_while_vision_is_off(plugin_on: dict[str, bool], monkeypatch: pytest.MonkeyPatch) -> None:
    rounds = {"n": 0}
    listed: list[str] = []

    async def one_round(_seconds: float) -> None:
        rounds["n"] += 1
        if rounds["n"] > 1:
            raise asyncio.CancelledError

    class Store:
        def list_sources(self) -> list[dict[str, Any]]:
            listed.append("listed")
            return []

    monkeypatch.setattr(vision_autostart.asyncio, "sleep", one_round)
    monkeypatch.setattr(vision_autostart, "load_vision_settings", lambda: {"vigilantia_armed": True})
    monkeypatch.setattr("aifred.lib.vision_store.VisionStore", Store)
    plugin_on["vision"] = False
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(vision_autostart.schedule_supervisor())
    assert listed == []


def test_vision_names_its_supervisor_and_disarms_when_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import vigilantia
    from aifred.plugins.tools.vision import plugin

    parts = plugin.background_parts()
    assert list(parts) == ["vision_supervisor"] and parts["vision_supervisor"].factory is vision_autostart.schedule_supervisor
    disarmed: list[bool] = []

    async def set_armed(armed: bool) -> int:
        disarmed.append(armed)
        return 0

    monkeypatch.setattr(vigilantia, "set_armed", set_armed)
    asyncio.run(plugin.on_switched_off())
    assert disarmed == [False]


# ── Vision: the garbage collection (aufräumlauf) ────────────

@pytest.fixture
def gc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plugin_on: dict[str, bool]) -> dict[str, Any]:
    settings_file = tmp_path / "vision_settings.json"
    settings_file.write_text(json.dumps({"face_recognition": {"retention_days": 7}}))
    monkeypatch.setattr(vision_settings, "VISION_SETTINGS_PATH", settings_file)
    calls: dict[str, Any] = {"describe": 0, "prune": 0, "file": settings_file}

    async def describe(**_kwargs: Any) -> Any:
        calls["describe"] += 1
        return type("Done", (), {"aborted_vram": False, "total_events": 0})()

    def prune(_base: Path, _cutoff: Any) -> int:
        calls["prune"] += 1
        return 0

    monkeypatch.setattr("aifred.lib.vision_bulk.run_bulk_describe", describe)
    monkeypatch.setattr(vision_cleanup, "_cleanup_dated_subdirs", prune)
    monkeypatch.setattr(vision_cleanup, "_cleanup_vision_db_events", lambda _cutoff: 0)
    return calls


def _switch(gc_state: dict[str, Any], **switches: bool) -> None:
    gc_state["file"].write_text(json.dumps({"face_recognition": {"retention_days": 7, **switches}}))


def test_the_nightly_describing_needs_the_plugin_and_its_switch(gc: dict[str, Any], plugin_on: dict[str, bool]) -> None:
    asyncio.run(vision_cleanup.run_vision_gc())
    assert gc["describe"] == 1                                  # both on: the default
    plugin_on["vision"] = False
    asyncio.run(vision_cleanup.run_vision_gc())
    assert gc["describe"] == 1                                  # plugin off: no describing
    plugin_on["vision"] = True
    _switch(gc, gc_describe=False)
    asyncio.run(vision_cleanup.run_vision_gc())
    assert gc["describe"] == 1                                  # switch off: none either


def test_pruning_past_the_retention_is_core_and_has_its_own_switch(gc: dict[str, Any], plugin_on: dict[str, bool]) -> None:
    plugin_on["vision"] = False
    asyncio.run(vision_cleanup.run_vision_gc())
    assert gc["prune"] > 0                                      # the plugin is off, the pictures are still pruned
    pruned = gc["prune"]
    _switch(gc, gc_prune=False)
    asyncio.run(vision_cleanup.run_vision_gc())
    assert gc["prune"] == pruned                                # its own switch stops it


# ── Endpoints ────────────────────────────────────────────────

@pytest.fixture
def client() -> TestClient:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    return test_client


def test_the_endpoints_of_vision_refuse_while_it_is_off(client: TestClient, plugin_on: dict[str, bool]) -> None:
    plugin_on["vision"] = False
    for method, path in (("get", "/vision/live"), ("get", "/vision/settings"), ("get", "/vision/face/list"),
                         ("get", "/vision/casus/events"), ("post", "/vision/enroll"), ("get", "/vision/alert-rules"),
                         ("get", "/vision/snapshot/cam")):
        response = getattr(client, method)(path)
        assert response.status_code == 409, path
        assert "ausgeschaltet" in response.json()["detail"] or "switched off" in response.json()["detail"]


def test_the_describer_list_is_not_visions(client: TestClient, plugin_on: dict[str, bool], monkeypatch: pytest.MonkeyPatch) -> None:
    """The image describer of the chat stands alone: its list answers with the vision plugin off."""
    plugin_on["vision"] = False
    monkeypatch.setattr("aifred.lib.api.describers._describers", lambda: {"rows": [], "measuring": ""})
    assert client.get("/describers").status_code == 200


def test_the_endpoints_of_the_player_refuse_while_it_is_off(client: TestClient, plugin_on: dict[str, bool]) -> None:
    plugin_on["audio_player"] = False
    for method, path in (("get", "/audio/file?key=a.mp3"), ("post", "/audio/position"),
                         ("post", "/audio/sources"), ("delete", "/audio/sources/x")):
        assert getattr(client, method)(path).status_code == 409, path


# ── Audio player ─────────────────────────────────────────────

class _Channel:
    def __init__(self, name: str, targets: list[str], failing: bool = False) -> None:
        self.name = name
        self.targets = targets
        self.failing = failing
        self.stopped: list[str] = []

    def running_targets(self) -> list[str]:
        return self.targets

    async def stop_player(self, target_id: str, ctx: Any = None) -> bool:
        if self.failing:
            raise RuntimeError("player gone")
        self.stopped.append(target_id)
        return True


def test_switching_the_player_off_stops_what_plays_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.plugins.tools.audio_player import plugin

    broken, local, browser = _Channel("a", ["x"], failing=True), _Channel("local", ["local"]), _Channel("browser", ["browser:s1", "browser:s2"])
    monkeypatch.setattr(audio_channels, "_REGISTRY", [broken, local, browser])
    asyncio.run(plugin.on_switched_off())
    assert local.stopped == ["local"] and browser.stopped == ["browser:s1", "browser:s2"]   # one failure stops no other


def test_the_player_names_its_cleanups() -> None:
    from aifred.lib.audio_index import sync_audio_index_task
    from aifred.lib.audio_state import cleanup_audio_state_task
    from aifred.plugins.tools.audio_player import plugin

    parts = plugin.background_parts()
    assert {name: part.factory for name, part in parts.items()} == {
        "audio_state_cleanup": cleanup_audio_state_task, "audio_index_sync": sync_audio_index_task,
    }
    assert not any(part.in_hub for part in parts.values())
