"""plan_engine: parts one after the other, the reminder, part failures, passing events on, and the rendered text."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator, Callable
from types import SimpleNamespace
from typing import Any

import pytest

import aifred.lib.subagent_run as run
from aifred.backends.base import LLMOptions
from aifred.lib.function_calling import Tool, ToolKit
from aifred.lib.plan_engine import PartOutcome, PlanResult, coverage_line, render_results, run_plan
from aifred.lib.plan_record import PartRecords, PlanEntry
from aifred.lib.plugin_base import PluginContext
from aifred.lib.security import TIER_READONLY, TIER_WRITE_DATA
from aifred.lib.task_plan import PlanPart, PlanPosition, TaskPlan

PAGE = "https://shop.example/seite"
Action = tuple[str, dict[str, Any]]
Script = Callable[[str, bool], list[Action]]


def _plan(parts: int = 3) -> TaskPlan:
    return TaskPlan.from_payload({
        "frame": "Preise beobachten",
        "parts": [{"id": f"t{n}", "title": f"Teil {n}", "task": f"Suche {n}",
                   "positions": [{"id": f"p{n}a", "name": f"Name {n}a"}, {"id": f"p{n}b", "name": f"Name {n}b"}]}
                  for n in range(1, parts + 1)],
        "columns": [{"name": "preis", "type": "zahl"}, {"name": "seite", "type": "link"}],
    })


def _read() -> Action:
    return "web_fetch", {"url": PAGE}


def _record(position_id: str, status: str = "gefunden", **more: Any) -> Action:
    arguments = {"position_id": position_id, "status": status, "befund": f"Befund {position_id}", **more}
    if status == "gefunden":
        arguments.setdefault("quelle", PAGE)
        arguments.setdefault("preis", 12.5)
    return "plan_record", arguments


def _complete(part_title: str, is_follow_up: bool) -> list[Action]:
    n = part_title.split()[-1]
    return [_read(), _record(f"p{n}a"), _record(f"p{n}b")]


async def _web_fetch(url: str) -> AsyncGenerator[dict[str, Any], None]:
    yield {"read": [url]}
    yield {"result": "Seiteninhalt"}


def _tool(name: str, executor: Callable[..., Any], tier: int = TIER_READONLY) -> Tool:
    return Tool(name=name, description=name, parameters={"type": "object"}, executor=executor, tier=tier)


async def _collect(agen: AsyncGenerator[dict[str, Any], None]) -> list[dict[str, Any]]:
    return [item async for item in agen]


def _drive(plan: TaskPlan, ctx: PluginContext, **kwargs: Any) -> list[dict[str, Any]]:
    return asyncio.run(_collect(run_plan(plan, ctx, allowed_tiers=frozenset({TIER_READONLY}), depth=0, **kwargs)))


def _result(events: list[dict[str, Any]]) -> PlanResult:
    results = [event["plan_result"] for event in events if "plan_result" in event]
    assert len(results) == 1 and "plan_result" in events[-1]
    assert isinstance(results[0], PlanResult)
    return results[0]


class Model:
    """The stand-in model: each call runs the script's actions through the toolkit, like the tool loop would."""

    def __init__(self) -> None:
        self.script: Script = _complete
        self.calls: list[dict[str, Any]] = []
        self.tool_results: list[str] = []

    async def stream(self, client: Any, model: str, messages: list[dict[str, Any]], options: Any, label: str, *,
                     source: str, toolkit: ToolKit | None = None,
                     compression: Any = None) -> AsyncGenerator[dict[str, Any], None]:
        assert toolkit is not None
        self.calls.append({"messages": list(messages), "toolkit": toolkit})
        title = re.search(r"TEIL: (.+)", messages[1]["content"])
        assert title is not None
        is_follow_up = len(messages) > 2
        index = len(self.calls)
        for name, arguments in self.script(title.group(1), is_follow_up):
            yield {"type": "tool_call", "name": name, "arguments": json.dumps(arguments)}
            async for item in toolkit.execute_streaming(name, arguments):
                if item.get("type") == "tool_result":
                    self.tool_results.append(item["result"])
        text = f"Bericht {index}"
        yield {"type": "content", "text": text}
        yield {"type": "pipeline_result", "result": SimpleNamespace(
            text_clean=text, artifacts=[], debug_msg="done",
            metrics={"work": {"prefill_tokens": 1, "prefill_s": 1.0, "decode_tokens": 1, "decode_s": 1.0}},
            metadata_dict={"ttft": 1.0, "tokens_per_sec": 1.0, "inference_time": 1.0, "source": "x",
                           "backend_type": "llamacpp"},
        )}


@pytest.fixture
def ctx() -> PluginContext:
    return PluginContext(agent_id="aifred", lang="de", session_id="s", max_tier=TIER_WRITE_DATA, source="browser")


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Model:
    fake = Model()

    async def prepare(agent_id: str, user_query: str, **kwargs: Any) -> tuple[str, ToolKit]:
        return "", ToolKit(tools=[_tool("web_fetch", _web_fetch)])

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr(run, "prepare_agent_toolkit", prepare)
    monkeypatch.setattr(run, "resolve_run_params", lambda agent_id: run.RunParams("llamacpp", "http://x", "m", 8192, 0.5))
    monkeypatch.setattr(run, "build_llm_options", lambda state, agent, model, t, n: LLMOptions(temperature=t, num_ctx=n))
    monkeypatch.setattr(run, "LLMClient", FakeClient)
    monkeypatch.setattr(run, "run_llm_stream", fake.stream)
    return fake


def test_a_three_parts_all_recorded_need_no_reminder(ctx: PluginContext, model: Model) -> None:
    result = _result(_drive(_plan(), ctx))
    assert len(model.calls) == 3 and all(len(call["messages"]) == 2 for call in model.calls)
    assert [outcome.part.id for outcome in result.outcomes] == ["t1", "t2", "t3"]
    assert all(outcome.failure is None and outcome.not_checked == [] for outcome in result.outcomes)
    assert result.checked_count == result.position_count == 6
    assert coverage_line(result, "de") == "6 von 6 Positionen geprüft"
    assert result.outcomes[0].report == "Bericht 1"


def test_b_an_open_position_gets_exactly_one_reminder(ctx: PluginContext, model: Model) -> None:
    def script(title: str, is_follow_up: bool) -> list[Action]:
        if title == "Teil 2":
            return [_read(), _record("p2b" if is_follow_up else "p2a")]
        return _complete(title, is_follow_up)

    model.script = script
    result = _result(_drive(_plan(), ctx))
    assert len(model.calls) == 4
    reminder = model.calls[2]["messages"]
    assert [m["role"] for m in reminder] == ["system", "user", "assistant", "user"]
    assert "Noch ohne Eintrag: p2b" in reminder[3]["content"]
    assert result.outcomes[1].not_checked == [] and result.checked_count == 6
    assert result.outcomes[1].report == "Bericht 2\n\nBericht 3"


def test_c_a_position_that_stays_open_is_not_checked(ctx: PluginContext, model: Model) -> None:
    model.script = lambda title, is_follow_up: [_read(), _record("p1a")] if title == "Teil 1" else _complete(title, is_follow_up)
    result = _result(_drive(_plan(2), ctx))
    assert len(model.calls) == 3  # one reminder only, the second run does not get another
    assert result.outcomes[0].not_checked == ["p1b"]
    assert (result.checked_count, result.position_count) == (3, 4)
    text = render_results(result, "de")
    assert "### Name 1b (p1b)\n- nicht geprüft" in text
    assert coverage_line(result, "de") == "3 von 4 Positionen geprüft\nNicht geprüft: Teil 1 / Name 1b"


def test_d_a_failing_part_is_a_part_failure_and_the_next_parts_run(ctx: PluginContext, model: Model) -> None:
    def script(title: str, is_follow_up: bool) -> list[Action]:
        if title == "Teil 2":
            raise RuntimeError("Modell weg")
        return _complete(title, is_follow_up)

    model.script = script
    result = _result(_drive(_plan(), ctx))
    failed = result.outcomes[1]
    assert failed.failure == "Modell weg" and failed.not_checked == ["p2a", "p2b"]
    assert result.outcomes[2].failure is None and len(result.outcomes[2].records.entries) == 2
    assert (result.checked_count, result.position_count) == (4, 6)
    assert "Teil fehlgeschlagen: Modell weg" in render_results(result, "de")
    assert coverage_line(result, "de").splitlines() == [
        "4 von 6 Positionen geprüft", "Nicht geprüft: Teil 2 / Name 2a, Teil 2 / Name 2b",
        "Teile fehlgeschlagen: Teil 2 (Modell weg)",
    ]


def test_d_a_failing_part_keeps_the_entries_it_made(ctx: PluginContext, model: Model) -> None:
    def script(title: str, is_follow_up: bool) -> list[Action]:
        if is_follow_up:
            raise RuntimeError("zweiter Anlauf kaputt")
        return [_read(), _record("p1a")]

    model.script = script
    result = _result(_drive(_plan(1), ctx))
    assert result.outcomes[0].failure == "zweiter Anlauf kaputt"
    assert result.outcomes[0].not_checked == ["p1b"] and result.checked_count == 1


def test_d_a_part_that_cannot_be_built_fails_too(ctx: PluginContext, model: Model, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(agent_id: str, user_query: str, **kwargs: Any) -> tuple[str, ToolKit]:
        raise OSError("kein Werkzeug")

    monkeypatch.setattr(run, "prepare_agent_toolkit", broken)
    result = _result(_drive(_plan(2), ctx))
    assert [outcome.failure for outcome in result.outcomes] == ["kein Werkzeug", "kein Werkzeug"]


def test_e_found_without_a_fetch_is_rejected_and_does_not_count(ctx: PluginContext, model: Model) -> None:
    model.script = lambda title, is_follow_up: [_record("p1a")] if is_follow_up else [_record("p1a"), _record("p1b", "nicht_gefunden")]
    result = _result(_drive(_plan(1), ctx))
    assert len(model.calls) == 2  # p1a is still open: the reminder
    assert any("error" in json.loads(text) for text in model.tool_results if text.startswith("{"))
    outcome = result.outcomes[0]
    assert [entry.position_id for entry in outcome.records.entries] == ["p1b"]
    assert outcome.not_checked == ["p1a"] and result.checked_count == 1


def test_f_the_part_prompt_has_frame_task_positions_columns_and_notes_hint_but_no_history(ctx: PluginContext, model: Model) -> None:
    _drive(_plan(2), ctx, notes_hint="Notizen: Ordner xyz")
    first = model.calls[0]["messages"][1]["content"]
    assert "Notizen: Ordner xyz" in first and "- preis (zahl)" in first and "- p1a: Name 1a" in first
    assert "Preise beobachten" in first and "Suche 1" in first
    assert "VORGESCHICHTE" not in first and "Vormals" not in first


def test_g_progress_work_and_artifacts_are_passed_on_but_not_the_session_result(ctx: PluginContext, model: Model) -> None:
    events = _drive(_plan(2), ctx)
    kinds = [next(iter(event)) for event in events]
    assert "result" not in kinds
    assert kinds.count("work") == 2 and kinds.count("artifacts") == 2 and kinds[-1] == "plan_result"
    progress = [event["progress"] for event in events if "progress" in event]
    assert progress and all(line.startswith(("Teil 1: ", "Teil 2: ")) for line in progress)
    assert any(line.endswith("plan_record") for line in progress)


def test_h_render_results_formats_numbers_extras_and_new_sources() -> None:
    plan = _plan(1)
    part = plan.parts[0]
    records = PartRecords(
        entries=[
            PlanEntry("p1a", "gefunden", "Preis gefunden", PAGE, {"preis": 1234.5, "seite": PAGE}),
            PlanEntry("p1b", "gefunden", "Zwei Preise", PAGE, {"preis": 7}),
            PlanEntry("p1b", "nicht_gefunden", "Zweiter Shop ohne Treffer", "", {}),
            PlanEntry("zusatz", "gefunden", "Anderes Gerät", PAGE, {"preis": 0.25}),
        ],
        new_sources={"https://neu.example/b", "https://neu.example/a"},
    )
    result = PlanResult(plan, [PartOutcome(part, records, "Alles gut.", None, [])])
    de = render_results(result, "de")
    assert "preis: 1.234,5" in de and "preis: 7" in de and "preis: 0,25" in de
    assert "### Zusatzfunde\n- gefunden: Anderes Gerät" in de
    assert "### Teilbericht\nAlles gut." in de
    assert de.endswith("## Neue Quellen\n- https://neu.example/a\n- https://neu.example/b")
    assert "- nicht_gefunden: Zweiter Shop ohne Treffer\n" in de + "\n"
    en = render_results(result, "en")
    assert "preis: 1,234.5" in en and "### Extra finds" in en and "## New sources" in en
    assert coverage_line(result, "en") == "2 of 2 positions checked"
    assert result.new_sources == records.new_sources


def test_i_a_cancellation_is_not_swallowed(ctx: PluginContext, model: Model) -> None:
    def script(title: str, is_follow_up: bool) -> list[Action]:
        raise asyncio.CancelledError

    model.script = script
    with pytest.raises(asyncio.CancelledError):
        _drive(_plan(2), ctx)
    assert len(model.calls) == 1


def test_counts_ignore_extra_finds() -> None:
    part = PlanPart("t", "T", "x", [PlanPosition("p1", "Eins")])
    records = PartRecords(entries=[PlanEntry("zusatz", "gefunden", "x", PAGE, {})])
    result = PlanResult(TaskPlan(parts=[part]), [PartOutcome(part, records, "", None, ["p1"])])
    assert (result.checked_count, result.position_count) == (0, 1)
