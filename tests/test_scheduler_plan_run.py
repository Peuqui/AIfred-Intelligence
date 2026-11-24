"""A Horarium job with parts: session first, then the parts, transcripts only in the chat history, closing
turn with the results, delivery with the coverage line. Every run stands for itself: no archive, no history."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest

import aifred.lib.scheduler as sched
from aifred.lib import file_manager, job_notes
from aifred.lib.agent_config import AgentConfig
from aifred.lib.message_processor import record_chat_only_bubble, session_for_channel
from aifred.lib.plan_engine import PartOutcome, PlanResult
from aifred.lib.plan_record import PartRecords, PlanEntry
from aifred.lib.plugin_base import PluginContext
from aifred.lib.security import TIER_COMMUNICATE, TIER_READONLY, TIER_WRITE_DATA, TIER_WRITE_SYSTEM
from aifred.lib.session_storage import load_session
from aifred.lib.task_plan import TaskPlan

SHOP = "https://shop.example/a"
PAYLOAD: dict[str, Any] = {
    "message": "Fasse zusammen.",
    "frame": "Preise beobachten",
    "parts": [{"id": "t1", "title": "Teil 1", "task": "Suche",
               "positions": [{"id": "p1", "name": "Pistole"}, {"id": "p2", "name": "Magazin"}]}],
    "columns": [{"name": "preis", "type": "zahl"}],
}


@pytest.fixture(autouse=True)
def environment(tmp_path, monkeypatch):
    sched._job_store = sched.JobStore(tmp_path / "jobs.db")
    monkeypatch.setattr("aifred.lib.file_manager.DOCUMENTS_DIR", tmp_path / "documents")
    monkeypatch.setattr("aifred.lib.settings.load_settings", lambda: {"ui_language": "de"})
    every_tool = AgentConfig(display_name="Agent", emoji="", description="", role="agent", color="#000000", tools=None)
    monkeypatch.setattr("aifred.lib.agent_config.get_agent_config", lambda agent_id: every_tool)
    yield
    sched._job_store = None


class Run:
    """The doubles of one job run and what they saw, in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, price: float = 10.0, new_sources: set[str] | None = None) -> None:
        self.order: list[str] = []
        self.price = price
        self.new_sources = new_sources or set()
        self.seen: dict[str, Any] = {}
        self.delivered: list[tuple[str, str]] = []
        monkeypatch.setattr("aifred.lib.message_processor.session_for_channel", self.session_for_channel)
        monkeypatch.setattr("aifred.lib.message_processor.record_chat_only_bubble", self.bubble)
        monkeypatch.setattr("aifred.lib.plan_engine.run_plan", self.run_plan)
        monkeypatch.setattr("aifred.lib.message_processor.process_inbound", self.process_inbound)
        monkeypatch.setattr(sched, "_deliver_result", self.deliver)

    def session_for_channel(self, channel: str, channel_id: str) -> str:
        self.order.append("session")
        self.seen["route"] = (channel, channel_id)
        return "sess"

    async def run_plan(
        self, plan: TaskPlan, ctx: PluginContext, *, allowed_tiers: frozenset[int], depth: int,
        notes_hint: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.order.append("parts")
        self.seen |= {"ctx": ctx, "allowed_tiers": allowed_tiers, "depth": depth, "notes_hint": notes_hint}
        yield {"progress": "Teil 1: sucht"}
        yield {"artifacts": [{"kind": "collapsible", "data": {"title": "Protokoll", "content": "Zeilen"}}]}
        records = PartRecords(
            entries=[PlanEntry("p1", "gefunden", "ok, billig", SHOP, {"preis": self.price})], new_sources=set(self.new_sources),
        )
        part = plan.parts[0]
        outcome = PartOutcome(part=part, records=records, report="", failure=None,
                              not_checked=records.open_positions(part))
        yield {"plan_result": PlanResult(plan=plan, outcomes=[outcome])}

    def bubble(self, session_id: str, channel: str, title: str, content: str, **kwargs: Any) -> None:
        self.order.append("bubble")
        self.seen["bubble"] = (session_id, channel, title, content)

    async def process_inbound(self, msg: Any) -> Any:
        self.order.append("closing")
        self.seen["msg"] = msg
        self.seen["archives_before_closing"] = self._archives()
        return SimpleNamespace(text="Bericht.", metadata={"session_id": "sess"})

    async def deliver(self, job: sched.Job, text: str, session_id: str) -> None:
        self.delivered.append((text, session_id))

    def _archives(self) -> list[str]:
        return [path.read_text(encoding="utf-8") for path in file_manager.DOCUMENTS_DIR.rglob("plan_archiv.csv")]


def _job(max_tier: int = TIER_WRITE_DATA, **payload: Any) -> sched.Job:
    return sched.get_job_store().add("Preise", "interval", "60", {**PAYLOAD, **payload}, max_tier=max_tier)


def test_the_run_goes_session_parts_bubble_closing_and_delivers_with_the_coverage_line(monkeypatch) -> None:
    run = Run(monkeypatch)
    job = _job()
    asyncio.run(sched._execute_job(job))

    assert run.order == ["session", "parts", "bubble", "closing"]
    assert run.seen["route"][0] == "scheduler"
    assert run.seen["msg"].channel_id == run.seen["route"][1]
    # the transcripts: one bubble, in the session of the route
    session_id, channel, _title, content = run.seen["bubble"]
    assert (session_id, channel) == ("sess", "scheduler")
    assert "Arbeitsprotokolle der Teilaufträge" in content and "Protokoll" in content
    # the closing prompt: the job prompt and the results
    prompt = run.seen["msg"].text
    assert prompt.startswith("[Geplanter Job: Preise]") and "Fasse zusammen." in prompt
    assert "ok, billig" in prompt and "VERLAUF" not in prompt and "plan_archiv" not in prompt
    assert run.seen["archives_before_closing"] == []   # a plan run writes no plan_archiv.csv
    # nothing but the code appends the line
    coverage = "1 von 2 Positionen geprüft\nNicht geprüft: Teil 1 / Magazin"
    assert run.delivered == [(f"Bericht.\n\n{coverage}", "sess")]
    assert [text for _, text in sched.get_job_store().recent_runs(job.job_id, 5)] == [f"Bericht.\n\n{coverage}"]


def test_the_parts_get_the_context_of_the_job_and_no_send_tier(monkeypatch) -> None:
    run = Run(monkeypatch)
    asyncio.run(sched._execute_job(_job(max_tier=TIER_WRITE_SYSTEM)))
    ctx: PluginContext = run.seen["ctx"]
    assert (ctx.session_id, ctx.source, ctx.trust, ctx.max_tier) == ("sess", "scheduler", "owner", TIER_WRITE_SYSTEM)
    assert ctx.user_query == "Fasse zusammen." and ctx.lang == "de"
    assert ctx.metadata["job_name"] == "Preise" and ctx.metadata["job_id"] == run.seen["msg"].metadata["job_id"]
    assert run.seen["depth"] == 0
    assert run.seen["allowed_tiers"] == frozenset({TIER_READONLY, TIER_WRITE_DATA, TIER_WRITE_SYSTEM})


@pytest.mark.parametrize(("max_tier", "expected"), [
    (TIER_READONLY, {TIER_READONLY}), (TIER_COMMUNICATE, {TIER_READONLY}), (TIER_WRITE_DATA, {TIER_READONLY, TIER_WRITE_DATA}),
])
def test_the_allowed_tiers_stay_within_the_job_without_communicate(monkeypatch, max_tier, expected) -> None:
    run = Run(monkeypatch)
    asyncio.run(sched._execute_job(_job(max_tier=max_tier)))
    assert run.seen["allowed_tiers"] == frozenset(expected)


def test_new_sources_land_in_the_notes_and_the_next_run_starts_without_history(monkeypatch) -> None:
    job = _job()
    first = Run(monkeypatch, price=10.0, new_sources={"https://neu.example", "https://alt.example"})
    asyncio.run(sched._execute_job(job))
    assert "NOTIZEN DES JOBS" in first.seen["notes_hint"]
    _, notes_file = job_notes.sync(job.job_id, job.name)
    notes = (file_manager.DOCUMENTS_DIR / notes_file).read_text(encoding="utf-8")
    assert "## Neue Quellen (" in notes and "- https://alt.example\n- https://neu.example\n" in notes

    second = Run(monkeypatch, price=8.0)
    asyncio.run(sched._execute_job(job))
    assert "VERLAUF" not in second.seen["msg"].text
    assert first.seen["archives_before_closing"] == second.seen["archives_before_closing"] == []   # no plan_archiv.csv


def test_a_job_without_notes_gets_no_hint_and_no_notes_file(monkeypatch) -> None:
    run = Run(monkeypatch, new_sources={"https://neu.example"})
    job = _job(max_tier=TIER_READONLY)
    asyncio.run(sched._execute_job(job))
    assert run.seen["notes_hint"] == ""
    _, notes_file = job_notes.sync(job.job_id, job.name)
    assert not job_notes.has_notes(notes_file)


def test_a_job_without_parts_runs_as_before(monkeypatch) -> None:
    run = Run(monkeypatch)
    job = sched.get_job_store().add("News", "interval", "60", {"message": "Nachrichten"})
    asyncio.run(sched._execute_job(job))
    assert run.order == ["closing"]
    assert run.delivered == [("Bericht.", "sess")]
    assert "VERLAUF" not in run.seen["msg"].text


def test_the_chat_only_bubble_does_not_touch_the_llm_history() -> None:
    session_id = session_for_channel("scheduler", "chan-1")
    record_chat_only_bubble(session_id, "scheduler", "Preise", "Protokolle")
    data = (load_session(session_id) or {})["data"]
    assert [entry["content"] for entry in data["chat_history"]] == ["Protokolle"]
    assert data.get("llm_history", []) == []
