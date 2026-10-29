"""EPIM maintenance: rebuild, mirror installation, snapshots."""

# EPIM stores naive local wall-clock time; the tests use exactly that.

from datetime import datetime, timedelta

import pytest
from epim_testdb import ALL_READ, ALL_WRITE, copy_fixture, requires_fixture

from aifred.plugins.tools.epim import firebird, maintenance
from aifred.plugins.tools.epim.db import EpimDatabase
from aifred.plugins.tools.epim.settings import EpimSettings

pytestmark = requires_fixture


def _add_task(path, title: str) -> None:
    db = EpimDatabase(path, ALL_WRITE)
    start = datetime(2026, 11, 3, 10)
    db.create_task(title=title, start=start, end=start + timedelta(hours=1), all_day=False,
                   location=None, text=None, category=None, calendar=None, tags=None)
    db.close()


def _is_read_only(path) -> bool:
    con = firebird.connect(path)
    try:
        cur = con.cursor()
        cur.execute("SELECT MON$READ_ONLY FROM MON$DATABASE")
        read_only = bool(cur.fetchone()[0])
        con.commit()
        return read_only
    finally:
        con.close()


def test_rebuild_keeps_every_record(tmp_path):
    source = copy_fixture(tmp_path)
    _add_task(source, "Bleibt")
    dest = tmp_path / "rebuilt.epim"
    maintenance.rebuild(source, dest)
    assert maintenance.record_counts(dest) == maintenance.record_counts(source)
    assert not (tmp_path / "rebuilt.epim.fbk").exists()


def test_install_mirror_replaces_atomically_and_keeps_previous(tmp_path, monkeypatch):
    target = copy_fixture(tmp_path, "mirror.epim")
    incoming = copy_fixture(tmp_path, "incoming.epim")
    _add_task(incoming, "Neu von Aragon")
    monkeypatch.setattr(maintenance, "load_epim_settings", lambda: EpimSettings(target, ALL_READ))
    maintenance.install_mirror(incoming)
    assert not incoming.exists()
    assert (tmp_path / "mirror.epim.previous").exists()
    assert _is_read_only(target)
    db = EpimDatabase(target, ALL_READ)
    assert [t["title"] for t in db.search_tasks(None, None, None, 10)] == ["Neu von Aragon"]
    db.close()


def test_install_mirror_refused_while_a_module_is_writable(tmp_path, monkeypatch):
    target = copy_fixture(tmp_path, "mirror.epim")
    monkeypatch.setattr(maintenance, "load_epim_settings", lambda: EpimSettings(target, ALL_WRITE))
    with pytest.raises(maintenance.MaintenanceError, match="writable"):
        maintenance.install_mirror(copy_fixture(tmp_path, "incoming.epim"))


def test_snapshot_diff_shows_inserted_task(tmp_path):
    path = copy_fixture(tmp_path)
    before = maintenance.snapshot(path)
    _add_task(path, "Referenz")
    lines = maintenance.diff(before, maintenance.snapshot(path))
    assert any(line.startswith("+ TASKS ") and "Referenz" in line for line in lines)
