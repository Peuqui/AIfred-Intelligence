"""Telegram-Zustellung eines Burst-Kapitels: die Keyframes gehen als EIN Album
(der Mini-Film des Vorkommnisses), Caption am ersten Bild, Rest als Text."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aifred.plugins.channels.telegram_channel as tg
from aifred.lib.video_split import split_video
from video_fixtures import make_video


class _FakeBot:
    """Zeichnet die Sendeaufrufe auf; ``async with bot`` wie beim echten Bot."""

    def __init__(self) -> None:
        self.groups: list[list] = []
        self.photos: list[dict] = []
        self.videos: list[dict] = []
        self.texts: list[str] = []
        self._next_id = 100

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def _msg(self):
        self._next_id += 1
        return SimpleNamespace(message_id=self._next_id)

    async def send_media_group(self, chat_id, media):
        self.groups.append(list(media))
        return [self._msg() for _ in media]

    async def send_video(self, chat_id, video, caption, filename, supports_streaming=False):
        self.videos.append({"caption": caption, "streaming": supports_streaming, "filename": filename})
        return self._msg()

    async def send_photo(self, chat_id, photo, caption, filename):
        self.photos.append({"caption": caption, "filename": filename})
        return self._msg()

    async def send_message(self, chat_id, text):
        self.texts.append(text)
        return self._msg()


def _images(tmp_path, count: int) -> list[str]:
    paths = []
    for index in range(count):
        path = tmp_path / f"frame{index}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xd9")
        paths.append(str(path))
    return paths


def _deliver(tmp_path, bot: _FakeBot, text: str, **kwargs) -> list[int]:
    channel = tg.TelegramChannel()
    with patch.object(tg, "_MSGLOG_FILE", tmp_path / "msglog.json"):
        return asyncio.run(channel._deliver(bot, 1, text, kwargs.pop("media", None), **kwargs))


class TestAlbum:
    def test_chapter_frames_go_out_as_one_album_in_order(self, tmp_path):
        bot = _FakeBot()
        frames = _images(tmp_path, 4)
        ids = _deliver(tmp_path, bot, "Bilanz", media_album=frames)
        assert len(bot.groups) == 1 and len(bot.groups[0]) == 4
        assert bot.groups[0][0].caption == "Bilanz"
        assert all(item.caption is None for item in bot.groups[0][1:])
        assert [item.media.filename for item in bot.groups[0]] == [f"frame{index}.jpg" for index in range(4)]
        assert len(ids) == 4
        assert bot.photos == [] and bot.texts == []

    def test_album_wins_over_media_and_context(self, tmp_path):
        bot = _FakeBot()
        frames = _images(tmp_path, 3)
        single = frames[0]
        _deliver(tmp_path, bot, "Bilanz", media=single, media_context=single, media_album=frames)
        assert len(bot.groups) == 1 and len(bot.groups[0]) == 3

    def test_long_text_overflows_into_text_messages(self, tmp_path):
        bot = _FakeBot()
        frames = _images(tmp_path, 2)
        _deliver(tmp_path, bot, "x" * 1500, media_album=frames)
        assert len(bot.groups[0][0].caption) == 1024
        assert "".join(bot.texts) == "x" * (1500 - 1024)

    def test_telegram_limit_caps_the_album_at_ten(self, tmp_path):
        bot = _FakeBot()
        _deliver(tmp_path, bot, "Bilanz", media_album=_images(tmp_path, 13))
        assert len(bot.groups[0]) == 10

    def test_a_single_frame_is_sent_as_plain_photo(self, tmp_path):
        bot = _FakeBot()
        _deliver(tmp_path, bot, "Bilanz", media_album=_images(tmp_path, 1))
        assert bot.groups == [] and bot.photos == [{"caption": "Bilanz", "filename": "frame0.jpg"}]

    def test_missing_frames_are_skipped_not_fatal(self, tmp_path):
        bot = _FakeBot()
        frames = _images(tmp_path, 3) + [str(tmp_path / "weg.jpg")]
        _deliver(tmp_path, bot, "Bilanz", media_album=frames)
        assert len(bot.groups[0]) == 3

    def test_without_album_nothing_changes(self, tmp_path):
        bot = _FakeBot()
        _deliver(tmp_path, bot, "nur Text")
        assert bot.groups == [] and bot.photos == [] and bot.texts == ["nur Text"]


def _films(tmp_path, count: int) -> list[str]:
    paths = []
    for index in range(count):
        path = tmp_path / f"film{index}.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        paths.append(str(path))
    return paths


class TestFilmAlbum:
    def test_two_films_go_out_as_one_album_of_videos(self, tmp_path):
        from telegram import InputMediaVideo

        bot = _FakeBot()
        _deliver(tmp_path, bot, "Zusammenfassung", media_album=_films(tmp_path, 2))
        assert len(bot.groups) == 1
        assert all(isinstance(item, InputMediaVideo) for item in bot.groups[0])
        assert bot.groups[0][0].caption == "Zusammenfassung"
        assert all(item.supports_streaming for item in bot.groups[0])  # spielt im Chat ab
        assert [item.media.filename for item in bot.groups[0]] == ["film0.mp4", "film1.mp4"]

    def test_a_single_film_is_sent_as_video(self, tmp_path):
        bot = _FakeBot()
        _deliver(tmp_path, bot, "Zusammenfassung", media_album=_films(tmp_path, 1))
        assert bot.groups == [] and bot.videos == [
            {"caption": "Zusammenfassung", "streaming": True, "filename": "film0.mp4"},
        ]

    def test_a_film_over_the_bot_limit_goes_out_in_parts(self, tmp_path):
        bot = _FakeBot()
        big = make_video(tmp_path / "Hauseingang_081500_wide.mp4", seconds=30, keyframe_every=20)
        small = make_video(tmp_path / "Hauseingang_081500_zoom.mp4", seconds=3, keyframe_every=10)
        limit = big.stat().st_size // 3
        assert small.stat().st_size < limit
        sent_parts: list[Path] = []

        def recording_split(*args):
            parts = split_video(*args)
            sent_parts.extend(parts)
            return parts

        with patch.object(tg, "_VIDEO_MAX_BYTES", limit), patch.object(tg, "split_video", side_effect=recording_split):
            ids = _deliver(tmp_path, bot, "Zusammenfassung", media_album=[str(big), str(small)])
        assert len(bot.groups) == 1
        assert [item.media.filename for item in bot.groups[0]] == [
            "Hauseingang_081500_wide_part000.mp4", "Hauseingang_081500_zoom.mp4",
        ]
        assert bot.groups[0][0].caption == "Zusammenfassung"
        follow_ups = bot.videos
        assert len(follow_ups) >= 2
        total = len(follow_ups) + 1
        assert [v["caption"] for v in follow_ups] == [f"Weitwinkel – Teil {n}/{total}" for n in range(2, total + 1)]
        assert ids == [101, 102] + [103 + n for n in range(len(follow_ups))]  # alle IDs, in Sendereihenfolge
        assert sent_parts and not any(part.exists() for part in sent_parts if part != big)  # Temp-Teile weg
        assert big.exists()

    def test_a_film_that_cannot_be_split_is_not_sent_and_logged(self, tmp_path):
        bot = _FakeBot()
        films = _films(tmp_path, 2)  # keine echten Videos: ffprobe scheitert
        with patch.object(tg, "_VIDEO_MAX_BYTES", 5), patch.object(tg.TelegramChannel, "channel_log") as log:
            _deliver(tmp_path, bot, "Zusammenfassung", media_album=films)
        assert bot.groups == [] and bot.videos == []
        assert bot.texts == ["Zusammenfassung"]
        assert any("splitting failed" in call.args[0] for call in log.call_args_list)


class TestSingleFilm:
    def test_a_film_as_the_attachment_plays_in_the_chat(self, tmp_path):
        """telegram_send with a recorded film (vision_capture): a playable video, not a file to download."""
        bot = _FakeBot()
        film = tmp_path / "Hauseingang_2026-11-12_00-12-36_6s.mp4"
        film.write_bytes(b"film")
        _deliver(tmp_path, bot, "Aufnahme", media=str(film))
        assert bot.videos == [{"caption": "Aufnahme", "streaming": True, "filename": film.name}]
        assert bot.photos == [] and bot.groups == []

    def test_a_single_film_over_the_bot_limit_is_sent_in_parts(self, tmp_path):
        bot = _FakeBot()
        film = make_video(tmp_path / "Garten_090000_zoom.mp4", seconds=30, keyframe_every=20)
        with patch.object(tg, "_VIDEO_MAX_BYTES", film.stat().st_size // 3):
            ids = _deliver(tmp_path, bot, "Aufnahme", media=str(film))
        assert bot.groups == []
        assert bot.videos[0]["caption"] == "Aufnahme"
        assert bot.videos[0]["filename"] == "Garten_090000_zoom_part000.mp4"
        total = len(bot.videos)
        assert total >= 3
        assert [v["caption"] for v in bot.videos[1:]] == [f"Zoom – Teil {n}/{total}" for n in range(2, total + 1)]
        assert ids == [101 + n for n in range(total)]
