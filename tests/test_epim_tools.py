"""EPIM tool facade: offered tools follow the write switch, arguments are
parsed strictly, IDs travel as strings."""

# EPIM stores naive local wall-clock time; the tests use exactly that.
# ruff: noqa: DTZ001

import asyncio
import json
from datetime import datetime

import pytest
from epim_testdb import copy_fixture, requires_fixture

from aifred.plugins.tools.epim.db import EpimDatabase
from aifred.plugins.tools.epim.tools import get_epim_tools

pytestmark = requires_fixture


def _tools(db: EpimDatabase, source: str = "browser") -> dict:
    return {t.name: t for t in get_epim_tools(db, source)}


def _call(tool, **kwargs) -> dict:
    result: dict = json.loads(asyncio.run(tool.executor(**kwargs)))
    return result


@pytest.fixture
def db(tmp_path):
    database = EpimDatabase(copy_fixture(tmp_path), write_enabled=True)
    yield database
    database.close()


def test_write_tools_only_with_the_switch_on(tmp_path, db):
    read_only = EpimDatabase(copy_fixture(tmp_path, "ro.epim"), write_enabled=False)
    assert set(_tools(read_only)) == {"epim_search", "epim_get"}
    assert set(_tools(db)) == {"epim_search", "epim_get", "epim_create", "epim_update", "epim_delete"}
    read_only.close()


def test_entity_types_are_an_enum_in_the_schema(db):
    schema = _tools(db)["epim_search"].parameters
    assert "contacts" in schema["properties"]["entity_type"]["enum"]


def test_all_day_end_is_the_inclusive_last_day(db):
    tools = _tools(db)
    created = _call(tools["epim_create"], entity_type="task",
                    data={"title": "Urlaub", "all_day": True, "start": "2026-11-02", "end": "2026-11-06"})
    assert created["success"] and isinstance(created["id"], str)
    with db._read() as cur:
        cur.execute("SELECT STARTTIME, ENDTIME FROM TASKS WHERE IDTASK = ?", (int(created["id"]),))
        stored = cur.fetchone()
    assert tuple(stored) == (datetime(2026, 11, 2), datetime(2026, 11, 7))
    shown = _call(tools["epim_get"], entity_type="task", entity_id=created["id"])["result"]
    assert (shown["start_time"], shown["end_time"]) == ("2026-11-02", "2026-11-06")


def test_unknown_field_is_reported_not_dropped(db):
    result = _call(_tools(db)["epim_create"], entity_type="task",
                   data={"title": "X", "start": "2026-11-02 10:00", "end": "2026-11-02 11:00", "priority": "high"})
    assert "priority" in result["error"]
    assert db.search_tasks(None, None, None, 10) == []


def test_ambiguous_date_is_rejected(db):
    result = _call(_tools(db)["epim_create"], entity_type="task",
                   data={"title": "X", "start": "10/11/2026 10:00", "end": "10/11/2026 11:00"})
    assert "YYYY-MM-DD" in result["error"]


def test_search_returns_ids_as_strings(db):
    tools = _tools(db)
    _call(tools["epim_create"], entity_type="task",
          data={"title": "Zahnarzt", "start": "2026-11-03 10:00", "end": "2026-11-03 11:00"})
    found = _call(tools["epim_search"], entity_type="tasks", query="zahn")
    assert found["total_count"] == 1
    assert isinstance(found["results"][0]["id"], str)
    assert found["results"][0]["start_time"] == "2026-11-03 10:00"


def test_password_delete_only_from_the_browser(db):
    result = _call(_tools(db, source="email")["epim_delete"], entity_type="password", entity_id="1")
    assert "web UI" in result["error"]
