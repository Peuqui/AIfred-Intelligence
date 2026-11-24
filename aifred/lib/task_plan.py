"""The plan of a split task: a frame, parts with their positions, and the columns of the archive.

One structure for the Horarium job payload and the chat tool (``frame``, ``parts``, ``columns``, ``trend``).
``plan_problem`` is the one check; ``job_problem`` (scheduler) and the tools use it, none words the rules a second time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .user_error import Invalid

COLUMN_TYPES = ("text", "zahl", "link")
TREND_TYPE = "zahl"
# Fields every entry carries itself; a job column of the same name would collide with them.
MANDATORY_FIELDS = ("position_id", "status", "befund", "quelle", "neue_quellen")
# Position id of the entry for something found besides the listed positions.
EXTRA_POSITION_ID = "zusatz"
# Payload keys that make a payload a plan.
PLAN_KEYS = ("frame", "parts", "columns", "trend")


@dataclass(frozen=True)
class PlanPosition:
    id: str
    name: str


@dataclass(frozen=True)
class PlanPart:
    id: str
    title: str
    task: str
    positions: list[PlanPosition]


@dataclass(frozen=True)
class PlanColumn:
    name: str
    type: str


@dataclass(frozen=True)
class TaskPlan:
    frame: str = ""
    parts: list[PlanPart] = field(default_factory=list)
    columns: list[PlanColumn] = field(default_factory=list)
    trend_value: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TaskPlan:
        """The plan of a payload; expects a payload ``plan_problem`` accepted."""
        trend = payload.get("trend")
        return cls(
            frame=payload.get("frame", ""),
            parts=[
                PlanPart(
                    id=part["id"], title=part["title"], task=part["task"],
                    positions=[PlanPosition(id=pos["id"], name=pos["name"]) for pos in part["positions"]],
                )
                for part in payload.get("parts", [])
            ],
            columns=[PlanColumn(name=column["name"], type=column["type"]) for column in payload.get("columns", [])],
            trend_value=trend["value"] if trend is not None else None,
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "frame": self.frame,
            "parts": [
                {
                    "id": part.id, "title": part.title, "task": part.task,
                    "positions": [{"id": pos.id, "name": pos.name} for pos in part.positions],
                }
                for part in self.parts
            ],
            "columns": [{"name": column.name, "type": column.type} for column in self.columns],
        }
        if self.trend_value is not None:
            payload["trend"] = {"value": self.trend_value}
        return payload


def _malformed(where: str) -> Invalid:
    return Invalid("plan_malformed", where=where)


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _shape_problem(payload: dict[str, Any]) -> Invalid | None:
    """Whether the payload has the form ``from_payload`` reads: lists of objects with text fields."""
    if not isinstance(payload.get("frame", ""), str):
        return _malformed("frame")
    parts, columns, trend = payload.get("parts", []), payload.get("columns", []), payload.get("trend")
    if not isinstance(parts, list):
        return _malformed("parts")
    for part in parts:
        if not isinstance(part, dict) or not all(_is_text(part.get(key)) for key in ("id", "title", "task")):
            return _malformed("parts (id, title, task)")
        positions = part.get("positions")
        if not isinstance(positions, list):
            return _malformed(f"parts[{part['id']}].positions")
        if not all(isinstance(pos, dict) and _is_text(pos.get("id")) and _is_text(pos.get("name")) for pos in positions):
            return _malformed(f"parts[{part['id']}].positions (id, name)")
    if not isinstance(columns, list):
        return _malformed("columns")
    if not all(isinstance(column, dict) and _is_text(column.get("name")) and isinstance(column.get("type"), str)
               for column in columns):
        return _malformed("columns (name, type)")
    if trend is not None and not (isinstance(trend, dict) and _is_text(trend.get("value"))):
        return _malformed("trend (value)")
    return None


def _duplicate(names: list[str]) -> str | None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            return name
        seen.add(name)
    return None


def plan_problem(payload: dict[str, Any]) -> Invalid | None:
    """What is wrong with the plan of a payload, None when nothing."""
    if (problem := _shape_problem(payload)) is not None:
        return problem
    plan = TaskPlan.from_payload(payload)
    if plan.parts and payload.get("fixed_text"):
        return Invalid("plan_parts_with_fixed_text")
    if (name := _duplicate([part.id for part in plan.parts])) is not None:
        return Invalid("plan_duplicate_part", part=name)
    for part in plan.parts:
        if not part.positions:
            return Invalid("plan_part_without_positions", part=part.id)
        if (name := _duplicate([pos.id for pos in part.positions])) is not None:
            return Invalid("plan_duplicate_position", part=part.id, position=name)
        if any(pos.id == EXTRA_POSITION_ID for pos in part.positions):
            return Invalid("plan_reserved_position", part=part.id, position=EXTRA_POSITION_ID)
    if (name := _duplicate([column.name for column in plan.columns])) is not None:
        return Invalid("plan_duplicate_column", column=name)
    for column in plan.columns:
        if column.name in MANDATORY_FIELDS:
            return Invalid("plan_reserved_column", column=column.name, reserved=", ".join(MANDATORY_FIELDS))
        if column.type not in COLUMN_TYPES:
            return Invalid("plan_unknown_column_type", column=column.name, value=column.type,
                           allowed=", ".join(COLUMN_TYPES))
    if plan.trend_value is not None and not any(
        column.name == plan.trend_value and column.type == TREND_TYPE for column in plan.columns
    ):
        return Invalid("plan_trend_not_a_number_column", value=plan.trend_value, type=TREND_TYPE)
    return None
