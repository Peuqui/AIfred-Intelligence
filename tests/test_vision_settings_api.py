"""The vision page's settings (lib/api/vigilantia.py, PATCH /vision/cameras): the eye armed and disarmed with every
background watcher, arming refused without the plugin, mode and model with their consequences (live loads, a new
model unloads the old Ollama one), the general settings checked; a camera's alerts, quiet hours, window and event
interval checked and kept, its background switch and window applied at once. Cameras, vision.db, the plugin's
settings.json, the watcher and the model backend are stand-ins."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import frame_sources, ptz_control, vision_settings, vision_store
from aifred.lib.frame_sources.base import SourceInfo


class _Camera:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    def info(self) -> SourceInfo:
        return SourceInfo(source_id=self.source_id, display_name="Reolink", kind="rtsp", width=0, height=0, fps=None, available=True)


class _Watcher:
    def __init__(self) -> None:
        self.running: set[str] = set()

    def is_running(self, source_id: str) -> bool:
        return source_id in self.running

    async def stop(self, source_id: str) -> None:
        self.running.discard(source_id)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, dict[str, Any], Path]]:
    from aifred.lib import plugin_registry, vision_autostart, vision_prewarm, vision_routing, vision_watcher
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    settings_file = tmp_path / "vision_settings.json"
    settings_file.write_text(json.dumps({"vision_mode": "on-demand", "vigilantia_armed": False, "vlm": {"model": "qwen3-vl:4b-instruct-q8_0"},
                                         "face_recognition": {"enabled": True, "retention_days": 7}}))
    monkeypatch.setattr(vision_settings, "VISION_SETTINGS_PATH", settings_file)
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    camera = _Camera("cam/rtsp_reolink")
    monkeypatch.setattr(frame_sources, "list_all", lambda: [camera])
    monkeypatch.setattr(frame_sources, "get", lambda source_id: camera if source_id == camera.source_id else None)
    monkeypatch.setattr(ptz_control, "get_controller", lambda _source_id: None)
    world: dict[str, Any] = {"plugin": True, "calls": [], "watcher": _Watcher()}
    monkeypatch.setattr(plugin_registry, "is_plugin_enabled", lambda name: world["plugin"] if name == "vision" else True)
    monkeypatch.setattr(vision_watcher, "get_default_watcher", lambda: world["watcher"])

    async def start_one(source_id: str) -> bool:
        world["watcher"].running.add(source_id)
        return True

    async def start_all() -> int:
        world["calls"].append("start_all")
        return 1

    async def stop_all() -> int:
        world["calls"].append("stop_all")
        return 1

    async def prewarm(**_kwargs: Any) -> bool:
        world["calls"].append("prewarm")
        return True

    async def unload(model: str, host: str | None = None) -> bool:
        world["calls"].append(f"unload {model}")
        return True

    monkeypatch.setattr(vision_autostart, "start_background_watcher", start_one)
    monkeypatch.setattr(vision_autostart, "start_all_background_watchers", start_all)
    monkeypatch.setattr(vision_autostart, "stop_all_background_watchers", stop_all)
    monkeypatch.setattr(vision_prewarm, "prewarm_vlm", prewarm)
    monkeypatch.setattr(vision_prewarm, "unload_vlm_model", unload)
    monkeypatch.setattr(vision_routing, "image_models", lambda: ["Qwen3VL-4B-Instruct-Q8_0", "qwen3-vl:8b"])
    # The 4B one has a llama-swap describer profile (llama-swap swaps it itself), the 8B one is Ollama's only.
    monkeypatch.setattr(vision_routing, "visiond_profile_for", lambda name: "Qwen3VL-4B-visiond" if "4b" in name.lower() else None)
    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client, world, settings_file


def test_the_eye_arms_and_disarms_every_background_watcher_and_needs_the_plugin(world: tuple[TestClient, dict[str, Any], Path]) -> None:
    client, state, settings_file = world
    assert client.put("/vision/armed", json={"armed": True}).json()["armed"] is True
    assert client.put("/vision/armed", json={"armed": False}).json()["armed"] is False
    assert state["calls"] == ["start_all", "stop_all"]
    state["plugin"] = False
    assert client.put("/vision/armed", json={"armed": True}).status_code == 409
    assert json.loads(settings_file.read_text())["vigilantia_armed"] is False


def test_the_settings_show_the_listed_model_and_change_with_their_consequences(world: tuple[TestClient, dict[str, Any], Path]) -> None:
    client, state, settings_file = world
    shown = client.get("/vision/settings").json()
    assert shown["model"] == "Qwen3VL-4B-Instruct-Q8_0"          # the Ollama spelling read as the listed model
    assert [kind["id"] for kind in shown["alert_types"]] == ["person", "vehicle", "animal", "face"]
    client.patch("/vision/settings", json={"mode": "live", "model": "qwen3-vl:8b", "face_continuous": True, "retention_days": 30})
    assert state["calls"] == ["prewarm"]                          # live loads; the old llama-swap model swaps itself
    client.patch("/vision/settings", json={"model": "Qwen3VL-4B-Instruct-Q8_0"})
    assert state["calls"] == ["prewarm", "unload qwen3-vl:8b"]    # the old Ollama model leaves the card
    stored = json.loads(settings_file.read_text())
    assert (stored["vision_mode"], stored["vlm"]["model"]) == ("live", "Qwen3VL-4B-Instruct-Q8_0")
    assert stored["face_recognition"] == {"enabled": True, "retention_days": 30, "continuous": True}   # the rest stays
    assert client.patch("/vision/settings", json={"mode": "off"}).status_code == 422
    assert client.patch("/vision/settings", json={"retention_days": 0}).status_code == 422


def test_a_cameras_alert_settings_are_checked_and_kept(world: tuple[TestClient, dict[str, Any], Path]) -> None:
    client, _state, _file = world
    changed = client.patch("/vision/cameras/cam/rtsp_reolink", json={
        "alerts_enabled": False, "alert_types": ["face", "person"], "quiet_enabled": True, "quiet_start": 23, "quiet_end": 5,
        "min_event_interval_sec": 3,
    }).json()
    assert (changed["alerts_enabled"], changed["alert_types"], changed["quiet_start"], changed["quiet_end"]) == (False, ["person", "face"], 23, 5)
    assert changed["min_event_interval_sec"] == 3.0
    for wrong in ({"alert_types": ["ufo"]}, {"quiet_start": 24}, {"schedule_start": "25:00"}, {"min_event_interval_sec": 0},
                  {"sensitivity": 1}):
        assert client.patch("/vision/cameras/cam/rtsp_reolink", json=wrong).status_code == 422, wrong


def test_the_background_switch_and_the_window_take_effect_at_once(world: tuple[TestClient, dict[str, Any], Path]) -> None:
    client, state, _file = world
    client.patch("/vision/cameras/cam/rtsp_reolink", json={"auto_start": True})
    assert state["watcher"].running == set()                      # the eye is not armed: nothing starts
    client.put("/vision/armed", json={"armed": True})
    client.patch("/vision/cameras/cam/rtsp_reolink", json={"auto_start": True})
    assert state["watcher"].running == {"cam/rtsp_reolink"}
    # a window that does not hold this minute stops it
    from datetime import datetime, timedelta

    now = datetime.now()
    later = (now + timedelta(hours=2)).strftime("%H:%M"), (now + timedelta(hours=3)).strftime("%H:%M")
    client.patch("/vision/cameras/cam/rtsp_reolink", json={"schedule_enabled": True, "schedule_start": later[0], "schedule_end": later[1]})
    assert state["watcher"].running == set()
    client.patch("/vision/cameras/cam/rtsp_reolink", json={"schedule_enabled": False})
    assert state["watcher"].running == {"cam/rtsp_reolink"}


def test_the_schedule_supervisor_decides_through_the_same_place(world: tuple[TestClient, dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import vision_autostart

    decided: list[str] = []

    async def apply(source_id: str) -> None:
        decided.append(source_id)
        raise asyncio.CancelledError   # one round is enough

    async def no_wait(_seconds: float) -> None:
        return None

    client, _state, _file = world
    client.patch("/vision/cameras/cam/rtsp_reolink", json={"auto_start": True, "schedule_enabled": True})
    client.put("/vision/armed", json={"armed": True})
    monkeypatch.setattr(vision_autostart, "apply_background_watch", apply)
    monkeypatch.setattr(vision_autostart.asyncio, "sleep", no_wait)

    async def plugged(_records: list[dict[str, Any]]) -> None:
        return None

    monkeypatch.setattr(vision_autostart, "_start_newly_plugged_cameras", plugged)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(vision_autostart.schedule_supervisor())
    assert decided == ["cam/rtsp_reolink"]


def test_an_alert_rule_switches_a_channel_and_the_description_and_the_dispatcher_reads_it_at_once(
    world: tuple[TestClient, dict[str, Any], Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aifred.lib import alert_bus, plugin_base, plugin_registry

    client, _state, _file = world
    rules_file = tmp_path / "alert_rules.json"
    rules_file.write_text(json.dumps([{"rule_id": "other", "producer": "calendar", "sinks": ["email"]}]))
    monkeypatch.setattr(alert_bus, "_rules_path", lambda: rules_file)
    monkeypatch.setattr(plugin_registry, "all_channels", lambda: {"telegram": object(), "freeecho2": object()})
    monkeypatch.setattr(plugin_base, "plugin_display_name", lambda plugin, lang: "Kanal")
    reloads: list[int] = []

    def reload_rules() -> int:
        reloads.append(1)
        return 0

    monkeypatch.setattr(alert_bus, "reload_rules", reload_rules)

    shown = client.get("/vision/alert-rules").json()
    assert [rule["category"] for rule in shown["rules"]] == ["person", "vehicle", "animal", "face_known", "face_unsure", "face_unknown"]
    assert shown["rules"][0] == {"category": "person", "label": "Person", "sinks": [], "vlm": False}
    after = client.patch("/vision/alert-rules/person", json={"sink": "telegram", "sink_on": True, "vlm": True}).json()
    assert after["rules"][0]["sinks"] == ["telegram"] and after["rules"][0]["vlm"] is True and reloads == [1]
    client.patch("/vision/alert-rules/person", json={"sink": "telegram", "sink_on": False})
    stored = json.loads(rules_file.read_text())
    assert stored[0] == {"rule_id": "other", "producer": "calendar", "sinks": ["email"]}        # another producer's rule stays
    assert stored[1] == {"rule_id": "vision-person", "producer": "vision", "category": "person", "source_id": None, "sinks": [], "compose": "vlm"}
    assert client.patch("/vision/alert-rules/ufo", json={"vlm": True}).status_code == 422
    assert client.patch("/vision/alert-rules/person", json={"sink": "fax", "sink_on": True}).status_code == 422
    assert client.patch("/vision/alert-rules/person", json={"sink": "telegram"}).status_code == 422


@pytest.fixture
def rtsp(world: tuple[TestClient, dict[str, Any], Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path, Path]:
    """The vision settings with the PTZ camera as it stands in the production (fields the old form did not know),
    a .env of the test, the environment restored after it."""
    from aifred.lib import config

    client, _state, settings_file = world
    settings = json.loads(settings_file.read_text())
    settings["rtsp_cameras"] = [{"name": "Reolink", "host": "192.0.2.10", "port": 554, "path": "h264Preview_01_main", "cred": "reolink",
                                 "profile": "ai_camera", "api_port": 443, "onvif_port": 8000, "face_channel": 1, "snap_channel": 1, "ptz": True}]
    settings_file.write_text(json.dumps(settings))
    env_file = tmp_path / ".env"
    env_file.write_text("RTSP_REOLINK_USER=admin\nRTSP_REOLINK_PASSWORD=geheim\n")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(frame_sources, "rescan", lambda: None)
    for key in ("RTSP_REOLINK_USER", "RTSP_REOLINK_PASSWORD", "RTSP_GARTEN_USER", "RTSP_GARTEN_PASSWORD"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("RTSP_REOLINK_USER", "admin")
    monkeypatch.setenv("RTSP_REOLINK_PASSWORD", "geheim")
    return client, settings_file, env_file


def test_rtsp_cameras_are_listed_without_their_secrets(rtsp: tuple[TestClient, Path, Path]) -> None:
    client, _settings, _env = rtsp
    listed = client.get("/vision/rtsp-cameras")
    camera = listed.json()["cameras"][0]
    assert (camera["source_id"], camera["ptz"], camera["user_set"], camera["password_set"]) == ("cam/rtsp_reolink", True, True, True)
    assert "geheim" not in listed.text and "admin" not in listed.text


def test_changing_the_connection_keeps_what_the_form_does_not_know(rtsp: tuple[TestClient, Path, Path]) -> None:
    # 07.11.2026: the old form rebuilt the entry and lost ptz, onvif_port and snap_channel of the real camera.
    client, settings_file, env_file = rtsp
    assert client.put("/vision/rtsp-cameras/Reolink", json={"host": "192.0.2.11", "path": ""}).status_code == 200
    entry = json.loads(settings_file.read_text())["rtsp_cameras"][0]
    assert entry["host"] == "192.0.2.11" and "path" not in entry
    assert (entry["ptz"], entry["onvif_port"], entry["snap_channel"], entry["face_channel"]) == (True, 8000, 1, 1)
    assert "RTSP_REOLINK_PASSWORD=geheim" in env_file.read_text().replace("'", "")       # an empty field leaves the secret
    assert client.put("/vision/rtsp-cameras/Keine", json={"host": "x"}).status_code == 404
    for wrong in ({"port": 70000}, {"cred": "Böse-Name"}, {"profile": "ufo"}, {"host": ""}, {"zoom": 2}):
        assert client.put("/vision/rtsp-cameras/Reolink", json=wrong).status_code == 422, wrong


def test_a_new_camera_needs_a_new_name_and_its_secrets_go_to_the_env(rtsp: tuple[TestClient, Path, Path]) -> None:
    import os

    client, settings_file, env_file = rtsp
    added = client.post("/vision/rtsp-cameras", json={"name": "Garten", "host": "192.0.2.20", "cred": "garten", "user": "gast", "password": "pw"})
    assert added.status_code == 201
    assert [camera["name"] for camera in added.json()["cameras"]] == ["Reolink", "Garten"]
    assert "RTSP_GARTEN_PASSWORD" in env_file.read_text() and os.environ["RTSP_GARTEN_PASSWORD"] == "pw"
    assert json.loads(settings_file.read_text())["rtsp_cameras"][1] == {"name": "Garten", "host": "192.0.2.20", "cred": "garten",
                                                                         "profile": "webcam", "port": 554}
    assert client.post("/vision/rtsp-cameras", json={"name": "Garten", "host": "192.0.2.21"}).status_code == 422


def test_taking_a_camera_out_forgets_secrets_no_other_camera_uses(rtsp: tuple[TestClient, Path, Path]) -> None:
    import os

    client, _settings, env_file = rtsp
    client.post("/vision/rtsp-cameras", json={"name": "Zoom", "host": "192.0.2.10", "cred": "reolink"})   # shares the secrets
    client.delete("/vision/rtsp-cameras/Reolink")
    assert "RTSP_REOLINK_PASSWORD" in env_file.read_text()             # Zoom still uses them
    assert [camera["name"] for camera in client.delete("/vision/rtsp-cameras/Zoom").json()["cameras"]] == []
    assert "RTSP_REOLINK" not in env_file.read_text() and "RTSP_REOLINK_PASSWORD" not in os.environ
    assert client.delete("/vision/rtsp-cameras/Zoom").status_code == 404
