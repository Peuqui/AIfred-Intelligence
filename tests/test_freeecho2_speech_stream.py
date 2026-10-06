"""Satzweises Sprach-Streaming im Echo-Plugin: Segmente, erster Satz, Erzeuger."""

import asyncio
from unittest.mock import patch

from aifred.lib.audio_channels._audio_orchestrator import silence_pcm
from aifred.lib.envelope import OutboundMessage
from aifred.plugins.channels.freeecho2_channel import FreeEchoChannel


def run(coro):
    return asyncio.run(coro)


def _outbound(text="", **metadata) -> OutboundMessage:
    return OutboundMessage(
        channel="freeecho2", channel_id="buero", recipient="buero",
        text=text, media=None, metadata=metadata,
    )


class TestSpeechSegments:
    def test_one_text_is_sentences_without_silence(self):
        segments = FreeEchoChannel._speech_segments(
            _outbound("Das ist der erste Satz hier. Und das ist der zweite Satz da."),
        )
        assert segments == ["Das ist der erste Satz hier.", "Und das ist der zweite Satz da."]

    def test_paragraphs_get_silence_between_them_but_not_around(self):
        segments = FreeEchoChannel._speech_segments(_outbound(
            "", paragraphs=["Erster Absatz mit genug Wörtern.", "Zweiter Absatz mit genug Wörtern."],
            pause_ms=800,
        ))
        assert segments == ["Erster Absatz mit genug Wörtern.", 800, "Zweiter Absatz mit genug Wörtern."]

    def test_no_silence_when_the_pause_is_zero(self):
        segments = FreeEchoChannel._speech_segments(_outbound(
            "", paragraphs=["Erster Absatz mit genug Wörtern.", "Zweiter Absatz mit genug Wörtern."],
            pause_ms=0,
        ))
        assert all(isinstance(s, str) for s in segments)

    def test_empty_text_has_nothing_to_speak(self):
        assert FreeEchoChannel._speech_segments(_outbound("   ")) == []


class TestSpeechUnit:
    TEXT = "Erster Satz mit genug Wörtern. Zweiter Satz mit genug Wörtern.\n\nNeuer Absatz mit genug Wörtern."

    def test_default_is_sentence_by_sentence(self, monkeypatch):
        monkeypatch.delenv("FREEECHO2_SPEECH_UNIT", raising=False)
        assert FreeEchoChannel._speech_segments(_outbound(self.TEXT)) == [
            "Erster Satz mit genug Wörtern.", "Zweiter Satz mit genug Wörtern.", "Neuer Absatz mit genug Wörtern.",
        ]

    def test_paragraph_by_paragraph(self, monkeypatch):
        monkeypatch.setenv("FREEECHO2_SPEECH_UNIT", "paragraph")
        assert FreeEchoChannel._speech_segments(_outbound(self.TEXT, pause_ms=500)) == [
            "Erster Satz mit genug Wörtern. Zweiter Satz mit genug Wörtern.", 500,
            "Neuer Absatz mit genug Wörtern.",
        ]

    def test_whole_text_at_once(self, monkeypatch):
        monkeypatch.setenv("FREEECHO2_SPEECH_UNIT", "whole")
        segments = FreeEchoChannel._speech_segments(_outbound(
            "", paragraphs=["Absatz eins hier.", "Absatz zwei dort."], pause_ms=500,
        ))
        # ein einziger Aufruf an die Engine, die Pause entsteht dort an der Leerzeile
        assert segments == ["Absatz eins hier.\n\nAbsatz zwei dort."]

    def test_a_given_paragraph_stays_one_piece_in_paragraph_mode(self, monkeypatch):
        monkeypatch.setenv("FREEECHO2_SPEECH_UNIT", "paragraph")
        assert FreeEchoChannel._speech_segments(_outbound(
            "", paragraphs=["Erster langer Absatz. Mit zwei Sätzen darin.", "Zweiter Absatz."], pause_ms=300,
        )) == ["Erster langer Absatz. Mit zwei Sätzen darin.", 300, "Zweiter Absatz."]

    def test_an_invalid_unit_is_a_configuration_error(self, monkeypatch):
        monkeypatch.setenv("FREEECHO2_SPEECH_UNIT", "word")
        try:
            FreeEchoChannel._speech_segments(_outbound("Irgendein Text hier."))
        except ValueError as exc:
            assert "FREEECHO2_SPEECH_UNIT" in str(exc)
            return
        raise AssertionError("expected ValueError")


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


