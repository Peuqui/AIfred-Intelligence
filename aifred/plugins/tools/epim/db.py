"""EPIM Firebird 2.5 database access layer.

Provides CRUD operations for all EPIM entities:
- Tasks (calendar events)
- Contacts
- Notes (with tabs)
- Todos
- Password entries

Connection: Firebird 2.5 embedded via fdb library.
"""

import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import fdb

logger = logging.getLogger(__name__)

# Singleton instance
_instance: Optional["EpimDatabase"] = None
_lock = threading.Lock()


def get_epim_db() -> Optional["EpimDatabase"]:
    """Get or create the singleton EpimDatabase instance.

    Returns None if EPIM is disabled or DB file doesn't exist.
    """
    global _instance
    if _instance is not None:
        return _instance

    with _lock:
        if _instance is not None:
            return _instance

        from ....lib.config import EPIM_DB_PATH, EPIM_ENABLED, EPIM_FB_DIR, EPIM_FB_LIB

        if not EPIM_ENABLED:
            return None

        db_path = Path(EPIM_DB_PATH)
        lib_path = Path(EPIM_FB_LIB)
        if not db_path.exists():
            logger.warning("EPIM database not found: %s", db_path)
            return None
        if not lib_path.exists():
            logger.warning("Firebird library not found: %s", lib_path)
            return None

        _instance = EpimDatabase(
            db_path=str(db_path),
            fb_lib=str(lib_path),
            fb_dir=str(EPIM_FB_DIR),
        )
        return _instance


# ============================================================
# FIELDSDATA codec
# ============================================================
# Format: [field_id (8 hex)][length (4 hex)][data (UTF-8 bytes)]...
# Example: "000000010006Stefan" → field_id=1, length=6, value="Stefan"

def decode_fieldsdata(raw: str, field_map: Optional[dict[int, str]] = None) -> dict[str, str]:
    """Decode EPIM hex-encoded FIELDSDATA to a dict.

    Args:
        raw: Hex-encoded field string from database.
        field_map: Optional mapping of field_id → human-readable name.

    Returns:
        Dict of field_name → value.
    """
    if not raw:
        return {}

    result: dict[str, str] = {}
    pos = 0
    while pos + 12 <= len(raw):  # Need at least 8 (id) + 4 (length)
        try:
            field_id = int(raw[pos:pos + 8], 16)
            length = int(raw[pos + 8:pos + 12], 16)
            pos += 12
            if pos + length > len(raw):
                break
            value = raw[pos:pos + length]
            pos += length

            if field_map and field_id in field_map:
                key = field_map[field_id]
            else:
                key = f"field_{field_id}"
            result[key] = value
        except (ValueError, IndexError):
            break

    return result


def encode_fieldsdata(fields: dict[str, str], name_to_id: dict[str, int]) -> str:
    """Encode a dict back to EPIM hex-encoded FIELDSDATA.

    Format per field: 8-char hex field_id + 4-char hex string_length + raw value.
    Length is CHARACTER count (not byte count) — matching EPIM's native format.

    Args:
        fields: Dict of field_name → value.
        name_to_id: Mapping of human-readable name → field_id.

    Returns:
        Hex-encoded field string for database.
    """
    parts: list[str] = []
    for name, value in fields.items():
        if name not in name_to_id:
            continue
        field_id = name_to_id[name]
        str_value = str(value) if not isinstance(value, str) else value
        # The length is a 4-hex-digit field: values longer than 0xFFFF chars
        # would overflow to 5 digits and corrupt the format for ALL following
        # fields. Fail loudly instead of silently writing garbage.
        if len(str_value) > 0xFFFF:
            raise ValueError(
                f"EPIM field {name!r} too long ({len(str_value)} chars, max {0xFFFF})"
            )
        parts.append(f"{field_id:08X}{len(str_value):04X}{str_value}")
    return "".join(parts)


# ============================================================
# Default contact field IDs (EPIM built-in)
# ============================================================
# IDs 1-60 are default fields with DEFFIELDINDEX mapping.
# Custom fields have large IDs and user-defined names.
DEFAULT_CONTACT_FIELDS: dict[int, str] = {
    1: "Vorname",
    2: "Nachname",
    3: "Telefon",
    4: "Telefon 2",
    5: "Mobiltelefon",
    6: "Adresse",
    7: "Ort",
    8: "Bundesland",
    9: "PLZ",
    10: "Land",
    14: "Firma",
    15: "Geburtstag",
    16: "Jahrestag",
    21: "E-Mail",
    22: "E-Mail 2",
    23: "Webseite",
    24: "Telefon geschäftlich",
    25: "Telefon geschäftlich 2",
    26: "Fax geschäftlich",
    27: "Adresse geschäftlich",
    28: "Ort geschäftlich",
    29: "Bundesland geschäftlich",
    30: "PLZ geschäftlich",
    31: "Land geschäftlich",
    32: "Firma geschäftlich",
    36: "Notizen",
    37: "Fax",
    38: "Pager",
    43: "IM",
    49: "Position",
    55: "Abteilung",
    56: "Assistent",
    57: "Primär",
    60: "Foto-URL",
    61: "Adresse 2",
    62: "Ort 2",
    63: "PLZ 2",
}

# Reverse map for encoding
_CONTACT_NAME_TO_ID = {v: k for k, v in DEFAULT_CONTACT_FIELDS.items()}

_LIMIT_MAX = 500


def _as_bool(value: object) -> bool:
    """Coerce an LLM-supplied boolean-ish value.

    The model sometimes sends the STRINGS "false"/"0"/"no" instead of a JSON
    boolean; ``bool("false")`` is True, which would e.g. mark a timed event as
    all-day. Treat the common falsey strings as False.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "none", "nein")
    return bool(value)


def _clamp_limit(limit: int) -> int:
    """Coerce a tool-supplied ``limit`` to a safe integer for ``SELECT FIRST``.

    ``limit`` is interpolated into SQL (Firebird has no parameter binding for
    FIRST), so it must never reach the query as an arbitrary string. The JSON
    tool schema declares ``integer`` but that is not enforced by the executor,
    so we coerce here. A non-numeric value raises ValueError (surfaced to the
    LLM as a tool error) rather than being silently dropped.
    """
    value = int(limit)
    return max(1, min(value, _LIMIT_MAX))


class EpimDatabase:
    """Firebird 2.5 embedded database access for EPIM."""

    def __init__(self, db_path: str, fb_lib: str, fb_dir: str) -> None:
        self._db_path = db_path
        self._fb_lib = fb_lib
        self._fb_dir = fb_dir
        self._con: Optional[fdb.Connection] = None
        self._custom_contact_fields: Optional[dict[int, str]] = None
        # Set the Firebird env once at construction — not on every _connect()
        # (that mutated process-global state repeatedly, racy across threads).
        os.environ["FIREBIRD"] = self._fb_dir

    def _preload_libs(self) -> None:
        """Preload Firebird's ICU dependencies before fdb loads libfbembed.

        LD_LIBRARY_PATH set at runtime doesn't affect dlopen() for transitive
        dependencies. We must load ICU explicitly via ctypes first.
        """
        import ctypes
        icu_libs = ["libicudata.so.57", "libicuuc.so.57", "libicui18n.so.57"]
        for lib_name in icu_libs:
            lib_path = os.path.join(self._fb_dir, lib_name)
            if os.path.exists(lib_path):
                try:
                    ctypes.cdll.LoadLibrary(lib_path)
                except OSError as e:
                    logger.warning("Failed to preload %s: %s", lib_name, e)

    def _connect(self) -> fdb.Connection:
        """Get or create database connection."""
        if self._con is not None:
            try:
                # Test if connection is still alive
                self._con.cursor().execute("SELECT 1 FROM RDB$DATABASE")
                return self._con
            except Exception:
                self._con = None

        self._preload_libs()

        self._con = fdb.connect(
            dsn=self._db_path,
            user="SYSDBA",
            password="masterkey",
            charset="UTF8",
            fb_library_name=self._fb_lib,
        )
        logger.info("EPIM database connected: %s", self._db_path)
        return self._con

    def close(self) -> None:
        """Close database connection."""
        if self._con is not None:
            self._con.close()
            self._con = None

    def _get_contact_field_map(self) -> dict[int, str]:
        """Get combined default + custom contact field mapping."""
        if self._custom_contact_fields is None:
            self._custom_contact_fields = dict(DEFAULT_CONTACT_FIELDS)
            con = self._connect()
            cur = con.cursor()
            cur.execute(
                "SELECT IDFIELD, NAME FROM CONTACTFIELDS "
                "WHERE ENABLED = 1 AND NAME IS NOT NULL"
            )
            for row in cur.fetchall():
                self._custom_contact_fields[row[0]] = row[1].strip()
        return self._custom_contact_fields

    # ============================================================
    # ID GENERATION
    # ============================================================

    _last_generated_id: int = 0

    @classmethod
    def _generate_id(cls) -> int:
        """Generate an EPIM-compatible entity ID.

        EPIM uses large 64-bit IDs based on timestamps. We replicate
        the pattern: millisecond timestamp shifted left with random bits.

        Kept strictly monotonic within the process so two creates in the same
        millisecond can't collide on the 10 random bits (p=1/1024) and raise a
        PK violation.
        """
        import random
        import time
        ts_ms = int(time.time() * 1000)
        candidate = (ts_ms << 10) | random.randint(0, 1023)
        if candidate <= cls._last_generated_id:
            candidate = cls._last_generated_id + 1
        cls._last_generated_id = candidate
        return candidate

    # ============================================================
    # NAME → ID RESOLUTION
    # ============================================================

    def resolve_category(self, name: str) -> Optional[int]:
        """Resolve a category name to its ID (case-insensitive)."""
        for cat in self.get_categories():
            if str(cat["name"]).lower() == name.lower():
                return int(cat["id"])
        return None

    def resolve_calendar(self, name: str) -> Optional[int]:
        """Resolve a calendar name to its ID (case-insensitive)."""
        for cal in self.get_calendars():
            if str(cal["name"]).lower() == name.lower():
                return int(cal["id"])
        return None

    def resolve_todolist(self, name: str) -> Optional[int]:
        """Resolve a todo list name to its ID (case-insensitive)."""
        for tl in self.get_todolists():
            if str(tl["name"]).lower() == name.lower():
                return int(tl["id"])
        return None

    def resolve_notetree(self, name: str) -> Optional[int]:
        """Resolve a note tree name to its ID (case-insensitive)."""
        for nt in self.get_notetrees():
            if str(nt["name"]).lower() == name.lower():
                return int(nt["id"])
        return None

    def _row_exists(self, table: str, id_col: str, entity_id: int) -> bool:
        """True if a live (STATUS=0) row with the given id exists.

        fdb does not reliably report rowcount for UPDATEs, so update/delete
        tools check existence explicitly instead of blindly reporting success —
        otherwise a hallucinated/truncated ID silently reports 'updated'/
        'deleted'. ``table``/``id_col`` are internal constants, never LLM input.
        """
        con = self._connect()
        cur = con.cursor()
        cur.execute(f"SELECT 1 FROM {table} WHERE {id_col} = ? AND STATUS = 0", (entity_id,))
        return cur.fetchone() is not None

    # ============================================================
    # TASKS / CALENDAR
    # ============================================================

    def search_tasks(
        self,
        title: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        location: Optional[str] = None,
        tags: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search calendar tasks/events."""
        con = self._connect()
        cur = con.cursor()

        conditions = ["t.STATUS = 0"]
        params: list = []

        if title:
            conditions.append("UPPER(t.TITLE) LIKE UPPER(?)")
            params.append(f"%{title}%")
        if date_from:
            conditions.append("t.STARTTIME >= ?")
            params.append(date_from)
        if date_to:
            # "2026-04-01" → "2026-04-01 23:59:59" (Firebird treats bare dates as 00:00:00)
            dt = date_to.strip()
            if len(dt) == 10:  # YYYY-MM-DD without time
                dt = f"{dt} 23:59:59"
            conditions.append("t.STARTTIME <= ?")
            params.append(dt)
        if location:
            conditions.append("UPPER(t.LOCATION) LIKE UPPER(?)")
            params.append(f"%{location}%")
        if tags:
            conditions.append("UPPER(t.TAGS) LIKE UPPER(?)")
            params.append(f"%{tags}%")
        if category:
            conditions.append(
                "t.CATEGORY IN (SELECT IDCATEGORY FROM CATEGORIES "
                "WHERE UPPER(NAME) LIKE UPPER(?))"
            )
            params.append(f"%{category}%")

        where = " AND ".join(conditions)
        sql = (
            f"SELECT FIRST {_clamp_limit(limit)} t.IDTASK, t.TITLE, t.STARTTIME, t.ENDTIME, "
            f"t.LOCATION, t.PRIORITY, t.ALLDAY, t.REPEATING, t.TAGS, "
            f"t.TEXT, t.COMPLETION, t.COMPLETED, "
            f"c.NAME AS CALENDAR_NAME, cat.NAME AS CATEGORY_NAME "
            f"FROM TASKS t "
            f"LEFT JOIN CALENDARS c ON c.IDCALENDAR = t.CALENDAR "
            f"LEFT JOIN CATEGORIES cat ON cat.IDCATEGORY = t.CATEGORY "
            f"WHERE {where} "
            f"ORDER BY t.STARTTIME"
        )
        cur.execute(sql, params)
        columns = [desc[0].strip() for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def get_task(self, task_id: int) -> Optional[dict]:
        """Get a single task by ID with full details."""
        con = self._connect()
        cur = con.cursor()
        cur.execute(
            "SELECT t.*, c.NAME AS CALENDAR_NAME, cat.NAME AS CATEGORY_NAME "
            "FROM TASKS t "
            "LEFT JOIN CALENDARS c ON c.IDCALENDAR = t.CALENDAR "
            "LEFT JOIN CATEGORIES cat ON cat.IDCATEGORY = t.CATEGORY "
            "WHERE t.IDTASK = ?",
            (task_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [desc[0].strip() for desc in cur.description]
        return dict(zip(columns, row))

    def create_task(
        self,
        title: str,
        start: str,
        end: str,
        location: Optional[str] = None,
        allday: bool = False,
        calendar_id: Optional[int] = None,
        calendar_name: Optional[str] = None,
        category_id: Optional[int] = None,
        category_name: Optional[str] = None,
        text: Optional[str] = None,
        priority: int = 0,
        tags: Optional[str] = None,
    ) -> int:
        """Create a new calendar task. Returns the new task ID.

        Accepts category/calendar by name or ID. Name takes precedence.
        """
        con = self._connect()
        cur = con.cursor()

        # Empty date strings would hit Firebird as invalid TIMESTAMP literals
        # (cryptic conversion error) — normalize to NULL.
        start_ts: Optional[str] = start or None
        end_ts: Optional[str] = end or None

        # Resolve names to IDs
        if category_name:
            category_id = self.resolve_category(category_name)
        if calendar_name:
            calendar_id = self.resolve_calendar(calendar_name)

        # Sanitize priority (LLMs sometimes send "high"/"low" instead of int)
        if isinstance(priority, str):
            priority_map = {"low": 1, "medium": 5, "high": 9, "none": 0}
            priority = priority_map.get(priority.lower(), 0)

        # Generate ID
        new_id = self._generate_id()

        # Default calendar
        if calendar_id is None:
            cur.execute("SELECT FIRST 1 IDCALENDAR FROM CALENDARS")
            row = cur.fetchone()
            calendar_id = row[0] if row else 0

        now = datetime.now()
        cur.execute(
            "INSERT INTO TASKS (IDTASK, IDPARENT, TITLE, STARTTIME, ENDTIME, "
            "LOCATION, ALLDAY, CALENDAR, CATEGORY, TEXT, PRIORITY, TAGS, "
            "CREATED, LASTCHANGED, STATUS, IDCREATOR, IDEDITOR, "
            "READACCESS, WRITEACCESS, COMPLETION, EXCLUSIVE, REPEATING) "
            "VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 1, -1, -1, 0, 0, 0)",
            (new_id, title, start_ts, end_ts, location, 1 if _as_bool(allday) else 0,
             calendar_id, category_id or 0, text, priority, tags,
             now, now),
        )
        con.commit()
        logger.info("EPIM: Created task %d: %s", new_id, title)
        return new_id

    def update_task(self, task_id: int, **fields: object) -> bool:
        """Update fields on an existing task."""
        if not fields:
            return False

        # Resolve name-based fields to IDs. Pop BOTH aliases unconditionally —
        # a short-circuit (`pop(a) or pop(b)`) would leave the second key in
        # `fields`, and "category"/"calendar" upper-case to allowed columns, so
        # the raw name string would be written into the integer FK column.
        if "category" in fields or "category_name" in fields:
            _cat_a = fields.pop("category_name", None)
            _cat_b = fields.pop("category", None)
            cat_name = _cat_a if _cat_a else _cat_b
            if cat_name and isinstance(cat_name, str):
                cat_id = self.resolve_category(str(cat_name))
                if cat_id is not None:
                    fields["CATEGORY"] = cat_id
        if "calendar" in fields or "calendar_name" in fields:
            _cal_a = fields.pop("calendar_name", None)
            _cal_b = fields.pop("calendar", None)
            cal_name = _cal_a if _cal_a else _cal_b
            if cal_name and isinstance(cal_name, str):
                cal_id = self.resolve_calendar(str(cal_name))
                if cal_id is not None:
                    fields["CALENDAR"] = cal_id

        con = self._connect()
        cur = con.cursor()

        allowed = {
            "TITLE", "STARTTIME", "ENDTIME", "LOCATION", "ALLDAY",
            "CALENDAR", "CATEGORY", "TEXT", "PRIORITY", "TAGS",
            "COMPLETION", "COMPLETED", "EXCLUSIVE", "REPEATING",
        }
        updates = []
        params: list = []
        for key, value in fields.items():
            col = key.upper()
            if col not in allowed:
                continue
            updates.append(f"{col} = ?")
            params.append(value)

        if not updates:
            return False

        if not self._row_exists("TASKS", "IDTASK", task_id):
            return False

        updates.append("LASTCHANGED = ?")
        params.append(datetime.now())
        params.append(task_id)

        sql = f"UPDATE TASKS SET {', '.join(updates)} WHERE IDTASK = ?"
        cur.execute(sql, params)
        con.commit()
        # Note: Firebird fdb driver does not reliably report rowcount for UPDATEs
        return True

    def delete_task(self, task_id: int) -> bool:
        """Soft-delete a task (set STATUS=1, DELETED=now)."""
        if not self._row_exists("TASKS", "IDTASK", task_id):
            return False
        con = self._connect()
        cur = con.cursor()
        now = datetime.now()
        cur.execute(
            "UPDATE TASKS SET STATUS = 1, DELETED = ?, LASTCHANGED = ? "
            "WHERE IDTASK = ?",
            (now, now, task_id),
        )
        con.commit()
        return True

    # ============================================================
    # CONTACTS
    # ============================================================

    def search_contacts(
        self,
        name: Optional[str] = None,
        tags: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search contacts by name or tags."""
        con = self._connect()
        cur = con.cursor()

        conditions = ["STATUS = 0"]
        params: list = []

        if name:
            conditions.append("UPPER(SUBJECT) LIKE UPPER(?)")
            params.append(f"%{name}%")
        if tags:
            conditions.append("UPPER(TAGS) LIKE UPPER(?)")
            params.append(f"%{tags}%")

        where = " AND ".join(conditions)
        cur.execute(
            f"SELECT FIRST {_clamp_limit(limit)} IDCONTACT, SUBJECT, FIELDSDATA, FIELDSDATA2, "
            f"TAGS, CREATED, LASTCHANGED "
            f"FROM CONTACTS WHERE {where} ORDER BY SUBJECT",
            params,
        )

        field_map = self._get_contact_field_map()
        results = []
        for row in cur.fetchall():
            contact = {
                "id": row[0],
                "name": row[1],
                "tags": row[4],
                "created": row[5],
                "last_changed": row[6],
            }
            # Decode fieldsdata
            raw = row[2] or ""
            if row[3]:  # FIELDSDATA2 (BLOB) overrides
                raw = row[3]
            contact["fields"] = decode_fieldsdata(raw, field_map)
            results.append(contact)
        return results

    def get_contact(self, contact_id: int) -> Optional[dict]:
        """Get a single contact by ID with decoded fields."""
        con = self._connect()
        cur = con.cursor()
        cur.execute(
            "SELECT IDCONTACT, SUBJECT, FIELDSDATA, FIELDSDATA2, TAGS, "
            "CREATED, LASTCHANGED "
            "FROM CONTACTS WHERE IDCONTACT = ?",
            (contact_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        field_map = self._get_contact_field_map()
        raw = row[2] or ""
        if row[3]:
            raw = row[3]
        return {
            "id": row[0],
            "name": row[1],
            "tags": row[4],
            "created": row[5],
            "last_changed": row[6],
            "fields": decode_fieldsdata(raw, field_map),
        }

    def create_contact(self, name: str, fields: Optional[dict[str, str]] = None, tags: Optional[str] = None) -> int:
        """Create a new contact. Returns the new contact ID."""
        con = self._connect()
        cur = con.cursor()

        new_id = self._generate_id()

        fieldsdata = ""
        if fields:
            name_to_id = {v: k for k, v in self._get_contact_field_map().items()}
            fieldsdata = encode_fieldsdata(fields, name_to_id)

        now = datetime.now()
        cur.execute(
            "INSERT INTO CONTACTS (IDCONTACT, IDACCOUNT, SUBJECT, FIELDSDATA, TAGS, "
            "CREATED, LASTCHANGED, STATUS, IDCREATOR, IDEDITOR, "
            "READACCESS, WRITEACCESS, COLLADR) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, 0, 1, 1, -1, -1, 0)",
            (new_id, name, fieldsdata, tags, now, now),
        )
        con.commit()
        logger.info("EPIM: Created contact %d: %s", new_id, name)
        return new_id

    def update_contact(self, contact_id: int, name: Optional[str] = None,
                       fields: Optional[dict[str, str]] = None, tags: Optional[str] = None) -> bool:
        """Update a contact."""
        if not self._row_exists("CONTACTS", "IDCONTACT", contact_id):
            return False
        con = self._connect()
        cur = con.cursor()

        updates = []
        params: list = []

        if name is not None:
            updates.append("SUBJECT = ?")
            params.append(name)
        if fields is not None:
            name_to_id = {v: k for k, v in self._get_contact_field_map().items()}
            updates.append("FIELDSDATA = ?")
            params.append(encode_fieldsdata(fields, name_to_id))
        if tags is not None:
            updates.append("TAGS = ?")
            params.append(tags)

        if not updates:
            return False

        updates.append("LASTCHANGED = ?")
        params.append(datetime.now())
        params.append(contact_id)

        sql = f"UPDATE CONTACTS SET {', '.join(updates)} WHERE IDCONTACT = ?"
        cur.execute(sql, params)
        con.commit()
        return True

    def delete_contact(self, contact_id: int) -> bool:
        """Soft-delete a contact."""
        if not self._row_exists("CONTACTS", "IDCONTACT", contact_id):
            return False
        con = self._connect()
        cur = con.cursor()
        now = datetime.now()
        cur.execute(
            "UPDATE CONTACTS SET STATUS = 1, DELETED = ?, LASTCHANGED = ? "
            "WHERE IDCONTACT = ?",
            (now, now, contact_id),
        )
        con.commit()
        return True

    # ============================================================
    # NOTES
    # ============================================================

    def search_notes(
        self,
        title: Optional[str] = None,
        text: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search notes by title OR tab content.

        The search tool passes the same query as both ``title`` and ``text``;
        a note must match if the term is in the title OR in the tab content/name
        (not both — that AND semantics made content hits with a non-matching
        title invisible).
        """
        con = self._connect()
        cur = con.cursor()

        conditions = ["n.STATUS = 0"]
        params: list = []
        match_terms: list[str] = []
        if title:
            match_terms.append("UPPER(n.TITLE) LIKE UPPER(?)")
            params.append(f"%{title}%")
        if text:
            match_terms.append(
                "n.IDNOTE IN (SELECT IDNOTE FROM NOTETABS "
                "WHERE UPPER(TEXT) LIKE UPPER(?) OR UPPER(NAME) LIKE UPPER(?))"
            )
            params.extend([f"%{text}%", f"%{text}%"])
        if match_terms:
            conditions.append("(" + " OR ".join(match_terms) + ")")
        where = " AND ".join(conditions)

        cur.execute(
            f"SELECT FIRST {_clamp_limit(limit)} n.IDNOTE, n.TITLE, n.TAGS, n.CREATED, "
            f"n.LASTCHANGED, nt.NAME AS TREE_NAME "
            f"FROM NOTES n "
            f"LEFT JOIN NOTETREES nt ON nt.IDNOTETREE = n.IDNOTETREE "
            f"WHERE {where} ORDER BY n.TITLE",
            params,
        )
        columns = [desc[0].strip() for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def get_note(self, note_id: int) -> Optional[dict]:
        """Get a note with all its tabs."""
        con = self._connect()
        cur = con.cursor()

        cur.execute(
            "SELECT IDNOTE, TITLE, TAGS, CREATED, LASTCHANGED, IDNOTETREE "
            "FROM NOTES WHERE IDNOTE = ?",
            (note_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        note = {
            "id": row[0], "title": row[1], "tags": row[2],
            "created": row[3], "last_changed": row[4], "tree_id": row[5],
        }

        # Get tabs
        cur.execute(
            "SELECT IDNOTETAB, NAME, TEXT, TEXT2 "
            "FROM NOTETABS WHERE IDNOTE = ? AND STATUS = 0 ORDER BY IDNOTETAB",
            (note_id,),
        )
        tabs = []
        for tab_row in cur.fetchall():
            tab_text = tab_row[3] if tab_row[3] else tab_row[2]
            tabs.append({
                "id": tab_row[0],
                "name": tab_row[1],
                "text": tab_text,
            })
        note["tabs"] = tabs
        return note

    def create_note(self, title: str, tree_id: Optional[int] = None,
                    tree_name: Optional[str] = None,
                    tab_name: str = "Tab 1", tab_text: str = "",
                    tags: Optional[str] = None) -> int:
        """Create a new note with one tab. Returns note ID."""
        con = self._connect()
        cur = con.cursor()

        # Resolve name to ID
        if tree_name:
            tree_id = self.resolve_notetree(tree_name)

        # Default tree
        if tree_id is None:
            cur.execute("SELECT FIRST 1 IDNOTETREE FROM NOTETREES")
            row = cur.fetchone()
            tree_id = row[0] if row else 0

        note_id = self._generate_id()

        now = datetime.now()
        # Two inserts (note + its tab) form one unit. Roll back on failure so a
        # half-written note (note row without its tab) can't be picked up by the
        # next unrelated commit on this shared connection.
        try:
            cur.execute(
                "INSERT INTO NOTES (IDNOTE, IDPARENT, TITLE, IDNOTETREE, TAGS, "
                "CREATED, LASTCHANGED, STATUS, IDCREATOR, IDEDITOR, "
                "READACCESS, WRITEACCESS, ICONINDEX) "
                "VALUES (?, 0, ?, ?, ?, ?, ?, 0, 1, 1, -1, -1, 0)",
                (note_id, title, tree_id, tags, now, now),
            )

            # Create default tab
            tab_id = self._generate_id()
            cur.execute(
                "INSERT INTO NOTETABS (IDNOTETAB, IDNOTE, NAME, TEXT, "
                "CREATED, LASTCHANGED, STATUS, IDCREATOR, IDEDITOR, "
                "READACCESS, WRITEACCESS, COLOR, BACKCOLOR) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, 1, 1, -1, -1, 0, 0)",
                (tab_id, note_id, tab_name, tab_text, now, now),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        logger.info("EPIM: Created note %d: %s", note_id, title)
        return note_id

    def update_note(self, note_id: int, title: Optional[str] = None,
                    tags: Optional[str] = None) -> bool:
        """Update note metadata."""
        if title is None and tags is None:
            return False
        if not self._row_exists("NOTES", "IDNOTE", note_id):
            return False
        con = self._connect()
        cur = con.cursor()
        updates = []
        params: list = []
        if title is not None:
            updates.append("TITLE = ?")
            params.append(title)
        if tags is not None:
            updates.append("TAGS = ?")
            params.append(tags)
        updates.append("LASTCHANGED = ?")
        params.append(datetime.now())
        params.append(note_id)
        cur.execute(f"UPDATE NOTES SET {', '.join(updates)} WHERE IDNOTE = ?", params)
        con.commit()
        return True

    def update_note_tab(self, tab_id: int, name: Optional[str] = None,
                        text: Optional[str] = None) -> bool:
        """Update a note tab's content."""
        if name is None and text is None:
            return False
        if not self._row_exists("NOTETABS", "IDNOTETAB", tab_id):
            return False
        con = self._connect()
        cur = con.cursor()
        updates = []
        params: list = []
        if name is not None:
            updates.append("NAME = ?")
            params.append(name)
        if text is not None:
            updates.append("TEXT = ?")
            params.append(text)
        updates.append("LASTCHANGED = ?")
        params.append(datetime.now())
        params.append(tab_id)
        cur.execute(f"UPDATE NOTETABS SET {', '.join(updates)} WHERE IDNOTETAB = ?", params)
        con.commit()
        return True

    def delete_note(self, note_id: int) -> bool:
        """Soft-delete a note and its tabs."""
        if not self._row_exists("NOTES", "IDNOTE", note_id):
            return False
        con = self._connect()
        cur = con.cursor()
        now = datetime.now()
        # Two updates form one unit — roll back on failure to avoid a
        # half-deleted note (tabs gone, note still visible or vice versa).
        try:
            cur.execute(
                "UPDATE NOTETABS SET STATUS = 1, DELETED = ?, LASTCHANGED = ? "
                "WHERE IDNOTE = ?",
                (now, now, note_id),
            )
            cur.execute(
                "UPDATE NOTES SET STATUS = 1, DELETED = ?, LASTCHANGED = ? "
                "WHERE IDNOTE = ?",
                (now, now, note_id),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return True

    # ============================================================
    # TODOS
    # ============================================================

    def search_todos(
        self,
        title: Optional[str] = None,
        completed: Optional[bool] = None,
        list_name: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search todo items."""
        con = self._connect()
        cur = con.cursor()

        conditions = ["t.STATUS = 0"]
        params: list = []

        if title:
            conditions.append("UPPER(t.TITLE) LIKE UPPER(?)")
            params.append(f"%{title}%")
        if completed is not None:
            if _as_bool(completed):
                conditions.append("t.COMPLETION = 100")
            else:
                conditions.append("(t.COMPLETION < 100 OR t.COMPLETION IS NULL)")
        if list_name:
            conditions.append(
                "t.IDLIST IN (SELECT IDTODOLIST FROM TODOLISTS "
                "WHERE UPPER(NAME) LIKE UPPER(?))"
            )
            params.append(f"%{list_name}%")

        where = " AND ".join(conditions)
        cur.execute(
            f"SELECT FIRST {_clamp_limit(limit)} t.IDTODO, t.TITLE, t.STARTTIME, t.ENDTIME, "
            f"t.PRIORITY, t.COMPLETION, t.COMPLETED, t.TAGS, t.TEXT, "
            f"l.NAME AS LIST_NAME "
            f"FROM TODOS t "
            f"LEFT JOIN TODOLISTS l ON l.IDTODOLIST = t.IDLIST "
            f"WHERE {where} ORDER BY t.STARTTIME",
            params,
        )
        columns = [desc[0].strip() for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def create_todo(
        self,
        title: str,
        list_id: Optional[int] = None,
        list_name: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        priority: int = 0,
        text: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> int:
        """Create a new todo item. Returns todo ID."""
        con = self._connect()
        cur = con.cursor()

        # Empty date strings → NULL (invalid TIMESTAMP literal otherwise).
        start = start or None
        end = end or None

        # Resolve name to ID
        if list_name:
            list_id = self.resolve_todolist(list_name)

        if list_id is None:
            cur.execute("SELECT FIRST 1 IDTODOLIST FROM TODOLISTS")
            row = cur.fetchone()
            list_id = row[0] if row else 0

        new_id = self._generate_id()

        now = datetime.now()
        cur.execute(
            "INSERT INTO TODOS (IDTODO, IDPARENT, TITLE, STARTTIME, ENDTIME, "
            "PRIORITY, TEXT, TAGS, IDLIST, "
            "CREATED, LASTCHANGED, STATUS, IDCREATOR, IDEDITOR, "
            "READACCESS, WRITEACCESS, COMPLETION, FLOATING, SHOWINSCH) "
            "VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 1, -1, -1, 0, 0, 0)",
            (new_id, title, start, end, priority, text, tags, list_id, now, now),
        )
        con.commit()
        logger.info("EPIM: Created todo %d: %s", new_id, title)
        return new_id

    def update_todo(self, todo_id: int, **fields: object) -> bool:
        """Update a todo item."""
        if not fields:
            return False

        # Resolve name-based fields to IDs
        if "list" in fields or "list_name" in fields:
            list_name = fields.pop("list_name", None) or fields.pop("list", None)
            if list_name and isinstance(list_name, str):
                list_id = self.resolve_todolist(str(list_name))
                if list_id is not None:
                    fields["IDLIST"] = list_id

        con = self._connect()
        cur = con.cursor()

        allowed = {
            "TITLE", "STARTTIME", "ENDTIME", "PRIORITY", "TEXT",
            "TAGS", "COMPLETION", "COMPLETED", "IDLIST",
        }
        updates = []
        params: list = []
        for key, value in fields.items():
            col = key.upper()
            if col not in allowed:
                continue
            updates.append(f"{col} = ?")
            params.append(value)

        if not updates:
            return False

        if not self._row_exists("TODOS", "IDTODO", todo_id):
            return False

        updates.append("LASTCHANGED = ?")
        params.append(datetime.now())
        params.append(todo_id)
        cur.execute(f"UPDATE TODOS SET {', '.join(updates)} WHERE IDTODO = ?", params)
        con.commit()
        return True

    def delete_todo(self, todo_id: int) -> bool:
        """Soft-delete a todo."""
        if not self._row_exists("TODOS", "IDTODO", todo_id):
            return False
        con = self._connect()
        cur = con.cursor()
        now = datetime.now()
        cur.execute(
            "UPDATE TODOS SET STATUS = 1, DELETED = ?, LASTCHANGED = ? "
            "WHERE IDTODO = ?",
            (now, now, todo_id),
        )
        con.commit()
        return True

    # ============================================================
    # PASSWORD ENTRIES
    # ============================================================

    def search_passwords(self, subject: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Search password entries (returns subjects only, no credentials)."""
        con = self._connect()
        cur = con.cursor()

        conditions = ["pe.STATUS = 0"]
        params: list = []
        if subject:
            conditions.append("UPPER(pe.SUBJECT) LIKE UPPER(?)")
            params.append(f"%{subject}%")

        where = " AND ".join(conditions)
        cur.execute(
            f"SELECT FIRST {_clamp_limit(limit)} pe.IDPASSENTRY, pe.SUBJECT, pe.TAGS, "
            f"pe.CREATED, pe.LASTCHANGED, pg.SUBJECT AS GROUP_NAME "
            f"FROM PASSENTRIES pe "
            f"LEFT JOIN PASSGROUPS pg ON pg.IDPASSGROUP = ("
            f"  SELECT FIRST 1 IDPASSGROUP FROM PASSGROUPS "
            f"  WHERE IDPASSGROUP IN ("
            f"    SELECT IDPARENT FROM PASSENTRIES WHERE IDPASSENTRY = pe.IDPASSENTRY"
            f"  )"
            f") "
            f"WHERE {where} ORDER BY pe.SUBJECT",
            params,
        )
        columns = [desc[0].strip() for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def get_password(self, entry_id: int) -> Optional[dict]:
        """Get a password entry with decoded fields."""
        con = self._connect()
        cur = con.cursor()
        cur.execute(
            "SELECT IDPASSENTRY, SUBJECT, FIELDSDATA, FIELDSDATA2, TAGS, "
            "CREATED, LASTCHANGED "
            "FROM PASSENTRIES WHERE IDPASSENTRY = ?",
            (entry_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        # Password field definitions
        cur.execute(
            "SELECT IDFIELD, NAME FROM PASSENTRYFIELDS WHERE ENABLED = 1"
        )
        pw_field_map = {r[0]: r[1].strip() for r in cur.fetchall() if r[1]}

        raw = row[2] or ""
        if row[3]:
            raw = row[3]
        return {
            "id": row[0],
            "subject": row[1],
            "tags": row[4],
            "created": row[5],
            "last_changed": row[6],
            "fields": decode_fieldsdata(raw, pw_field_map),
        }

    def create_password(self, subject: str, fields: Optional[dict[str, str]] = None,
                        group_id: Optional[int] = None, tags: Optional[str] = None) -> int:
        """Create a new password entry."""
        con = self._connect()
        cur = con.cursor()

        new_id = self._generate_id()

        fieldsdata = ""
        if fields:
            cur.execute("SELECT IDFIELD, NAME FROM PASSENTRYFIELDS WHERE ENABLED = 1")
            pw_name_to_id = {r[1].strip(): r[0] for r in cur.fetchall() if r[1]}
            fieldsdata = encode_fieldsdata(fields, pw_name_to_id)

        parent_id = group_id or 0
        now = datetime.now()
        cur.execute(
            "INSERT INTO PASSENTRIES (IDPASSENTRY, SUBJECT, FIELDSDATA, TAGS, "
            "PATH, CREATED, LASTCHANGED, STATUS, IDCREATOR, IDEDITOR, "
            "READACCESS, WRITEACCESS, ICONINDEX) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, 1, -1, -1, 0)",
            (new_id, subject, fieldsdata, tags, str(parent_id), now, now),
        )
        con.commit()
        logger.info("EPIM: Created password entry %d: %s", new_id, subject)
        return new_id

    def update_password(self, entry_id: int, subject: Optional[str] = None,
                        fields: Optional[dict[str, str]] = None,
                        tags: Optional[str] = None) -> bool:
        """Update a password entry."""
        if not self._row_exists("PASSENTRIES", "IDPASSENTRY", entry_id):
            return False
        con = self._connect()
        cur = con.cursor()
        updates = []
        params: list = []
        if subject is not None:
            updates.append("SUBJECT = ?")
            params.append(subject)
        if fields is not None:
            cur.execute("SELECT IDFIELD, NAME FROM PASSENTRYFIELDS WHERE ENABLED = 1")
            pw_name_to_id = {r[1].strip(): r[0] for r in cur.fetchall() if r[1]}
            updates.append("FIELDSDATA = ?")
            params.append(encode_fieldsdata(fields, pw_name_to_id))
        if tags is not None:
            updates.append("TAGS = ?")
            params.append(tags)
        if not updates:
            return False
        updates.append("LASTCHANGED = ?")
        params.append(datetime.now())
        params.append(entry_id)
        cur.execute(f"UPDATE PASSENTRIES SET {', '.join(updates)} WHERE IDPASSENTRY = ?", params)
        con.commit()
        return True

    def delete_password(self, entry_id: int) -> bool:
        """Soft-delete a password entry."""
        if not self._row_exists("PASSENTRIES", "IDPASSENTRY", entry_id):
            return False
        con = self._connect()
        cur = con.cursor()
        now = datetime.now()
        cur.execute(
            "UPDATE PASSENTRIES SET STATUS = 1, DELETED = ?, LASTCHANGED = ? "
            "WHERE IDPASSENTRY = ?",
            (now, now, entry_id),
        )
        con.commit()
        return True

    # ============================================================
    # LOOKUP TABLES
    # ============================================================

    def get_categories(self) -> list[dict[str, int | str]]:
        """Get all categories."""
        con = self._connect()
        cur = con.cursor()
        cur.execute("SELECT IDCATEGORY, NAME FROM CATEGORIES WHERE NAME IS NOT NULL ORDER BY CATEGORYINDEX")
        return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

    def get_calendars(self) -> list[dict[str, int | str]]:
        """Get all calendars."""
        con = self._connect()
        cur = con.cursor()
        cur.execute("SELECT IDCALENDAR, NAME FROM CALENDARS WHERE NAME IS NOT NULL")
        return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

    def get_todolists(self) -> list[dict[str, int | str]]:
        """Get all todo lists."""
        con = self._connect()
        cur = con.cursor()
        cur.execute("SELECT IDTODOLIST, NAME FROM TODOLISTS")
        return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

    def get_notetrees(self) -> list[dict[str, int | str]]:
        """Get all note trees."""
        con = self._connect()
        cur = con.cursor()
        cur.execute("SELECT IDNOTETREE, NAME FROM NOTETREES")
        return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
