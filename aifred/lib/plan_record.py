"""The recording tool ``plan_record`` of a plan part: the one way a part's sub-agent reports what it found.

The engine owns it (not the Horarium): the schema comes from the plan, the checks live here, the accepted entries
go to a ``PartRecords`` the engine reads afterwards. Every text the model sees is a prompt (prompts/<lang>/task_plan).

A ``found`` entry needs a source the sub-agent really read in this run: the toolkit collects the addresses the
reading tools report (``ToolKit.read_urls``), the tool compares the normalized source with them.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .function_calling import Tool
from .prompt_loader import load_prompt
from .security import TIER_READONLY
from .task_plan import EXTRA_POSITION_ID, PlanPart, TaskPlan
from .tools.url_utils import normalize_url

TOOL_NAME = "plan_record"
STATUS_FOUND = "gefunden"
STATUS_NOT_FOUND = "nicht_gefunden"
STATUS_ERROR = "fehler"
STATUSES = (STATUS_FOUND, STATUS_NOT_FOUND, STATUS_ERROR)

_NUMBER_TYPE = "zahl"
_FIELD_SCHEMA_TYPES = {"zahl": "number", "text": "string", "link": "string"}


@dataclass(frozen=True)
class PlanEntry:
    """One accepted entry; ``values`` holds the column values the entry gave."""

    position_id: str
    status: str
    befund: str
    quelle: str
    values: dict[str, Any]


@dataclass
class PartRecords:
    """What a part's sub-agent recorded: the accepted entries (several per position are allowed) and the sources it
    found besides the given ones."""

    entries: list[PlanEntry] = field(default_factory=list)
    new_sources: set[str] = field(default_factory=set)

    def open_positions(self, part: PlanPart) -> list[str]:
        """The ids of the part's positions without any entry."""
        recorded = {entry.position_id for entry in self.entries}
        return [position.id for position in part.positions if position.id not in recorded]


def _text(prompt: str, lang: str, **placeholders: str) -> str:
    """A prompt of the tool, without the file's closing line break."""
    return load_prompt(f"task_plan/{prompt}", lang=lang, **placeholders).strip()


def _field_description(field_key: str, lang: str, **placeholders: str) -> str:
    return _text(f"record_field_{field_key}", lang, **placeholders)


def record_schema(plan: TaskPlan, part: PlanPart, lang: str) -> dict[str, Any]:
    """The JSON schema of the tool's parameters, built from the part's positions and the plan's columns."""
    properties: dict[str, Any] = {
        "position_id": {
            "type": "string", "enum": [*(position.id for position in part.positions), EXTRA_POSITION_ID],
            "description": _field_description("position_id", lang, extra_position_id=EXTRA_POSITION_ID),
        },
        "status": {"type": "string", "enum": list(STATUSES), "description": _field_description("status", lang)},
        "befund": {"type": "string", "description": _field_description("befund", lang)},
        "quelle": {"type": "string", "description": _field_description("quelle", lang)},
    }
    for column in plan.columns:
        properties[column.name] = {
            "type": _FIELD_SCHEMA_TYPES[column.type],
            "description": _field_description(f"column_{column.type}", lang, name=column.name),
        }
    properties["neue_quellen"] = {
        "type": "array", "items": {"type": "string"}, "description": _field_description("neue_quellen", lang),
    }
    return {"type": "object", "properties": properties, "required": ["position_id", "status", "befund"]}


# A new source is entered in the job's notes, which every later run reads as its own memory: one line, one address.
_WEB_ADDRESS_FORBIDDEN = re.compile(r"\s")


def is_web_address(text: str) -> bool:
    """A single-line http(s) address (no whitespace inside, a host)."""
    parsed = urlparse(text)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and not _WEB_ADDRESS_FORBIDDEN.search(text)


# An address inside written text ends where Markdown or prose closes around it: a link "[x](…)", backticks, quotes.
_WEB_ADDRESS_IN_TEXT = re.compile(r"https?://[^\s<>()\[\]`'\"]+")


def web_addresses_in(text: str) -> list[str]:
    """The http(s) addresses a text names, also inside Markdown links and backticks."""
    return _WEB_ADDRESS_IN_TEXT.findall(text)


def make_record_tool(
    plan: TaskPlan, part: PlanPart, read_urls_source: Callable[[], set[str]], collector: PartRecords, lang: str,
) -> Tool:
    """The ``plan_record`` tool of one part.

    ``read_urls_source`` is called at every call of the tool and returns the addresses read so far. The sub-agent's
    toolkit does not exist yet when the tool is built (``SubagentSession.run`` builds it), so the caller passes a
    function that looks it up when needed: ``lambda: session.toolkit.read_urls``.
    """
    column_types = {column.name: column.type for column in plan.columns}
    allowed_positions = [position.id for position in part.positions] + [EXTRA_POSITION_ID]

    def reject(key: str, **placeholders: str) -> str:
        return json.dumps({"error": _text(f"record_error_{key}", lang, **placeholders)}, ensure_ascii=False)

    def value_problem(column: str, value: Any) -> str | None:
        """The rejection for a column value of the wrong type, None when it fits."""
        if column_types[column] == _NUMBER_TYPE:
            if isinstance(value, bool) or not isinstance(value, int | float):
                key = "number_text" if isinstance(value, str) else "type"
                return reject(key, column=column, value=json.dumps(value, ensure_ascii=False), expected=_NUMBER_TYPE)
            if not math.isfinite(value):
                return reject("not_finite", column=column, value=json.dumps(value))
        elif not isinstance(value, str):
            return reject("type", column=column, value=json.dumps(value, ensure_ascii=False),
                          expected=column_types[column])
        return None

    def entry_problem(position_id: str, status: str, befund: Any, quelle: Any, columns: dict[str, Any]) -> str | None:
        if position_id not in allowed_positions:
            return reject("position", position_id=position_id, allowed=", ".join(allowed_positions))
        if status not in STATUSES:
            return reject("status", status=status, allowed=", ".join(STATUSES))
        if not isinstance(befund, str):
            return reject("befund_missing")
        if status != STATUS_FOUND and not befund.strip():
            return reject("befund_empty", status=status)
        if not isinstance(quelle, str):
            return reject("type", column="quelle", value=json.dumps(quelle, ensure_ascii=False), expected="text")
        unknown = [name for name in columns if name not in column_types]
        if unknown:
            return reject("unknown_field", field=", ".join(unknown), allowed=", ".join(column_types) or "-")
        for column, value in columns.items():
            if (problem := value_problem(column, value)) is not None:
                return problem
        if status == STATUS_FOUND:
            if not quelle.strip():
                return reject("source_missing")
            if normalize_url(quelle) not in read_urls_source():
                return reject("source_unread", url=quelle)
        return None

    def record(
        position_id: str, status: str, befund: str, quelle: str = "", neue_quellen: list[str] | None = None,
        **columns: Any,
    ) -> str:
        new_sources = [] if neue_quellen is None else neue_quellen
        if not isinstance(new_sources, list) or not all(isinstance(source, str) for source in new_sources):
            return reject("type", column="neue_quellen", value=json.dumps(new_sources, ensure_ascii=False),
                          expected="list")
        new_sources = [source.strip() for source in new_sources]
        if (bad := next((source for source in new_sources if not is_web_address(source)), None)) is not None:
            return reject("new_source", source=json.dumps(bad, ensure_ascii=False))
        # A column the model left out stays out of the entry (null is the same as left out).
        given = {name: value for name, value in columns.items() if value is not None}
        if (problem := entry_problem(position_id, status, befund, quelle, given)) is not None:
            return problem
        entry = PlanEntry(position_id, status, befund, quelle, given)
        # A repeated call (same position, same fields) is the same finding: stored once, confirmed as the first time.
        if entry not in collector.entries:
            collector.entries.append(entry)
        collector.new_sources.update(new_sources)
        still_open = collector.open_positions(part)
        if still_open:
            return _text("record_accepted", lang, position_id=position_id, open=", ".join(still_open))
        return _text("record_accepted_complete", lang, position_id=position_id)

    positions = "\n".join(f"- {position.id}: {position.name}" for position in part.positions)
    return Tool(
        name=TOOL_NAME,
        description=_text("record_description", lang, part_title=part.title, positions=positions,
                          extra_position_id=EXTRA_POSITION_ID),
        parameters=record_schema(plan, part, lang),
        executor=record,
        tier=TIER_READONLY,
    )
