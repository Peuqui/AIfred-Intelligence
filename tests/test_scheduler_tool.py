"""Tests for scheduler tool plugin — create, list, delete jobs via LLM."""

import asyncio
import json

import pytest

from aifred.plugins.tools.scheduler_tool import SchedulerPlugin
from aifred.lib.plugin_base import PluginContext


@pytest.fixture
def plugin():
    return SchedulerPlugin()


@pytest.fixture
def ctx():
    return PluginContext(
        agent_id="aifred",
        lang="de",
        session_id="test_session",
        max_tier=4,
        source="browser",
    )


@pytest.fixture(autouse=True)
def temp_job_store(tmp_path):
    """Use a temp database for all tests."""
    import aifred.lib.scheduler as sched
    sched._job_store = sched.JobStore(tmp_path / "test_jobs.db")
    yield
    sched._job_store = None


def _run(coro):
    """Helper to run async tool executors."""
    return asyncio.new_event_loop().run_until_complete(coro)


class TestSchedulerPlugin:
    def test_name(self, plugin):
        # MUST equal the folder name (plugin_registry invariant) — a mismatch
        # hides the plugin from the Plugin-Manager UI.
        assert plugin.name == "scheduler_tool"
        assert plugin.display_name == "Scheduler"

    def test_is_available(self, plugin):
        assert plugin.is_available() is True

    def test_provides_three_tools(self, plugin, ctx):
        tools = plugin.get_tools(ctx)
        names = [t.name for t in tools]
        assert "scheduler_create" in names
        assert "scheduler_list" in names
        assert "scheduler_delete" in names

    def test_tool_tiers(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        from aifred.lib.security import TIER_WRITE_DATA, TIER_READONLY
        assert tools["scheduler_create"].tier == TIER_WRITE_DATA
        assert tools["scheduler_list"].tier == TIER_READONLY
        assert tools["scheduler_delete"].tier == TIER_WRITE_DATA


class TestSchedulerCreate:
    def test_create_interval_job(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        result = _run(tools["scheduler_create"].executor(
            name="test_job",
            schedule_type="interval",
            schedule_expr="3600",
            message="Check emails",
        ))
        data = json.loads(result)
        assert data["success"] is True
        assert data["job_id"] == 1
        assert data["name"] == "test_job"
        assert data["next_run"] != ""

    def test_create_cron_job(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        result = _run(tools["scheduler_create"].executor(
            name="morning_check",
            schedule_type="cron",
            schedule_expr="0 8 * * *",
            message="Summarize emails",
            delivery="announce",
            channel="telegram",
        ))
        data = json.loads(result)
        assert data["success"] is True
        assert data["delivery"] == "announce"

    def test_create_once_job(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        result = _run(tools["scheduler_create"].executor(
            name="reminder",
            schedule_type="once",
            schedule_expr="2026-04-01T10:00:00",
            message="Arzttermin!",
            delivery="review",
        ))
        data = json.loads(result)
        assert data["success"] is True
        assert data["schedule_type"] == "once"

    def test_create_invalid_type(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        result = _run(tools["scheduler_create"].executor(
            name="bad",
            schedule_type="invalid",
            schedule_expr="foo",
            message="test",
        ))
        data = json.loads(result)
        assert "error" in data


class TestSchedulerList:
    def test_list_empty(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        result = _run(tools["scheduler_list"].executor())
        data = json.loads(result)
        assert data["jobs"] == []

    def test_list_after_create(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        _run(tools["scheduler_create"].executor(
            name="job1", schedule_type="interval", schedule_expr="60", message="test1",
        ))
        _run(tools["scheduler_create"].executor(
            name="job2", schedule_type="cron", schedule_expr="0 8 * * *", message="test2",
        ))
        result = _run(tools["scheduler_list"].executor())
        data = json.loads(result)
        assert len(data["jobs"]) == 2
        names = [j["name"] for j in data["jobs"]]
        assert "job1" in names
        assert "job2" in names


class TestSchedulerDelete:
    def test_delete_existing(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        create_result = _run(tools["scheduler_create"].executor(
            name="to_delete", schedule_type="interval", schedule_expr="60", message="test",
        ))
        job_id = json.loads(create_result)["job_id"]

        result = _run(tools["scheduler_delete"].executor(job_id=job_id))
        data = json.loads(result)
        assert data["success"] is True
        assert data["name"] == "to_delete"

        # Verify deleted
        list_result = _run(tools["scheduler_list"].executor())
        assert len(json.loads(list_result)["jobs"]) == 0

    def test_delete_nonexistent(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        result = _run(tools["scheduler_delete"].executor(job_id=9999))
        data = json.loads(result)
        assert "error" in data


class TestPromptInstructions:
    def test_de_instructions(self, plugin):
        instr = plugin.get_prompt_instructions("de")
        assert "scheduler_create" in instr
        assert "cron" in instr

    def test_en_instructions(self, plugin):
        instr = plugin.get_prompt_instructions("en")
        assert "scheduler_create" in instr
        assert "cron" in instr

    def test_ui_status(self, plugin):
        assert "Creating" in plugin.get_ui_status("scheduler_create", {"name": "test"}, "en")
        assert "Deleting" in plugin.get_ui_status("scheduler_delete", {"job_id": 1}, "en")
        assert "Loading" in plugin.get_ui_status("scheduler_list", {}, "en")
