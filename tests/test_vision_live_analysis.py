"""The live analysis of a camera (lib/vision_live_analysis.py) on the camera's one watcher, and the background watch.

Before 08.11.2026 the live view switched the watcher with its own config and, with both switches off, stopped it —
the background watch of an armed camera ended with it; when the last viewer left, a second rule (it did not ask
whether the vision was off) decided what ran next. Now the analysis is the server's, ends in one place, and gives
the camera back to ``apply_background_watch``; while it runs, the eye and the window supervisor leave it alone.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import frame_sources, ptz_control, vision_cameras, vision_event_bus, vision_live_analysis, vision_settings, vision_store
from aifred.lib.config import VISION_LIVE_ANALYSIS_MIN_FPS
from aifred.lib.frame_sources.base import SourceInfo
from aifred.lib.user_error import Missing, NotDone
from aifred.lib.vision_live_analysis import LiveAnalysis
from aifred.lib.vision_watch_types import WatchConfig

CAMERA = "cam/rtsp_garten"


class _Camera:
    display_name = "Garten"
    kind = "rtsp"
    source_id = CAMERA

    def info(self) -> SourceInfo:
        return SourceInfo(source_id=CAMERA, display_name=self.display_name, kind=self.kind, width=0, height=0, fps=None, available=True)


class _Watcher:
    """Runs one config per camera, as the real one: start does nothing while one runs."""

    def __init__(self) -> None:
        self.running: dict[str, WatchConfig] = {}
        self.refuse = False

    def is_running(self, source_id: str) -> bool:
        return source_id in self.running

    async def start(self, source_id: str, config: WatchConfig) -> None:
        if self.refuse:
            raise RuntimeError("camera does not answer")
        self.running.setdefault(source_id, config)

    async def stop(self, source_id: str) -> bool:
        return self.running.pop(source_id, None) is not None


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_Watcher, Path]]:
    from aifred.lib import vision_watcher

    settings_file = tmp_path / "vision_settings.json"
    settings_file.write_text(json.dumps({"vision_mode": "on-demand", "vigilantia_armed": True,
                                         "face_recognition": {"enabled": True, "continuous": True},
                                         "watch": {"run_person_detect_on_motion": True}}))
    monkeypatch.setattr(vision_settings, "VISION_SETTINGS_PATH", settings_file)
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    monkeypatch.setattr(vision_cameras, "PREVIEW_SETTINGS_FILE", tmp_path / "vision_preview.json")
    (tmp_path / "vision_preview.json").write_text(json.dumps({"fps": 0, "vlm_cooldown_sec": 7.0, "face_throttle_sec": 2.0}))
    monkeypatch.setattr(frame_sources, "list_all", lambda: [_Camera()])
    monkeypatch.setattr(frame_sources, "get", lambda source_id: _Camera() if source_id == CAMERA else None)
    monkeypatch.setattr(ptz_control, "get_controller", lambda _source_id: None)
    watcher = _Watcher()
    monkeypatch.setattr(vision_watcher, "get_default_watcher", lambda: watcher)
    store = vision_store.VisionStore()
    store.update_source_fields(CAMERA, fallback_display_name="Garten", fallback_kind="rtsp", auto_start=True)
    store.patch_source_settings(CAMERA, {"motion_min_area_ratio": 0.05})
    yield watcher, settings_file
    vision_live_analysis._running.clear()
    vision_event_bus._pending_teardown.clear()    # a test's loop is gone with its tasks
    vision_event_bus._subscribers.clear()


def test_the_analysis_runs_with_the_live_views_pace_and_the_eyes_face_settings(world: tuple[_Watcher, Path]) -> None:
    watcher, _ = world
    assert asyncio.run(vision_live_analysis.set_analysis(CAMERA, LiveAnalysis(vlm=True, faces=True))) == LiveAnalysis(True, True)
    config = watcher.running[CAMERA]
    assert (config.run_vlm_continuous, config.vlm_cooldown_sec, config.fps) == (True, 7.0, VISION_LIVE_ANALYSIS_MIN_FPS)
    assert (config.run_face_detect_on_motion, config.face_recognition_continuous, config.run_person_detect_on_motion) == (True, True, True)
    assert (config.min_event_interval_sec, config.motion_min_area_ratio) == (2.0, 0.05)
    assert vision_live_analysis.is_live(CAMERA)


def test_ending_it_gives_an_armed_camera_back_to_its_background_watch(world: tuple[_Watcher, Path]) -> None:
    watcher, _ = world
    asyncio.run(vision_live_analysis.set_analysis(CAMERA, LiveAnalysis(vlm=True)))
    assert asyncio.run(vision_live_analysis.set_analysis(CAMERA, LiveAnalysis())) == LiveAnalysis()
    background = watcher.running[CAMERA]                       # running again, with the background's config
    assert not background.run_vlm_continuous and background.save_event_frames and not vision_live_analysis.is_live(CAMERA)


def test_ending_it_on_an_unarmed_camera_stops_the_watcher(world: tuple[_Watcher, Path]) -> None:
    watcher, settings_file = world
    settings_file.write_text(json.dumps({"vision_mode": "on-demand", "vigilantia_armed": False}))
    asyncio.run(vision_live_analysis.set_analysis(CAMERA, LiveAnalysis(faces=True)))
    asyncio.run(vision_live_analysis.set_analysis(CAMERA, LiveAnalysis()))
    assert watcher.running == {}


def test_the_background_decisions_leave_a_live_analysis_alone(world: tuple[_Watcher, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import vision_autostart

    watcher, settings_file = world
    asyncio.run(vision_live_analysis.set_analysis(CAMERA, LiveAnalysis(vlm=True)))
    settings_file.write_text(json.dumps({"vision_mode": "on-demand", "vigilantia_armed": False}))
    asyncio.run(vision_autostart.apply_background_watch(CAMERA))       # the eye unarmed: it would stop the camera
    asyncio.run(vision_autostart.stop_all_background_watchers())
    settings_file.write_text(json.dumps({"vision_mode": "on-demand", "vigilantia_armed": True}))
    asyncio.run(vision_autostart.start_all_background_watchers())
    assert watcher.running[CAMERA].run_vlm_continuous


def test_an_unknown_camera_and_a_watcher_that_does_not_start_are_said(world: tuple[_Watcher, Path]) -> None:
    watcher, _ = world
    with pytest.raises(Missing):
        asyncio.run(vision_live_analysis.set_analysis("cam/keine", LiveAnalysis(vlm=True)))
    watcher.refuse = True
    with pytest.raises(NotDone) as refused:
        asyncio.run(vision_live_analysis.set_analysis(CAMERA, LiveAnalysis(vlm=True)))
    assert refused.value.values["reason"] == "camera does not answer" and not vision_live_analysis.is_live(CAMERA)


def test_the_last_viewer_leaving_ends_it(world: tuple[_Watcher, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    watcher, _ = world
    monkeypatch.setattr(vision_event_bus, "_IDLE_GRACE_SEC", 0.0)

    async def follow() -> None:
        async for _event in vision_event_bus.subscribe(CAMERA):
            pass

    async def follow_and_leave() -> None:
        await vision_live_analysis.set_analysis(CAMERA, LiveAnalysis(vlm=True))
        page = asyncio.create_task(follow())
        await asyncio.sleep(0.01)
        page.cancel()                                   # the page closes: its stream ends
        await asyncio.sleep(0.05)

    asyncio.run(follow_and_leave())
    assert not vision_live_analysis.is_live(CAMERA) and not watcher.running[CAMERA].run_vlm_continuous


def test_the_page_switches_it_and_every_page_sees_it(world: tuple[_Watcher, Path]) -> None:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    with TestClient(api_app) as client:
        client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        assert client.put(f"/vision/live/analysis/{CAMERA}", json={"vlm": True, "faces": False}).json() == {"vlm": True, "faces": False}
        assert client.get("/vision/live").json()["analysis"] == {CAMERA: {"vlm": True, "faces": False}}
        assert client.put("/vision/live/analysis/cam/keine", json={"vlm": True, "faces": False}).status_code == 404
        assert client.put(f"/vision/live/analysis/{CAMERA}", json={"vlm": False, "faces": False}).json() == {"vlm": False, "faces": False}
        assert client.get("/vision/live").json()["analysis"] == {}


def test_a_new_pace_reaches_a_running_analysis_at_once(world: tuple[_Watcher, Path]) -> None:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    watcher, _ = world
    with TestClient(api_app) as client:
        client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        client.put(f"/vision/live/analysis/{CAMERA}", json={"vlm": True, "faces": True})
        assert watcher.running[CAMERA].vlm_cooldown_sec == 7.0
        view = client.patch("/vision/live", json={"vlm_cooldown_sec": 30, "face_throttle_sec": 0.5}).json()
        assert (watcher.running[CAMERA].vlm_cooldown_sec, watcher.running[CAMERA].min_event_interval_sec) == (30.0, 0.5)
        assert [choice["label"] for choice in view["face_throttle_choices"]][:2] == ["0,1 s · 10/s", "0,2 s · 5/s"]
        assert view["analysis"] == {CAMERA: {"vlm": True, "faces": True}}


def test_an_analysis_nobody_follows_ends_after_the_grace(world: tuple[_Watcher, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    watcher, _ = world
    monkeypatch.setattr(vision_event_bus, "_IDLE_GRACE_SEC", 0.0)

    async def start_and_wait() -> None:
        await vision_live_analysis.set_analysis(CAMERA, LiveAnalysis(faces=True))
        await asyncio.sleep(0.05)

    asyncio.run(start_and_wait())
    background = watcher.running[CAMERA]                       # its own event interval, not the live view's 2 s
    assert not vision_live_analysis.is_live(CAMERA) and background.min_event_interval_sec == WatchConfig().min_event_interval_sec
