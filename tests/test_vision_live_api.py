"""The live view of the vision page (lib/api/cameras.py over lib/vision_cameras.py): one camera list with the stored
name, briefing and resolution, the view's settings checked and kept, the resolutions a camera offers, the head of a
camera (moved only where it can move) and the photo. Cameras, vision.db and the settings file are stand-ins."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import frame_sources, ptz_control, vision_cameras, vision_store
from aifred.lib.frame_sources.base import SourceInfo
from aifred.lib.user_error import Invalid


class _Camera:
    kind = "webcam"

    def __init__(self, source_id: str, name: str, available: bool = True, modes: list[tuple[int, int]] | None = None) -> None:
        self.source_id = source_id
        self.display_name = name
        self._available = available
        self._modes = modes or []

    def info(self) -> SourceInfo:
        return SourceInfo(source_id=self.source_id, display_name=self.display_name, kind=self.kind, width=640, height=480,
                          fps=1.0, available=self._available)

    def detect_resolutions(self) -> list[tuple[int, int]]:
        return self._modes


CAMERAS = [
    _Camera("cam/v4l2_0", "USB Kamera", available=False),
    _Camera("cam/rtsp_garten", "Reolink Garten", modes=[(640, 480), (1920, 1080), (2304, 1296)]),
]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    monkeypatch.setattr(vision_cameras, "PREVIEW_SETTINGS_FILE", tmp_path / "vision_preview.json")
    monkeypatch.setattr(frame_sources, "list_all", lambda: list(CAMERAS))
    monkeypatch.setattr(frame_sources, "get", lambda source_id: next((c for c in CAMERAS if c.source_id == source_id), None))
    monkeypatch.setattr(frame_sources, "rescan", lambda: None)
    monkeypatch.setattr(ptz_control, "get_controller", lambda source_id: object() if source_id == "cam/rtsp_garten" else None)
    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_one_list_with_the_stored_name_and_briefing_and_the_first_available_camera_shown(client: TestClient) -> None:
    store = vision_store.VisionStore()
    store.patch_source_settings("cam/rtsp_garten", {"alias": "Garten"})
    store.update_source_fields("cam/rtsp_garten", prompt_context="Rasen, Gartentor rechts")
    live = client.get("/vision/live").json()
    cameras = {camera["id"]: camera for camera in live["cameras"]}
    assert cameras["cam/rtsp_garten"]["label"] == "Garten" and cameras["cam/rtsp_garten"]["prompt_context"] == "Rasen, Gartentor rechts"
    assert cameras["cam/v4l2_0"]["label"] == "USB Kamera" and not cameras["cam/v4l2_0"]["available"]
    assert (cameras["cam/rtsp_garten"]["ptz"], cameras["cam/v4l2_0"]["ptz"]) == (True, False)
    assert live["visible"] == ["cam/rtsp_garten"]                          # nothing chosen yet: the first available
    assert live["fps_choices"][0]["label"] == "Einzelbild (von Hand)"
    assert {"id": "0.2", "label": "0,2 fps · 1 Bild / 5 s"} in live["fps_choices"]


def test_the_views_settings_are_checked_and_an_empty_choice_stays_empty(client: TestClient, tmp_path: Path) -> None:
    assert client.patch("/vision/live", json={"visible_sources": [], "fps": 2}).json()["visible"] == []
    before = (tmp_path / "vision_preview.json").read_text()
    assert client.patch("/vision/live", json={"fps": 31}).status_code == 422
    assert client.patch("/vision/live", json={"teleprompter_mode": "beside"}).status_code == 422
    assert (tmp_path / "vision_preview.json").read_text() == before
    assert json.loads(before)["fps"] == 2.0


def test_a_broken_settings_file_is_an_error_not_a_default(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "vision_preview.json").write_text(json.dumps({"fps": 99}))
    with pytest.raises(Invalid) as refused:
        vision_cameras.preview_settings()
    assert (refused.value.key, refused.value.values["value"]) == ("number_range", 99)


def test_a_camera_offers_its_drivers_modes_and_keeps_only_one_of_them(client: TestClient) -> None:
    offered = client.get("/vision/cameras/cam/rtsp_garten/resolutions").json()
    assert [option["label"] for option in offered] == ["Treiber-Standard", "640 × 480 (VGA)", "1920 × 1080 (Full HD)", "2304 × 1296"]
    assert client.get("/vision/cameras/cam/v4l2_0/resolutions").json() == [{"id": "default", "label": "Treiber-Standard"}]   # away
    assert client.patch("/vision/cameras/cam/rtsp_garten", json={"resolution": "800x600"}).status_code == 422
    changed = client.patch("/vision/cameras/cam/rtsp_garten", json={"resolution": "1920x1080", "alias": " Garten "}).json()
    assert (changed["resolution"], changed["label"]) == ("1920x1080", "Garten")
    assert client.get("/vision/cameras/cam/keine/resolutions").status_code == 404


def test_the_head_moves_only_where_it_can_and_with_a_known_speed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    async def press(source_id: str, direction: str, speed: str) -> None:
        if ptz_control.get_controller(source_id) is None:
            raise ptz_control.PTZError(f"{source_id} hat keine PTZ-Steuerung")
        calls.append(("press", source_id, direction, speed))

    async def release(source_id: str) -> None:
        calls.append(("release", source_id))

    monkeypatch.setattr(ptz_control.ptz_driver, "press", press)
    monkeypatch.setattr(ptz_control.ptz_driver, "release", release)
    assert client.post("/vision/ptz/cam/rtsp_garten/press", json={"direction": "left", "speed": "fine"}).status_code == 204
    assert client.post("/vision/ptz/cam/rtsp_garten/release").status_code == 204
    assert calls == [("press", "cam/rtsp_garten", "left", "fine"), ("release", "cam/rtsp_garten")]
    assert client.post("/vision/ptz/cam/rtsp_garten/press", json={"direction": "left", "speed": "warp"}).status_code == 422
    assert client.post("/vision/ptz/cam/rtsp_garten/press", json={"direction": "sideways", "speed": "fine"}).status_code == 422
    assert client.post("/vision/ptz/cam/v4l2_0/press", json={"direction": "up", "speed": "fine"}).status_code == 409


def test_a_photo_answers_with_its_url_or_says_why_not(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import vision_photo

    async def taken(source_id: str) -> str:
        if source_id == "cam/v4l2_0":
            raise vision_photo.PhotoError("Schnappschuss fehlgeschlagen")
        return "/_upload/vigilantia/photos/garten/2026-11-07/a.jpg"

    monkeypatch.setattr(vision_photo, "take_photo", taken)
    assert client.post("/vision/photo/cam/rtsp_garten").json() == {"url": "/_upload/vigilantia/photos/garten/2026-11-07/a.jpg"}
    assert client.post("/vision/photo/cam/v4l2_0").status_code == 502


def test_the_trigger_threshold_is_a_camera_setting_a_running_watch_takes_at_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from aifred.lib import vision_watcher
    from aifred.lib.config import VISION_MOTION_MIN_AREA_RANGE

    reloaded: list[tuple[str, float]] = []
    watcher = SimpleNamespace(reload_motion_min=lambda source_id, ratio: reloaded.append((source_id, ratio)))
    monkeypatch.setattr(vision_watcher, "get_default_watcher", lambda: watcher)
    changed = client.patch("/vision/cameras/cam/rtsp_garten", json={"motion_min_area_ratio": 0.05}).json()
    assert changed["motion_min_area_ratio"] == 0.05 and reloaded == [("cam/rtsp_garten", 0.05)]
    too_much = VISION_MOTION_MIN_AREA_RANGE[1] * 2
    assert client.patch("/vision/cameras/cam/rtsp_garten", json={"motion_min_area_ratio": too_much}).status_code == 422
    assert client.post("/vision/motion-min", json={"source_id": "cam/rtsp_garten", "motion_min_area_ratio": 0.03}).status_code == 200
    assert client.get("/vision/motion-min", params={"source_id": "cam/rtsp_garten"}).json()["motion_min_area_ratio"] == 0.03
    assert reloaded[-1] == ("cam/rtsp_garten", 0.03)                     # the Reflex editor's way is the same


def test_a_camera_that_detects_on_its_own_says_so(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib.frame_sources import rtsp_source

    monkeypatch.setattr(rtsp_source, "find_camera_config",
                        lambda source_id: {"profile": "ai_camera"} if source_id == "cam/rtsp_garten" else None)
    cameras = {camera["id"]: camera["detects_itself"] for camera in client.get("/vision/live").json()["cameras"]}
    assert cameras == {"cam/rtsp_garten": True, "cam/v4l2_0": False}


def test_the_first_setting_of_a_camera_keeps_its_name_and_an_unknown_one_stays_unknown(client: TestClient) -> None:
    # The store knew neither camera: the first change used to file it under its id as its name.
    assert vision_store.VisionStore().get_source("cam/rtsp_garten") is None
    changed = client.patch("/vision/cameras/cam/rtsp_garten", json={"quiet_enabled": True}).json()
    assert (changed["label"], changed["hardware_name"]) == ("Reolink Garten", "Reolink Garten")
    stored = vision_store.VisionStore().get_source("cam/rtsp_garten")
    assert stored is not None and (stored["display_name"], stored["kind"]) == ("Reolink Garten", "webcam")
    assert client.patch("/vision/cameras/cam/keine", json={"quiet_enabled": True}).status_code == 404
    assert vision_store.VisionStore().get_source("cam/keine") is None                   # nothing filed for it
