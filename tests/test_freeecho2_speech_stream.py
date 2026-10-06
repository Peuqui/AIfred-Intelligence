"""Satzweises Sprach-Streaming im Echo-Plugin: Segmente, erster Satz, Erzeuger."""

import asyncio
from unittest.mock import patch

import pytest

from aifred.lib.audio_channels._audio_orchestrator import silence_pcm
from aifred.lib.envelope import OutboundMessage
from aifred.plugins.channels.freeecho2_channel import FreeEchoChannel, tts_reply


def run(coro):
    return asyncio.run(coro)


def _outbound(text="", **metadata) -> OutboundMessage:
    return OutboundMessage(
        channel="freeecho2", channel_id="buero", recipient="buero",
        text=text, media=None, metadata=metadata,
    )


@pytest.fixture
def unit(monkeypatch):
    """Die Einheit der gewählten Engine (SSOT speech_unit_for) für den Test festlegen."""
    chosen = {"unit": "sentence"}
    monkeypatch.setattr(tts_reply, "speech_unit_for", lambda engine: chosen["unit"])
    return chosen


class TestSpeechSegments:
    """Das Plugin liest die Einheit nur (SSOT: lib.tts_engines.speech_unit_for); die Aufteilung
    je Einheit ist in lib/audio_processing getestet (test_speech_units.py)."""

    def test_the_unit_of_the_chosen_engine_is_used(self, monkeypatch):
        asked: list[str] = []
        monkeypatch.setattr(tts_reply, "speech_unit_for", lambda engine: asked.append(engine) or "whole")
        channel = FreeEchoChannel()
        channel._get_wanted_tts = lambda: "piper"      # type: ignore[method-assign]
        segments = channel._speech_segments(_outbound("Erster Satz mit genug Wörtern. Zweiter Satz hier."))
        assert asked == ["piper"]
        assert segments == ["Erster Satz mit genug Wörtern. Zweiter Satz hier."]

    def test_one_text_is_sentences_without_silence(self, unit):
        segments = FreeEchoChannel()._speech_segments(
            _outbound("Das ist der erste Satz hier. Und das ist der zweite Satz da."),
        )
        assert segments == ["Das ist der erste Satz hier.", "Und das ist der zweite Satz da."]

    def test_paragraphs_get_silence_between_them_but_not_around(self, unit):
        segments = FreeEchoChannel()._speech_segments(_outbound(
            "", paragraphs=["Erster Absatz mit genug Wörtern.", "Zweiter Absatz mit genug Wörtern."],
            pause_ms=800,
        ))
        assert segments == ["Erster Absatz mit genug Wörtern.", 800, "Zweiter Absatz mit genug Wörtern."]

    def test_empty_text_has_nothing_to_speak(self, unit):
        assert FreeEchoChannel()._speech_segments(_outbound("   ")) == []

    def test_paragraph_unit_reaches_the_segments(self, unit):
        unit["unit"] = "paragraph"
        segments = FreeEchoChannel()._speech_segments(_outbound(
            "", paragraphs=["Erster langer Absatz. Mit zwei Sätzen darin.", "Zweiter Absatz."], pause_ms=300,
        ))
        assert segments == ["Erster langer Absatz. Mit zwei Sätzen darin.", 300, "Zweiter Absatz."]


def _channel_with_fake_tts(spoken: list[str], fail_on: str | None = None) -> FreeEchoChannel:
    channel = FreeEchoChannel()

    async def fake_run_tts(text, agent="aifred"):
        spoken.append(text)
        return None if text == fail_on else f"/tmp/{len(spoken)}.wav"

    async def fake_convert(path, rate):
        index = int(path.split("/")[-1].split(".")[0])
        return bytes([index]) * 10

    channel._run_tts = fake_run_tts            # type: ignore[method-assign]
    channel._convert_to_pcm = fake_convert     # type: ignore[method-assign]
    return channel


class TestSpeechStream:
    def test_first_sentence_is_ready_and_the_rest_follows(self):
        spoken: list[str] = []
        channel = _channel_with_fake_tts(spoken)

        async def go():
            buffer = await channel._start_speech_stream(
                "buero", ["Satz eins hier.", 50, "Satz zwei dort."], "aifred",
            )
            assert buffer is not None
            first = buffer.next_chunk()            # synthesized before the stream returned
            await buffer.producer
            return first, buffer

        with patch("pathlib.Path.unlink"):
            first, buffer = run(go())
        assert first == bytes([1]) * 10
        assert spoken == ["Satz eins hier.", "Satz zwei dort."]
        assert buffer.done is False                # silence + sentence two still to be read
        rest = buffer.next_chunk()
        assert rest == silence_pcm(50) + bytes([2]) * 10
        assert buffer.done

    def test_a_single_sentence_closes_the_buffer_at_once(self):
        channel = _channel_with_fake_tts([])

        async def go():
            return await channel._start_speech_stream("buero", ["Nur ein Satz hier."], "aifred")

        with patch("pathlib.Path.unlink"):
            buffer = run(go())
        assert buffer is not None and buffer.known_size == 10 and buffer.producer is None

    def test_a_failing_first_sentence_gives_no_stream(self):
        channel = _channel_with_fake_tts([], fail_on="Kaputt hier.")

        async def go():
            return await channel._start_speech_stream("buero", ["Kaputt hier."], "aifred")

        with patch("pathlib.Path.unlink"):
            assert run(go()) is None

    def test_a_failing_later_sentence_closes_the_stream_as_failed(self):
        channel = _channel_with_fake_tts([], fail_on="Satz zwei dort.")

        async def go():
            buffer = await channel._start_speech_stream(
                "buero", ["Satz eins hier.", "Satz zwei dort.", "Satz drei da."], "aifred",
            )
            await buffer.producer
            return buffer

        with patch("pathlib.Path.unlink"):
            buffer = run(go())
        assert buffer.failed is True
        buffer.next_chunk()
        assert buffer.done                          # nothing after the failure was synthesized

    def test_nothing_to_speak_gives_no_stream(self):
        channel = _channel_with_fake_tts([])
        assert run(channel._start_speech_stream("buero", [], "aifred")) is None


def test_start_with_silence_is_a_programming_error():
    channel = _channel_with_fake_tts([])
    try:
        run(channel._start_speech_stream("buero", [100, "Satz hier."], "aifred"))
    except ValueError:
        return
    raise AssertionError("expected ValueError")


