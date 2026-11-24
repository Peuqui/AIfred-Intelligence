"""plan_archive: the CSV round trip, the header that grows, the last entries and the trend table over several runs."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from aifred.lib.plan_archive import STATUS_NOT_CHECKED, append_run, last_entries, trend_table
from aifred.lib.plan_engine import PartOutcome, PlanResult
from aifred.lib.plan_record import PartRecords, PlanEntry
from aifred.lib.task_plan import TaskPlan

SHOP_A = "https://a.example/x"
SHOP_B = "https://b.example/y"


def _plan(columns: list[dict[str, str]] | None = None, trend: bool = True) -> TaskPlan:
    payload: dict = {
        "frame": "f",
        "parts": [
            {"id": "t1", "title": "Teil 1", "task": "x",
             "positions": [{"id": "p1", "name": "Pistole"}, {"id": "p2", "name": "Magazin"}]},
        ],
        "columns": columns if columns is not None else [{"name": "preis", "type": "zahl"}, {"name": "shop", "type": "text"}],
    }
    if trend:
        payload["trend"] = {"value": "preis"}
    return TaskPlan.from_payload(payload)


def _entry(position_id: str, price: float | None, source: str = SHOP_A, status: str = "gefunden",
           befund: str = "ok", **more: object) -> PlanEntry:
    values: dict = dict(more)
    if price is not None:
        values["preis"] = price
    return PlanEntry(position_id, status, befund, source, values)


def _result(plan: TaskPlan, entries: list[PlanEntry], failure: str | None = None) -> PlanResult:
    records = PartRecords(entries=list(entries))
    part = plan.parts[0]
    outcome = PartOutcome(part=part, records=records, report="", failure=failure,
                          not_checked=records.open_positions(part))
    return PlanResult(plan=plan, outcomes=[outcome])


def _when(day: int) -> datetime:
    return datetime(2026, 11, day, 8, 30, 45)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_commas_quotes_and_line_breaks_survive_the_round_trip(tmp_path: Path) -> None:
    plan = _plan()
    path = tmp_path / "a.csv"
    befund = 'Preis "inkl. Versand", nicht\nlieferbar; zweite Zeile, mit Komma'
    append_run(path, plan, _result(plan, [_entry("p1", 12.5, befund=befund, shop='Shop "X", DE'), _entry("p2", 3)]), _when(1))
    rows = _rows(path)
    assert rows[0]["befund"] == befund
    assert rows[0]["shop"] == 'Shop "X", DE'
    assert rows[0]["zeitpunkt"] == "2026-11-01T08:30:45"
    assert (rows[0]["teil"], rows[0]["position"], rows[0]["status"], rows[0]["quelle"]) == ("t1", "p1", "gefunden", SHOP_A)
    history = last_entries(path, plan)
    assert history[("t1", "p1")] == [_entry("p1", 12.5, befund=befund, shop='Shop "X", DE')]
    assert history[("t1", "p2")][0].values == {"preis": 3}
    assert isinstance(history[("t1", "p2")][0].values["preis"], int)


def test_empty_values_stay_empty_and_are_left_out_when_read(tmp_path: Path) -> None:
    plan = _plan()
    path = tmp_path / "a.csv"
    append_run(path, plan, _result(plan, [_entry("p1", None, status="nicht_gefunden", befund="nichts"), _entry("p2", 4.0)]), _when(1))
    assert _rows(path)[0]["preis"] == ""
    assert _rows(path)[0]["shop"] == ""
    assert last_entries(path, plan)[("t1", "p1")][0].values == {}


def test_a_new_column_widens_the_header_and_old_rows_stay_empty(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    old = _plan([{"name": "preis", "type": "zahl"}])
    append_run(path, old, _result(old, [_entry("p1", 10), _entry("p2", 5)]), _when(1))
    new = _plan([{"name": "preis", "type": "zahl"}, {"name": "versand", "type": "zahl"}])
    append_run(path, new, _result(new, [_entry("p1", 9, versand=4.9), _entry("p2", 5)]), _when(2))
    rows = _rows(path)
    assert list(rows[0]) == ["zeitpunkt", "teil", "position", "status", "befund", "quelle", "preis", "versand"]
    assert [row["versand"] for row in rows] == ["", "", "4.9", ""]
    assert [row["preis"] for row in rows] == ["10", "5", "9", "5"]


def test_a_dropped_column_stays_in_the_header(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    old = _plan([{"name": "preis", "type": "zahl"}, {"name": "shop", "type": "text"}])
    append_run(path, old, _result(old, [_entry("p1", 10, shop="A"), _entry("p2", 5)]), _when(1))
    new = _plan([{"name": "preis", "type": "zahl"}])
    append_run(path, new, _result(new, [_entry("p1", 9), _entry("p2", 5)]), _when(2))
    rows = _rows(path)
    assert "shop" in rows[0]
    assert [row["shop"] for row in rows] == ["A", "", "", ""]
    assert len(rows) == 4


def test_extra_finds_and_not_checked_positions_get_rows(tmp_path: Path) -> None:
    plan = _plan()
    path = tmp_path / "a.csv"
    entries = [_entry("p1", 10), _entry("zusatz", 8, source=SHOP_B, befund="Alternative")]
    append_run(path, plan, _result(plan, entries, failure="Zeitüberschreitung"), _when(1))
    rows = _rows(path)
    assert [(row["position"], row["status"], row["befund"]) for row in rows] == [
        ("p1", "gefunden", "ok"), ("zusatz", "gefunden", "Alternative"), ("p2", STATUS_NOT_CHECKED, "Zeitüberschreitung"),
    ]
    assert rows[2]["preis"] == "" and rows[2]["quelle"] == ""
    append_run(path, plan, _result(plan, [_entry("p1", 10)]), _when(2))
    assert _rows(path)[-1]["befund"] == ""


def test_last_entries_take_the_last_time_the_position_had_entries(tmp_path: Path) -> None:
    plan = _plan()
    path = tmp_path / "a.csv"
    append_run(path, plan, _result(plan, [_entry("p1", 10), _entry("p1", 11, source=SHOP_B), _entry("p2", 5)]), _when(1))
    append_run(path, plan, _result(plan, [_entry("p1", 9)]), _when(2))   # p2 not checked in run 2
    history = last_entries(path, plan)
    assert [entry.values["preis"] for entry in history[("t1", "p1")]] == [9]
    assert [entry.values["preis"] for entry in history[("t1", "p2")]] == [5]
    assert last_entries(tmp_path / "missing.csv", plan) == {}


def _table_rows(table: str) -> dict[str, list[str]]:
    lines = table.splitlines()
    return {cells[0]: cells[1:] for cells in ([c.strip() for c in line.strip("|").split("|")] for line in lines[2:])}


def test_trend_table_over_three_runs(tmp_path: Path) -> None:
    plan = _plan()
    path = tmp_path / "a.csv"
    # p1 falls, p2 stays, p3-like cases: p1 falls then rises; p2 equal; the second position only from run 2 on
    append_run(path, plan, _result(plan, [_entry("p1", 10), _entry("p1", 12, source=SHOP_B)]), _when(1))
    append_run(path, plan, _result(plan, [_entry("p1", 8), _entry("p2", 5.5, source=SHOP_B)]), _when(2))
    append_run(path, plan, _result(plan, [_entry("p1", 9, source=SHOP_B), _entry("p2", 5.5, source=SHOP_B)]), _when(3))
    table = trend_table(path, plan, "de")
    lines = table.splitlines()
    assert lines[0] == "| Position | Dieser Lauf | Vorheriger Lauf | Bisher bester Wert | Trend |"
    assert set(lines[1]) <= {"|", "-"}
    rows = _table_rows(table)
    assert rows["Pistole"] == [f"9 ({SHOP_B})", "8", f"8 (2026-11-02T08:30, {SHOP_A})".replace("T", " "), "↑"]
    assert rows["Magazin"] == [f"5,5 ({SHOP_B})", "5,5", f"5,5 (2026-11-02 08:30, {SHOP_B})", "→"]
    append_run(path, plan, _result(plan, [_entry("p1", 7, source=SHOP_A), _entry("p2", 5.5, source=SHOP_B)]), _when(4))
    assert _table_rows(trend_table(path, plan, "en"))["Pistole"][-1] == "↓"


def test_trend_table_new_position_and_never_found(tmp_path: Path) -> None:
    plan = _plan()
    path = tmp_path / "a.csv"
    append_run(path, plan, _result(plan, [_entry("p1", 10)]), _when(1))
    append_run(path, plan, _result(plan, [_entry("p1", 10), _entry("p2", 5)]), _when(2))
    rows = _table_rows(trend_table(path, plan, "de"))
    assert rows["Magazin"][1] == "kein Wert" and rows["Magazin"][-1] == "neu"
    assert rows["Pistole"][-1] == "→"
    never = tmp_path / "never.csv"
    append_run(never, plan, _result(plan, [_entry("p1", None, status="nicht_gefunden", befund="nichts")]), _when(1))
    rows = _table_rows(trend_table(never, plan, "de"))
    assert rows["Pistole"] == ["kein Wert", "kein Wert", "kein Wert", "kein Wert"]


def test_trend_table_needs_a_trend_column_and_escapes_pipes(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    plan = _plan(trend=False)
    append_run(path, plan, _result(plan, [_entry("p1", 10)]), _when(1))
    assert trend_table(path, plan, "de") == ""
    with_trend = _plan()
    append_run(path, with_trend, _result(with_trend, [_entry("p1", 10, source="https://a.example/?q=a|b")]), _when(2))
    assert "a\\|b" in trend_table(path, with_trend, "de")
