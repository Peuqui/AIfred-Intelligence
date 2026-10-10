"""Vollständigkeit der TTS-Engines: jede hat ihren Ordner, ihre Beschriftungen und — als
Container-Engine — eine compose-Datei, die zu ihren Angaben passt."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from aifred.lib.config import TTS_ENGINE_KEYS, default_tts_escalation
from aifred.lib.i18n import tts_key_to_label, tts_label_to_key
from aifred.lib.tts_engines import TTS_ENGINES
from aifred.lib.tts_engines.base import TTSEngine

ENGINES_DIR = Path(__file__).parent.parent / "aifred" / "lib" / "tts_engines"
LANGUAGES = ("de", "en")
CONTAINER_ENGINES = [e for e in TTS_ENGINES.values() if e.runs_in_container]


def test_every_engine_folder_holds_exactly_one_registered_engine() -> None:
    folders = sorted(p.parent.name for p in ENGINES_DIR.glob("*/engine.py"))
    assert folders == sorted(TTS_ENGINES), "folder name must be the engine key"


@pytest.mark.parametrize("engine", TTS_ENGINES.values(), ids=lambda e: e.key)
def test_engine_has_own_folder_and_labels(engine: TTSEngine) -> None:
    assert engine.package_dir == ENGINES_DIR / engine.key
    for lang in LANGUAGES:
        assert engine.label(lang).strip(), f"{engine.key}: label {lang} missing"
    assert tts_label_to_key(tts_key_to_label(engine.key, "de")) == engine.key
    assert tts_label_to_key(tts_key_to_label(engine.key, "en")) == engine.key
    assert engine.key in TTS_ENGINE_KEYS


@pytest.mark.parametrize("engine", CONTAINER_ENGINES, ids=lambda e: e.key)
def test_container_engine_matches_its_compose_file(engine: TTSEngine) -> None:
    compose = engine.docker_compose_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert re.search(rf"^\s*image:\s*{re.escape(engine.image_name or '')}\s*$", compose, re.M)
    assert re.search(rf'-\s*"{engine.default_port}:\d+"', compose), "host port"
    assert engine.startup_timeout_s > 0


@pytest.mark.parametrize("engine", [e for e in TTS_ENGINES.values() if not e.runs_in_container], ids=lambda e: e.key)
def test_lightweight_engine_declares_no_container_data(engine: TTSEngine) -> None:
    assert engine.image_name is None
    assert engine.docker_compose_path is None


def test_default_escalation_comes_from_the_engines_and_the_browser_speaks_last() -> None:
    entries = default_tts_escalation()
    assert [e["engine"] for e in entries] == [e.key for e in TTS_ENGINES.values() if e.in_default_escalation]
    assert entries[-1]["engine"] == "browser"
    assert all(e["host"] is None and e["enabled"] for e in entries)
