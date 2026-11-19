"""Tests für scripts/epim_deletions_to_nextcloud.py, die Entscheidung je Eintrag.

Kontrakt:
- Gelöscht wird nur bei genau einem Treffer mit gleichem Titel und gleichem Beginn,
  der keine Serie ist und zu dem EPIM keinen aktiven gleichen Eintrag hat.
- Serien und mehrdeutige Treffer bleiben stehen und werden gemeldet.
- Ein Eintrag, den Nextcloud nicht mehr hat, ist kein Fehler.
- Kontakte haben nur ihren Namen und in Nextcloud keinen Papierkorb: gelöscht wird nur bei
  genau einem Kontakt dieses Namens, solange EPIM keinen aktiven gleichnamigen hat.
- Aufgaben stehen in Nextcloud in mehreren Listen zugleich (EPIM verteilt sie so): gelöscht
  werden alle Kopien gleichen Titels, aber höchstens eine je Liste. Zwei gleiche in einer
  Liste sind mehrdeutig und bleiben stehen.
- Die Bremse: Über max_deletions wird nichts gelöscht, die Zeitmarke bleibt stehen, und der
  Benutzer bekommt je Sicherung genau eine Nachricht. Scheitert sie, kommt der nächste Lauf
  dran. Der Lauf ohne --apply benachrichtigt nie.
- Nur neue Löschungen: Der erste Lauf löscht nichts, was schon im Papierkorb von EPIM lag, und
  setzt die Zeitmarke auf die neueste Löschung. Jeder weitere Lauf liest ab der Marke des
  vorigen und nimmt nur, was danach gelöscht wurde.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from epim_testdb import ALL_WRITE, copy_fixture, requires_fixture
from script_module import load_script

from aifred.plugins.tools.epim.db import EpimDatabase

if TYPE_CHECKING:
    from aifred.plugins.tools.nextcloud.store import NextcloudStore

tool = load_script("epim_deletions_to_nextcloud.py", "epim_deletions_to_nextcloud")

START = datetime(2026, 11, 2, 10, 0)


def _entry(title: str = "Zahnarzt", start: datetime = START, series: bool = False) -> Any:
    return tool.EpimEntry(title, start, series)


def _row(title: str = "Zahnarzt", start: Any = START, recurring: bool = False, object_id: str = "cal/1.ics") -> dict:
    return {"id": object_id, "title": title, "start": start, "recurring": recurring}


def _plan(entry: Any, rows: list[dict], active: set | None = None) -> Any:
    return tool.plan_deletions([entry], active or set(), lambda _entry: rows)


def test_single_exact_match_is_deleted() -> None:
    plan = _plan(_entry(), [_row()])
    assert plan.delete == [(_entry(), "cal/1.ics")]


def test_aware_time_from_nextcloud_matches_naive_time_from_epim() -> None:
    aware = START.replace(tzinfo=timezone(timedelta(hours=1)))
    assert _plan(_entry(), [_row(start=aware)]).delete


def test_all_day_date_from_nextcloud_matches_midnight_from_epim() -> None:
    midnight = datetime(2026, 11, 2)
    assert _plan(_entry(start=midnight), [_row(start=date(2026, 11, 2))]).delete


def test_entry_gone_from_nextcloud_is_no_error() -> None:
    plan = _plan(_entry(), [])
    assert plan.already_gone == [_entry()] and not plan.delete


def test_other_title_or_time_does_not_match() -> None:
    rows = [_row(title="Zahnarzt Kontrolle"), _row(start=START + timedelta(hours=1))]
    plan = _plan(_entry(), rows)
    assert plan.already_gone == [_entry()] and not plan.delete


def test_two_matches_are_left_alone() -> None:
    plan = _plan(_entry(), [_row(object_id="a.ics"), _row(object_id="b.ics")])
    assert plan.ambiguous == [_entry()] and not plan.delete


def test_active_entry_with_same_key_protects_it() -> None:
    plan = _plan(_entry(), [_row()], active={("Zahnarzt", START)})
    assert plan.still_active == [_entry()] and not plan.delete


def test_series_in_epim_is_left_alone_without_asking_nextcloud() -> None:
    def fail(_entry: Any) -> list[dict]:
        raise AssertionError("must not search")

    plan = tool.plan_deletions([_entry(series=True)], set(), fail)
    assert plan.series and not plan.delete


def test_recurring_match_in_nextcloud_is_left_alone() -> None:
    plan = _plan(_entry(), [_row(recurring=True)])
    assert plan.series == [_entry()] and not plan.delete


def _contact(name: str = "Hans Muster", object_id: str = "book/1.vcf") -> dict:
    return {"id": object_id, "name": name}


def _task(title: str = "Milch", task_list: str = "Einkauf", object_id: str = "list/1.ics") -> dict:
    return {"id": object_id, "title": title, "list": task_list}


def test_contact_with_a_single_exact_name_is_deleted() -> None:
    plan = tool.plan_contact_deletions(["Hans Muster"], set(), lambda _name: [_contact()])
    assert [object_id for _entry, object_id in plan.delete] == ["book/1.vcf"]


def test_contact_search_hit_with_another_name_does_not_count() -> None:
    plan = tool.plan_contact_deletions(["Hans Muster"], set(), lambda _name: [_contact("Hans Mustermann")])
    assert not plan.delete and len(plan.already_gone) == 1


def test_two_contacts_of_that_name_are_left_alone() -> None:
    rows = [_contact(), _contact(object_id="book/2.vcf")]
    plan = tool.plan_contact_deletions(["Hans Muster"], set(), lambda _name: rows)
    assert not plan.delete and len(plan.ambiguous) == 1


def test_active_contact_of_that_name_protects_it() -> None:
    plan = tool.plan_contact_deletions(["Hans Muster"], {"Hans Muster"}, lambda _name: [_contact()])
    assert not plan.delete and len(plan.still_active) == 1


def test_contact_without_a_name_is_never_searched() -> None:
    def fail(_name: str) -> list[dict]:
        raise AssertionError("must not search")

    plan = tool.plan_contact_deletions([""], set(), fail)
    assert not plan.delete and len(plan.ambiguous) == 1


def test_same_name_twice_in_the_bin_is_one_deletion() -> None:
    plan = tool.plan_contact_deletions(["Hans Muster", "Hans Muster"], set(), lambda _name: [_contact()])
    assert len(plan.delete) == 1


def test_task_copies_in_several_lists_all_go() -> None:
    rows = [_task(task_list=name, object_id=f"{name}/1.ics") for name in ("Einkauf", "ToDo", "Liste 1")]
    plan = tool.plan_task_deletions(["Milch"], set(), lambda _title: rows)
    assert sorted(object_id for _entry, object_id in plan.delete) == ["Einkauf/1.ics", "Liste 1/1.ics", "ToDo/1.ics"]


def test_two_equal_tasks_in_one_list_are_left_alone() -> None:
    rows = [_task(), _task(object_id="list/2.ics"), _task(task_list="ToDo", object_id="other/1.ics")]
    plan = tool.plan_task_deletions(["Milch"], set(), lambda _title: rows)
    assert not plan.delete and len(plan.ambiguous) == 1


def test_active_task_of_that_title_protects_it() -> None:
    plan = tool.plan_task_deletions(["Milch"], {"Milch"}, lambda _title: [_task()])
    assert not plan.delete and len(plan.still_active) == 1


def test_task_search_hit_with_another_title_does_not_count() -> None:
    plan = tool.plan_task_deletions(["Milch"], set(), lambda _title: [_task("Milchreis")])
    assert not plan.delete and len(plan.already_gone) == 1


def _settings() -> Any:
    return tool.Settings("host", 22, "user", "/backups", "MP.backup_*.epim", Path("/tmp/x"), 20,
                         "email", "Lord Helmchen", "Bremse", "{total} über {limit}")


def _announce(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, delivered: bool = True) -> list[tuple]:
    sent: list[tuple] = []

    async def announce(channel: str, recipient: str, text: str, *, session_id: str | None,
                       metadata: dict | None = None) -> bool:
        sent.append((channel, recipient, text, session_id, metadata))
        return delivered

    monkeypatch.setattr("aifred.lib.message_processor.announce_to_channel", announce)
    monkeypatch.setattr(tool, "STATE_FILE", tmp_path / "state.json")
    return sent


def test_stop_tells_the_user_what_is_pending(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _announce(monkeypatch, tmp_path)
    tool.notify_stop(_settings(), {"deleted_after": "2026-10-31T15:42:07"}, "/backups/new.epim", 750)
    assert sent == [("email", "Lord Helmchen", "750 über 20", None, {"subject": "Bremse"})]


def test_stop_notifies_once_per_backup_and_keeps_the_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _announce(monkeypatch, tmp_path)
    state = {"backup": "/backups/old.epim", "deleted_after": "2026-10-31T15:42:07"}
    tool.notify_stop(_settings(), state, "/backups/new.epim", 750)
    saved = json.loads(tool.STATE_FILE.read_text(encoding="utf-8"))
    tool.notify_stop(_settings(), saved, "/backups/new.epim", 750)
    assert len(sent) == 1
    assert saved == {**state, "stop_notified": "/backups/new.epim"}


def test_next_backup_notifies_again(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _announce(monkeypatch, tmp_path)
    tool.notify_stop(_settings(), {"stop_notified": "/backups/old.epim"}, "/backups/new.epim", 750)
    assert len(sent) == 1


def test_failed_notification_is_tried_again(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _announce(monkeypatch, tmp_path, delivered=False)
    tool.notify_stop(_settings(), {}, "/backups/new.epim", 750)
    assert len(sent) == 1 and not tool.STATE_FILE.exists()


def _ssh_result(monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str) -> None:
    def run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    monkeypatch.setattr(tool.subprocess, "run", run)


def test_machine_switched_off_is_no_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _ssh_result(monkeypatch, tool.SSH_CONNECTION_FAILED, "")
    assert tool.latest_backup(_settings()) is None


def test_newest_backup_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _ssh_result(monkeypatch, 0, "/backups/new.epim\n/backups/old.epim\n")
    assert tool.latest_backup(_settings()) == "/backups/new.epim"


def test_missing_backup_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _ssh_result(monkeypatch, 2, "")
    with pytest.raises(subprocess.CalledProcessError):
        tool.latest_backup(_settings())


# ── Off means off (14.11.2026) ──────────────────────────────────

def _switches(monkeypatch: pytest.MonkeyPatch, *, epim: bool = True, nextcloud: bool = True, sync: bool = True) -> None:
    from aifred.lib import plugin_registry
    from aifred.plugins.tools.epim import settings as epim_settings

    monkeypatch.setattr(plugin_registry, "is_plugin_enabled", lambda name: {"epim": epim, "nextcloud": nextcloud}[name])
    monkeypatch.setattr(epim_settings, "load_epim_settings", lambda: epim_settings.EpimSettings(db_path=None, deletion_sync=sync))


def test_it_runs_only_with_both_plugins_and_its_setting_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _switches(monkeypatch)
    assert tool.reason_not_to_run() is None
    _switches(monkeypatch, epim=False)
    assert "EPIM plugin is off" in str(tool.reason_not_to_run())
    _switches(monkeypatch, nextcloud=False)
    assert "Nextcloud plugin is off" in str(tool.reason_not_to_run())
    _switches(monkeypatch, sync=False)
    assert "setting" in str(tool.reason_not_to_run())


@pytest.mark.parametrize("apply", [True, False])
def test_a_run_that_must_not_run_ends_without_touching_anything(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], apply: bool,
) -> None:
    _switches(monkeypatch, epim=False)

    def untouched(*_args: Any) -> None:
        raise AssertionError("the run went on although the plugin is off")

    monkeypatch.setattr(tool, "load_settings", untouched)
    monkeypatch.setattr(tool, "latest_backup", untouched)
    assert tool.run(apply) == 0
    assert "does nothing" in capsys.readouterr().out


def test_the_setting_is_a_field_of_the_plugin_with_the_default_on() -> None:
    from aifred.plugins.tools.epim import plugin
    from aifred.plugins.tools.epim.settings import DELETION_SYNC_KEY, parse_settings

    assert parse_settings({}).deletion_sync is True
    assert parse_settings({DELETION_SYNC_KEY: "off"}).deletion_sync is False
    with pytest.raises(ValueError):
        parse_settings({DELETION_SYNC_KEY: "maybe"})
    field = next(field for field in plugin.credential_fields if field.env_key == DELETION_SYNC_KEY)
    assert field.default == "on" and [value for value, _label in field.options or []] == ["on", "off"]


# ── Only new deletions (Issue #87) ──────────────────────────────

if TYPE_CHECKING:
    _StoreBase = NextcloudStore
else:
    _StoreBase = object


class FakeStore(_StoreBase):
    """Nextcloud with one event and one contact per name; it records what is deleted."""

    def __init__(self, names: list[str]) -> None:
        self.names = set(names)
        self.deleted: list[str] = []

    def events(self, date_from: date, date_to: date, query: str | None) -> list[dict[str, Any]]:
        assert query is not None
        return [_row(query, START, object_id=f"cal/{query}.ics")] if query in self.names else []

    def contacts(self, query: str | None) -> list[dict[str, Any]]:
        assert query is not None
        return [_contact(query, f"book/{query}.vcf")] if query in self.names else []

    def tasks(self, query: str | None, done: bool | None) -> list[dict[str, Any]]:
        return []

    def delete_event(self, object_id: str) -> None:
        self.deleted.append(object_id)

    def delete_contact(self, object_id: str) -> None:
        self.deleted.append(object_id)

    def delete_task(self, object_id: str) -> None:
        self.deleted.append(object_id)


def _wire_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, store: FakeStore,
              restored: Callable[[], Path]) -> None:
    """Everything around the run replaced: the switches, the settings, the machine with the backups (each run
    finds a newer one), the copy and restore of the backup, and Nextcloud."""
    from aifred.plugins.tools.nextcloud import store as store_module

    backups = iter(f"/backups/{number}.epim" for number in range(1, 10))
    monkeypatch.setattr(tool, "reason_not_to_run", lambda: None)
    monkeypatch.setattr(tool, "load_settings", _settings)
    monkeypatch.setattr(tool, "latest_backup", lambda _settings: next(backups))
    monkeypatch.setattr(tool, "fetch_restored", lambda _settings, _remote: (tmp_path / "backup.fbk", restored()))
    monkeypatch.setattr(tool, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(store_module, "get_store", lambda: store)


def _marker() -> datetime:
    state = json.loads(tool.STATE_FILE.read_text(encoding="utf-8"))
    return datetime.fromisoformat(state["deleted_after"])


def test_the_marker_of_one_run_is_what_the_next_run_reads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = FakeStore(["Zahnarzt"])
    _wire_run(monkeypatch, tmp_path, store, lambda: tmp_path / "restored.epim")
    first, second = datetime(2026, 11, 1, 9, 0), datetime(2026, 11, 2, 9, 0)
    asked: list[datetime | None] = []

    def read_epim(_path: Path, deleted_after: datetime | None) -> Any:
        asked.append(deleted_after)
        events = [_entry()] if deleted_after is not None else []
        return tool.EpimBin(events, set(), [], set(), [], set(), first if deleted_after is None else second)

    monkeypatch.setattr(tool, "read_epim", read_epim)
    assert tool.run(apply=True) == 0
    assert asked == [None] and store.deleted == [] and _marker() == first
    assert tool.run(apply=True) == 0
    assert asked == [None, first] and store.deleted == ["cal/Zahnarzt.ics"] and _marker() == second


@requires_fixture
def test_the_first_run_deletes_nothing_and_the_next_only_what_came_later(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The whole run against a real EPIM file: what lay in the recycle bin before the first run stays in
    Nextcloud, even where Nextcloud still has it; only what EPIM deletes after that goes."""
    master = copy_fixture(tmp_path, "master.epim")
    store = FakeStore(["Alt", "Neu", "Alt Kontakt", "Neu Kontakt"])

    def restored() -> Path:
        copy = tmp_path / "restored.epim"
        shutil.copyfile(master, copy)
        return copy

    def delete_in_epim(title: str) -> None:
        database = EpimDatabase(master, ALL_WRITE)
        try:
            database.delete_task(database.create_task(
                title=title, start=START, end=START + timedelta(hours=1), all_day=False, location=None,
                text=None, category=None, calendar=None, tags=None))
            database.delete_contact(database.create_contact(name=f"{title} Kontakt", fields={}, tags=None))
        finally:
            database.close()

    _wire_run(monkeypatch, tmp_path, store, restored)
    before = datetime.now()
    delete_in_epim("Alt")
    after = datetime.now()
    assert tool.run(apply=True) == 0
    assert store.deleted == []
    first_marker = _marker()
    assert before <= first_marker <= after

    delete_in_epim("Neu")
    assert tool.run(apply=True) == 0
    assert sorted(store.deleted) == ["book/Neu Kontakt.vcf", "cal/Neu.ics"]
    assert _marker() > first_marker
