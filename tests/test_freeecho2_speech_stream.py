"""Echo-Plugin: Sprach-Segmente (die Erzeugung selbst: test_speech_synthesis.py)."""

import asyncio

import pytest

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

    def test_the_unit_of_the_speaking_engine_is_used(self, monkeypatch):
        asked: list[str] = []

        def unit_for(engine: str) -> str:
            asked.append(engine)
            return "whole"

        monkeypatch.setattr(tts_reply, "speech_unit_for", unit_for)
        segments = FreeEchoChannel()._speech_segments(
            _outbound("Erster Satz mit genug Wörtern. Zweiter Satz hier."), "piper",
        )
        assert asked == ["piper"]
        assert segments == ["Erster Satz mit genug Wörtern. Zweiter Satz hier."]

    def test_one_text_is_sentences_without_silence(self, unit):
        segments = FreeEchoChannel()._speech_segments(
            _outbound("Das ist der erste Satz hier. Und das ist der zweite Satz da."), "piper",
        )
        assert segments == ["Das ist der erste Satz hier.", "Und das ist der zweite Satz da."]

    def test_paragraphs_get_silence_between_them_but_not_around(self, unit):
        segments = FreeEchoChannel()._speech_segments(_outbound(
            "", paragraphs=["Erster Absatz mit genug Wörtern.", "Zweiter Absatz mit genug Wörtern."],
            pause_ms=800,
        ), "piper")
        assert segments == ["Erster Absatz mit genug Wörtern.", 800, "Zweiter Absatz mit genug Wörtern."]

    def test_empty_text_has_nothing_to_speak(self, unit):
        assert FreeEchoChannel()._speech_segments(_outbound("   "), "piper") == []

    def test_paragraph_unit_reaches_the_segments(self, unit):
        unit["unit"] = "paragraph"
        segments = FreeEchoChannel()._speech_segments(_outbound(
            "", paragraphs=["Erster langer Absatz. Mit zwei Sätzen darin.", "Zweiter Absatz."], pause_ms=300,
        ), "piper")
        assert segments == ["Erster langer Absatz. Mit zwei Sätzen darin.", 300, "Zweiter Absatz."]
