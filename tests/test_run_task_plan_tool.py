"""run_task_plan: the chat tool of the sub-agent plugin that runs a plan through the engine."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

import aifred.lib.plan_engine as engine
import aifred.lib.subagent_run as run
import aifred.plugins.tools.subagent as sub
from aifred.lib.plan_engine import PartDone, PartOutcome, PlanResult, coverage_line, render_results
from aifred.lib.plan_record import PartRecords, PlanEntry
from aifred.lib.plugin_base import PluginContext
from aifred.lib.security import TIER_READONLY, TIER_WRITE_DATA
from aifred.lib.task_plan import TaskPlan

PLAN: dict[str, Any] = {
    "frame": "Preise beobachten",
    "parts": [{"id": "t1", "title": "Teil 1", "task": "Suche", "positions": [{"id": "a", "name": "Alpha"}]}],
    "columns": [{"name": "preis", "type": "zahl"}],
}


@pytest.fixture
def plugin(monkeypatch):
    instance = sub.SubAgentPlugin()
    monkeypatch.setattr(instance, "_load_settings", dict)
    return instance


@pytest.fixture
def ctx():
    return PluginContext(agent_id="aifred", lang="de", session_id="s", max_tier=TIER_WRITE_DATA, source="browser")


def _tool(plugin, ctx):
    return next(tool for tool in plugin.get_tools(ctx) if tool.name == sub.PLAN_TOOL_NAME)


async def _collect(agen):
    return [item async for item in agen]


def _result_for(plan: TaskPlan) -> PlanResult:
    part = plan.parts[0]
    records = PartRecords(entries=[PlanEntry("a", "gefunden", "12,5 Euro", "https://x.example", {"preis": 12.5})])
    return PlanResult(plan=plan, outcomes=[PartOutcome(part, records, "Bericht", None, [])])


class TestTool:
    def test_the_tool_is_readonly_next_to_delegate_task(self, plugin, ctx):
        tools = {tool.name: tool for tool in plugin.get_tools(ctx)}
        assert set(tools) == {sub.TOOL_NAME, sub.PLAN_TOOL_NAME}
        assert tools[sub.PLAN_TOOL_NAME].tier == TIER_READONLY

    def test_the_schema_is_the_shared_plan_schema(self, plugin, ctx):
        parameters = _tool(plugin, ctx).parameters
        assert set(parameters["properties"]) == {"frame", "parts", "columns"}
        assert parameters["required"] == ["frame", "parts"]
        column_type = parameters["properties"]["columns"]["items"]["properties"]["type"]
        assert column_type["enum"] == ["text", "zahl", "link"]
        assert parameters["properties"]["parts"]["items"]["required"] == ["id", "title", "task", "positions"]

    def test_neither_tool_at_max_depth(self, plugin, ctx):
        ctx.metadata[run.DEPTH_METADATA_KEY] = 1
        assert plugin.get_tools(ctx) == []

    def test_both_tools_below_max_depth(self, plugin, ctx, monkeypatch):
        monkeypatch.setattr(plugin, "_load_settings", lambda: {sub.MAX_DEPTH_KEY: "2"})
        ctx.metadata[run.DEPTH_METADATA_KEY] = 1
        assert {tool.name for tool in plugin.get_tools(ctx)} == {sub.TOOL_NAME, sub.PLAN_TOOL_NAME}

    def test_ui_status(self, plugin):
        assert "Preise" in plugin.get_ui_status(sub.PLAN_TOOL_NAME, {"frame": "Preise\nbeobachten"}, "de")
        assert plugin.get_ui_status(sub.PLAN_TOOL_NAME, {"frame": "x"}, "de") != plugin.get_ui_status(
            sub.PLAN_TOOL_NAME, {"frame": "x"}, "en")


class TestExecutor:
    def test_an_invalid_plan_is_an_error_result_and_nothing_runs(self, plugin, ctx, monkeypatch):
        called: list[Any] = []

        async def fake_run_plan(*args: Any, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
            called.append(args)
            yield {}

        monkeypatch.setattr(engine, "run_plan", fake_run_plan)
        bad = {**PLAN, "columns": [{"name": "preis", "type": "datum"}]}
        events = asyncio.run(_collect(_tool(plugin, ctx).executor(**bad)))
        assert len(events) == 1 and "error" in json.loads(events[0]["result"])
        assert not called

    def test_a_plan_without_parts_is_refused(self, plugin, ctx):
        events = asyncio.run(_collect(_tool(plugin, ctx).executor(frame="Rahmen", parts=[])))
        assert "error" in json.loads(events[0]["result"])

    def test_events_pass_on_and_the_result_is_text_and_coverage(self, plugin, ctx, monkeypatch):
        seen: dict[str, Any] = {}

        async def fake_run_plan(plan: TaskPlan, plan_ctx: PluginContext, *, allowed_tiers: frozenset[int], depth: int,
                                **more: Any) -> AsyncGenerator[dict[str, Any], None]:
            seen.update(plan=plan, ctx=plan_ctx, tiers=allowed_tiers, depth=depth, more=more)
            yield {"progress": "Teil 1: läuft"}
            yield {"work": "arbeit"}
            yield {"artifacts": [{"kind": "x"}]}
            yield {"part_done": PartDone(_result_for(plan).outcomes[0], [{"kind": "x"}], 1.0)}   # not for the chat tool
            yield {"plan_result": _result_for(plan)}

        monkeypatch.setattr(engine, "run_plan", fake_run_plan)
        events = asyncio.run(_collect(_tool(plugin, ctx).executor(**PLAN)))

        assert events[:3] == [{"progress": "Teil 1: läuft"}, {"work": "arbeit"}, {"artifacts": [{"kind": "x"}]}]
        result = _result_for(TaskPlan.from_payload(PLAN))
        assert events[3] == {"result": f"{coverage_line(result, 'de')}\n\n{render_results(result, 'de')}"}
        assert len(events) == 4
        assert seen["plan"] == TaskPlan.from_payload(PLAN)
        assert seen["ctx"] is ctx
        assert seen["tiers"] == plugin.settings().allowed_tiers
        assert seen["depth"] == 0
        assert not seen["more"]

    def test_the_coverage_line_leads_so_that_capping_the_tail_cannot_cut_it(self, plugin, ctx, monkeypatch):
        from aifred.lib.tool_output_cap import cap_tool_output

        async def fake_run_plan(plan: TaskPlan, plan_ctx: PluginContext, **more: Any) -> AsyncGenerator[dict[str, Any], None]:
            yield {"plan_result": _result_for(plan)}

        monkeypatch.setattr(engine, "run_plan", fake_run_plan)
        events = asyncio.run(_collect(_tool(plugin, ctx).executor(**PLAN)))
        result = _result_for(TaskPlan.from_payload(PLAN))
        text = events[0]["result"]
        assert text.startswith(coverage_line(result, "de"))
        padded = text + "\n" + "x" * 20000
        assert coverage_line(result, "de") in cap_tool_output(padded, 200)

    def test_the_depth_of_the_context_goes_to_the_engine(self, plugin, ctx, monkeypatch):
        monkeypatch.setattr(plugin, "_load_settings", lambda: {sub.MAX_DEPTH_KEY: "2"})
        ctx.metadata[run.DEPTH_METADATA_KEY] = 1
        depths: list[int] = []

        async def fake_run_plan(plan: TaskPlan, plan_ctx: PluginContext, *, depth: int,
                                **more: Any) -> AsyncGenerator[dict[str, Any], None]:
            depths.append(depth)
            yield {"plan_result": _result_for(plan)}

        monkeypatch.setattr(engine, "run_plan", fake_run_plan)
        asyncio.run(_collect(_tool(plugin, ctx).executor(**PLAN)))
        assert depths == [1]
