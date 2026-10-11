"""Neu-Synthese einer Blase über den TTS-Dienst (Fake-Dienst): Text der Blase, Segmente -> eine Audiodatei der
Sitzung samt Vermerk, Fehler des Dienstes gehen an den Aufrufer (der sie zeigt)."""

import asyncio
from types import SimpleNamespace

import pytest
from fake_tts_service import (
    FakeTTSService,
    audio_frame,
    catalog_row,
    install_fake_service,
    keepalive_frame,
    pause_frame,
    summary_frame,
)

import aifred.lib.audio_processing as audio_processing
from aifred.lib.tts_client import NoEngineAvailable
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
