"""Neu-Synthese einer Blase über den TTS-Dienst (Fake-Dienst): Text der Blase, Segmente -> eine Audiodatei der
Sitzung samt Vermerk, Fehler des Dienstes gehen an den Aufrufer (der sie zeigt)."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fake_tts_service import (
    FakeStreamingService,
    FakeTTSService,
    audio_frame,
    catalog_row,
    error_frame,
    install_fake_service,
    keepalive_frame,
    pause_frame,
    serve_stream,
    summary_frame,
)

from aifred.lib import api, audio_processing
from aifred.lib.tts_client import NoEngineAvailable
from aifred.state import _tts_streaming_mixin
from aifred.state._tts_streaming_mixin import TTSStreamingMixin

TRACE = [{"entry": "xtts", "outcome": "spoke", "reason": None, "detail": ""}]


@pytest.fixture
def service(monkeypatch, tmp_path):
    fake = FakeTTSService([catalog_row("xtts"), catalog_row("browser", browser=True, needs_gpu=False)])
    install_fake_service(monkeypatch, tmp_path, fake, listed=["xtts", "browser"])
    joined: list[list[str]] = []

    def fake_save(urls: list[str], session_id: str) -> str:
        joined.append(list(urls))
        return f"/_upload/audio/{session_id}/replay.wav"

    monkeypatch.setattr(audio_processing, "save_audio_to_session", fake_save)
    fake.joined = joined  # type: ignore[attr-defined]
    return fake


def request(text: str = "Der Text der Blase.") -> dict:
    return {
        "bubble_index": 1, "timestamp": "t", "agent": "aifred", "text": text, "language": "de",
        "ui_language": "de", "session_id": "s1",
    }


def synthesize(req: dict):
    return asyncio.run(TTSStreamingMixin._synthesize_bubble_audio(req))


def test_the_segments_become_one_session_audio_without_markers(service, tmp_path) -> None:
    service.script = [
        audio_frame(b"one") + keepalive_frame() + pause_frame(500) + audio_frame(b"two") + summary_frame(TRACE),
    ]
    assert synthesize(request()) == ("/_upload/audio/s1/replay.wav", "XTTS · lokal")
    (urls,) = service.joined
    assert [(tmp_path / url.rsplit("/", 1)[-1]).read_bytes() for url in urls] == [b"one", b"two"]
    (sent,) = service.speaks
    assert sent["text"] == "Der Text der Blase."
    assert sent["paragraph_pause_ms"] == 0  # the replay is one audio
    assert [ref["engine"] for ref in sent["engines"]] == ["xtts", "browser"]  # the service skips the browser itself


def test_a_service_without_audio_is_no_success(service) -> None:
    service.script = [summary_frame([])]
    assert synthesize(request()) is None
    assert service.joined == []


def test_a_service_error_goes_to_the_caller(service) -> None:
    service.script = [503]
    with pytest.raises(NoEngineAvailable):
        synthesize(request())


class _Bubbles:
    def __init__(self, llm_content: str) -> None:
        self.session_id = "s1"
        self.ui_language = "de"
        self._last_detected_language = "de"
        self.tts_agents: dict = {}
        self.chat = SimpleNamespace(
            chat_history=[
                {"role": "user", "content": "Frage"},
                {"role": "assistant", "agent": "aifred", "content": "angezeigt", "timestamp": "t1"},
            ],
            llm_history=[{"role": "user", "content": "Frage"}, {"role": "assistant", "content": llm_content}],
        )

    def _chat_sub(self) -> SimpleNamespace:
        return self.chat

    _resolve_tts_language = TTSStreamingMixin._resolve_tts_language
    _extract = TTSStreamingMixin._extract_bubble_tts_request


def test_the_request_takes_the_llm_text_without_label_and_interface_content() -> None:
    bubbles = _Bubbles("[AIFRED]: <think>geheim</think>Das ist die **Antwort** hier.\n\n*( TTFT: 1.0s )*")
    req = bubbles._extract(1)
    assert req is not None
    assert req["text"] == "Das ist die **Antwort** hier."  # Markdown is the service's business
    assert req["agent"] == "aifred" and req["language"] == "de" and req["ui_language"] == "de"
    assert req["timestamp"] == "t1"


def test_a_bubble_with_nothing_to_say_gives_no_request() -> None:
    bubbles = _Bubbles("[AIFRED]: <think>nur Gedanken</think>")
    assert bubbles._extract(1) is None


# ── Der Knopf an der Blase: abschnittsweise hören, am Ende den ganzen Ton behalten ──

CATALOG = [catalog_row("xtts"), catalog_row("browser", browser=True, needs_gpu=False)]


class Playlist:
    def __init__(self, monkeypatch) -> None:
        self.kinds: list[str] = []
        self.joined: list[list[str]] = []
        monkeypatch.setattr(api, "browser_push", lambda session_id, kind, url, **_: self.kinds.append(kind))
        monkeypatch.setattr(audio_processing, "save_audio_to_session", self._join)
        monkeypatch.setattr(_tts_streaming_mixin, "_tts_backend_states", {})

    def _join(self, urls: list[str], session_id: str) -> str:
        self.joined.append(list(urls))
        return f"/_upload/audio/{session_id}/replay.wav"


async def read_bubble(monkeypatch, tmp_path, stream, listed=("xtts", "browser")):
    async with serve_stream(monkeypatch, tmp_path, FakeTTSService(CATALOG), stream, listed=list(listed)):
        reading = TTSStreamingMixin._read_bubble_aloud(request("Der Text. Noch ein Satz."))
        kept = await asyncio.wait_for(TTSStreamingMixin._keep_bubble_reading(reading), timeout=10)
        return reading, kept


def test_every_section_reaches_the_playlist_and_the_whole_audio_stays_on_the_bubble(monkeypatch, tmp_path) -> None:
    playlist = Playlist(monkeypatch)
    stream = FakeStreamingService(after_text=[[audio_frame(b"one")]], after_finish=[audio_frame(b"two"), summary_frame(TRACE)])
    reading, kept = asyncio.run(read_bubble(monkeypatch, tmp_path, stream))
    assert playlist.kinds == ["tts", "tts"]
    assert kept == "/_upload/audio/s1/replay.wav"
    assert len(playlist.joined[0]) == 2
    assert reading.note == "XTTS · lokal"
    assert stream.texts == ["Der Text. Noch ein Satz."]


def test_a_reading_that_broke_off_leaves_the_audio_of_the_bubble_alone(monkeypatch, tmp_path) -> None:
    playlist = Playlist(monkeypatch)
    stream = FakeStreamingService(after_text=[[audio_frame(b"one")]], after_finish=[error_frame("no_engine", "engine died")])
    reading, kept = asyncio.run(read_bubble(monkeypatch, tmp_path, stream))
    assert reading.broke_off and kept is None
    assert playlist.joined == []
    assert _tts_streaming_mixin.get_tts_backend_state("s1").notices[0][0] == "error"


def test_a_service_that_cannot_speak_leaves_the_bubble_to_the_browser_voice(monkeypatch, tmp_path) -> None:
    playlist = Playlist(monkeypatch)
    stream = FakeStreamingService(on_start=[error_frame("no_engine", "nope")])
    reading, kept = asyncio.run(read_bubble(monkeypatch, tmp_path, stream))
    assert kept == ""  # no file, but the browser spoke: the bubble keeps that
    assert reading.browser_speech
    assert "tts_speak" in playlist.kinds


def test_an_aborted_reading_keeps_nothing(monkeypatch, tmp_path) -> None:
    playlist = Playlist(monkeypatch)
    stream = FakeStreamingService(after_text=[[audio_frame(b"one")]])

    async def scenario():
        async with serve_stream(monkeypatch, tmp_path, FakeTTSService(CATALOG), stream, listed=["xtts"]):
            reading = TTSStreamingMixin._read_bubble_aloud(request())
            await asyncio.sleep(0.3)
            _tts_streaming_mixin.abort_tts_reading("s1")
            return await asyncio.wait_for(TTSStreamingMixin._keep_bubble_reading(reading), timeout=10)

    assert asyncio.run(scenario()) is None
    assert playlist.joined == []


def test_a_new_bubble_reading_ends_the_one_before(monkeypatch, tmp_path) -> None:
    Playlist(monkeypatch)
    stream = FakeStreamingService(after_text=[[audio_frame(b"one")]])

    async def scenario():
        async with serve_stream(monkeypatch, tmp_path, FakeTTSService(CATALOG), stream, listed=["xtts"]):
            first = TTSStreamingMixin._read_bubble_aloud(request())
            second = TTSStreamingMixin._read_bubble_aloud(request())
            await asyncio.wait_for(first.wait(), timeout=5)
            second.abort()
            await second.wait()
            return first.aborted

    assert asyncio.run(scenario()) is True


def _bubble(audio: list[str]) -> SimpleNamespace:
    history = [{"role": "assistant", "timestamp": "t", "metadata": {}, "audio_urls_json": json.dumps(audio)}]
    return SimpleNamespace(chat_history=history)


def test_a_bubble_only_the_browser_spoke_has_no_audio_url_but_its_sentences() -> None:
    chat = _bubble(["/old.wav"])
    state = SimpleNamespace(_chat_sub=lambda: chat, _save_current_session=lambda: None)
    spoken = [{"text": "Ein Satz.", "language": "de"}]
    assert TTSStreamingMixin._apply_bubble_audio(state, 0, "t", "", "Browser", save_session=False, browser_speech=spoken)
    bubble = chat.chat_history[0]
    assert json.loads(bubble["audio_urls_json"]) == []
    assert json.loads(bubble["browser_speech_json"]) == spoken
    assert bubble["has_audio"] is True
