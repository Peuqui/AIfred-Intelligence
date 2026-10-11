"""Tests für aifred.lib.vision_photo — hochauflösender Schnappschuss der Live-Ansicht."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from aifred.lib import vision_photo
from aifred.lib.frame_sources import Frame
from aifred.lib.vision_output import OutputChain

SOURCE = "cam/rtsp_test"
JPEG = b"\xff\xd8\xff" + b"x" * 200


def _frame(source_id: str = SOURCE, data: bytes = JPEG) -> Frame:
    return Frame(source_id=source_id, timestamp=datetime.now(), image_bytes=data)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    vigilantia = tmp_path / "vigilantia"
    monkeypatch.setattr(vision_photo, "VIGILANTIA_DIR", vigilantia)
    monkeypatch.setattr(vision_photo, "PHOTOS_DIR", vigilantia / "photos")
    monkeypatch.setattr(vision_photo, "load_zone_mask", lambda source_id: None)
    # Der Stempel öffnet den Default-Store und braucht ein echtes Bild.
    monkeypatch.setattr(OutputChain, "_stamp", staticmethod(lambda frame, suffix: frame))
    return vigilantia


def test_snap_capable_camera_uses_the_snap_api_and_stores_the_photo(monkeypatch, _env):
    snapped: list[str] = []

    async def fake_snap(source_id, n=1, interval=0.0, *, prefer_face=False):
        snapped.append(source_id)
        return [_frame(source_id)]

    monkeypatch.setattr(vision_photo, "resolve_snap_channel", lambda source_id: 0)
    monkeypatch.setattr(vision_photo, "snap_frames", fake_snap)
    url = asyncio.run(vision_photo.take_photo(SOURCE))
    assert snapped == [SOURCE]
    assert url.startswith("/_upload/vigilantia/photos/cam_rtsp_test/")
    assert url.endswith(".jpg")
    stored = _env / url.removeprefix("/_upload/vigilantia/")
    assert stored.read_bytes() == JPEG


def test_failed_snap_is_an_error(monkeypatch):
    async def fake_snap(*args, **kwargs):
        return None

    monkeypatch.setattr(vision_photo, "resolve_snap_channel", lambda source_id: 0)
    monkeypatch.setattr(vision_photo, "snap_frames", fake_snap)
    with pytest.raises(vision_photo.PhotoError):
        asyncio.run(vision_photo.take_photo(SOURCE))


def test_camera_without_snap_api_uses_the_hub_frame(monkeypatch, _env):
    class FakeHub:
        async def snapshot(self, source):
            return _frame(source.source_id, b"\xff\xd8hub" + b"y" * 100)

    class FakeSource:
        source_id = "cam/v4l2_0"

    monkeypatch.setattr(vision_photo, "resolve_snap_channel", lambda source_id: None)
    monkeypatch.setattr(vision_photo, "get_source", lambda source_id: FakeSource())
    monkeypatch.setattr(vision_photo, "get_default_hub", lambda: FakeHub())
    url = asyncio.run(vision_photo.take_photo("cam/v4l2_0"))
    stored = _env / url.removeprefix("/_upload/vigilantia/")
    assert stored.read_bytes().startswith(b"\xff\xd8hub")


def test_unknown_source_without_snap_is_an_error(monkeypatch):
    monkeypatch.setattr(vision_photo, "resolve_snap_channel", lambda source_id: None)
    monkeypatch.setattr(vision_photo, "get_source", lambda source_id: None)
    with pytest.raises(vision_photo.PhotoError):
        asyncio.run(vision_photo.take_photo("cam/gibt-es-nicht"))
