"""Tests für aifred.lib.vision_settings — SSOT der Vigilantia-Einstellungsdatei."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

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
    with pytest.raises(ValueError, match="unreadable"):
        vision_settings.load_vision_settings()


def test_non_object_raises(settings_path: Path) -> None:
    settings_path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        vision_settings.load_vision_settings()


def test_update_changes_the_current_content_in_place_and_writes_atomically(settings_path: Path) -> None:
    settings_path.write_text('{"vigilantia_armed": true}', encoding="utf-8")
    vision_settings.update_vision_settings(lambda settings: settings.update({"name": "Küche"}))
    text = settings_path.read_text(encoding="utf-8")
    assert "Küche" in text and text.endswith("\n")
    assert vision_settings.load_vision_settings() == {"vigilantia_armed": True, "name": "Küche"}
    assert [path.name for path in settings_path.parent.iterdir()] == ["settings.json"]   # no temporary file stays


def test_a_refusing_change_writes_nothing(settings_path: Path) -> None:
    settings_path.write_text('{"a": 1}', encoding="utf-8")

    def refuse(settings: dict[str, Any]) -> None:
        settings["a"] = 2
        raise ValueError("no")

    with pytest.raises(ValueError):
        vision_settings.update_vision_settings(refuse)
    assert vision_settings.load_vision_settings() == {"a": 1}


def test_two_changes_of_different_keys_lose_nothing_on_stale_reads(settings_path: Path) -> None:
    """The old way (read, change, write the whole dict) lost the first change when both started from one read;
    now each change sees what the file holds when it runs."""
    settings_path.write_text("{}", encoding="utf-8")
    vision_settings.update_vision_settings(lambda settings: settings.update({"zone_masks": {"cam/a": {"cols": 2}}}))
    vision_settings.update_vision_settings(lambda settings: settings.update({"vigilantia_armed": True}))
    assert vision_settings.load_vision_settings() == {"zone_masks": {"cam/a": {"cols": 2}}, "vigilantia_armed": True}


def test_changes_from_several_threads_all_arrive(settings_path: Path) -> None:
    settings_path.write_text("{}", encoding="utf-8")
    keys = [f"key{index}" for index in range(40)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda key: vision_settings.update_vision_settings(lambda settings: settings.update({key: True})), keys))
    assert set(vision_settings.load_vision_settings()) == set(keys)
