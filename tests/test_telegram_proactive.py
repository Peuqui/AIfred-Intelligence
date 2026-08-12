"""Tests for the Telegram channel's photo-media resolution (used by
send_reply when an alert carries an image). The actual Bot send needs live
verification."""

from __future__ import annotations

from pathlib import Path

from aifred.lib.vision_utils import local_media_path
from aifred.plugins.channels.telegram_channel import _photo_url


class TestMediaResolution:
    def test_local_path_when_file_exists(self, tmp_path: Path):
        f = tmp_path / "frame.jpg"
        f.write_bytes(b"x")
        assert local_media_path(str(f)) == str(f)
        assert _photo_url(str(f)) is None

    def test_missing_local_path_is_none(self):
        assert local_media_path("/nope/x.jpg") is None

    def test_url_recognised(self):
        url = "https://example.com/x.jpg"
        assert _photo_url(url) == url
        assert local_media_path(url) is None

    def test_none_media(self):
        assert local_media_path(None) is None
        assert _photo_url(None) is None
