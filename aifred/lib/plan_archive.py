"""The archive of a plan job: ``plan_archiv.csv`` in the job's folder, written only by the code, only appended.

One row per entry of a run (extra finds with position ``zusatz``), one row ``nicht_geprueft`` for each position that
got no entry (its finding is the reason of the part's failure, if the part failed). The columns are ``zeitpunkt``,
``teil``, ``position``, ``status``, ``befund``, ``quelle`` and then the job's columns in the plan's order. A column
the plan gained is added to the header (the file is written anew once, old rows stay empty there); a column the plan
dropped stays in the header. The ``csv`` module does all writing and reading (commas, quotes and line breaks in a
finding survive).

``last_entries`` gives the plan engine its history, ``trend_table`` the table the closing model gets: per position
the best value of this run, of the run before and of all runs. Every word of the table is a prompt
(prompts/<lang>/task_plan/trend_*.txt).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import file_manager
from .plan_engine import HistoryKey, PlanResult, value_text
from .plan_record import STATUS_FOUND, PlanEntry
from .prompt_loader import load_prompt
from .task_plan import TaskPlan

ARCHIVE_FILE = "plan_archiv.csv"
STATUS_NOT_CHECKED = "nicht_geprueft"
_FIXED_COLUMNS = ("zeitpunkt", "teil", "position", "status", "befund", "quelle")
_NUMBER_TYPE = "zahl"
ARROW_DOWN, ARROW_SAME, ARROW_UP = "↓", "→", "↑"


def archive_path(job_folder: str) -> Path:
    """The archive file of a job whose folder (relative to the workspace) ``job_notes.sync`` gave."""
    return file_manager.DOCUMENTS_DIR / job_folder / ARCHIVE_FILE


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """The header and the rows; empty when there is no archive yet."""
    if not path.is_file():
        return [], []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_all(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, restval="")
        writer.writeheader()
        writer.writerows(rows)


def _cell(value: object) -> str:
    return str(value)


def _row_dicts(result: PlanResult, when: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for outcome in result.outcomes:
        part_id = outcome.part.id
        for entry in outcome.records.entries:
            rows.append({
                "zeitpunkt": when, "teil": part_id, "position": entry.position_id, "status": entry.status,
                "befund": entry.befund, "quelle": entry.quelle,
                **{name: _cell(value) for name, value in entry.values.items()},
            })
        rows += [
            {"zeitpunkt": when, "teil": part_id, "position": position_id, "status": STATUS_NOT_CHECKED,
             "befund": outcome.failure or ""}
            for position_id in outcome.not_checked
        ]
    return rows


def append_run(path: Path, plan: TaskPlan, result: PlanResult, when: datetime) -> None:
    """Append the rows of a run; a column new in the plan first widens the header."""
    header, old_rows = _read(path)
    wanted = [*_FIXED_COLUMNS, *(column.name for column in plan.columns)]
    new_rows = _row_dicts(result, when.isoformat(timespec="seconds"))
    if not header:
        _write_all(path, wanted, new_rows)
        return
    widened = [*header, *(name for name in wanted if name not in header)]
    if widened != header:
        _write_all(path, widened, [*old_rows, *new_rows])
        return
    with path.open("a", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=header, restval="").writerows(new_rows)


def _number(cell: str) -> int | float:
    try:
        return int(cell)
    except ValueError:
        return float(cell)


def _entry(row: dict[str, str], plan: TaskPlan) -> PlanEntry:
    values = {
        column.name: _number(row[column.name]) if column.type == _NUMBER_TYPE else row[column.name]
        for column in plan.columns if row.get(column.name)
    }
    return PlanEntry(row["position"], row["status"], row["befund"], row["quelle"], values)


def last_entries(path: Path, plan: TaskPlan) -> dict[HistoryKey, list[PlanEntry]]:
    """Per position of the plan the entries of the last time it had any (``nicht_geprueft`` rows are no entries)."""
    _, rows = _read(path)
    wanted = {(part.id, position.id) for part in plan.parts for position in part.positions}
    latest: dict[HistoryKey, str] = {}
    for row in rows:
        key = (row["teil"], row["position"])
        if key in wanted and row["status"] != STATUS_NOT_CHECKED:
            latest[key] = max(latest.get(key, ""), row["zeitpunkt"])
    history: dict[HistoryKey, list[PlanEntry]] = {key: [] for key in latest}
    for row in rows:
        key = (row["teil"], row["position"])
        if key in latest and row["status"] != STATUS_NOT_CHECKED and row["zeitpunkt"] == latest[key]:
            history[key].append(_entry(row, plan))
    return history


@dataclass(frozen=True)
class _Best:
    value: int | float
    when: str
    source: str


def _best_per_moment(rows: list[dict[str, str]], part_id: str, position_id: str, trend_column: str) -> dict[str, _Best]:
    """The smallest trend value of the position's ``gefunden`` rows, per time of a run."""
    best: dict[str, _Best] = {}
    for row in rows:
        if (row["teil"], row["position"], row["status"]) != (part_id, position_id, STATUS_FOUND) or not row.get(trend_column):
            continue
        value = _number(row[trend_column])
        if row["zeitpunkt"] not in best or value < best[row["zeitpunkt"]].value:
            best[row["zeitpunkt"]] = _Best(value, row["zeitpunkt"], row["quelle"])
    return best


def _text(prompt: str, lang: str, **placeholders: str) -> str:
    return load_prompt(f"task_plan/{prompt}", lang=lang, **placeholders).strip()


def _table_cell(text: str) -> str:
    """Text for a Markdown table cell: no pipe, no line break."""
    return " ".join(text.replace("|", "\\|").split())


def _when_text(when: str) -> str:
    """A time of the archive for the table: date and minute (the archive keeps seconds so two runs stay apart)."""
    return when[:16].replace("T", " ")


def _arrow(current: _Best | None, previous: _Best | None, lang: str) -> str:
    if current is None:
        return _text("trend_none", lang)
    if previous is None:
        return _text("trend_new", lang)
    if current.value < previous.value:
        return ARROW_DOWN
    return ARROW_UP if current.value > previous.value else ARROW_SAME


def trend_table(path: Path, plan: TaskPlan, lang: str) -> str:
    """The table of the trend, Markdown, one row per position of the plan: the best value of the last run in the
    archive (smallest ``gefunden`` value of the plan's trend column) with its source, the best value of the run before
    that had one, the best value of all runs with its time and source, and the arrow of the last against the one before.
    Call it after ``append_run``. Empty when the plan has no trend column."""
    if plan.trend_value is None:
        return ""
    _, rows = _read(path)
    if not rows:
        return ""
    last_moment = max(row["zeitpunkt"] for row in rows)
    none = _text("trend_none", lang)
    header = _text("trend_header", lang)
    lines = [header, "|" + "---|" * (header.count("|") - 1)]
    for part in plan.parts:
        for position in part.positions:
            moments = _best_per_moment(rows, part.id, position.id, plan.trend_value)
            current = moments.get(last_moment)
            earlier = [best for when, best in moments.items() if when < last_moment]
            previous = max(earlier, key=lambda best: best.when) if earlier else None
            overall = min(moments.values(), key=lambda best: (best.value, best.when)) if moments else None
            lines.append("| " + " | ".join([
                _table_cell(position.name),
                _text("trend_value", lang, value=value_text(current.value, lang), source=_table_cell(current.source)) if current else none,
                value_text(previous.value, lang) if previous else none,
                _text("trend_best", lang, value=value_text(overall.value, lang), when=_when_text(overall.when),
                      source=_table_cell(overall.source)) if overall else none,
                _arrow(current, previous, lang),
            ]) + " |")
    return "\n".join(lines)
