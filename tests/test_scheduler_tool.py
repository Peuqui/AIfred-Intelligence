"""Tests for scheduler tool plugin — create, list, update, delete jobs via LLM."""

import asyncio
import json
from typing import Any

import pytest

from aifred.lib.i18n import t
from aifred.lib.plugin_base import PluginContext, plugin_display_name
from aifred.lib.scheduler import get_job_store
from aifred.plugins.tools.scheduler_tool import SchedulerPlugin

# The tool answers the model, which reads English.
NEEDS_A_CHANNEL = t("error_fixed_text_needs_announce", lang="en")


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
def temp_job_store(tmp_path, monkeypatch):
    """Use a temp database and a temp workspace for all tests."""
    import aifred.lib.scheduler as sched
    monkeypatch.setattr("aifred.lib.file_manager.DOCUMENTS_DIR", tmp_path / "documents")
    sched._job_store = sched.JobStore(tmp_path / "test_jobs.db")
    yield
    sched._job_store = None


def _run(coro):
    """Helper to run async tool executors."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _stored_payload(job_id: int) -> dict[str, Any]:
    job = get_job_store().get(job_id)
    assert job is not None, f"job {job_id} is not in the store"
    return job.payload


class TestSchedulerPlugin:
    def test_name(self, plugin):
        # MUST equal the folder name (plugin_registry invariant) — a mismatch
        # hides the plugin from the Plugin-Manager UI.
        assert plugin.name == "scheduler_tool"
        assert plugin_display_name(plugin, "de") == "Scheduler"

    def test_is_available(self, plugin):
        assert plugin.is_available() is True

    def test_provides_four_tools(self, plugin, ctx):
        tools = plugin.get_tools(ctx)
        names = [t.name for t in tools]
        assert "scheduler_create" in names
        assert "scheduler_list" in names
        assert "scheduler_update" in names
        assert "scheduler_delete" in names

    def test_tool_tiers(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        from aifred.lib.security import TIER_READONLY, TIER_WRITE_DATA
        assert tools["scheduler_create"].tier == TIER_WRITE_DATA
        assert tools["scheduler_list"].tier == TIER_READONLY
        assert tools["scheduler_update"].tier == TIER_WRITE_DATA
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


class TestJobTier:
    """A job made in chat gets its creator's tier (at most the job default), never more than the creator has."""

    @staticmethod
    def _tools(plugin, max_tier):
        ctx = PluginContext(agent_id="aifred", lang="de", session_id="s", max_tier=max_tier, source="telegram")
        return {t.name: t for t in plugin.get_tools(ctx)}

    @staticmethod
    def _create(tools, **extra):
        return json.loads(_run(tools["scheduler_create"].executor(
            name="j", schedule_type="interval", schedule_expr="3600", message="x", **extra,
        )))

    def _tier(self, data):
        job = get_job_store().get(data["job_id"])
        assert job is not None
        return job.max_tier

    def test_an_owner_job_may_write_data(self, plugin):
        assert self._tier(self._create(self._tools(plugin, 2))) == 2

    def test_a_browser_job_is_capped_at_the_job_default(self, plugin):
        assert self._tier(self._create(self._tools(plugin, 4))) == 2

    def test_a_communicate_only_creator_gets_communicate(self, plugin):
        assert self._tier(self._create(self._tools(plugin, 1))) == 1

    def test_a_job_can_be_set_lower(self, plugin):
        assert self._tier(self._create(self._tools(plugin, 4), max_tier=0)) == 0

    def test_a_creator_cannot_grant_more_than_they_have(self, plugin):
        assert "error" in self._create(self._tools(plugin, 1), max_tier=2)

    def test_update_keeps_the_tier_unless_asked(self, plugin):
        tools = self._tools(plugin, 4)
        job_id = self._create(tools, max_tier=1)["job_id"]
        _run(tools["scheduler_update"].executor(job_id=job_id, name="renamed"))
        assert self._tier({"job_id": job_id}) == 1
        _run(tools["scheduler_update"].executor(job_id=job_id, max_tier=2))
        assert self._tier({"job_id": job_id}) == 2


class TestFixedText:
    def _create(self, plugin, ctx, **arguments):
        tool = next(t for t in plugin.get_tools(ctx) if t.name == "scheduler_create")
        return json.loads(_run(tool.executor(
            name="Wecker", schedule_type="cron", schedule_expr="0 7 * * *", message="Guten Morgen", **arguments,
        )))

    def test_a_fixed_text_on_the_pucks(self, plugin, ctx):
        data = self._create(plugin, ctx, delivery="announce", channel="freeecho2", recipient="*",
                            fixed_text=True, audio_type="alarm")
        payload = _stored_payload(data["job_id"])
        assert payload["fixed_text"] is True and payload["audio_type"] == "alarm" and payload["recipient"] == "*"

    def test_models_may_send_the_word_true(self, plugin, ctx):
        assert self._create(plugin, ctx, delivery="announce", channel="telegram", fixed_text="true")["success"]

    def test_a_fixed_text_needs_somewhere_to_go(self, plugin, ctx):
        assert self._create(plugin, ctx, fixed_text=True)["error"] == NEEDS_A_CHANNEL
        assert self._create(plugin, ctx, delivery="announce", fixed_text=True)["error"] == NEEDS_A_CHANNEL

    def test_nonsense_is_refused(self, plugin, ctx):
        assert "fixed_text" in self._create(plugin, ctx, delivery="announce", channel="email", fixed_text="ja")["error"]

    def test_update_can_switch_it_on_and_off(self, plugin, ctx):
        update = next(t for t in plugin.get_tools(ctx) if t.name == "scheduler_update")
        job_id = self._create(plugin, ctx, delivery="announce", channel="email")["job_id"]
        _run(update.executor(job_id=job_id, fixed_text=True))
        assert _stored_payload(job_id)["fixed_text"] is True
        _run(update.executor(job_id=job_id, fixed_text=False))
        assert _stored_payload(job_id)["fixed_text"] is False

    def test_update_refuses_a_fixed_text_without_a_channel(self, plugin, ctx):
        update = next(t for t in plugin.get_tools(ctx) if t.name == "scheduler_update")
        job_id = self._create(plugin, ctx)["job_id"]
        assert json.loads(_run(update.executor(job_id=job_id, fixed_text=True)))["error"] == NEEDS_A_CHANNEL

    def test_the_tool_names_the_pucks_and_all_rooms(self, plugin, ctx):
        tool = next(t for t in plugin.get_tools(ctx) if t.name == "scheduler_create")
        parameters = tool.parameters["properties"]
        assert "freeecho2" in parameters["channel"]["description"]
        assert "'*'" in parameters["recipient"]["description"]


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


class TestSchedulerUpdate:
    def _create(self, tools):
        return json.loads(_run(tools["scheduler_create"].executor(
            name="report", schedule_type="cron", schedule_expr="15 7 * * *",
            message="Original text", delivery="announce", channel="email",
        )))["job_id"]

    def test_list_shows_message(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        self._create(tools)
        job = json.loads(_run(tools["scheduler_list"].executor()))["jobs"][0]
        assert job["message"] == "Original text"
        assert job["channel"] == "email"
        assert job["agent"] == "aifred"

    def test_update_changes_only_given_fields(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        job_id = self._create(tools)
        data = json.loads(_run(tools["scheduler_update"].executor(
            job_id=job_id, schedule_expr="5 7 * * *",
        )))
        assert data["success"] is True
        assert data["job_id"] == job_id
        assert data["schedule_expr"] == "5 7 * * *"
        job = json.loads(_run(tools["scheduler_list"].executor()))["jobs"][0]
        assert job["message"] == "Original text"
        assert job["channel"] == "email"
        assert job["expr"] == "5 7 * * *"

    def test_update_message_and_enabled(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        job_id = self._create(tools)
        data = json.loads(_run(tools["scheduler_update"].executor(
            job_id=job_id, message="New text", enabled=False,
        )))
        assert data["enabled"] is False
        job = json.loads(_run(tools["scheduler_list"].executor()))["jobs"][0]
        assert job["message"] == "New text"
        assert job["enabled"] is False

    def test_update_invalid_cron_leaves_job_unchanged(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        job_id = self._create(tools)
        data = json.loads(_run(tools["scheduler_update"].executor(
            job_id=job_id, schedule_expr="not a cron",
        )))
        assert "error" in data
        job = json.loads(_run(tools["scheduler_list"].executor()))["jobs"][0]
        assert job["expr"] == "15 7 * * *"

    def test_update_invalid_delivery(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        job_id = self._create(tools)
        data = json.loads(_run(tools["scheduler_update"].executor(
            job_id=job_id, delivery="pigeon",
        )))
        assert "error" in data

    def test_update_nonexistent(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        data = json.loads(_run(tools["scheduler_update"].executor(job_id=9999, name="x")))
        assert "error" in data


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


class TestScheduleEndInTheTool:
    """end_date: optional, a day or a moment, for cron and interval jobs; the model reads English errors."""

    @staticmethod
    def _tool(plugin, ctx, name):
        return next(tool.executor for tool in plugin.get_tools(ctx) if tool.name == name)

    def test_a_job_several_times_a_day_with_an_end(self, plugin, ctx):
        create = self._tool(plugin, ctx, "scheduler_create")
        result = json.loads(_run(create(
            name="deals", schedule_type="cron", schedule_expr="0 8,14,20 * * *", message="Prüfe die Angebote",
            end_date="2099-11-30",
        )))
        assert result["success"] is True and result["end_date"] == "2099-11-30"
        stored = get_job_store().get(result["job_id"])
        assert stored is not None and stored.ends_at == "2099-11-30"

    def test_without_an_end_the_job_has_none(self, plugin, ctx):
        create = self._tool(plugin, ctx, "scheduler_create")
        result = json.loads(_run(create(name="x", schedule_type="cron", schedule_expr="0 8 * * *", message="m")))
        assert result["end_date"] == ""

    @pytest.mark.parametrize("bad", ["30.11.2026", "morgen", "2026-11-30 18:00"])
    def test_a_bad_end_is_refused_in_english(self, plugin, ctx, bad):
        create = self._tool(plugin, ctx, "scheduler_create")
        result = json.loads(_run(create(name="x", schedule_type="cron", schedule_expr="0 8 * * *", message="m", end_date=bad)))
        assert result["error"] == t("error_invalid_ends_at", lang="en", value=bad)
        assert get_job_store().list_all() == []

    def test_a_one_shot_job_takes_no_end(self, plugin, ctx):
        create = self._tool(plugin, ctx, "scheduler_create")
        result = json.loads(_run(create(
            name="x", schedule_type="once", schedule_expr="2099-01-01T10:00:00", message="m", end_date="2099-02-01",
        )))
        assert result["error"] == t("error_ends_at_not_for_once", lang="en")

    def test_an_end_before_the_first_run_is_refused(self, plugin, ctx):
        create = self._tool(plugin, ctx, "scheduler_create")
        result = json.loads(_run(create(name="x", schedule_type="cron", schedule_expr="0 8 * * *", message="m", end_date="2020-01-01")))
        assert "never run" in result["error"] and "job not created" in result["error"]

    def test_update_sets_keeps_and_removes_the_end(self, plugin, ctx):
        create, update, listing = (self._tool(plugin, ctx, name) for name in ("scheduler_create", "scheduler_update", "scheduler_list"))
        job_id = json.loads(_run(create(name="x", schedule_type="cron", schedule_expr="0 8 * * *", message="m")))["job_id"]
        assert json.loads(_run(update(job_id=job_id, end_date="2099-12-31")))["end_date"] == "2099-12-31"
        assert json.loads(_run(update(job_id=job_id, message="neu")))["end_date"] == "2099-12-31"      # left out: stays
        assert json.loads(_run(listing()))["jobs"][0]["end_date"] == "2099-12-31"
        assert json.loads(_run(update(job_id=job_id, end_date="")))["end_date"] == ""                   # empty: no end

    def test_the_tools_offer_end_date_and_the_hints_name_the_list_of_hours(self, plugin, ctx):
        tools = {tool.name: tool for tool in plugin.get_tools(ctx)}
        for name in ("scheduler_create", "scheduler_update"):
            assert "end_date" in tools[name].parameters["properties"] and "end_date" not in tools[name].parameters.get("required", [])
        assert "0 8,14,20 * * *" in tools["scheduler_create"].description
        for lang in ("de", "en"):
            assert "0 8,14,20 * * *" in plugin.get_prompt_instructions(lang) and "end_date" in plugin.get_prompt_instructions(lang)
