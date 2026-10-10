"""Voice enrollment for both DashScope model families (one profile each)."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from aifred.lib import dashscope_enroll
from aifred.lib.dashscope_enroll import AUDIO3_PROFILE, QWEN3_PROFILE, EnrollProfile, enroll_progress


@pytest.fixture
def voices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import wave
    voice_dir = tmp_path / "voices" / "Codine"
    voice_dir.mkdir(parents=True)
    with wave.open(str(voice_dir / "Codine.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    monkeypatch.setattr(dashscope_enroll, "TTS_VOICES_DIR", tmp_path / "voices")
    return tmp_path


def _profile(base: EnrollProfile, tmp_path: Path) -> EnrollProfile:
    from dataclasses import replace
    return replace(base, mapping_path=tmp_path / "mapping.json")


def _capture_post(monkeypatch: pytest.MonkeyPatch, output: dict[str, Any]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> SimpleNamespace:
        sent.append({"url": url, **kwargs})
        return SimpleNamespace(status_code=200, text="", json=lambda: {"output": output})

    monkeypatch.setattr(requests, "post", fake_post)
    return sent


def test_audio3_request_follows_the_cosyvoice_variant(voices: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture_post(monkeypatch, {"voice_id": "qwen-audio-3.0-tts-flash-codine-1"})
    profile = _profile(AUDIO3_PROFILE, voices)
    lines = list(enroll_progress("key", profile))
    body = sent[0]["json"]
    assert body["model"] == "voice-enrollment"
    assert body["input"]["action"] == "create_voice"
    assert body["input"]["target_model"] == "qwen-audio-3.0-tts-flash"
    assert body["input"]["prefix"] == "codine"
    assert body["input"]["url"].startswith("data:audio/wav;base64,")
    assert profile.mapping_path.exists()
    assert "DashScope Audio 3 enroll done: 1 new" in lines[-1]


def test_qwen3_request_keeps_its_own_shape(voices: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture_post(monkeypatch, {"voice": "qwen-tts-vc-codine-1"})
    list(enroll_progress("key", _profile(QWEN3_PROFILE, voices)))
    body = sent[0]["json"]
    assert body["model"] == "qwen-voice-enrollment"
    assert body["input"]["action"] == "create"
    assert body["input"]["preferred_name"] == "codine"
    assert body["input"]["audio"]["data"].startswith("data:audio/wav;base64,")


def test_an_enrolled_unchanged_voice_is_not_uploaded_again(voices: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture_post(monkeypatch, {"voice_id": "v1"})
    profile = _profile(AUDIO3_PROFILE, voices)
    list(enroll_progress("key", profile))
    list(enroll_progress("key", profile))
    assert len(sent) == 1


def test_a_response_without_voice_id_is_a_failure(voices: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_post(monkeypatch, {})
    profile = _profile(AUDIO3_PROFILE, voices)
    lines = list(enroll_progress("key", profile))
    assert any("failed" in line for line in lines)
    assert not profile.mapping_path.exists()


def test_every_dashscope_engine_has_a_profile() -> None:
    from aifred.lib.tts_engines import TTS_ENGINES
    from aifred.lib.tts_engines.dashscope import DashScopeEngine
    keys = {key for key, engine in TTS_ENGINES.items() if isinstance(engine, DashScopeEngine)}
    assert keys == set(dashscope_enroll.ENROLL_PROFILES)
