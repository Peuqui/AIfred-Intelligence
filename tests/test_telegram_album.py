"""Telegram-Zustellung eines Burst-Kapitels: die Keyframes gehen als EIN Album
(der Mini-Film des Vorkommnisses), Caption am ersten Bild, Rest als Text."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import aifred.plugins.channels.telegram_channel as tg


class _FakeBot:
    """Zeichnet die Sendeaufrufe auf; ``async with bot`` wie beim echten Bot."""

    def __init__(self) -> None:
        self.groups: list[list] = []
        self.photos: list[dict] = []
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

    async def send_photo(self, chat_id, photo, caption):
        self.photos.append({"caption": caption})
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
        assert bot.groups == [] and bot.photos == [{"caption": "Bilanz"}]

    def test_missing_frames_are_skipped_not_fatal(self, tmp_path):
        bot = _FakeBot()
        frames = _images(tmp_path, 3) + [str(tmp_path / "weg.jpg")]
        _deliver(tmp_path, bot, "Bilanz", media_album=frames)
        assert len(bot.groups[0]) == 3

    def test_without_album_nothing_changes(self, tmp_path):
        bot = _FakeBot()
        _deliver(tmp_path, bot, "nur Text")
        assert bot.groups == [] and bot.photos == [] and bot.texts == ["nur Text"]
