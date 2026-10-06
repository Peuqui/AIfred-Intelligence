"""Sprach-Erzeugung im Kern: erster Satz, satzweiser Erzeuger, Fehlerfälle."""

import asyncio
from unittest.mock import patch

from aifred.lib import speech_synthesis
from aifred.lib.audio_channels._audio_orchestrator import silence_pcm


def run(coro):
    return asyncio.run(coro)


def fake_tts(spoken: list[str], fail_on: str | None = None):
    """Synthese und Konvertierung ersetzen: ein Satz wird zu Bytes seiner laufenden Nummer."""
    async def fake_run_tts(text, agent, engine, language, label):
        spoken.append(text)
        return None if text == fail_on else f"/tmp/{len(spoken)}.wav"

    async def fake_convert(path, label):
        index = int(path.split("/")[-1].split(".")[0])
        return bytes([index]) * 10

    return (
        patch.object(speech_synthesis, "_run_tts", fake_run_tts),
        patch.object(speech_synthesis, "_convert_to_pcm", fake_convert),
        patch("pathlib.Path.unlink"),
    )


def start(segments, patches):
    async def go():
        buffer = await speech_synthesis.start_speech_stream(segments, "aifred", "piper", "de", "test")
        if buffer is not None and buffer.producer is not None:
            await buffer.producer
        return buffer

    with patches[0], patches[1], patches[2]:
        return run(go())


class TestSpeechStream:
    def test_first_sentence_is_ready_and_the_rest_follows(self):
        spoken: list[str] = []
        buffer = start(["Satz eins hier.", 50, "Satz zwei dort."], fake_tts(spoken))
        assert spoken == ["Satz eins hier.", "Satz zwei dort."]
        assert buffer.next_chunk() == bytes([1]) * 10 + silence_pcm(50) + bytes([2]) * 10
        assert buffer.done

    def test_a_single_sentence_closes_the_buffer_at_once(self):
        buffer = start(["Nur ein Satz hier."], fake_tts([]))
        assert buffer is not None and buffer.known_size == 10 and buffer.producer is None

    def test_a_failing_first_sentence_gives_no_stream(self):
        assert start(["Kaputt hier."], fake_tts([], fail_on="Kaputt hier.")) is None

    def test_a_failing_later_sentence_closes_the_stream_as_failed(self):
        spoken: list[str] = []
        buffer = start(
            ["Satz eins hier.", "Satz zwei dort.", "Satz drei da."],
            fake_tts(spoken, fail_on="Satz zwei dort."),
        )
        assert buffer.failed is True
        assert "Satz drei da." not in spoken        # nothing after the failure was synthesized

    def test_nothing_to_speak_gives_no_stream(self):
        assert start([], fake_tts([])) is None


class TestCleaning:
    def test_markdown_and_emojis_are_not_spoken(self):
        spoken: list[str] = []
        start(["**Wichtig** ist das hier. 😀", 100, "Und ein [Link](http://a.b) dazu."], fake_tts(spoken))
        assert spoken == ["Wichtig ist das hier.", "Und ein Link dazu."]

    def test_leading_silence_is_dropped(self):
        spoken: list[str] = []
        start([100, "Satz hier genau."], fake_tts(spoken))
        assert spoken == ["Satz hier genau."]

    def test_segments_that_are_empty_after_cleaning_are_dropped(self):
        spoken: list[str] = []
        start(["😀😀", 100, "Nur dieser Satz hier."], fake_tts(spoken))
        assert spoken == ["Nur dieser Satz hier."]

    def test_only_unspeakable_text_gives_no_stream(self):
        assert start(["😀"], fake_tts([])) is None
