"""Tests for the Telegram channel's proactive-send helpers (target + media
resolution). The actual Bot send needs live verification."""

from __future__ import annotations

from pathlib import Path

from aifred.plugins.channels.telegram_channel import (
    _local_photo_path,
    _photo_url,
    _proactive_targets,
)


class TestProactiveTargets:
    def test_numeric_ids_parsed(self, monkeypatch):
        import aifred.plugins.channels.telegram_channel as tg
        monkeypatch.setattr(tg.broker, "get", lambda *a: "111, 222 , x, 333")
        assert _proactive_targets() == [111, 222, 333]

    def test_star_yields_no_concrete_target(self, monkeypatch):
        import aifred.plugins.channels.telegram_channel as tg
        monkeypatch.setattr(tg.broker, "get", lambda *a: "*")
        assert _proactive_targets() == []

    def test_empty_yields_none(self, monkeypatch):
        import aifred.plugins.channels.telegram_channel as tg
        monkeypatch.setattr(tg.broker, "get", lambda *a: "   ")
        assert _proactive_targets() == []


class TestMediaResolution:
    def test_local_path_when_file_exists(self, tmp_path: Path):
        f = tmp_path / "frame.jpg"
        f.write_bytes(b"x")
        assert _local_photo_path(str(f)) == str(f)
        assert _photo_url(str(f)) is None

    def test_missing_local_path_is_none(self):
        assert _local_photo_path("/nope/x.jpg") is None

    def test_url_recognised(self):
        url = "https://example.com/x.jpg"
        assert _photo_url(url) == url
        assert _local_photo_path(url) is None

    def test_none_media(self):
        assert _local_photo_path(None) is None
        assert _photo_url(None) is None
