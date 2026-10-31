"""Tests für scripts/epim_deletions_to_nextcloud.py, die Entscheidung je Eintrag.

Kontrakt:
- Gelöscht wird nur bei genau einem Treffer mit gleichem Titel und gleichem Beginn,
  der keine Serie ist und zu dem EPIM keinen aktiven gleichen Eintrag hat.
- Serien und mehrdeutige Treffer bleiben stehen und werden gemeldet.
- Ein Eintrag, den Nextcloud nicht mehr hat, ist kein Fehler.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
