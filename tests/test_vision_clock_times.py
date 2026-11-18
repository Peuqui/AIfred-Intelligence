"""One format for the times of day of a camera: "HH:MM" for the quiet hours and the active window alike
(lib/vision_autostart.clock_minutes / in_clock_window). A whole hour is refused (the data is migrated once by
scripts/migrate_vision_clock_times.py), a window over midnight works to the minute."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from aifred.lib import vision_alerts, vision_autostart

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_vision_clock_times.py"


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 18, hour, minute)


@pytest.mark.parametrize("value", [22, "22", "7", "24:00", "22:60", "7:00", "22:0", "", None, "22:30:00"])
def test_anything_but_hh_mm_is_refused(value: Any) -> None:
    with pytest.raises(ValueError):
        vision_autostart.clock_minutes(value)


def test_the_window_22_30_to_06_15_holds_across_midnight_to_the_minute() -> None:
    inside = [(22, 30), (23, 59), (0, 0), (3, 10), (6, 14)]
    outside = [(22, 29), (6, 15), (12, 0), (21, 0)]
    assert all(vision_autostart.in_clock_window("22:30", "06:15", _at(*time)) for time in inside)
    assert not any(vision_autostart.in_clock_window("22:30", "06:15", _at(*time)) for time in outside)


def test_a_window_within_one_day_and_an_empty_one() -> None:
    assert vision_autostart.in_clock_window("08:00", "17:30", _at(17, 29))
    assert not vision_autostart.in_clock_window("08:00", "17:30", _at(17, 30))
    assert vision_autostart.in_clock_window("08:00", "08:00", _at(3, 0))      # start == end: the whole day


def test_the_alert_filter_uses_the_quiet_window_to_the_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    class Store:
        def get_source(self, _source_id: str) -> dict[str, Any]:
            return {"settings": {"quiet_enabled": True, "quiet_start": "22:30", "quiet_end": "06:15"}}

    class Clock:
        current = _at(23, 0)

        @classmethod
        def now(cls) -> datetime:
            return cls.current

    monkeypatch.setattr(vision_alerts, "datetime", Clock)
    assert not vision_alerts._alert_passes_filters("cam/x", Store(), "face")      # 23:00 is quiet
    Clock.current = _at(6, 14)
    assert not vision_alerts._alert_passes_filters("cam/x", Store(), "face")      # after midnight, still quiet
    Clock.current = _at(6, 15)
    assert vision_alerts._alert_passes_filters("cam/x", Store(), "face")


def test_an_old_whole_hour_in_a_camera_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError):
        vision_autostart._schedule_active_now({"schedule_enabled": True, "schedule_start": 22, "schedule_end": "05:30"})


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("migrate_vision_clock_times", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _database(path: Path, sources: dict[str, dict[str, Any]]) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sources (source_id TEXT PRIMARY KEY, settings_json TEXT NOT NULL)")
    connection.executemany("INSERT INTO sources VALUES (?, ?)", [(key, json.dumps(value)) for key, value in sources.items()])
    connection.commit()
    connection.close()


def _stored(path: Path) -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(path)
    try:
        return {source: json.loads(settings) for source, settings in connection.execute("SELECT * FROM sources")}
    finally:
        connection.close()


def test_the_script_turns_whole_hours_into_hh_00_keeps_a_copy_and_runs_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "vision.db"
    _database(path, {
        "cam/a": {"schedule_start": 22, "schedule_end": "7", "quiet_start": 23, "quiet_end": "05:30", "alias": "Tür"},
        "cam/b": {"schedule_start": "22:00", "quiet_enabled": False},
    })
    script = _script()
    changed = script.migrate(path)
    assert len(changed) == 3
    assert _stored(path) == {
        "cam/a": {"schedule_start": "22:00", "schedule_end": "07:00", "quiet_start": "23:00", "quiet_end": "05:30", "alias": "Tür"},
        "cam/b": {"schedule_start": "22:00", "quiet_enabled": False},
    }
    backups = list(tmp_path.glob("vision.db.bak-*"))
    assert len(backups) == 1 and _stored(backups[0])["cam/a"]["schedule_start"] == 22
    assert script.migrate(path) == []                              # nothing left: no second copy, no change
    assert len(list(tmp_path.glob("vision.db.bak-*"))) == 1


def test_the_script_refuses_an_unknown_value_and_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "vision.db"
    _database(path, {"cam/a": {"schedule_start": 22}, "cam/b": {"quiet_start": "abends"}})
    with pytest.raises(SystemExit):
        _script().migrate(path)
    assert _stored(path)["cam/a"] == {"schedule_start": 22}
    assert not list(tmp_path.glob("vision.db.bak-*"))
