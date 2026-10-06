"""FreeEcho.2 end tone: configured WAV is read as PCM, wrong format is refused."""

import wave
from pathlib import Path

import pytest

from aifred.plugins.channels.freeecho2_channel.tts_reply import _end_tone_pcm


def _write_wav(path: Path, rate: int, channels: int, frames: bytes) -> None:
    with wave.open(str(path), "wb") as out:
        out.setframerate(rate)
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.writeframes(frames)


def test_not_configured_means_no_end_tone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREEECHO2_END_TONE_WAV", "")
    assert _end_tone_pcm() == b""


def test_configured_wav_is_returned_as_pcm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    frames = b"\x01\x00\x02\x00" * 100
    _write_wav(tmp_path / "dong.wav", 48000, 1, frames)
    monkeypatch.setenv("FREEECHO2_END_TONE_WAV", str(tmp_path / "dong.wav"))
    assert _end_tone_pcm() == frames


@pytest.mark.parametrize(("rate", "channels"), [(44100, 1), (48000, 2)])
def test_wrong_format_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rate: int, channels: int,
) -> None:
    _write_wav(tmp_path / "bad.wav", rate, channels, b"\x00\x00" * 100)
    monkeypatch.setenv("FREEECHO2_END_TONE_WAV", str(tmp_path / "bad.wav"))
    with pytest.raises(ValueError, match="48000 Hz"):
        _end_tone_pcm()


def test_missing_file_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FREEECHO2_END_TONE_WAV", str(tmp_path / "nope.wav"))
    with pytest.raises(FileNotFoundError):
        _end_tone_pcm()
