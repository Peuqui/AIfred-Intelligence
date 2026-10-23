"""EPIM database layer against a copy of an entry-free EPIM database
(EPIM's own triggers, views and lookup tables)."""

# EPIM stores naive local wall-clock time; the tests use exactly that.
# ruff: noqa: DTZ001

import os
from datetime import datetime, timedelta

import fdb
import pytest
from epim_testdb import ALL_READ, ALL_WRITE, copy_fixture, requires_fixture

from aifred.plugins.tools.epim import codec, firebird
from aifred.plugins.tools.epim.db import (
    EpimDatabase,
    EpimModuleDisabled,
    EpimRefused,
    EpimWriteDisabled,
)
from aifred.plugins.tools.epim.maintenance import set_access_mode

pytestmark = requires_fixture

START = datetime(2026, 11, 3, 10, 0)


def _task(db: EpimDatabase, title: str = "Zahnarzt", start: datetime = START,
          end: datetime = START + timedelta(hours=1), all_day: bool = False) -> int:
    return db.create_task(title=title, start=start, end=end, all_day=all_day, location=None,
                          text=None, category=None, calendar=None, tags=None)


def _raw(db: EpimDatabase, sql: str, params: tuple = ()) -> list[tuple]:
    with db._read() as cur:
        cur.execute(sql, params)
        rows: list[tuple] = cur.fetchall()
        return rows


def _raw_write(db: EpimDatabase, sql: str, params: tuple = ()) -> None:
    with db._write() as cur:
        cur.execute(sql, params)


@pytest.fixture
def db(tmp_path):
    database = EpimDatabase(copy_fixture(tmp_path), ALL_WRITE)
    yield database
    database.close()


def test_write_switch_off_refuses_before_touching_the_file(tmp_path):
    database = EpimDatabase(copy_fixture(tmp_path), ALL_READ)
    with pytest.raises(EpimWriteDisabled):
        _task(database)
    assert database.search_tasks(None, None, None, 10) == []
    database.close()


def test_module_levels_are_enforced_in_the_database_layer(tmp_path):
    database = EpimDatabase(copy_fixture(tmp_path), {**ALL_WRITE, "contacts": "off", "notes": "read"})
    with pytest.raises(EpimModuleDisabled):
        database.search_contacts(None, 10)
    with pytest.raises(EpimWriteDisabled):
        database.delete_note(1)
    assert "contact_fields" not in database.lookups() and "notetrees" in database.lookups()
    database.close()


def test_read_only_file_is_refused_by_the_engine(tmp_path):
    path = copy_fixture(tmp_path)
    set_access_mode(path, read_only=True)
    database = EpimDatabase(path, ALL_WRITE)
    with pytest.raises(fdb.DatabaseError):
        _task(database)
    assert database.search_tasks(None, None, None, 10) == []
    database.close()


def test_reading_a_read_only_file_leaves_it_byte_identical(tmp_path):
    import hashlib
    path = copy_fixture(tmp_path)
    set_access_mode(path, read_only=True)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    database = EpimDatabase(path, ALL_READ)
    database.search_tasks(None, None, None, 10)
    database.search_contacts(None, 10)
    database.lookups()
    database.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_timed_task_roundtrip(db):
    task_id = _task(db)
    task = db.get_task(task_id)
    assert task is not None
    assert (task["title"], task["start_time"], task["end_time"]) == ("Zahnarzt", START, START + timedelta(hours=1))
    assert task["all_day"] is False and task["recurring"] is False
    assert task["calendar"] == "Kalender 1"


def test_search_matches_events_overlapping_the_window(db):
    _task(db, "Urlaub", datetime(2026, 11, 1), datetime(2026, 11, 8), all_day=True)
    window = db.search_tasks(None, datetime(2026, 11, 3).date(), datetime(2026, 11, 4).date(), 10)
    assert [t["title"] for t in window] == ["Urlaub"]
    assert db.search_tasks(None, datetime(2026, 11, 9).date(), None, 10) == []


def test_moving_start_keeps_duration(db):
    task_id = _task(db)
    db.update_task(task_id, {"start": START + timedelta(days=1, hours=5)})
    task = db.get_task(task_id)
    assert task is not None
    assert task["end_time"] - task["start_time"] == timedelta(hours=1)


def test_end_before_start_is_refused(db):
    with pytest.raises(EpimRefused):
        _task(db, end=START - timedelta(minutes=1))
    task_id = _task(db)
    with pytest.raises(EpimRefused):
        db.update_task(task_id, {"end": START - timedelta(hours=2)})


def test_recurring_series_is_refused(db):
    master = 777000000001
    _raw_write(db, "INSERT INTO TASKS (IDTASK, IDPARENT, READACCESS, WRITEACCESS, IDCREATOR, IDEDITOR, "
                   "STATUS, TITLE, STARTTIME, ENDTIME, REPEATING, CALENDAR) "
                   "VALUES (?, ?, -1, -1, 1, 1, 0, 'Serie', ?, ?, 1, "
                   "(SELECT IDCALENDAR FROM CALENDARS WHERE NAME IS NOT NULL))",
               (master, master, START, START + timedelta(hours=1)))
    with pytest.raises(EpimRefused, match="recurring"):
        db.update_task(master, {"title": "x"})
    with pytest.raises(EpimRefused, match="recurring"):
        db.delete_task(master)


def test_soft_delete_like_epim(db):
    task_id = _task(db)
    (before,) = _raw(db, "SELECT LASTCHANGED FROM TASKS WHERE IDTASK = ?", (task_id,))
    db.delete_task(task_id)
    status, deleted, last_changed = _raw(db, "SELECT STATUS, DELETED, LASTCHANGED FROM TASKS WHERE IDTASK = ?",
                                         (task_id,))[0]
    assert status == 1 and deleted is not None
    assert last_changed == before[0]  # EPIM leaves LASTCHANGED alone on delete
    assert db.get_task(task_id) is None


def test_no_transaction_stays_open(db):
    _task(db)
    db.search_tasks(None, None, None, 10)
    db.lookups()
    observer = firebird.connect(db.db_path)
    try:
        cur = observer.cursor()
        cur.execute("SELECT COUNT(*) FROM MON$TRANSACTIONS WHERE MON$ATTACHMENT_ID <> CURRENT_CONNECTION")
        assert cur.fetchone()[0] == 0
        observer.commit()
    finally:
        observer.close()


def test_replaced_file_is_reconnected(db, tmp_path):
    other_path = copy_fixture(tmp_path, "other.epim")
    other = EpimDatabase(other_path, ALL_WRITE)
    _task(other, "Aus dem Spiegel")
    other.close()
    assert db.search_tasks("Spiegel", None, None, 10) == []
    os.replace(other_path, db.db_path)
    assert [t["title"] for t in db.search_tasks("Spiegel", None, None, 10)] == ["Aus dem Spiegel"]


def test_nested_transaction_is_a_programming_error(db):
    with db._read(), pytest.raises(RuntimeError, match="nested"), db._read():
        pass


def test_contact_create_derives_name_and_stores_dates_as_day_numbers(db):
    # Delphi day numbers: day 0 = 1899-12-30, so 1900-01-01 is day 2.
    contact_id = db.create_contact(name=None, fields={"Vorname": "Jörg", "Nachname": "Meier",
                                                      "Geburtstag": "1900-01-01"}, tags=None)
    contact = db.get_contact(contact_id)
    assert contact is not None
    assert contact["name"] == "Jörg Meier"
    assert contact["fields"]["Geburtstag"] == "1900-01-01"
    (raw,) = _raw(db, "SELECT FIELDSDATA FROM CONTACTS WHERE IDCONTACT = ?", (contact_id,))[0]
    assert (15, "2") in codec.decode(raw).items


def _raw_contact(db: EpimDatabase, contact_id: int, fieldsdata: str) -> None:
    _raw_write(db, "INSERT INTO CONTACTS (IDCONTACT, IDACCOUNT, READACCESS, WRITEACCESS, IDCREATOR, IDEDITOR, "
                   "STATUS, SUBJECT, COLLADR, FIELDSDATA) VALUES (?, 1, -1, -1, 1, 1, 0, 'Roh', 0, ?)",
               (contact_id, fieldsdata))


def test_contact_update_keeps_fields_without_a_label(db):
    _raw_contact(db, 888000000001, codec.encode([(1, "Stefan"), (99, "Bürozeiten Mo–Fr")]))
    db.update_contact(888000000001, name=None, fields={"Telefon": "0561 123"}, tags=None, tags_given=False)
    contact = db.get_contact(888000000001)
    assert contact is not None
    assert contact["fields"] == {"Vorname": "Stefan", "field_99": "Bürozeiten Mo–Fr", "Telefon": "0561 123"}


def test_damaged_contact_is_read_with_a_warning_and_not_written(db):
    _raw_contact(db, 888000000002, "000000010006Muster00000006000FHauptstraße 111000000070009Musterort")
    contact = db.get_contact(888000000002)
    assert contact is not None and "field_data_error" in contact
    with pytest.raises(EpimRefused, match="damaged"):
        db.update_contact(888000000002, name=None, fields={"Telefon": "1"}, tags=None, tags_given=False)


def test_unknown_contact_field_is_refused(db):
    with pytest.raises(EpimRefused, match="unknown contact field"):
        db.create_contact(name="X", fields={"Mobiltelefon": "0172"}, tags=None)


def test_ambiguous_custom_field_is_not_writable(db):
    labels = {e.name for e in db.lookups()["contact_fields"]}
    assert "Spitzname" in labels and "Vorname" in labels
    assert "Obsolete" not in labels  # exists twice among the custom fields


def test_note_with_live_children_is_not_deleted(db):
    tree = _raw(db, "SELECT FIRST 1 IDNOTETREE FROM NOTETREES")[0][0]
    for note_id, parent in ((990000000001, 0), (990000000002, 990000000001)):
        _raw_write(db, "INSERT INTO NOTES (IDNOTE, IDPARENT, READACCESS, WRITEACCESS, IDCREATOR, IDEDITOR, "
                       "STATUS, TITLE, IDNOTETREE) VALUES (?, ?, -1, -1, 1, 1, 0, 'N', ?)", (note_id, parent, tree))
    with pytest.raises(EpimRefused, match="sub-entries"):
        db.delete_note(990000000001)
    db.delete_note(990000000002)
    assert db.get_note(990000000002) is None


def test_zero_date_reads_as_no_date(db):
    zero = datetime(1899, 12, 30)
    list_id = _raw(db, "SELECT FIRST 1 IDTODOLIST FROM TODOLISTS")[0][0]
    _raw_write(db, "INSERT INTO TODOS (IDTODO, IDPARENT, READACCESS, WRITEACCESS, IDCREATOR, IDEDITOR, STATUS, "
                   "TITLE, STARTTIME, ENDTIME, COMPLETION, COMPLETED, IDLIST) "
                   "VALUES (991000000001, 0, -1, -1, 1, 1, 0, 'Milch', ?, ?, -1, ?, ?)", (zero, zero, zero, list_id))
    (todo,) = db.search_todos(None, None, 10)
    assert todo["start_time"] is None and todo["end_time"] is None and todo["done"] is False
