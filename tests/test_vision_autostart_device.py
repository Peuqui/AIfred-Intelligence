"""Autostart: eine USB-Kamera, die nicht angesteckt ist, ist keine registrierte Quelle — kein
Start, keine Warnung. Eine registrierte Quelle, die nicht startet, bleibt eine Warnung."""

import asyncio
import logging
from typing import Any

import pytest

import aifred.lib.vision_autostart as autostart
from aifred.lib import frame_sources


class FakeWatcher:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.started: list[str] = []

    async def start(self, source_id: str, config: Any) -> None:
        if self.fail:
            raise ValueError(f"unknown source: {source_id}")
        self.started.append(source_id)


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch):
    records = [
        {"source_id": "cam/usb", "kind": "webcam", "auto_start": True, "settings": {}},
        {"source_id": "cam/rtsp", "kind": "rtsp", "auto_start": True, "settings": {}},
    ]
    watcher = FakeWatcher()

    class FakeStore:
        def list_sources(self) -> list[dict[str, Any]]:
            return records

    monkeypatch.setattr("aifred.lib.vision_store.VisionStore", FakeStore)
    monkeypatch.setattr("aifred.lib.vision_watcher.get_default_watcher", lambda: watcher)
    monkeypatch.setattr(autostart, "load_vision_settings", lambda: {"vigilantia_armed": True})
    monkeypatch.setattr(autostart, "_build_background_config", lambda record, plugin: None)
    monkeypatch.setattr(frame_sources, "get", lambda source_id: None)  # no device registered
    return watcher


def test_an_unplugged_usb_camera_is_skipped_without_warning(setup: FakeWatcher, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        started = asyncio.run(autostart.start_all_background_watchers())
    assert (started, setup.started) == (1, ["cam/rtsp"])  # the network camera still starts
    assert [r.levelname for r in caplog.records if "cam/usb" in r.message] == ["INFO"]


def test_a_network_camera_that_does_not_start_stays_a_warning(setup: FakeWatcher, caplog: pytest.LogCaptureFixture) -> None:
    setup.fail = True
    with caplog.at_level(logging.INFO):
        asyncio.run(autostart.start_all_background_watchers())
    assert [r.levelname for r in caplog.records if "cam/rtsp" in r.message] == ["WARNING"]
