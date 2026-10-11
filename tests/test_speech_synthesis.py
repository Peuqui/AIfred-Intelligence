"""Sprach-Erzeugung im Kern über den TTS-Dienst (Fake-Dienst): Segmente -> PCM-Puffer, Keep-alive-Marker,
Pausen, AIfreds Filter, Fehler (stumm + Log), Ablage für die Blase."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from fake_tts_service import (
    FakeTTSService,
    audio_frame,
    catalog_row,
    error_frame,
    install_fake_service,
    keepalive_frame,
    summary_frame,
)

import aifred.lib.tts_client as tts_client
from aifred.lib import speech_synthesis
from aifred.lib.audio_channels._audio_orchestrator import KEEPALIVE_SILENCE, silence_pcm


def run(coro):
    return asyncio.run(coro)


async def fake_convert(path: str, label: str) -> bytes:
    """The 'conversion': the PCM of a segment is its audio bytes (tests tell segments apart by them)."""
    return Path(path).read_bytes()


@pytest.fixture
def service(monkeypatch, tmp_path):
    fake = FakeTTSService([catalog_row("xtts"), catalog_row("edge", cloud=True, needs_gpu=False)])
    fake.settings = install_fake_service(monkeypatch, tmp_path, fake, listed=["xtts", "edge"])  # type: ignore[attr-defined]
    monkeypatch.setattr(speech_synthesis, "_convert_to_pcm", fake_convert)
    return fake


def start(paragraphs, pause_ms=0, agent="aifred", session_id=None):
    async def go():
        buffer = await speech_synthesis.start_speech_stream(
            paragraphs, pause_ms, agent, "de", "test", session_id=session_id,
        )
        if buffer is not None and buffer.producer is not None:
            await buffer.producer
        return buffer

    return run(go())


def drain(buffer) -> bytes:
    data = b""
    while True:
        chunk = buffer.next_chunk()
        if not chunk:
            return data
        data += chunk


class TestSegmentsToBuffer:
    def test_segments_arrive_in_order_in_the_growing_buffer(self, service):
        service.script = [audio_frame(b"\x01" * 10) + audio_frame(b"\x02" * 10) + summary_frame()]
        buffer = start(["Satz eins hier. Satz zwei dort."])
        assert drain(buffer) == b"\x01" * 10 + b"\x02" * 10
        assert buffer.done and not buffer.failed

    def test_a_keepalive_marker_becomes_silence_in_the_buffer(self, service):
        service.script = [
            audio_frame(b"\x01" * 10) + keepalive_frame() + keepalive_frame() + audio_frame(b"\x02" * 10)
            + summary_frame(),
        ]
        assert drain(start(["Ein langer Satz hier."])) == b"\x01" * 10 + KEEPALIVE_SILENCE * 2 + b"\x02" * 10

    def test_the_request_carries_one_text_the_list_the_voices_and_the_language(self, service):
        service.script = [audio_frame(b"\x01") + summary_frame()]
        start(["Erster Absatz hier.", "Zweiter Absatz dort."], agent="aifred")
        (sent,) = service.speaks
        assert sent["text"] == "Erster Absatz hier.\n\nZweiter Absatz dort."
        assert sent["language"] == "de"
        assert sent["engines"] == [
            {"engine": "xtts", "unit": "sentence", "host": None},
            {"engine": "edge", "unit": "sentence", "host": None},
        ]
        assert sent["voices"] == {
            "xtts": {"voice": "Alice", "speed": 1.0, "pitch": 1.0},
            "edge": {"voice": "Alice", "speed": 1.0, "pitch": 1.0},
        }
        assert sent["run"]

    def test_the_unit_of_the_list_entry_is_the_users_choice(self, service):
        service.settings["tts_toggles_per_engine"] = {"xtts": {"unit": "whole"}}
        service.script = [audio_frame(b"\x01") + summary_frame()]
        start(["Text hier."])
        assert [ref["unit"] for ref in service.speaks[0]["engines"]] == ["whole", "sentence"]


class TestParagraphPauses:
    def test_paragraph_unit_puts_silence_before_each_segment_but_the_first(self, service):
        service.settings["tts_toggles_per_engine"] = {"xtts": {"unit": "paragraph"}}
        service.script = [audio_frame(b"\x01" * 4) + audio_frame(b"\x02" * 4) + audio_frame(b"\x03" * 4) + summary_frame()]
        buffer = start(["Absatz eins.", "Absatz zwei.", "Absatz drei."], pause_ms=500)
        assert drain(buffer) == b"\x01" * 4 + silence_pcm(500) + b"\x02" * 4 + silence_pcm(500) + b"\x03" * 4

    def test_other_units_get_no_silence_from_aifred(self, service):
        service.script = [audio_frame(b"\x01" * 4) + audio_frame(b"\x02" * 4) + summary_frame()]
        assert drain(start(["Absatz eins.", "Absatz zwei."], pause_ms=500)) == b"\x01" * 4 + b"\x02" * 4


class TestAIfredFilter:
    def test_only_aifreds_own_content_is_filtered_the_rest_is_the_services(self, service):
        service.script = [audio_frame(b"\x01") + summary_frame()]
        start(["<think>geheim</think>**Wichtig** für AIfred 😀 (STT: 2.5s)\n\n*( TTFT: 1s  Source: x )*"])
        # no Markdown stripping, no emoji removal, no "AIfred" -> "Alfred": the service does that
        assert service.speaks[0]["text"] == "**Wichtig** für AIfred 😀"

    def test_nothing_speakable_sends_nothing(self, service):
        assert start(["<think>nur Gedanken</think>", "  "]) is None
        assert service.speaks == []


class TestFailures:
    def test_no_engine_keeps_the_device_silent_and_logs(self, service):
        service.script = [503]
        with patch.object(speech_synthesis, "log_message") as log:
            assert start(["Text hier."]) is None
        assert any("NoEngineAvailable" in str(call) and "silent" in str(call) for call in log.call_args_list)

    def test_a_service_that_cannot_be_started_keeps_the_device_silent(self, service, monkeypatch):
        monkeypatch.setattr(tts_client.TTSClient, "_is_up", lambda self: False)
        monkeypatch.setattr(tts_client, "_systemctl_start", lambda: (1, "unit not found"))
        with patch.object(speech_synthesis, "log_message") as log:
            assert start(["Text hier."]) is None
        assert any("TTSSuiteStartFailed" in str(call) for call in log.call_args_list)

    def test_an_error_after_the_first_segment_closes_the_stream_as_failed(self, service):
        service.script = [audio_frame(b"\x01" * 4) + error_frame("engine_control_failed", "container died")]
        with patch.object(speech_synthesis, "log_message") as log:
            buffer = start(["Satz eins hier. Satz zwei dort."])
        assert buffer.failed is True
        assert drain(buffer) == b"\x01" * 4
        assert any("broke off" in str(call) for call in log.call_args_list)

    def test_a_failed_conversion_before_the_first_segment_gives_no_stream(self, service, monkeypatch):
        async def broken(path, label):
            return None

        monkeypatch.setattr(speech_synthesis, "_convert_to_pcm", broken)
        service.script = [audio_frame(b"\x01") + summary_frame()]
        with patch.object(speech_synthesis, "log_message") as log:
            assert start(["Text hier."]) is None
        assert any("conversion" in str(call) for call in log.call_args_list)

    def test_an_unknown_media_type_ends_the_stream_as_failed(self, service):
        service.script = [audio_frame(b"\x01" * 4) + audio_frame(b"\x02", media_type="audio/flac") + summary_frame()]
        buffer = start(["Satz eins hier. Satz zwei dort."])
        assert buffer.failed is True


class TestSpokenAudioOnTheBubble:
    """What the Echo spoke is kept on the reply's bubble like a browser reply's."""

    @pytest.fixture
    def bubble(self, monkeypatch):
        saved: list[tuple] = []
        attached: list[tuple] = []
        monkeypatch.setattr("aifred.lib.audio_processing.save_audio_to_session",
                            lambda urls, sid: saved.append((list(urls), sid)) or f"/_upload/audio/{sid}/joined.wav")
        monkeypatch.setattr("aifred.lib.session_storage.attach_reply_audio",
                            lambda sid, url, note: attached.append((sid, url, note)) or True)
        return saved, attached

    def test_every_segment_lands_on_the_bubble_with_the_note(self, service, bubble, tmp_path):
        saved, attached = bubble
        trace = [{"entry": "xtts", "outcome": "spoke"}]
        service.script = [audio_frame(b"\x01" * 4) + audio_frame(b"\x02" * 4) + summary_frame(trace)]
        start(["Satz eins hier. Satz zwei dort."], session_id="abc")
        ((urls, session),) = saved
        assert session == "abc" and len(urls) == 2
        assert all((tmp_path / url.rsplit("/", 1)[-1]).is_file() for url in urls)
        assert attached == [("abc", "/_upload/audio/abc/joined.wav", "XTTS · lokal")]

    def test_a_voice_change_is_noted_with_its_reason(self, service, bubble):
        _, attached = bubble
        trace = [
            {"entry": "xtts", "outcome": "spoke"},
            {"entry": "xtts", "outcome": "failed", "reason": "unreachable", "detail": "gone"},
            {"entry": "edge", "outcome": "spoke"},
        ]
        service.script = [audio_frame(b"\x01") + audio_frame(b"\x02", engine="edge") + summary_frame(trace, "unreachable")]
        start(["Text hier."], session_id="abc")
        assert attached[0][2] == "XTTS · lokal → EDGE · Cloud (Serverausfall)"

    def test_an_entry_that_failed_before_it_ever_spoke_is_no_change(self, service, bubble):
        _, attached = bubble
        trace = [
            {"entry": "xtts", "outcome": "skipped", "reason": "stopped"},
            {"entry": "edge", "outcome": "spoke"},
        ]
        service.script = [audio_frame(b"\x01", engine="edge") + summary_frame(trace)]
        start(["Text hier."], session_id="abc")
        assert attached[0][2] == "EDGE · Cloud"

    def test_a_broken_stream_keeps_what_was_spoken(self, service, bubble):
        saved, _ = bubble
        service.script = [audio_frame(b"\x01" * 4) + error_frame("engine_control_failed", "died")]
        start(["Satz eins hier. Satz zwei dort."], session_id="abc")
        assert len(saved[0][0]) == 1

    def test_without_a_session_nothing_is_kept(self, service, bubble):
        saved, attached = bubble
        service.script = [audio_frame(b"\x01") + summary_frame()]
        start(["Text hier."], session_id=None)
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
