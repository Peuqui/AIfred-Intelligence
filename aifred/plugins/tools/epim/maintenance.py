"""EPIM database maintenance on the Mini — command line.

    python -m aifred.plugins.tools.epim.maintenance validate FILE
    python -m aifred.plugins.tools.epim.maintenance rebuild SOURCE DEST
    python -m aifred.plugins.tools.epim.maintenance set-mode FILE read-only|read-write
    python -m aifred.plugins.tools.epim.maintenance snapshot FILE OUT.json
    python -m aifred.plugins.tools.epim.maintenance diff BEFORE.json AFTER.json
    python -m aifred.plugins.tools.epim.maintenance install-mirror INCOMING
    python -m aifred.plugins.tools.epim.maintenance make-fixture SOURCE DEST

``install-mirror`` is what the Aragon push script calls: it rebuilds the
incoming copy (Firebird backup + restore, which reads every record and
blob), compares the record count of every table, switches the result to
read-only and atomically replaces the configured database. The replaced
file stays as ``<name>.previous``; the incoming copy is removed.
"""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import sys
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import fdb.services

from . import firebird
from .settings import load_epim_settings

logger = logging.getLogger(__name__)

_ZLIB_HEADER = b"\x78\x9c"
# Backups must not change their source: no garbage collection in it, and no
# EPIM connect trigger (it would register the backup session in CONNECTIONS,
# and the backup would carry that row along).
_BACKUP_OPTIONS = {"collect_garbage": 0, "no_db_triggers": 1}


class MaintenanceError(RuntimeError):
    pass


# ── engine operations ─────────────────────────────────────────────

def _service_lines(start: Any) -> list[str]:
    svc = firebird.services()
    try:
        start(svc)
        lines: list[str] = svc.readlines()
        return lines
    finally:
        svc.close()


def validate(path: Path) -> None:
    """Firebird's read-only structure validation (``gfix -v -full -n``).
    The engine reports nothing for a healthy file."""
    lines = _service_lines(lambda svc: svc.repair(
        str(path), read_only_validation=1, validate_database=1, validate_record_fragments=1))
    if lines:
        raise MaintenanceError(f"validation of {path} reported:\n" + "\n".join(lines))


def set_access_mode(path: Path, read_only: bool) -> None:
    mode = fdb.services.ACCESS_READ_ONLY if read_only else fdb.services.ACCESS_READ_WRITE
    _service_lines(lambda svc: svc.set_access_mode(str(path), mode))


def _user_tables(cur: Any) -> list[str]:
    cur.execute("SELECT TRIM(RDB$RELATION_NAME) FROM RDB$RELATIONS "
                "WHERE RDB$SYSTEM_FLAG = 0 AND RDB$VIEW_BLR IS NULL ORDER BY 1")
    return [row[0] for row in cur.fetchall()]


def record_counts(path: Path) -> dict[str, int]:
    con = firebird.connect(path)
    try:
        tr = con.trans(default_tpb=fdb.ISOLATION_LEVEL_READ_COMMITED_RO)
        tr.begin()
        cur = tr.cursor()
        counts = {}
        for table in _user_tables(cur):
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            counts[table] = int(cur.fetchone()[0])
        tr.commit()
        return counts
    finally:
        con.close()


def rebuild(source: Path, dest: Path) -> None:
    """Backup + restore ``source`` into the new file ``dest`` and verify it.

    The backup reads every record and blob; the restore writes a compact
    file with fresh indexes. Every table must keep its record count."""
    if dest.exists():
        raise MaintenanceError(f"{dest} already exists")
    backup_file = dest.with_name(dest.name + ".fbk")
    if backup_file.exists():
        raise MaintenanceError(f"{backup_file} already exists")
    try:
        for step, start in (
            ("backup", lambda svc: svc.backup(str(source), str(backup_file), **_BACKUP_OPTIONS)),
            ("restore", lambda svc: svc.restore(str(backup_file), str(dest), no_db_triggers=1)),
        ):
            lines = _service_lines(start)
            errors = [line for line in lines if "ERROR" in line.upper()]
            if errors:
                raise MaintenanceError(f"{step} of {source} failed:\n" + "\n".join(errors))
    finally:
        if backup_file.exists():
            backup_file.unlink()
    before, after = record_counts(source), record_counts(dest)
    if before != after:
        differing = sorted(t for t in before.keys() | after.keys() if before.get(t) != after.get(t))
        raise MaintenanceError(f"record counts differ after rebuild in: {', '.join(differing)}")
    validate(dest)


@contextmanager
def _install_lock(directory: Path) -> Iterator[None]:
    with open(directory / ".epim-install.lock", "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def install_mirror(incoming: Path) -> Path:
    """Replace the configured database with a verified copy of ``incoming``.

    Refused while any module is writable: then the Mini copy may hold
    AIfred's own changes, which a mirror from Aragon would overwrite."""
    settings = load_epim_settings()
    if settings.db_path is None:
        raise MaintenanceError("no EPIM database path is configured")
    if settings.modules.any_write:
        raise MaintenanceError("an EPIM module is writable; a mirror would overwrite AIfred's changes")
    target = settings.db_path
    staged = target.with_name(target.name + ".staged")
    previous = target.with_name(target.name + ".previous")
    with _install_lock(target.parent):
        rebuild(incoming, staged)
        set_access_mode(staged, read_only=True)
        if target.exists():
            if previous.exists():
                previous.unlink()
            # A hard link keeps the old file without a moment in which the
            # configured path does not exist.
            os.link(target, previous)
        os.replace(staged, target)
        incoming.unlink()
    return target


# ── test fixture ──────────────────────────────────────────────────

# Lookup tables an EPIM database needs before entries can be written, in
# insert order (EPIM's triggers resolve the editing user from USERS).
FIXTURE_TABLES = (
    "USERS", "ACCOUNTS", "CALENDARS", "CALENDARUSERDATA", "CATEGORIES",
    "CONTACTFIELDS", "CONTACTFIELDUSERDATA", "TODOLISTS", "TODOLISTUSERDATA",
    "NOTETREES", "NOTETREEUSERDATA", "PASSENTRYFIELDS", "PASSENTRYFIELDUSERDATA", "DBPARAMS",
)


def make_fixture(source: Path, dest: Path) -> None:
    """An entry-free EPIM database for tests: the full schema (triggers,
    views, procedures) of ``source`` plus its lookup tables."""
    if dest.exists():
        raise MaintenanceError(f"{dest} already exists")
    backup_file = dest.with_name(dest.name + ".fbk")
    try:
        _service_lines(lambda svc: svc.backup(str(source), str(backup_file), metadata_only=1, **_BACKUP_OPTIONS))
        _service_lines(lambda svc: svc.restore(str(backup_file), str(dest), no_db_triggers=1))
    finally:
        if backup_file.exists():
            backup_file.unlink()
    src, dst = firebird.connect(source), firebird.connect(dest)
    try:
        src_tr = src.trans(default_tpb=fdb.ISOLATION_LEVEL_READ_COMMITED_RO)
        src_tr.begin()
        src_cur, dst_cur = src_tr.cursor(), dst.cursor()
        for table in FIXTURE_TABLES:
            src_cur.execute(f'SELECT * FROM "{table}"')
            columns = [d[0].strip() for d in src_cur.description]
            rows = [tuple(v.read() if hasattr(v, "read") else v for v in row) for row in src_cur.fetchall()]
            if rows:
                dst_cur.executemany(
                    f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES ({", ".join("?" * len(columns))})',
                    rows)
        dst.commit()
        src_tr.commit()
    finally:
        src.close()
        dst.close()


# ── snapshots for the reference protocol ──────────────────────────

def _primary_keys(cur: Any) -> dict[str, list[str]]:
    cur.execute(
        "SELECT TRIM(rc.RDB$RELATION_NAME), TRIM(s.RDB$FIELD_NAME) FROM RDB$RELATION_CONSTRAINTS rc "
        "JOIN RDB$INDEX_SEGMENTS s ON s.RDB$INDEX_NAME = rc.RDB$INDEX_NAME "
        "WHERE rc.RDB$CONSTRAINT_TYPE = 'PRIMARY KEY' ORDER BY 1, s.RDB$FIELD_POSITION")
    keys: dict[str, list[str]] = {}
    for table, column in cur.fetchall():
        keys.setdefault(table, []).append(column)
    return keys


def _snapshot_value(value: Any) -> Any:
    if hasattr(value, "read"):
        value = value.read()
    if isinstance(value, bytes):
        out: dict[str, Any] = {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()[:16]}
        if value.startswith(_ZLIB_HEADER):
            try:
                out["zlib_text"] = zlib.decompress(value).decode("latin-1")
            except zlib.error:
                out["zlib_error"] = True
        return out
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def snapshot(path: Path) -> dict[str, Any]:
    """Every row of every user table, keyed by primary key (or by the whole
    row for tables without one)."""
    con = firebird.connect(path)
    try:
        tr = con.trans(default_tpb=fdb.ISOLATION_LEVEL_READ_COMMITED_RO)
        tr.begin()
        cur = tr.cursor()
        keys = _primary_keys(cur)
        out: dict[str, Any] = {}
        for table in _user_tables(cur):
            cur.execute(f'SELECT * FROM "{table}"')
            columns = [d[0].strip() for d in cur.description]
            key_columns = keys.get(table, columns)
            rows: dict[str, dict[str, Any]] = {}
            for raw in cur.fetchall():
                row = {c: _snapshot_value(v) for c, v in zip(columns, raw)}
                rows[json.dumps([row[k] for k in key_columns], default=str)] = row
            out[table] = {"key": key_columns, "rows": rows}
        tr.commit()
        return out
    finally:
        con.close()


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for table in sorted(before.keys() | after.keys()):
        old = before.get(table, {"rows": {}})["rows"]
        new = after.get(table, {"rows": {}})["rows"]
        for key in sorted(new.keys() - old.keys()):
            values = {c: v for c, v in new[key].items() if v is not None}
            lines.append(f"+ {table} {key}: {json.dumps(values, ensure_ascii=False)}")
        for key in sorted(old.keys() - new.keys()):
            lines.append(f"- {table} {key}")
        for key in sorted(old.keys() & new.keys()):
            changed = {c: [old[key].get(c), v] for c, v in new[key].items() if old[key].get(c) != v}
            if changed:
                lines.append(f"~ {table} {key}: {json.dumps(changed, ensure_ascii=False)}")
    return lines


# ── command line ──────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aifred.plugins.tools.epim.maintenance")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate").add_argument("file", type=Path)
    p = sub.add_parser("rebuild")
    p.add_argument("source", type=Path)
    p.add_argument("dest", type=Path)
    p = sub.add_parser("set-mode")
    p.add_argument("file", type=Path)
    p.add_argument("mode", choices=("read-only", "read-write"))
    p = sub.add_parser("snapshot")
    p.add_argument("file", type=Path)
    p.add_argument("out", type=Path)
    p = sub.add_parser("diff")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    sub.add_parser("install-mirror").add_argument("incoming", type=Path)
    p = sub.add_parser("make-fixture")
    p.add_argument("source", type=Path)
    p.add_argument("dest", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command == "validate":
        validate(args.file)
        print(f"{args.file}: no validation findings")
    elif args.command == "rebuild":
        rebuild(args.source, args.dest)
        print(f"{args.dest}: rebuilt and verified")
    elif args.command == "set-mode":
        set_access_mode(args.file, read_only=args.mode == "read-only")
        print(f"{args.file}: {args.mode}")
    elif args.command == "snapshot":
        args.out.write_text(json.dumps(snapshot(args.file), ensure_ascii=False), encoding="utf-8")
        print(f"{args.out}: written")
    elif args.command == "diff":
        before = json.loads(args.before.read_text(encoding="utf-8"))
        after = json.loads(args.after.read_text(encoding="utf-8"))
        print("\n".join(diff(before, after)) or "no differences")
    elif args.command == "install-mirror":
        target = install_mirror(args.incoming)
        print(f"{target}: mirror installed (read-only)")
    elif args.command == "make-fixture":
        make_fixture(args.source, args.dest)
        print(f"{args.dest}: test fixture written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
