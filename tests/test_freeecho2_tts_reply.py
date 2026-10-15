"""Echo-Plugin: send_reply spricht über den TTS-Dienst (Fake-Dienst) — Text, Pause, Sprache, stumm bei Fehler."""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fake_tts_service import (
    FakeTTSService,
    audio_frame,
    catalog_row,
    install_fake_service,
    pause_frame,
    summary_frame,
)

from aifred.lib import speech_synthesis
from aifred.lib.audio_channels._audio_orchestrator import silence_pcm
from aifred.lib.envelope import InboundMessage, OutboundMessage
from aifred.plugins.channels.freeecho2_channel import (
    FreeEchoChannel,
    _devices,
    tts_reply,
)


def run(coro):
    return asyncio.run(coro)


async def fake_convert(path: str, label: str) -> bytes:
    return Path(path).read_bytes()


@pytest.fixture
def echo(monkeypatch, tmp_path):
    """A connected puck with a mocked orchestrator, and the fake TTS service behind the channel."""
    fake = FakeTTSService([catalog_row("xtts")])
    settings = install_fake_service(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(speech_synthesis, "_convert_to_pcm", fake_convert)
    monkeypatch.setattr(tts_reply, "channel_language", lambda: "en")
    _devices.clear()
    ws = MagicMock()
    ws.closed = False
    _devices["buero"] = ws
    orc = MagicMock()
    orc.played = bytearray()

    async def play_tts(buffer):
        """Like the orchestrator's pump: take what the growing buffer delivers until it is done."""
        while not buffer.done:
            chunk = buffer.next_chunk()
            if chunk:
                orc.played.extend(chunk)
            else:
                await buffer.wait_for_data()

    orc.play_tts = AsyncMock(side_effect=play_tts)
    audio_channel = MagicMock()
    audio_channel.get_orchestrator = MagicMock(return_value=orc)
    with patch("aifred.lib.audio_channels.resolve", return_value=audio_channel):
        yield fake, settings, orc
    _devices.clear()


def reply(text="", **metadata) -> OutboundMessage:
    return OutboundMessage(
        channel="freeecho2", channel_id="buero", recipient="buero", text=text, media=None, metadata=metadata,
    )


def user_turn() -> InboundMessage:
    return InboundMessage(channel="freeecho2", channel_id="buero", sender="buero", text="", timestamp=datetime.now())


class TestEchoReply:
    def test_the_reply_is_spoken_by_the_service_and_played_as_a_stream(self, echo):
        fake, _, orc = echo
        fake.script = [audio_frame(b"\x01" * 4) + audio_frame(b"\x02" * 4) + summary_frame()]
        run(FreeEchoChannel().send_reply(reply("Erster Satz hier. Zweiter Satz dort."), user_turn()))
        assert fake.speaks[0]["language"] == "en"           # the household language of the channel
        assert fake.speaks[0]["text"] == "Erster Satz hier. Zweiter Satz dort."
        orc.play_tts.assert_awaited_once()
        assert bytes(orc.played) == b"\x01" * 4 + b"\x02" * 4

    def test_paragraphs_of_an_announcement_are_one_text_and_the_pause_marker_becomes_silence(self, echo):
        fake, _, orc = echo
        fake.script = [audio_frame(b"\x01" * 4) + pause_frame(800) + audio_frame(b"\x02" * 4) + summary_frame()]
        run(FreeEchoChannel().send_reply(
            reply("", paragraphs=["Absatz eins.", "Absatz zwei."], paragraph_pause_ms=800), user_turn(),
        ))
        assert len(fake.speaks) == 1                         # ONE reading, not one per paragraph
        assert fake.speaks[0]["text"] == "Absatz eins.\n\nAbsatz zwei."
        assert fake.speaks[0]["paragraph_pause_ms"] == 800   # the wish goes to the service, which marks the border
        assert bytes(orc.played) == b"\x01" * 4 + silence_pcm(800) + b"\x02" * 4

    def test_a_plain_reply_asks_for_no_pause(self, echo):
        fake, _, _ = echo
        fake.script = [audio_frame(b"\x01") + summary_frame()]
        run(FreeEchoChannel().send_reply(reply("Text hier."), user_turn()))
        assert fake.speaks[0]["paragraph_pause_ms"] == 0

    def test_the_agent_of_the_inbound_message_speaks(self, echo):
        fake, settings, _ = echo
        settings["tts_agent_voices_per_engine"] = {"xtts": {"sokrates": {"voice": "Sokrates-Stimme", "speed": "1.2"}}}
        fake.script = [audio_frame(b"\x01") + summary_frame()]
        inbound = user_turn()
        inbound.target_agent = "sokrates"
        run(FreeEchoChannel().send_reply(reply("Text hier."), inbound))
        assert fake.speaks[0]["voices"] == {"xtts": {"voice": "Sokrates-Stimme", "speed": 1.2, "pitch": 1.0}}

    def test_a_service_that_cannot_speak_leaves_the_device_silent_and_says_so(self, echo):
        fake, _, orc = echo
        fake.script = [503]
        channel = FreeEchoChannel()
        logged: list[str] = []
        with patch.object(channel, "channel_log", lambda message, level="info": logged.append(f"{level}: {message}")):
            run(channel.send_reply(reply("Text hier."), user_turn()))
        orc.play_tts.assert_not_awaited()
        assert any(line.startswith("error:") and "silent" in line for line in logged)

    def test_silent_reply_speaks_nothing(self, echo):
        fake, _, orc = echo
        run(FreeEchoChannel().send_reply(reply("Text.", silent_reply=True), user_turn()))
        assert fake.speaks == [] and not orc.play_tts.await_count
