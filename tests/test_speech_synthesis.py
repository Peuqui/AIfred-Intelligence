"""Sprach-Erzeugung im Kern: erster Satz, satzweiser Erzeuger, Fehlerfälle."""

import asyncio
from unittest.mock import patch

from aifred.lib import speech_synthesis
from aifred.lib.audio_channels._audio_orchestrator import silence_pcm
from aifred.lib.tts_escalation import NoSpeechAvailable


def run(coro):
    return asyncio.run(coro)


class FakeRun:
    """Steht für ``SpeechRun``: ein Satz wird zur Audio-URL seiner laufenden Nummer;
    ``fail_on`` = für diesen Satz kann kein Eintrag der Liste mehr sprechen."""

    def __init__(self, spoken: list[str], fail_on: str | None = None, language: str = "de"):
        self.spoken = spoken
        self.fail_on = fail_on
        self.language = language
        self.label = "test"
        self.audio_urls: list[str] = []

    async def synthesize(self, text, agent):
        self.spoken.append(text)
        if text == self.fail_on:
            raise NoSpeechAvailable("[test] no TTS entry can speak")
        url = f"/_upload/tts_audio/{len(self.spoken)}.wav"
        self.audio_urls.append(url)
        return url

    def note(self, lang):
        return "Qwen3-TTS · Aragon"


def fake_tts(spoken: list[str], fail_on: str | None = None, language: str = "de"):
    """Lauf und Konvertierung ersetzen: ein Satz wird zu Bytes seiner laufenden Nummer."""
    async def fake_convert(path, label):
        index = int(path.split("/")[-1].split(".")[0])
        return bytes([index]) * 10

    return FakeRun(spoken, fail_on, language), (
        patch.object(speech_synthesis, "_convert_to_pcm", fake_convert),
        patch("pathlib.Path.unlink"),
    )


def start(segments, fake):
    fake_run, patches = fake

    async def go():
        buffer = await speech_synthesis.start_speech_stream(segments, "aifred", fake_run)
        if buffer is not None and buffer.producer is not None:
            await buffer.producer
        return buffer

    with patches[0], patches[1]:
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


class TestHintLanguage:
    """Hinweise für Code usw. kommen aus den Übersetzungsdateien, in der Sprache des Textes."""

    def test_the_hint_for_code_follows_the_language(self):
        from aifred.lib.audio_processing import clean_text_for_tts, reset_content_hint_flags

        text = "Look at this: ```print(1)``` and then continue."
        reset_content_hint_flags()
        assert "There is code here." in clean_text_for_tts(text, "en")
        reset_content_hint_flags()
        assert "Hier steht Code." in clean_text_for_tts(text, "de")

    def test_the_stream_cleans_in_the_language_it_is_given(self):
        spoken: list[str] = []
        start(["Here is `x = 1` for you."], fake_tts(spoken, language="en"))
        assert spoken == ["There is code here."]  # a sentence with inline code becomes the hint


class TestSpokenAudioOnTheBubble:
    """What the Echo spoke is kept on the reply's bubble like a browser reply's."""

    def _stream(self, segments, session_id, fail_on=None, monkeypatch=None):
        spoken: list[str] = []
        saved: list[tuple] = []
        attached: list[tuple] = []
        monkeypatch.setattr("aifred.lib.audio_processing.save_audio_to_session",
                            lambda urls, sid: saved.append((list(urls), sid)) or f"/_upload/audio/{sid}/joined.wav")
        monkeypatch.setattr("aifred.lib.session_storage.attach_reply_audio",
                            lambda sid, url, note: attached.append((sid, url, note)) or True)
        fake_run, patches = fake_tts(spoken, fail_on)

        async def go():
            buffer = await speech_synthesis.start_speech_stream(segments, "aifred", fake_run, session_id=session_id)
            if buffer is not None and buffer.producer is not None:
                await buffer.producer

        with patches[0], patches[1]:
            run(go())
        return saved, attached

    def test_every_sentence_lands_on_the_bubble(self, monkeypatch):
        saved, attached = self._stream(["Satz eins hier.", "Satz zwei dort."], "abc", monkeypatch=monkeypatch)
        assert saved == [(["/_upload/tts_audio/1.wav", "/_upload/tts_audio/2.wav"], "abc")]
        assert attached == [("abc", "/_upload/audio/abc/joined.wav", "Qwen3-TTS · Aragon")]

    def test_a_single_sentence_too(self, monkeypatch):
        saved, attached = self._stream(["Nur ein Satz."], "abc", monkeypatch=monkeypatch)
        assert saved == [(["/_upload/tts_audio/1.wav"], "abc")] and len(attached) == 1

    def test_a_broken_stream_keeps_what_was_spoken(self, monkeypatch):
        saved, _ = self._stream(["Satz eins hier.", "Kaputt.", "Nie gesprochen."], "abc",
                                fail_on="Kaputt.", monkeypatch=monkeypatch)
        assert saved == [(["/_upload/tts_audio/1.wav"], "abc")]

    def test_without_a_session_nothing_is_kept(self, monkeypatch):
        saved, attached = self._stream(["Satz eins hier."], None, monkeypatch=monkeypatch)
        assert saved == [] and attached == []


def test_attach_reply_audio_takes_the_newest_reply_without_audio(tmp_path, monkeypatch):
    import aifred.lib.session_storage as storage

    sessions: dict = {"s": {"data": {"chat_history": [
        {"role": "assistant", "content": "alt", "has_audio": False},
        {"role": "user", "content": "Frage"},
        {"role": "assistant", "content": "neu", "has_audio": False},
        {"role": "assistant", "content": "mit Ton", "has_audio": True},
    ]}}}
    monkeypatch.setattr(storage, "load_session", lambda sid: sessions.get(sid))
    monkeypatch.setattr(storage, "save_session", lambda sid, session: True)
    assert storage.attach_reply_audio("s", "/_upload/audio/s/a.wav", "Edge · Cloud")
    old, _, new, _ = sessions["s"]["data"]["chat_history"]
    assert (new["has_audio"], new["tts_note"], new["audio_urls_json"]) == (True, "Edge · Cloud", '["/_upload/audio/s/a.wav"]')
    assert new["metadata"]["audio_urls"] == ["/_upload/audio/s/a.wav"]
    assert old["has_audio"] is False
    assert storage.attach_reply_audio("missing", "x", "y") is False
