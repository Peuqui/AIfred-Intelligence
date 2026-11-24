"""The plan engine: runs the parts of a ``TaskPlan`` one after the other, one sub-agent each.

Every part gets its own ``SubagentSession`` with the ``plan_record`` tool; what the sub-agent reports goes through
that tool into ``PartRecords``. Positions without an entry after the run get exactly one reminder, then count as not
checked. A part that fails (exception) is a part failure with its reason; the next parts run on.

``run_plan`` yields ``progress``, ``work`` and ``artifacts`` like a tool executor (so one can pass them on) and
at last ``{"plan_result": PlanResult}``. ``render_results`` is the one place for the text a model gets,
``coverage_line`` the fixed line the code appends to a message. Every text is a prompt (prompts/<lang>/task_plan).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .formatting import format_number
from .logging_utils import log_exception
from .plan_record import PartRecords, PlanEntry, make_record_tool
from .plugin_base import PluginContext
from .prompt_loader import load_prompt
from .subagent_run import SubagentSession
from .task_plan import EXTRA_POSITION_ID, PlanPart, TaskPlan

# Key of the history mapping: (part id, position id).
HistoryKey = tuple[str, str]


@dataclass
class PartOutcome:
    part: PlanPart
    records: PartRecords
    report: str
    failure: str | None
    not_checked: list[str]


@dataclass
class PlanResult:
    plan: TaskPlan
    outcomes: list[PartOutcome]

    @property
    def new_sources(self) -> set[str]:
        return set().union(*(outcome.records.new_sources for outcome in self.outcomes))

    @property
    def position_count(self) -> int:
        return sum(len(outcome.part.positions) for outcome in self.outcomes)

    @property
    def checked_count(self) -> int:
        """Positions with an entry; extra finds do not count, positions of a failed part count when they have entries."""
        return self.position_count - sum(len(outcome.not_checked) for outcome in self.outcomes)


def _text(prompt: str, lang: str, **placeholders: str) -> str:
    return load_prompt(f"task_plan/{prompt}", lang=lang, **placeholders).strip()


def value_text(value: Any, lang: str) -> str:
    """A column value for a person or a model: numbers in the language's notation."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        exponent = Decimal(repr(value)).as_tuple().exponent
        return format_number(value, max(0, -exponent) if isinstance(exponent, int) else 0, locale=lang)
    return str(value)


def _entry_text(entry: PlanEntry, lang: str) -> str:
    """One entry as a list line: status, finding, source and column values."""
    details = [_text("render_detail_source", lang, source=entry.quelle)] if entry.quelle else []
    details += [f"{name}: {value_text(value, lang)}" for name, value in entry.values.items()]
    return _text("render_entry", lang, status=entry.status, befund=entry.befund,
                 details=f" ({'; '.join(details)})" if details else "")


def _history_text(part: PlanPart, history: Mapping[HistoryKey, Sequence[PlanEntry]], lang: str) -> str:
    blocks = []
    for position in part.positions:
        entries = history.get((part.id, position.id), ())
        if entries:
            blocks.append("\n".join([_text("part_history_position", lang, name=position.name, id=position.id),
                                     *(_entry_text(entry, lang) for entry in entries)]))
    return "\n".join(blocks) if blocks else _text("part_history_none", lang)


def part_task(plan: TaskPlan, part: PlanPart, history: Mapping[HistoryKey, Sequence[PlanEntry]], notes_hint: str,
              lang: str) -> str:
    columns = "\n".join(f"- {column.name} ({column.type})" for column in plan.columns)
    return _text(
        "part", lang, frame=plan.frame, part_title=part.title, task=part.task,
        positions="\n".join(f"- {position.id}: {position.name}" for position in part.positions),
        columns=columns or _text("part_columns_none", lang), history=_history_text(part, history, lang),
        notes_hint=notes_hint,
    )


async def _run_part(
    plan: TaskPlan, part: PlanPart, ctx: PluginContext, allowed_tiers: frozenset[int], depth: int, task: str,
    outcome: PartOutcome,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run one part into ``outcome`` (its records and report); yields the events to pass on."""

    def read_urls() -> set[str]:
        toolkit = session.toolkit
        assert toolkit is not None  # the tool runs in the sub-agent's loop, after run() built the toolkit
        return toolkit.read_urls

    session = SubagentSession(
        ctx, allowed_tiers=allowed_tiers, depth=depth, target_agent=ctx.agent_id, task=task,
        expected_result=_text("part_expected", ctx.lang), with_persona=False,
        extra_tools=[make_record_tool(plan, part, read_urls, outcome.records, ctx.lang)],
    )
    reports: list[str] = []

    async def pass_on(events: AsyncGenerator[dict[str, Any], None]) -> AsyncGenerator[dict[str, Any], None]:
        """The session's events for the caller; its ``result`` is a report of the part and stays in the outcome."""
        async for event in events:
            if "result" in event:
                reports.append(event["result"])
            elif "progress" in event:
                yield {"progress": f"{part.title}: {event['progress']}"}
            else:
                yield event

    async for event in pass_on(session.run()):
        yield event
    if open_positions := outcome.records.open_positions(part):
        async for event in pass_on(session.follow_up(_text("missing", ctx.lang, positions=", ".join(open_positions)))):
            yield event
    outcome.report = "\n\n".join(reports)


async def run_plan(
    plan: TaskPlan, ctx: PluginContext, *, allowed_tiers: frozenset[int], depth: int,
    history: Mapping[HistoryKey, Sequence[PlanEntry]] | None = None, notes_hint: str = "",
) -> AsyncGenerator[dict[str, Any], None]:
    """Run the plan's parts one after the other; ends with ``{"plan_result": PlanResult}``."""
    outcomes: list[PartOutcome] = []
    for part in plan.parts:
        outcome = PartOutcome(part=part, records=PartRecords(), report="", failure=None, not_checked=[])
        outcomes.append(outcome)
        task = part_task(plan, part, history or {}, notes_hint, ctx.lang)
        try:
            async for event in _run_part(plan, part, ctx, allowed_tiers, depth, task, outcome):
                yield event
        except Exception as exc:  # noqa: BLE001 — a failed part must not stop the others; reported in the result
            log_exception(f"Plan part '{part.id}' failed", exc)
            outcome.failure = str(exc) or type(exc).__name__
        outcome.not_checked = outcome.records.open_positions(part)
    yield {"plan_result": PlanResult(plan=plan, outcomes=outcomes)}


def _outcome_text(outcome: PartOutcome, lang: str) -> str:
    lines = [_text("render_part_head", lang, title=outcome.part.title)]
    if outcome.failure is not None:
        lines.append(_text("render_part_failed", lang, reason=outcome.failure))
    for position in outcome.part.positions:
        lines.append(_text("render_position", lang, name=position.name, id=position.id))
        entries = [entry for entry in outcome.records.entries if entry.position_id == position.id]
        lines += [_entry_text(entry, lang) for entry in entries]
        if position.id in outcome.not_checked:
            lines.append(_text("render_not_checked", lang))
    extras = [entry for entry in outcome.records.entries if entry.position_id == EXTRA_POSITION_ID]
    if extras:
        lines += [_text("render_extra_heading", lang), *(_entry_text(entry, lang) for entry in extras)]
    if outcome.report:
        lines += [_text("render_report_heading", lang), outcome.report]
    return "\n".join(lines)


def render_results(result: PlanResult, lang: str) -> str:
    """The text a model gets: every part with its entries, what was not checked, extra finds and reports, then the
    new sources."""
    blocks = [_outcome_text(outcome, lang) for outcome in result.outcomes]
    if result.new_sources:
        blocks.append("\n".join([_text("render_new_sources", lang), *(f"- {source}" for source in sorted(result.new_sources))]))
    return "\n\n".join(blocks)


def coverage_line(result: PlanResult, lang: str) -> str:
    """The fixed line for the message: how many positions were checked, and if not all, which not and which parts failed."""
    total, checked = result.position_count, result.checked_count
    lines = [_text("coverage", lang, checked=format_number(checked, locale=lang), total=format_number(total, locale=lang))]
    if checked < total:
        missing = [
            f"{outcome.part.title} / {position.name}"
            for outcome in result.outcomes for position in outcome.part.positions if position.id in outcome.not_checked
        ]
        lines.append(_text("coverage_missing", lang, positions=", ".join(missing)))
    failed = [f"{outcome.part.title} ({outcome.failure})" for outcome in result.outcomes if outcome.failure is not None]
    if failed:
        lines.append(_text("coverage_failed", lang, parts=", ".join(failed)))
    return "\n".join(lines)
