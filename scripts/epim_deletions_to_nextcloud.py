#!/usr/bin/env python3
"""Carry deletions from EssentialPIM over to Nextcloud.

EPIM's option "delete on the other side" has to stay off: in about every fourth
sync EPIM loses entries while reading them from the server, takes them for
deleted there and deletes them locally. Without the option a deletion in EPIM
never reaches Nextcloud, so this tool does that part from EPIM's recycle bin.

It reads the newest backup EPIM wrote itself, a consistent file that does not
need EPIM to be closed, and takes what entered the recycle bin since the last
run. The first run only notes where the bin stands and deletes nothing. With the
option off EPIM deletes nothing by itself, so all of this is the user's doing.

    AIFRED_CLI_MODE=1 venv/bin/python scripts/epim_deletions_to_nextcloud.py           # shows the plan
    AIFRED_CLI_MODE=1 venv/bin/python scripts/epim_deletions_to_nextcloud.py --apply   # deletes

Settings: data/epim/deletion_sync.json with ssh_host, ssh_port, ssh_user,
backup_dir (on the EPIM machine), backup_glob (the working database only,
e.g. MP.backup_*.epim), work_dir, max_deletions. A run deleting more
than max_deletions stops without deleting anything.
"""

import argparse
import json
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from aifred.lib import firebird_embedded
from aifred.lib.config import DATA_DIR

SETTINGS_FILE = DATA_DIR / "epim" / "deletion_sync.json"
STATE_FILE = DATA_DIR / "epim" / "deletion_sync_state.json"
SEARCH_LIMIT = 50
SSH_CONNECTION_FAILED = 255


@dataclass(frozen=True)
class Settings:
    ssh_host: str
    ssh_port: int
    ssh_user: str
    backup_dir: str
    backup_glob: str
    work_dir: Path
    max_deletions: int


@dataclass(frozen=True)
class EpimEntry:
    title: str
    start: datetime
    series: bool


@dataclass
class Plan:
    delete: list[tuple[EpimEntry, str]] = field(default_factory=list)
    already_gone: list[EpimEntry] = field(default_factory=list)
    still_active: list[EpimEntry] = field(default_factory=list)
    series: list[EpimEntry] = field(default_factory=list)
    ambiguous: list[EpimEntry] = field(default_factory=list)


def load_settings() -> Settings:
    raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return Settings(raw["ssh_host"], int(raw["ssh_port"]), raw["ssh_user"], raw["backup_dir"],
                    raw["backup_glob"], Path(raw["work_dir"]), int(raw["max_deletions"]))


def _naive(value: date | datetime) -> datetime:
    """Nextcloud gives all-day events as a date and others as aware local times;
    EPIM stores naive local times."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime(value.year, value.month, value.day)


def plan_deletions(deleted: list[EpimEntry], active: set[tuple[str, datetime]],
                   find: Callable[[EpimEntry], list[dict[str, Any]]]) -> Plan:
    """Decide per deleted entry. Only an exact, single, non-recurring match in
    Nextcloud is deleted, and only while EPIM holds no active entry like it."""
    plan = Plan()
    for entry in deleted:
        if entry.series:
            plan.series.append(entry)
        elif (entry.title, entry.start) in active:
            plan.still_active.append(entry)
        else:
            matches = [row for row in find(entry)
                       if row["title"] == entry.title and _naive(row["start"]) == entry.start]
            if not matches:
                plan.already_gone.append(entry)
            elif len(matches) > 1:
                plan.ambiguous.append(entry)
            elif matches[0]["recurring"]:
                plan.series.append(entry)
            else:
                plan.delete.append((entry, matches[0]["id"]))
    return plan


def latest_backup(settings: Settings) -> str | None:
    """Path of the newest backup, None while the EPIM machine cannot be reached:
    it is often off, and the next hourly run simply tries again."""
    listing = f"ls -t {shlex.quote(settings.backup_dir)}/{settings.backup_glob}"
    result = subprocess.run(["ssh", "-p", str(settings.ssh_port), "-o", "ConnectTimeout=10",
                             f"{settings.ssh_user}@{settings.ssh_host}", listing],
                            capture_output=True, text=True, timeout=60, check=False)
    if result.returncode == SSH_CONNECTION_FAILED:
        return None
    result.check_returncode()
    return result.stdout.split("\n")[0].strip()


def fetch_restored(settings: Settings, remote: str) -> tuple[Path, Path]:
    """Copy the backup here and restore it into a readable database file."""
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    backup = settings.work_dir / "epim-backup.fbk"
    restored = settings.work_dir / "epim-restored.epim"
    subprocess.run(["scp", "-q", "-P", str(settings.ssh_port),
                    f"{settings.ssh_user}@{settings.ssh_host}:{remote}", str(backup)],
                   timeout=900, check=True)
    firebird_embedded.restore(backup, restored)
    return backup, restored


def read_epim(path: Path, deleted_after: datetime | None) -> tuple[list[EpimEntry], set[tuple[str, datetime]], datetime]:
    """Entries deleted after ``deleted_after``, the keys of all active entries and
    the newest deletion. Without a point in time there is nothing to compare with."""
    con = firebird_embedded.connect(path)
    try:
        cur = con.cursor()
        cur.execute("SELECT MAX(DELETED) FROM TASKS WHERE STATUS = 1")
        # An empty bin leaves no time: whatever enters it later is new.
        newest = cur.fetchone()[0] or datetime.min
        deleted: list[EpimEntry] = []
        if deleted_after is not None:
            cur.execute("SELECT TITLE, STARTTIME, REPEATING FROM TASKS WHERE STATUS = 1 AND DELETED > ?", (deleted_after,))
            deleted = [EpimEntry(title or "", start, bool(repeating)) for title, start, repeating in cur.fetchall()]
        cur.execute("SELECT TITLE, STARTTIME FROM TASKS WHERE STATUS = 0")
        return deleted, {(title or "", start) for title, start in cur.fetchall()}, newest
    finally:
        con.close()


def _report(plan: Plan) -> None:
    print(f"to delete in Nextcloud: {len(plan.delete)}")
    print(f"already gone there: {len(plan.already_gone)}")
    print(f"skipped, active again in EPIM: {len(plan.still_active)}")
    print(f"skipped, part of a series: {len(plan.series)}")
    print(f"skipped, several matches: {len(plan.ambiguous)}")
    for title in sorted({entry.title for entry in plan.series + plan.ambiguous}):
        print(f"  by hand: {title}")


def run(apply: bool) -> int:
    from aifred.plugins.tools.nextcloud import store as store_module

    settings = load_settings()
    remote = latest_backup(settings)
    if remote is None:
        print(f"{settings.ssh_host} not reachable, trying again next run")
        return 0
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    if apply and state.get("backup") == remote:
        print(f"nothing new: {Path(remote).name} is already processed")
        return 0
    print(f"backup: {Path(remote).name}")

    deleted_after = datetime.fromisoformat(state["deleted_after"]) if "deleted_after" in state else None
    if deleted_after is None:
        print("first run: the recycle bin as it stands is the starting point, nothing to delete")

    backup, restored = fetch_restored(settings, remote)
    try:
        deleted, active, newest = read_epim(restored, deleted_after)
    finally:
        restored.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)

    store = store_module.get_store()
    if store is None:
        raise RuntimeError("Nextcloud is not configured")

    def find(entry: EpimEntry) -> list[dict[str, Any]]:
        day = entry.start.date()
        return store.events(day, day, entry.title, SEARCH_LIMIT)

    plan = plan_deletions(deleted, active, find)
    _report(plan)
    if len(plan.delete) > settings.max_deletions:
        print(f"STOPPED: {len(plan.delete)} deletions exceed max_deletions ({settings.max_deletions}), nothing deleted")
        return 1
    if apply:
        for entry, object_id in plan.delete:
            store.delete_event(object_id)
            print(f"deleted: {entry.start:%Y-%m-%d %H:%M} {entry.title}")
        STATE_FILE.write_text(json.dumps({"backup": remote, "deleted_after": newest.isoformat()}), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="delete in Nextcloud, otherwise only show the plan")
    return run(parser.parse_args().apply)


if __name__ == "__main__":
    sys.exit(main())
