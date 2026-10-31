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
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "epim_deletions_to_nextcloud.py"
_SPEC = importlib.util.spec_from_file_location("epim_deletions_to_nextcloud", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
tool = importlib.util.module_from_spec(_SPEC)
sys.modules["epim_deletions_to_nextcloud"] = tool
_SPEC.loader.exec_module(tool)

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
    return tool.Settings("host", 22, "user", "/backups", "MP.backup_*.epim", Path("/tmp/x"), 20)


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
