"""Tests für aifred.lib.vision_settings — SSOT der Vigilantia-Einstellungsdatei."""

from __future__ import annotations

from pathlib import Path

import pytest

from aifred.lib import vision_settings


@pytest.fixture
def settings_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "settings.json"
    monkeypatch.setattr(vision_settings, "VISION_SETTINGS_PATH", path)
    return path


def test_missing_file_is_empty(settings_path: Path) -> None:
    assert vision_settings.load_vision_settings() == {}


def test_valid_file_is_loaded(settings_path: Path) -> None:
    settings_path.write_text('{"vigilantia_armed": true}', encoding="utf-8")
    assert vision_settings.load_vision_settings() == {"vigilantia_armed": True}


def test_broken_json_raises(settings_path: Path) -> None:
    settings_path.write_text("{kaputt", encoding="utf-8")
    with pytest.raises(ValueError, match="unlesbar"):
        vision_settings.load_vision_settings()


def test_non_object_raises(settings_path: Path) -> None:
    settings_path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON-Objekt"):
        vision_settings.load_vision_settings()


def test_save_load_roundtrip(settings_path: Path) -> None:
    data = {"name": "Küche", "zone_masks": {"cam/x": {"cols": 2}}}
    vision_settings.save_vision_settings(data)
    text = settings_path.read_text(encoding="utf-8")
    assert "Küche" in text and text.endswith("\n")
    assert vision_settings.load_vision_settings() == data
