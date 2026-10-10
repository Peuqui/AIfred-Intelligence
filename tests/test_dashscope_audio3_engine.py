"""DashScope Qwen-Audio 3 engine: voice catalog, model choice, error mapping."""

import io
import wave
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from aifred.lib import credential_broker
from aifred.lib.tts_engines import TTS_ENGINES, TTSFailure, get_engine
from aifred.lib.tts_engines import dashscope_audio3


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x01\x00" * 24000)
    return buffer.getvalue()


class FakeCloud:
    def __init__(self, post_reply: SimpleNamespace | Exception) -> None:
        self.post_reply = post_reply
        self.posts: list[dict[str, Any]] = []
        self.downloads: list[str] = []

    def post(self, url: str, **kwargs: Any) -> SimpleNamespace:
        self.posts.append({"url": url, **kwargs})
        if isinstance(self.post_reply, Exception):
            raise self.post_reply
        return self.post_reply

    def get(self, url: str, **kwargs: Any) -> SimpleNamespace:
        self.downloads.append(url)
        return SimpleNamespace(content=_wav_bytes(), raise_for_status=lambda: None)


def _ok_reply() -> SimpleNamespace:
    return SimpleNamespace(
        ok=True, status_code=200, text="",
        json=lambda: {"output": {"audio": {"url": "http://result.example/audio.wav"}}},
    )


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> FakeCloud:
    fake = FakeCloud(_ok_reply())
    monkeypatch.setattr(requests, "post", fake.post)
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(credential_broker.broker, "get", lambda provider, field: "key")
    from aifred.lib import audio_processing
    monkeypatch.setattr(audio_processing, "TTS_AUDIO_DIR", tmp_path)
    return fake


def _engine() -> dashscope_audio3.DashScopeAudio3Engine:
    engine = get_engine("dashscope_audio3")
    assert isinstance(engine, dashscope_audio3.DashScopeAudio3Engine)
    return engine


def test_registered_next_to_the_old_dashscope_engine() -> None:
    keys = list(TTS_ENGINES)
    assert keys.index("dashscope_audio3") == keys.index("dashscope") + 1


def test_flash_voice_uses_the_flash_model_and_https_download(cloud: FakeCloud) -> None:
    url = _engine().generate_speech("Hallo", "Mary", "de")
    body = cloud.posts[0]["json"]
    assert (body["model"], body["input"]["voice"], body["input"]["language_type"]) == (
        "qwen-audio-3.0-tts-flash", "loongmary", "German")
    assert cloud.downloads == ["https://result.example/audio.wav"]
    assert url.startswith("/_upload/tts_audio/")


def test_flagship_voice_needs_the_plus_model(cloud: FakeCloud) -> None:
    _engine().generate_speech("Hallo", "Ling Xin", "de")
    assert cloud.posts[0]["json"]["model"] == "qwen-audio-3.0-tts-plus"


def test_unknown_voice_is_an_engine_failure(cloud: FakeCloud) -> None:
    with pytest.raises(TTSFailure) as failure:
        _engine().generate_speech("Hallo", "Bella", "de")
    assert failure.value.reason == "engine"
    assert cloud.posts == []


def test_http_error_is_an_engine_failure(cloud: FakeCloud) -> None:
    cloud.post_reply = SimpleNamespace(ok=False, status_code=400, text="InvalidParameter")
    with pytest.raises(TTSFailure) as failure:
        _engine().generate_speech("Hallo", "Mary", "de")
    assert failure.value.reason == "engine"
    assert "400" in failure.value.detail


def test_network_outage_is_unreachable(cloud: FakeCloud) -> None:
    cloud.post_reply = requests.ConnectionError("no route")
    with pytest.raises(TTSFailure) as failure:
        _engine().generate_speech("Hallo", "Mary", "de")
    assert failure.value.reason == "unreachable"


def test_default_voice_is_in_the_catalog() -> None:
    engine = _engine()
    assert engine.default_voice in engine.get_voices()
