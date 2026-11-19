#!/usr/bin/env python3
"""Once, at the switch to line-ups per discussion mode (lib/mode_lineups.py): the session files of a directory get
the new config keys. The old ``debate_seats`` (one pair for every debate mode) becomes the line-up of the session's
current debate mode, with the session's ``active_agent`` as its author; the old ``symposion_agents`` becomes the
symposion's line-up; both old keys are dropped, ``lineups`` is added. ``active_agent`` stays: it is the standard
mode's agent. A session whose current mode is no debate mode loses its old seats (they belonged to no mode), the
script says so.

A copy of every file it changes goes beside it first (<name>.json.bak-lineups); a file without the old keys is left
alone. Afterwards the global last choice of every mode (settings.json ``last_lineups``) is the line-up of the session
used last that has one: what Peuqui chose before stays chosen in new sessions. ``--dry-run`` only prints. Run it on the
sessions directory with the service stopped.

    venv/bin/python scripts/migrate_mode_lineups.py [--dry-run] <sessions directory>
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

# Before any aifred import: a helper script is not the service and must not reset its debug log.
os.environ.setdefault("AIFRED_CLI_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aifred.lib.atomic_write import write_text_atomic
from aifred.lib.debate_seats import SEAT_MAIN, SPEAKING_SEATS, chosen_seats
from aifred.lib.mode_lineups import MODE_SYMPOSION, SYMPOSION_AGENTS
from aifred.lib.settings import persisted_settings, update_settings

OLD_KEYS = ("debate_seats", "symposion_agents")


def migrate_config(config: dict[str, Any]) -> list[str]:
    """Convert the config block in place; returns what was done, one line each (empty: nothing to do)."""
    if not any(key in config for key in OLD_KEYS):
        return []
    done: list[str] = []
    mode = config.get("multi_agent_mode", "standard")
    lineups: dict[str, Any] = dict(config.get("lineups", {}))
    seats = config.pop("debate_seats", {})
    agents = config.pop("symposion_agents", [])
    if seats:
        if mode in SPEAKING_SEATS:
            lineup = {SEAT_MAIN: config["active_agent"], **{seat: agent for seat, agent in seats.items() if seat in chosen_seats(mode)}}
            lineups[mode] = lineup
            done.append(f"line-up of {mode}: {lineup}")
        else:
            done.append(f"seats {seats} dropped (the mode is {mode}, no debate mode)")
    if agents:
        lineups[MODE_SYMPOSION] = {SYMPOSION_AGENTS: agents}
        done.append(f"line-up of {MODE_SYMPOSION}: {lineups[MODE_SYMPOSION]}")
    config["lineups"] = lineups
    return done or ["old keys dropped (empty)"]


def migrate_directory(directory: Path, dry_run: bool) -> int:
    """Convert every session file of ``directory``; returns how many files changed."""
    changed = 0
    for path in sorted(directory.glob("*.json")):
        session = json.loads(path.read_text(encoding="utf-8"))
        config = session.get("data", {}).get("config")
        if not isinstance(config, dict):
            continue
        done = migrate_config(config)
        if not done:
            continue
        changed += 1
        print(f"{path.name}: {'; '.join(done)}")
        if not dry_run:
            shutil.copy2(path, path.with_name(f"{path.name}.bak-lineups"))
            write_text_atomic(path, json.dumps(session, ensure_ascii=False, indent=2))
    return changed


def last_lineups(directory: Path) -> dict[str, Any]:
    """Per mode the line-up of the session used last that has one (by the file's modification time)."""
    chosen: dict[str, Any] = {}
    for path in sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime):
        config = json.loads(path.read_text(encoding="utf-8")).get("data", {}).get("config")
        if isinstance(config, dict):
            migrate_config(config)   # in memory: a dry run sees the line-ups the real run would write
            chosen.update(config.get("lineups", {}))   # a later session overwrites an earlier one's mode
    return chosen


def seed_last_lineups(directory: Path, dry_run: bool) -> dict[str, Any]:
    """Set the global last choice of every mode the sessions hold a line-up for; returns what it sets."""
    chosen = last_lineups(directory)
    print(f"last choice per mode: {chosen or 'none'}")
    if chosen and not dry_run and not update_settings({"last_lineups": {**persisted_settings()["last_lineups"], **chosen}}):
        raise SystemExit("settings.json could not be written")
    return chosen


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", type=Path, help="the sessions directory (data/sessions)")
    parser.add_argument("--dry-run", action="store_true", help="only print what would change")
    arguments = parser.parse_args()
    count = migrate_directory(arguments.directory, arguments.dry_run)
    print(f"{count} file(s) {'would change' if arguments.dry_run else 'changed'}")
    seed_last_lineups(arguments.directory, arguments.dry_run)
