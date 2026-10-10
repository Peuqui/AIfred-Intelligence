"""Cloned voices come from the shared voice tree while no container answers."""

from pathlib import Path

import pytest

from aifred.lib import config
from aifred.lib.tts_engines import get_engine, voice_names
from aifred.lib.tts_engines.base import shared_voice_names


@pytest.fixture
def voice_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("Salomo", "Codine"):
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{name}.wav").write_bytes(b"")
    # A work file next to the reference is no voice (<Name>/<Name>.wav only).
    (tmp_path / "Codine" / "Appetit-roh.wav").write_bytes(b"")
    monkeypatch.setattr(config, "TTS_VOICES_DIR", tmp_path)
    return tmp_path


def test_names_follow_the_tree(voice_tree: Path) -> None:
    assert shared_voice_names() == ["Codine", "Salomo"]


@pytest.mark.parametrize("engine_key", ["qwen3local", "moss", "fishspeech"])
def test_container_engines_list_the_tree_while_down(voice_tree: Path, engine_key: str) -> None:
    engine = get_engine(engine_key)
    assert engine is not None
    assert list(engine.voices_fallback) == ["Codine", "Salomo"]


def test_xtts_puts_the_cloned_voices_first_with_a_star(voice_tree: Path) -> None:
    engine = get_engine("xtts")
    assert engine is not None
    names = list(engine.voices_fallback)
    assert names[:2] == ["★ Codine", "★ Salomo"]
    assert engine.voices_fallback["★ Codine"] == "Codine"
    assert all(not name.startswith("★") for name in names[2:])


def test_editor_catalog_shows_a_new_voice_without_a_running_container(
    voice_tree: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = get_engine("qwen3local")
    assert engine is not None
    monkeypatch.setattr(type(engine), "get_voices", lambda self: {})
    assert "Codine" in voice_names(engine)
