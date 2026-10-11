"""Engine-Ordner zur Laufzeit ein- und ausschalten (tts_engines/disabled/)."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from aifred.lib.tts_engines import TTS_ENGINES, is_deactivated, reload_engines
from aifred.lib.tts_engines import registry
from aifred.lib.tts_escalation import _skip_reason, entry_status, escalation_entries, location_label

SETTINGS = {
    "tts_hosts": [{"name": "Aragon", "address": "10.0.0.2", "enabled": True, "ssh": "", "ports": {}}],
    "tts_escalation": [
        {"engine": "xtts", "host": None, "enabled": True},
        {"engine": "xtts", "host": "Aragon", "enabled": True},
        {"engine": "edge", "host": None, "enabled": True},
    ],
}


@pytest.fixture
def xtts_deactivated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "xtts").mkdir()
    (tmp_path / "xtts" / "engine.py").write_text("")
    monkeypatch.setattr(registry, "DISABLED_DIR", tmp_path)
    monkeypatch.delitem(TTS_ENGINES, "xtts")


def test_entry_of_a_deactivated_engine_stays_in_the_list_and_is_off(xtts_deactivated: None) -> None:
    entries = escalation_entries(SETTINGS)
    assert [(e.engine.key, e.host.name if e.host else None, e.enabled) for e in entries] == [
        ("xtts", None, False), ("xtts", "Aragon", False), ("edge", None, True),
    ]
    assert all(e.engine.deactivated for e in entries[:2])
    assert entry_status(entries[0]) == "engine_deactivated"
    assert location_label(entries[0], "de") == "deaktiviert"
    assert location_label(entries[1], "de") == "Aragon"


def test_deactivated_entry_is_skipped_with_a_visible_reason(xtts_deactivated: None) -> None:
    entry = escalation_entries(SETTINGS)[0]
    assert asyncio.run(_skip_reason(entry, lambda _line: None)) == "engine deactivated"


def test_a_misspelled_engine_is_still_an_error(xtts_deactivated: None) -> None:
    broken = {**SETTINGS, "tts_escalation": [{"engine": "xtss", "host": None, "enabled": True}]}
    with pytest.raises(ValueError, match="unknown TTS engine"):
        escalation_entries(broken)
    assert not is_deactivated("xtss")


def test_moving_an_engine_folder_takes_effect_on_reload() -> None:
    engine_dir = registry.ENGINES_DIR / "espeak"
    disabled_dir = registry.DISABLED_DIR / "espeak"
    assert "espeak" in TTS_ENGINES
    try:
        shutil.move(engine_dir, disabled_dir)
        assert reload_engines() == (["espeak"], [])
        assert "espeak" not in TTS_ENGINES and is_deactivated("espeak")
        shutil.move(disabled_dir, engine_dir)
        assert reload_engines() == ([], ["espeak"])
        assert "espeak" in TTS_ENGINES and not is_deactivated("espeak")
    finally:
        if disabled_dir.exists():
            shutil.move(disabled_dir, engine_dir)
            reload_engines()
