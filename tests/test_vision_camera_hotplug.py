"""Eine später angesteckte USB-Kamera: ``connect_device`` meldet nur dieses Gerät an (kein Neuscan aller
Geräte, der eine laufende Kamera stören würde), und der Supervisor startet ihren Watcher."""

import asyncio
from typing import Any

import pytest

import aifred.lib.vision_autostart as autostart
from aifred.lib import frame_sources
from aifred.lib.frame_sources import v4l2_source

USB = "cam/image_ugreen_camera_4k_ll_0000000001"


class FakeV4L2Source:
    kind = "webcam"

    def __init__(self, index: int, display_name: str, source_id: str) -> None:
        self.index, self.display_name, self.source_id = index, display_name, source_id

    def detect_resolutions(self) -> None:
        pass


@pytest.fixture
def plugged(monkeypatch: pytest.MonkeyPatch):
    registered: dict[str, Any] = {}
    probed: list[int] = []

    def can_capture(index: int) -> bool:
        probed.append(index)
        return True

    monkeypatch.setattr(v4l2_source, "_enumerate_devices", lambda: [(0, "Running cam", "cam/other"), (2, "UGREEN", USB)])
    monkeypatch.setattr(v4l2_source, "_can_capture", can_capture)
    monkeypatch.setattr(v4l2_source, "V4L2Source", FakeV4L2Source)
    monkeypatch.setattr(v4l2_source, "get", registered.get)
    monkeypatch.setattr(v4l2_source, "register", lambda source: registered.update({source.source_id: source}))
    return registered, probed


def test_connect_device_registers_only_the_named_device(plugged) -> None:
    registered, probed = plugged
    assert v4l2_source.connect_device(USB) is True
    assert list(registered) == [USB] and probed == [2]  # the other camera is not even probed


def test_connect_device_does_nothing_for_a_registered_or_absent_device(plugged) -> None:
    registered, probed = plugged
    assert v4l2_source.connect_device("cam/not-there") is False
    registered[USB] = object()
    assert v4l2_source.connect_device(USB) is False
    assert probed == []


def test_the_supervisor_starts_a_camera_that_was_plugged_in_later(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []
    connected: list[str] = []

    async def start(source_id: str) -> bool:
        started.append(source_id)
        return True

    def connect(source_id: str) -> bool:
        connected.append(source_id)
        return source_id == USB

    monkeypatch.setattr(frame_sources, "get", lambda source_id: None)
    monkeypatch.setattr(autostart, "start_background_watcher", start)
    monkeypatch.setattr(v4l2_source, "connect_device", connect)
    records = [
        {"source_id": USB, "kind": "webcam", "auto_start": True},
        {"source_id": "cam/usb2", "kind": "webcam", "auto_start": True},  # still not plugged in
        {"source_id": "cam/rtsp", "kind": "rtsp", "auto_start": True},  # not a USB camera
        {"source_id": "cam/manual", "kind": "webcam", "auto_start": False},
    ]
    asyncio.run(autostart._start_newly_plugged_cameras(records))
    assert (connected, started) == ([USB, "cam/usb2"], [USB])
