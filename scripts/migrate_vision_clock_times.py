#!/usr/bin/env python3
"""Once, at the switch to one time format: the times of day of every camera in data/vision/vision.db (table
sources, column settings_json) become "HH:MM". Quiet hours (quiet_start, quiet_end) and the active window
(schedule_start, schedule_end) were stored as whole hours (22, "7") in part; lib/vision_autostart.clock_minutes
reads "HH:MM" only and refuses anything else. A whole hour N becomes "NN:00"; "HH:MM" stays.

A consistent copy of the database goes beside it first (vision.db.bak-<date>-<time>); a database without old
values is left alone (the script can run again). Stop AIfred first or accept that a change made through the
settings page during the few milliseconds of the run is lost.

    venv/bin/python scripts/migrate_vision_clock_times.py [path to vision.db]
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

TIME_KEYS = ("quiet_start", "quiet_end", "schedule_start", "schedule_end")
CLOCK = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
WHOLE_HOUR = re.compile(r"\d{1,2}")


def migrated(value: object) -> str | None:
    """The "HH:MM" for an old whole hour; None when the value is already "HH:MM"; SystemExit for anything else."""
    if isinstance(value, str) and CLOCK.fullmatch(value):
        return None
    text = str(value) if isinstance(value, int) and not isinstance(value, bool) else value
    if isinstance(text, str) and WHOLE_HOUR.fullmatch(text) and int(text) <= 23:
        return f"{int(text):02d}:00"
    sys.exit(f"Unknown time value {value!r}, nothing changed")


def migrate(path: Path) -> list[str]:
    """What changed as "source key: old -> new"; the database is rewritten only when there is anything."""
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("SELECT source_id, settings_json FROM sources").fetchall()
        updates: list[tuple[str, str]] = []
        changes: list[str] = []
        for source_id, settings_json in rows:
            settings = json.loads(settings_json)
            source_changed = False
            for key in TIME_KEYS:
                if key in settings and (new := migrated(settings[key])) is not None:
                    changes.append(f"{source_id} {key}: {settings[key]!r} -> {new!r}")
                    settings[key] = new
                    source_changed = True
            if source_changed:
                updates.append((json.dumps(settings), source_id))
        if not changes:
            return []
        backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        copy = sqlite3.connect(backup)
        try:
            connection.backup(copy)
        finally:
            copy.close()
        with connection:
            connection.executemany("UPDATE sources SET settings_json = ? WHERE source_id = ?", updates)
        print(f"Backup: {backup}")
        return changes
    finally:
        connection.close()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "vision" / "vision.db"
    done = migrate(target)
    print(f"{target}: " + ("\n  ".join(["changed:", *done]) if done else "no old time values"))
