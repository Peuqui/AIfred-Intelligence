"""EPIM database access (EssentialPIM, embedded Firebird 2.5).

Every call runs in its own short transaction and ends it: reads in a
read-only read-committed transaction, writes in a read-committed write
transaction with a lock timeout that commits or rolls back as one unit.
Nothing keeps a transaction open between calls (an open transaction holds
back Firebird's garbage collection and, after a crash, the oldest
interesting transaction).

Writes are refused unless the plugin's write switch is on; the tool layer
does not even offer write tools then. The database file is opened by path;
when it is replaced (a new mirror from Aragon), the next call reconnects.

EPIM's own conventions this module follows (verified on the database):
- STATUS 0 = live, 1 = deleted (soft delete with a DELETED timestamp).
- Recurring series: the master row points to itself (IDPARENT = IDTASK),
  EPIM materializes every occurrence as its own row pointing to the master.
  Series are edited in EPIM only.
- "No date" is stored as 1899-12-30 (Delphi's zero date), not NULL.
- Note text is shown from FORMATEDTEXT (zlib-compressed RTF); TEXT is the
  plain copy used for search and preview.
"""

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import fdb

from ....lib.plugin_modules import ModuleAccess
from . import codec, firebird
from .contact_fields import CONTACT_FIELD_LABELS, DATE_FIELD_TYPE, DELPHI_EPOCH
from .settings import load_epim_settings

logger = logging.getLogger(__name__)

LOCK_TIMEOUT_SEC = 10
LIMIT_MAX = 500
_ZERO_DATE = datetime(1899, 12, 30)  # noqa: DTZ001 — EPIM stores naive local time
_LIVE, _DELETED = 0, 1


class EpimWriteDisabled(PermissionError):
    """The module is not writable in the plugin settings."""


class EpimModuleDisabled(PermissionError):
    """The module is switched off in the plugin settings."""


class EpimRefused(ValueError):
    """The operation would break EPIM's data model and is refused."""


@dataclass(frozen=True)
class LookupEntry:
    id: int
    name: str


def _write_tpb() -> fdb.TPB:
    tpb = fdb.TPB()
    tpb.access_mode = fdb.isc_tpb_write
    tpb.isolation_level = (fdb.isc_tpb_read_committed, fdb.isc_tpb_rec_version)
    tpb.lock_resolution = fdb.isc_tpb_wait
    tpb.lock_timeout = LOCK_TIMEOUT_SEC
    return tpb


def _clamp_limit(limit: int) -> int:
    # FIRST takes no bound parameter, so the value is interpolated — as int only.
    return max(1, min(int(limit), LIMIT_MAX))


def _like_pattern(term: str) -> str:
    """``%term%`` with LIKE wildcards escaped; every LIKE using it carries ``ESCAPE '\\'``."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _now() -> datetime:
    return datetime.now()  # noqa: DTZ005 — EPIM stores naive local wall-clock time


def _date_out(value: datetime | None) -> datetime | None:
    """EPIM's zero date means "no date"."""
    return None if value is None or value == _ZERO_DATE else value


def _generate_id(last: list[int]) -> int:
    """64-bit ID from the millisecond clock plus 10 random bits, strictly
    increasing within the process (EPIM's IDs are random 64-bit values; the
    primary keys reject a collision loudly)."""
    import random
    candidate = (int(time.time() * 1000) << 10) | random.randint(0, 1023)
    if candidate <= last[0]:
        candidate = last[0] + 1
    last[0] = candidate
    return candidate


class EpimDatabase:
    """One EPIM database file. All methods are safe to call from any thread;
    a lock serializes them (Firebird connections are not thread-safe)."""

    def __init__(self, db_path: Path, modules: ModuleAccess) -> None:
        self._db_path = db_path
        self._modules = modules
        self._lock = threading.RLock()
        self._in_transaction = False
        self._con: fdb.Connection | None = None
        self._file_ident: tuple[int, int] | None = None
        self._read_tr: Any = None
        self._write_tr: Any = None
        self._last_id = [0]

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def modules(self) -> ModuleAccess:
        return self._modules

    def can_read(self, module: str) -> bool:
        return self._modules.can_read(module)

    def can_write(self, module: str) -> bool:
        return self._modules.can_write(module)

    @property
    def any_write(self) -> bool:
        return self._modules.any_write

    def _allow(self, module: str, write: bool) -> None:
        """Second guard behind the tool layer: the settings decide per module."""
        if not self.can_read(module):
            raise EpimModuleDisabled(f"the EPIM module {module!r} is switched off in the plugin settings")
        if write and not self.can_write(module):
            raise EpimWriteDisabled(f"the EPIM module {module!r} is read-only in the plugin settings")

    # ── connection and transactions ───────────────────────────────

    def close(self) -> None:
        with self._lock:
            self._discard_connection()

    def _discard_connection(self) -> None:
        con, self._con = self._con, None
        self._read_tr = self._write_tr = None
        self._file_ident = None
        if con is not None:
            try:
                con.close()
            except fdb.Error as exc:
                logger.warning("EPIM: closing the connection failed: %s", exc)

    def _ensure_connection(self) -> None:
        stat = self._db_path.stat()
        ident = (stat.st_dev, stat.st_ino)
        if self._con is not None and ident != self._file_ident:
            logger.info("EPIM database file was replaced, reconnecting: %s", self._db_path)
            self._discard_connection()
        if self._con is None:
            con = firebird.connect(self._db_path)
            self._read_tr = con.trans(default_tpb=fdb.ISOLATION_LEVEL_READ_COMMITED_RO)
            self._write_tr = con.trans(default_tpb=_write_tpb())
            self._con, self._file_ident = con, ident
            logger.info("EPIM database connected: %s (%s)", self._db_path,
                        ", ".join(f"{m} {level}" for m, level in self._modules.levels.items()))

    @contextmanager
    def _transaction(self, write: bool) -> Iterator[Any]:
        if write and not self.any_write:
            raise EpimWriteDisabled("no EPIM module is writable in the plugin settings")
        with self._lock:
            if self._in_transaction:
                # A nested begin() would end the outer transaction half-way.
                raise RuntimeError("nested EPIM transaction")
            self._in_transaction = True
            try:
                self._ensure_connection()
                tr = self._write_tr if write else self._read_tr
                tr.begin()
                cur = tr.cursor()
                try:
                    yield cur
                except BaseException:
                    cur.close()
                    try:
                        tr.rollback()
                    except fdb.Error as exc:
                        logger.error("EPIM: rollback failed: %s", exc)
                        self._discard_connection()
                    raise
                else:
                    cur.close()
                    tr.commit()
            except fdb.DatabaseError:
                self._discard_connection()
                raise
            finally:
                self._in_transaction = False

    def _read(self) -> Any:
        return self._transaction(write=False)

    def _write(self) -> Any:
        return self._transaction(write=True)

    def _new_id(self) -> int:
        return _generate_id(self._last_id)

    @staticmethod
    def _current_user_id(cur: Any) -> int:
        """EPIM's user row for the connection user (its triggers resolve the
        editor the same way)."""
        cur.execute("SELECT IDUSER FROM USERS WHERE NAME = CURRENT_USER")
        row = cur.fetchone()
        if row is None:
            raise EpimRefused(f"EPIM has no user {firebird.EPIM_USER!r} in this database")
        return int(row[0])

    @staticmethod
    def _single_account(cur: Any) -> int:
        cur.execute("SELECT IDACCOUNT FROM ACCOUNTS")
        rows = cur.fetchall()
        if len(rows) != 1:
            raise EpimRefused(f"expected exactly one EPIM account, found {len(rows)}")
        return int(rows[0][0])

    @staticmethod
    def _rows(cur: Any) -> list[dict[str, Any]]:
        columns = [desc[0].strip().lower() for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    # ── lookup tables ─────────────────────────────────────────────

    @staticmethod
    def _lookup(cur: Any, sql: str) -> list[LookupEntry]:
        cur.execute(sql)
        return [LookupEntry(int(r[0]), str(r[1]).strip()) for r in cur.fetchall()]

    def _categories(self, cur: Any) -> list[LookupEntry]:
        return self._lookup(cur, "SELECT IDCATEGORY, NAME FROM CATEGORIES "
                                 "WHERE NAME IS NOT NULL ORDER BY CATEGORYINDEX")

    def _calendars(self, cur: Any) -> list[LookupEntry]:
        # Calendars without a name are EPIM-internal; READONLY ones take no entries.
        return self._lookup(cur, "SELECT IDCALENDAR, NAME FROM CALENDARS "
                                 "WHERE NAME IS NOT NULL AND COALESCE(READONLY, 0) = 0")

    def _todolists(self, cur: Any) -> list[LookupEntry]:
        return self._lookup(cur, "SELECT IDTODOLIST, NAME FROM TODOLISTS WHERE NAME IS NOT NULL")

    def _notetrees(self, cur: Any) -> list[LookupEntry]:
        return self._lookup(cur, "SELECT IDNOTETREE, NAME FROM NOTETREES WHERE NAME IS NOT NULL")

    def lookups(self) -> dict[str, list[LookupEntry]]:
        """The lookup lists of the readable modules, in one transaction:
        categories (calendar, todos), calendars, todo lists, note trees and
        the writable contact fields."""
        out: dict[str, list[LookupEntry]] = {}
        with self._read() as cur:
            if self.can_read("calendar") or self.can_read("todos"):
                out["categories"] = self._categories(cur)
            if self.can_read("calendar"):
                out["calendars"] = self._calendars(cur)
            if self.can_read("todos"):
                out["todolists"] = self._todolists(cur)
            if self.can_read("notes"):
                out["notetrees"] = self._notetrees(cur)
            if self.can_read("contacts"):
                fields = self._contact_fields(cur)
                out["contact_fields"] = [LookupEntry(fid, label) for label, fid in sorted(fields.id_by_label.items())]
        return out

    @staticmethod
    def _resolve(entries: list[LookupEntry], name: str, what: str) -> int:
        matches = [e.id for e in entries if e.name.casefold() == name.strip().casefold()]
        names = ", ".join(e.name for e in entries)
        if not matches:
            raise EpimRefused(f"unknown {what} {name!r}; available: {names}")
        if len(matches) > 1:
            raise EpimRefused(f"{what} name {name!r} exists {len(matches)} times; rename it in EPIM")
        return matches[0]

    @staticmethod
    def _single(entries: list[LookupEntry], what: str) -> int:
        if len(entries) == 1:
            return entries[0].id
        names = ", ".join(e.name for e in entries)
        raise EpimRefused(f"several {what}s exist, name one: {names}" if entries
                          else f"this database has no {what}")

    # ── tasks (calendar) ──────────────────────────────────────────

    _TASK_COLUMNS = (
        "t.IDTASK AS id, t.TITLE AS title, t.STARTTIME AS start_time, t.ENDTIME AS end_time, "
        "t.ALLDAY AS all_day, t.LOCATION AS location, t.TEXT AS text, t.TAGS AS tags, "
        "t.REPEATING AS repeating, c.NAME AS calendar, cat.NAME AS category, "
        "t.CREATED AS created, t.LASTCHANGED AS last_changed "
        "FROM TASKS t "
        "LEFT JOIN CALENDARS c ON c.IDCALENDAR = t.CALENDAR "
        "LEFT JOIN CATEGORIES cat ON cat.IDCATEGORY = t.CATEGORY "
    )

    @staticmethod
    def _task_out(row: dict[str, Any]) -> dict[str, Any]:
        row["all_day"] = bool(row["all_day"])
        row["recurring"] = bool(row.pop("repeating"))
        start, end = _date_out(row["start_time"]), _date_out(row["end_time"])
        if row["all_day"] and start is not None and end is not None:
            # Same rule as the input: dates, and the inclusive last day
            # (EPIM stores the midnight after it).
            row["start_time"], row["end_time"] = start.date(), (end - timedelta(days=1)).date()
        else:
            row["start_time"], row["end_time"] = start, end
        return row

    def search_tasks(self, title: str | None, date_from: date | None, date_to: date | None,
                     limit: int) -> list[dict[str, Any]]:
        self._allow("calendar", write=False)
        conditions = ["t.STATUS = 0"]
        params: list[Any] = []
        if title:
            conditions.append("UPPER(t.TITLE) LIKE UPPER(?) ESCAPE '\\'")
            params.append(_like_pattern(title))
        if date_from:
            # Overlap: an event that started earlier but reaches into the window matches.
            conditions.append("t.ENDTIME >= ?")
            params.append(datetime.combine(date_from, datetime.min.time()))
        if date_to:
            conditions.append("t.STARTTIME < ?")
            params.append(datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
        sql = (f"SELECT FIRST {_clamp_limit(limit)} {self._TASK_COLUMNS}"
               f"WHERE {' AND '.join(conditions)} ORDER BY t.STARTTIME")
        with self._read() as cur:
            cur.execute(sql, params)
            return [self._task_out(r) for r in self._rows(cur)]

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        self._allow("calendar", write=False)
        with self._read() as cur:
            cur.execute(f"SELECT {self._TASK_COLUMNS}WHERE t.IDTASK = ? AND t.STATUS = 0", (task_id,))
            rows = self._rows(cur)
        return self._task_out(rows[0]) if rows else None

    @staticmethod
    def _check_range(start: datetime, end: datetime) -> None:
        if end < start:
            raise EpimRefused(f"end {end:%Y-%m-%d %H:%M} lies before start {start:%Y-%m-%d %H:%M}")

    def create_task(self, *, title: str, start: datetime, end: datetime, all_day: bool,
                    location: str | None, text: str | None, category: str | None,
                    calendar: str | None, tags: str | None) -> int:
        self._allow("calendar", write=True)
        self._check_range(start, end)
        with self._write() as cur:
            calendars = self._calendars(cur)
            calendar_id = (self._resolve(calendars, calendar, "calendar") if calendar
                           else self._single(calendars, "calendar"))
            category_id = self._resolve(self._categories(cur), category, "category") if category else 0
            user_id = self._current_user_id(cur)
            new_id = self._new_id()
            now = _now()
            cur.execute(
                "INSERT INTO TASKS (IDTASK, IDPARENT, READACCESS, WRITEACCESS, CREATED, LASTCHANGED, "
                "IDCREATOR, IDEDITOR, STATUS, TITLE, STARTTIME, ENDTIME, TEXT, LOCATION, PRIORITY, "
                "CATEGORY, COMPLETION, EXCLUSIVE, REPEATING, ALLDAY, TAGS, CALENDAR) "
                "VALUES (?, 0, -1, -1, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, 0, ?, 0, 0, 0, ?, ?, ?)",
                (new_id, now, now, user_id, user_id, title, start, end, text, location,
                 category_id, 1 if all_day else 0, tags, calendar_id),
            )
        logger.info("EPIM: created task %d", new_id)
        return new_id

    def _live_single_task(self, cur: Any, task_id: int) -> dict[str, Any]:
        cur.execute("SELECT IDPARENT, REPEATING, STARTTIME, ENDTIME, ALLDAY FROM TASKS "
                    "WHERE IDTASK = ? AND STATUS = 0", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise EpimRefused(f"task {task_id} not found")
        if row[0] != 0 or row[1] != 0:
            raise EpimRefused(f"task {task_id} belongs to a recurring series; edit series in EPIM")
        return {"start": row[2], "end": row[3], "all_day": bool(row[4])}

    def update_task(self, task_id: int, changes: dict[str, Any]) -> None:
        """``changes`` keys: title, start, end, all_day, location, text,
        category, calendar, tags. A new start without a new end moves the
        event and keeps its duration."""
        self._allow("calendar", write=True)
        with self._write() as cur:
            current = self._live_single_task(cur, task_id)
            columns: dict[str, Any] = {}
            start = changes.get("start", current["start"])
            end = changes.get("end", current["end"])
            if "start" in changes and "end" not in changes:
                end = current["end"] + (start - current["start"])
            if "start" in changes or "end" in changes:
                self._check_range(start, end)
                columns["STARTTIME"], columns["ENDTIME"] = start, end
            for key, column in (("title", "TITLE"), ("location", "LOCATION"),
                                ("text", "TEXT"), ("tags", "TAGS")):
                if key in changes:
                    columns[column] = changes[key]
            if "all_day" in changes:
                columns["ALLDAY"] = 1 if changes["all_day"] else 0
            if "category" in changes:
                columns["CATEGORY"] = (self._resolve(self._categories(cur), changes["category"], "category")
                                       if changes["category"] else 0)
            if "calendar" in changes:
                columns["CALENDAR"] = self._resolve(self._calendars(cur), changes["calendar"], "calendar")
            self._update_columns(cur, "TASKS", "IDTASK", task_id, columns)

    def delete_task(self, task_id: int) -> None:
        self._allow("calendar", write=True)
        with self._write() as cur:
            self._live_single_task(cur, task_id)
            self._soft_delete(cur, "TASKS", "IDTASK", task_id)

    # ── shared write helpers ──────────────────────────────────────

    @staticmethod
    def _update_columns(cur: Any, table: str, id_col: str, row_id: int, columns: dict[str, Any]) -> None:
        """``table``/``id_col``/column names are internal constants, never model input."""
        if not columns:
            raise EpimRefused("nothing to change")
        columns = {**columns, "LASTCHANGED": _now()}
        assignments = ", ".join(f"{name} = ?" for name in columns)
        cur.execute(f"UPDATE {table} SET {assignments} WHERE {id_col} = ? AND STATUS = 0",
                    (*columns.values(), row_id))

    @staticmethod
    def _soft_delete(cur: Any, table: str, id_col: str, row_id: int) -> None:
        # EPIM leaves LASTCHANGED alone when it moves an entry to the trash.
        cur.execute(f"UPDATE {table} SET STATUS = 1, DELETED = ? WHERE {id_col} = ? AND STATUS = 0",
                    (_now(), row_id))

    @staticmethod
    def _require_live(cur: Any, table: str, id_col: str, row_id: int, what: str) -> None:
        cur.execute(f"SELECT 1 FROM {table} WHERE {id_col} = ? AND STATUS = 0", (row_id,))
        if cur.fetchone() is None:
            raise EpimRefused(f"{what} {row_id} not found")

    @staticmethod
    def _require_no_live_children(cur: Any, table: str, id_col: str, row_id: int, what: str) -> None:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE IDPARENT = ? AND {id_col} <> ? AND STATUS = 0",
                    (row_id, row_id))
        children = int(cur.fetchone()[0])
        if children:
            raise EpimRefused(f"{what} {row_id} has {children} sub-entries; delete or move them in EPIM first")

    # ── contacts ──────────────────────────────────────────────────

    def _contact_fields(self, cur: Any) -> "ContactFieldMap":
        cur.execute("SELECT IDFIELD, NAME, FIELDTYPE FROM CONTACTFIELDS WHERE ENABLED = 1")
        return ContactFieldMap.build(cur.fetchall())

    def _contact_out(self, fields: "ContactFieldMap", row: tuple[Any, ...]) -> dict[str, Any]:
        contact_id, subject, fieldsdata, fieldsdata2, tags, created, last_changed = row
        if fieldsdata2 is not None:
            raise EpimRefused(f"contact {contact_id} stores its fields in FIELDSDATA2, which this plugin cannot read")
        decoded = codec.decode(fieldsdata or "")
        out: dict[str, Any] = {
            "id": contact_id, "name": subject, "fields": fields.to_labels(decoded.items),
            "tags": tags, "created": created, "last_changed": last_changed,
        }
        if decoded.error is not None:
            out["field_data_error"] = f"stored fields are incomplete ({decoded.error})"
        return out

    _CONTACT_COLUMNS = "IDCONTACT, SUBJECT, FIELDSDATA, FIELDSDATA2, TAGS, CREATED, LASTCHANGED"

    def search_contacts(self, name: str | None, limit: int) -> list[dict[str, Any]]:
        self._allow("contacts", write=False)
        conditions = ["STATUS = 0"]
        params: list[Any] = []
        if name:
            conditions.append("UPPER(SUBJECT) LIKE UPPER(?) ESCAPE '\\'")
            params.append(_like_pattern(name))
        with self._read() as cur:
            fields = self._contact_fields(cur)
            cur.execute(f"SELECT FIRST {_clamp_limit(limit)} {self._CONTACT_COLUMNS} FROM CONTACTS "
                        f"WHERE {' AND '.join(conditions)} ORDER BY SUBJECT", params)
            return [self._contact_out(fields, row) for row in cur.fetchall()]

    def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        self._allow("contacts", write=False)
        with self._read() as cur:
            fields = self._contact_fields(cur)
            cur.execute(f"SELECT {self._CONTACT_COLUMNS} FROM CONTACTS WHERE IDCONTACT = ? AND STATUS = 0",
                        (contact_id,))
            row = cur.fetchone()
            return self._contact_out(fields, row) if row else None

    def create_contact(self, *, name: str | None, fields: dict[str, str | None], tags: str | None) -> int:
        self._allow("contacts", write=True)
        with self._write() as cur:
            field_map = self._contact_fields(cur)
            updates = field_map.to_ids(fields)
            fieldsdata = codec.merge("", updates)
            subject = name or field_map.display_name(codec.decode(fieldsdata).items)
            if not subject:
                raise EpimRefused("a contact needs a name or a first/last name")
            user_id = self._current_user_id(cur)
            account_id = self._single_account(cur)
            new_id = self._new_id()
            now = _now()
            cur.execute(
                "INSERT INTO CONTACTS (IDCONTACT, IDACCOUNT, READACCESS, WRITEACCESS, CREATED, LASTCHANGED, "
                "IDCREATOR, IDEDITOR, STATUS, SUBJECT, COLLADR, FIELDSDATA, TAGS) "
                "VALUES (?, ?, -1, -1, ?, ?, ?, ?, 0, ?, 0, ?, ?)",
                (new_id, account_id, now, now, user_id, user_id, subject, fieldsdata, tags),
            )
        logger.info("EPIM: created contact %d", new_id)
        return new_id

    def update_contact(self, contact_id: int, *, name: str | None, fields: dict[str, str | None],
                       tags: str | None, tags_given: bool) -> None:
        self._allow("contacts", write=True)
        with self._write() as cur:
            cur.execute("SELECT FIELDSDATA, FIELDSDATA2 FROM CONTACTS WHERE IDCONTACT = ? AND STATUS = 0",
                        (contact_id,))
            row = cur.fetchone()
            if row is None:
                raise EpimRefused(f"contact {contact_id} not found")
            if row[1] is not None:
                raise EpimRefused(f"contact {contact_id} stores its fields in FIELDSDATA2; edit it in EPIM")
            field_map = self._contact_fields(cur)
            columns: dict[str, Any] = {}
            if fields:
                try:
                    merged = codec.merge(row[0] or "", field_map.to_ids(fields))
                except codec.MalformedFieldData as exc:
                    raise EpimRefused(f"contact {contact_id} has damaged field data ({exc}); repair it in EPIM") from exc
                columns["FIELDSDATA"] = merged
                if name is None and field_map.changes_display_name(fields):
                    name = field_map.display_name(codec.decode(merged).items)
            if name is not None:
                columns["SUBJECT"] = name
            if tags_given:
                columns["TAGS"] = tags
            self._update_columns(cur, "CONTACTS", "IDCONTACT", contact_id, columns)

    def delete_contact(self, contact_id: int) -> None:
        self._allow("contacts", write=True)
        with self._write() as cur:
            self._require_live(cur, "CONTACTS", "IDCONTACT", contact_id, "contact")
            self._soft_delete(cur, "CONTACTS", "IDCONTACT", contact_id)

    # ── notes ─────────────────────────────────────────────────────

    def search_notes(self, query: str | None, limit: int) -> list[dict[str, Any]]:
        self._allow("notes", write=False)
        conditions = ["n.STATUS = 0"]
        params: list[Any] = []
        if query:
            pattern = _like_pattern(query)
            conditions.append(
                "(UPPER(n.TITLE) LIKE UPPER(?) ESCAPE '\\' OR n.IDNOTE IN "
                "(SELECT IDNOTE FROM NOTETABS WHERE STATUS = 0 AND "
                "(UPPER(TEXT) LIKE UPPER(?) ESCAPE '\\' OR UPPER(NAME) LIKE UPPER(?) ESCAPE '\\')))"
            )
            params.extend([pattern, pattern, pattern])
        with self._read() as cur:
            cur.execute(
                f"SELECT FIRST {_clamp_limit(limit)} n.IDNOTE AS id, n.TITLE AS title, n.TAGS AS tags, "
                f"nt.NAME AS note_tree, n.CREATED AS created, n.LASTCHANGED AS last_changed "
                f"FROM NOTES n LEFT JOIN NOTETREES nt ON nt.IDNOTETREE = n.IDNOTETREE "
                f"WHERE {' AND '.join(conditions)} ORDER BY n.TITLE", params)
            return self._rows(cur)

    def get_note(self, note_id: int) -> dict[str, Any] | None:
        self._allow("notes", write=False)
        with self._read() as cur:
            cur.execute("SELECT n.IDNOTE AS id, n.TITLE AS title, n.TAGS AS tags, nt.NAME AS note_tree, "
                        "n.CREATED AS created, n.LASTCHANGED AS last_changed FROM NOTES n "
                        "LEFT JOIN NOTETREES nt ON nt.IDNOTETREE = n.IDNOTETREE "
                        "WHERE n.IDNOTE = ? AND n.STATUS = 0", (note_id,))
            rows = self._rows(cur)
            if not rows:
                return None
            note = rows[0]
            cur.execute("SELECT IDNOTETAB, NAME, TEXT, TEXT2 FROM NOTETABS "
                        "WHERE IDNOTE = ? AND STATUS = 0 ORDER BY IDNOTETAB", (note_id,))
            tabs = []
            for tab_id, tab_name, text, text2 in cur.fetchall():
                if text2 is not None:
                    raise EpimRefused(f"note tab {tab_id} keeps its text in TEXT2, which this plugin cannot read")
                tabs.append({"id": tab_id, "name": tab_name, "text": text})
            note["tabs"] = tabs
            return note

    def create_note(self, *, title: str, text: str, tab_name: str, note_tree: str | None,
                    tags: str | None) -> int:
        self._allow("notes", write=True)
        raise EpimRefused("creating notes is not available until EPIM's note format is verified")

    def update_note(self, note_id: int, *, title: str | None, tags: str | None, tags_given: bool) -> None:
        self._allow("notes", write=True)
        with self._write() as cur:
            self._require_live(cur, "NOTES", "IDNOTE", note_id, "note")
            columns: dict[str, Any] = {}
            if title is not None:
                columns["TITLE"] = title
            if tags_given:
                columns["TAGS"] = tags
            self._update_columns(cur, "NOTES", "IDNOTE", note_id, columns)

    def update_note_tab(self, tab_id: int, *, name: str | None, text: str | None,
                        append_text: str | None) -> None:
        self._allow("notes", write=True)
        if text is not None or append_text is not None:
            raise EpimRefused("changing note text is not available until EPIM's note format is verified")
        with self._write() as cur:
            self._require_live(cur, "NOTETABS", "IDNOTETAB", tab_id, "note tab")
            self._update_columns(cur, "NOTETABS", "IDNOTETAB", tab_id, {"NAME": name} if name is not None else {})

    def delete_note(self, note_id: int) -> None:
        self._allow("notes", write=True)
        with self._write() as cur:
            self._require_live(cur, "NOTES", "IDNOTE", note_id, "note")
            self._require_no_live_children(cur, "NOTES", "IDNOTE", note_id, "note")
            now = _now()
            cur.execute("UPDATE NOTETABS SET STATUS = 1, DELETED = ? WHERE IDNOTE = ? AND STATUS = 0",
                        (now, note_id))
            self._soft_delete(cur, "NOTES", "IDNOTE", note_id)

    # ── todos ─────────────────────────────────────────────────────

    def search_todos(self, title: str | None, completed: bool | None, limit: int) -> list[dict[str, Any]]:
        self._allow("todos", write=False)
        conditions = ["t.STATUS = 0"]
        params: list[Any] = []
        if title:
            conditions.append("UPPER(t.TITLE) LIKE UPPER(?) ESCAPE '\\'")
            params.append(_like_pattern(title))
        if completed is not None:
            conditions.append("t.COMPLETION = 100" if completed else "COALESCE(t.COMPLETION, -1) < 100")
        with self._read() as cur:
            cur.execute(
                f"SELECT FIRST {_clamp_limit(limit)} t.IDTODO AS id, t.TITLE AS title, "
                f"t.STARTTIME AS start_time, t.ENDTIME AS end_time, t.COMPLETION AS completion, "
                f"t.TEXT AS text, t.TAGS AS tags, l.NAME AS todo_list "
                f"FROM TODOS t LEFT JOIN TODOLISTS l ON l.IDTODOLIST = t.IDLIST "
                f"WHERE {' AND '.join(conditions)} ORDER BY t.ENDTIME, t.TITLE", params)
            rows = self._rows(cur)
        for row in rows:
            row["start_time"] = _date_out(row["start_time"])
            row["end_time"] = _date_out(row["end_time"])
            row["done"] = row.pop("completion") == 100
        return rows

    def create_todo(self, *, title: str, todo_list: str | None, start: datetime | None,
                    end: datetime | None, text: str | None, tags: str | None) -> int:
        self._allow("todos", write=True)
        raise EpimRefused("creating todos is not available until EPIM's todo format is verified")

    def update_todo(self, todo_id: int, changes: dict[str, Any]) -> None:
        self._allow("todos", write=True)
        if "done" in changes:
            raise EpimRefused("marking todos done is not available until EPIM's todo format is verified")
        with self._write() as cur:
            cur.execute("SELECT STARTTIME, ENDTIME FROM TODOS WHERE IDTODO = ? AND STATUS = 0", (todo_id,))
            row = cur.fetchone()
            if row is None:
                raise EpimRefused(f"todo {todo_id} not found")
            columns: dict[str, Any] = {}
            for key, column in (("title", "TITLE"), ("text", "TEXT"), ("tags", "TAGS")):
                if key in changes:
                    columns[column] = changes[key]
            start = changes.get("start", _date_out(row[0]))
            end = changes.get("end", _date_out(row[1]))
            if start is not None and end is not None:
                self._check_range(start, end)
            for key, column in (("start", "STARTTIME"), ("end", "ENDTIME")):
                if key in changes:
                    columns[column] = changes[key] if changes[key] is not None else _ZERO_DATE
            if "todo_list" in changes:
                columns["IDLIST"] = self._resolve(self._todolists(cur), changes["todo_list"], "todo list")
            self._update_columns(cur, "TODOS", "IDTODO", todo_id, columns)

    def delete_todo(self, todo_id: int) -> None:
        self._allow("todos", write=True)
        with self._write() as cur:
            self._require_live(cur, "TODOS", "IDTODO", todo_id, "todo")
            self._require_no_live_children(cur, "TODOS", "IDTODO", todo_id, "todo")
            self._soft_delete(cur, "TODOS", "IDTODO", todo_id)

    # ── password entries (subjects only) ──────────────────────────

    def search_passwords(self, subject: str | None, limit: int) -> list[dict[str, Any]]:
        self._allow("passwords", write=False)
        conditions = ["STATUS = 0"]
        params: list[Any] = []
        if subject:
            conditions.append("UPPER(SUBJECT) LIKE UPPER(?) ESCAPE '\\'")
            params.append(_like_pattern(subject))
        with self._read() as cur:
            cur.execute(f"SELECT FIRST {_clamp_limit(limit)} IDPASSENTRY AS id, SUBJECT AS subject, "
                        f"TAGS AS tags, LASTCHANGED AS last_changed FROM PASSENTRIES "
                        f"WHERE {' AND '.join(conditions)} ORDER BY SUBJECT", params)
            return self._rows(cur)

    def delete_password(self, entry_id: int) -> None:
        self._allow("passwords", write=True)
        with self._write() as cur:
            self._require_live(cur, "PASSENTRIES", "IDPASSENTRY", entry_id, "password entry")
            self._soft_delete(cur, "PASSENTRIES", "IDPASSENTRY", entry_id)


@dataclass(frozen=True)
class ContactFieldMap:
    """Contact field ids ↔ labels for one database.

    Standard fields carry no name in the database (EPIM names them in its UI
    language); their labels come from :data:`CONTACT_FIELD_LABELS`. Custom
    fields carry their own name. A label that exists twice is ambiguous: it
    is shown with its id and cannot be written by name."""

    label_by_id: dict[int, str]
    id_by_label: dict[str, int]
    date_fields: frozenset[int]

    @classmethod
    def build(cls, rows: list[tuple[Any, ...]]) -> "ContactFieldMap":
        names: dict[int, str] = {}
        date_fields: set[int] = set()
        for field_id, name, field_type in rows:
            label = name.strip() if name else CONTACT_FIELD_LABELS.get(int(field_id))
            if label:
                names[int(field_id)] = label
            if field_type == DATE_FIELD_TYPE:
                date_fields.add(int(field_id))
        counts: dict[str, int] = {}
        for label in names.values():
            counts[label] = counts.get(label, 0) + 1
        label_by_id = {fid: (label if counts[label] == 1 else f"{label} [{fid}]") for fid, label in names.items()}
        id_by_label = {label: fid for fid, label in label_by_id.items() if counts[names[fid]] == 1}
        return cls(label_by_id, id_by_label, frozenset(date_fields))

    def to_labels(self, items: list[tuple[int, str]]) -> dict[str, str]:
        out: dict[str, str] = {}
        for field_id, value in items:
            label = self.label_by_id.get(field_id, f"field_{field_id}")
            out[label] = self._date_value_out(field_id, value)
        return out

    def to_ids(self, fields: dict[str, str | None]) -> dict[int, str | None]:
        unknown = sorted(label for label in fields if label not in self.id_by_label)
        if unknown:
            raise EpimRefused(f"unknown contact field(s) {', '.join(unknown)}; "
                              f"writable: {', '.join(sorted(self.id_by_label))}")
        out: dict[int, str | None] = {}
        for label, value in fields.items():
            field_id = self.id_by_label[label]
            out[field_id] = self._date_value_in(field_id, value) if value is not None else None
        return out

    def _date_value_out(self, field_id: int, value: str) -> str:
        # Date fields hold Delphi day numbers (days since 1899-12-30).
        if field_id in self.date_fields and value.isdigit():
            return (DELPHI_EPOCH + timedelta(days=int(value))).isoformat()
        return value

    def _date_value_in(self, field_id: int, value: str) -> str:
        if field_id not in self.date_fields:
            return value
        from ....lib.tool_args import parse_date
        return str((parse_date(value, self.label_by_id[field_id]) - DELPHI_EPOCH).days)

    def display_name(self, items: list[tuple[int, str]]) -> str:
        values = dict(items)
        parts = [values.get(field_id, "").strip() for field_id in _NAME_FIELD_IDS]
        return " ".join(p for p in parts if p)

    def changes_display_name(self, fields: dict[str, str | None]) -> bool:
        return any(self.id_by_label.get(label) in _NAME_FIELD_IDS for label in fields)


# EPIM builds a contact's display name (SUBJECT) as "first last".
_NAME_FIELD_IDS = (1, 2)


_settings_lock = threading.Lock()
_instance: EpimDatabase | None = None


def get_epim_db() -> EpimDatabase | None:
    """The database configured in the plugin settings, or None when no path
    is set. A changed path or access level yields a fresh instance."""
    global _instance
    settings = load_epim_settings()
    with _settings_lock:
        if settings.db_path is None:
            if _instance is not None:
                _instance.close()
                _instance = None
            return None
        if _instance is None or _instance.db_path != settings.db_path or _instance.modules != settings.modules:
            if _instance is not None:
                _instance.close()
            _instance = EpimDatabase(settings.db_path, settings.modules)
        return _instance
