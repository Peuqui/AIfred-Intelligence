"""A Horarium job with parts: session first, then the parts, transcripts only in the chat history, closing
turn with the results, delivery with the coverage line. Every run stands for itself: no history."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

import aifred.lib.scheduler as sched
from aifred.lib import file_manager, job_notes, pipeline_registry, session_storage, turn_service
from aifred.lib.agent_config import AgentConfig
from aifred.lib.config import SCHEDULER_JOBS_FOLDER
from aifred.lib.message_processor import record_chat_only_bubble, session_for_channel
from aifred.lib.plan_engine import PartDone, PartOutcome, PlanResult
from aifred.lib.plan_record import PartRecords, PlanEntry
from aifred.lib.plugin_base import PluginContext
from aifred.lib.security import TIER_COMMUNICATE, TIER_READONLY, TIER_WRITE_DATA, TIER_WRITE_SYSTEM
from aifred.lib.session_storage import create_empty_session, load_session
from aifred.lib.task_plan import TaskPlan

SESSION_ID = "0123456789abcdef0123456789abcdef"
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
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("aifred.lib.settings.load_settings", lambda: {"ui_language": "de"})
    every_tool = AgentConfig(display_name="Agent", emoji="", description="", role="agent", color="#000000", tools=None)
    monkeypatch.setattr("aifred.lib.agent_config.get_agent_config", lambda agent_id: every_tool)
    yield
    sched._job_store = None


class Run:
    """The doubles of one job run and what they saw, in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, price: float = 10.0, new_sources: set[str] | None = None,
                 record_bubbles: bool = True) -> None:
        self.order: list[str] = []
        self.price = price
        self.new_sources = new_sources or set()
        self.seen: dict[str, Any] = {}
        self.delivered: list[tuple[str, str]] = []
        self.bubbles: list[tuple[str, str, str, str]] = []
        self.scenarios: dict[str, str] = {}   # part id -> "gap" (default: first position only) | "full" | "fail"
        self.seconds = 645.0
        self.during_parts: Callable[[], Awaitable[None]] | None = None   # runs inside the parts, as a user's action would
        monkeypatch.setattr("aifred.lib.message_processor.session_for_channel", self.session_for_channel)
        if record_bubbles:
            monkeypatch.setattr("aifred.lib.message_processor.record_chat_only_bubble", self.bubble)
        monkeypatch.setattr("aifred.lib.plan_engine.run_plan", self.run_plan)
        monkeypatch.setattr("aifred.lib.message_processor.process_inbound", self.process_inbound)
        monkeypatch.setattr(sched, "_deliver_result", self.deliver)

    def session_for_channel(self, channel: str, channel_id: str) -> str:
        self.order.append("session")
        self.seen["route"] = (channel, channel_id)
        return SESSION_ID

    async def run_plan(
        self, plan: TaskPlan, ctx: PluginContext, *, allowed_tiers: frozenset[int], depth: int,
        notes_hint: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.order.append("parts")
        if self.during_parts is not None:
            await self.during_parts()
        self.seen |= {"ctx": ctx, "allowed_tiers": allowed_tiers, "depth": depth, "notes_hint": notes_hint}
        outcomes: list[PartOutcome] = []
        for part in plan.parts:
            yield {"progress": f"{part.title}: sucht"}
            artifact = {"kind": "collapsible", "data": {"title": f"Protokoll {part.title}", "content": "Zeilen"}}
            yield {"artifacts": [artifact]}
            scenario = self.scenarios.get(part.id, "gap")
            checked = part.positions if scenario == "full" else part.positions[:1] if scenario == "gap" else []
            records = PartRecords(
                entries=[PlanEntry(position.id, "gefunden", "ok, billig", SHOP, {"preis": self.price}) for position in checked],
                new_sources=set(self.new_sources),
            )
            outcome = PartOutcome(part=part, records=records, report="", failure="Modell weg" if scenario == "fail" else None,
                                  not_checked=records.open_positions(part))
            outcomes.append(outcome)
            self.order.append(f"done:{part.id}")
            yield {"part_done": PartDone(outcome, [artifact], self.seconds)}
        yield {"plan_result": PlanResult(plan=plan, outcomes=outcomes)}

    def bubble(self, session_id: str, channel: str, title: str, content: str, **kwargs: Any) -> None:
        self.order.append("bubble")
        self.bubbles.append((session_id, channel, title, content))

    async def process_inbound(self, msg: Any) -> Any:
        self.order.append("closing")
        self.seen["msg"] = msg
        return SimpleNamespace(text="Bericht.", metadata={"session_id": SESSION_ID})

    async def deliver(self, job: sched.Job, text: str, session_id: str) -> None:
        self.delivered.append((text, session_id))


def _job(max_tier: int = TIER_WRITE_DATA, **payload: Any) -> sched.Job:
    return sched.get_job_store().add("Preise", "interval", "60", {**PAYLOAD, **payload}, max_tier=max_tier)


def test_the_run_goes_session_parts_bubble_closing_and_delivers_with_the_coverage_line(monkeypatch) -> None:
    run = Run(monkeypatch)
    job = _job()
    asyncio.run(sched._execute_job(job))

    assert run.order == ["session", "bubble", "parts", "done:t1", "bubble", "closing", "bubble"]   # the last: the coverage line
    assert run.seen["route"][0] == "scheduler"
    assert run.seen["msg"].channel_id == run.seen["route"][1]
    # a start bubble and the part's bubble, in the session of the route; the transcripts sit in the part's bubble
    assert [(session_id, channel) for session_id, channel, _title, _content in run.bubbles] == [(SESSION_ID, "scheduler")] * 3
    assert run.bubbles[2][3] == "1 von 2 Positionen geprüft\nNicht geprüft: Teil 1 / Magazin"
    assert "Protokoll Teil 1" in run.bubbles[1][3]
    # the closing prompt: the job prompt and the results
    prompt = run.seen["msg"].text
    assert prompt.startswith("[Geplanter Job: Preise]") and "Fasse zusammen." in prompt
    assert "ok, billig" in prompt and "VERLAUF" not in prompt
    # nothing but the code appends the line
    coverage = "1 von 2 Positionen geprüft\nNicht geprüft: Teil 1 / Magazin"
    assert run.delivered == [(f"Bericht.\n\n{coverage}", SESSION_ID)]
    assert [text for _, text in sched.get_job_store().recent_runs(job.job_id, 5)] == [f"Bericht.\n\n{coverage}"]


def test_the_parts_get_the_context_of_the_job_and_no_send_tier(monkeypatch) -> None:
    run = Run(monkeypatch)
    asyncio.run(sched._execute_job(_job(max_tier=TIER_WRITE_SYSTEM)))
    ctx: PluginContext = run.seen["ctx"]
    assert (ctx.session_id, ctx.source, ctx.trust, ctx.max_tier) == (SESSION_ID, "scheduler", "owner", TIER_WRITE_SYSTEM)
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


def _notes_of(job: sched.Job) -> str:
    _, notes_file = job_notes.sync(job.job_id, job.name)
    return (file_manager.DOCUMENTS_DIR / notes_file).read_text(encoding="utf-8")


def _write_notes(job: sched.Job, text: str) -> None:
    _, notes_file = job_notes.sync(job.job_id, job.name)
    assert file_manager.write_file(notes_file, text).success


def test_new_sources_land_in_existing_notes_once_and_the_next_run_starts_without_history(monkeypatch) -> None:
    job = _job()
    _write_notes(job, "# Akte\n- https://www.alt.example/\n")
    first = Run(monkeypatch, price=10.0, new_sources={"https://neu.example", "http://alt.example"})
    asyncio.run(sched._execute_job(job))
    assert "NOTIZEN DES JOBS" in first.seen["notes_hint"]
    notes = _notes_of(job)
    assert "## Neue Quellen (" in notes and notes.endswith("- https://neu.example\n")
    assert notes.count("alt.example") == 1   # known in the notes (normalized): not entered again

    second = Run(monkeypatch, price=8.0, new_sources={"https://neu.example/"})
    asyncio.run(sched._execute_job(job))
    assert "VERLAUF" not in second.seen["msg"].text
    assert _notes_of(job).count("neu.example") == 1 and _notes_of(job).count("## Neue Quellen (") == 1


def test_an_address_the_notes_name_in_a_link_or_in_backticks_counts_as_known(monkeypatch) -> None:
    job = _job()
    _write_notes(job, "# Akte\n- [Shop](https://link.example/a) und `https://code.example/b`.\n")
    Run(monkeypatch, new_sources={"https://link.example/a", "https://code.example/b"})
    asyncio.run(sched._execute_job(job))
    notes = _notes_of(job)
    assert "## Neue Quellen (" not in notes
    assert notes.count("link.example") == 1 and notes.count("code.example") == 1


def test_new_sources_do_not_create_the_notes_so_the_next_run_still_gets_the_notes_new_prompt(monkeypatch) -> None:
    job = _job()
    Run(monkeypatch, new_sources={"https://neu.example"})
    asyncio.run(sched._execute_job(job))
    _, notes_file = job_notes.sync(job.job_id, job.name)
    assert not job_notes.has_notes(notes_file)
    assert "gibt es noch nicht" in sched._notes_text(job, "de")


def test_a_plan_job_that_keeps_no_notes_gets_no_job_folder(monkeypatch) -> None:
    Run(monkeypatch, new_sources={"https://neu.example"})
    job = _job(max_tier=TIER_READONLY)
    asyncio.run(sched._execute_job(job))
    assert not (file_manager.DOCUMENTS_DIR / SCHEDULER_JOBS_FOLDER).exists()


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
    assert run.order == ["session", "closing"]   # the session first: the run is its pipeline
    assert run.delivered == [("Bericht.", SESSION_ID)]
    assert "VERLAUF" not in run.seen["msg"].text


def test_the_chat_only_bubble_does_not_touch_the_llm_history() -> None:
    session_id = session_for_channel("scheduler", "chan-1")
    record_chat_only_bubble(session_id, "scheduler", "Preise", "Protokolle")
    data = (load_session(session_id) or {})["data"]
    assert [entry["content"] for entry in data["chat_history"]] == ["Protokolle"]
    assert data.get("llm_history", []) == []


def _three_parts() -> dict[str, Any]:
    names = {"t1": ("AAP01 und Tuning", ["Alpha", "Beta"]), "t2": ("Zubehör", ["Gamma"]), "t3": ("Ersatzteile", ["Delta", "Eps"])}
    return {"parts": [
        {"id": part_id, "title": title, "task": "Suche", "positions": [{"id": f"{part_id}-{n}", "name": name} for n, name in enumerate(positions)]}
        for part_id, (title, positions) in names.items()
    ]}


def test_the_start_bubble_names_every_part_with_its_positions_and_every_part_gets_its_own_status_bubble(monkeypatch) -> None:
    run = Run(monkeypatch)
    run.scenarios = {"t1": "full", "t2": "fail", "t3": "gap"}
    asyncio.run(sched._execute_job(_job(**_three_parts())))

    assert run.order == ["session", "bubble", "parts", "done:t1", "bubble", "done:t2", "bubble", "done:t3", "bubble", "closing", "bubble"]
    started, first, second, third, _coverage = (content for *_rest, content in run.bubbles)
    # the start bubble: one line, the parts and their positions in a collapsed block
    assert started.startswith("Job „Preise“ gestartet: 3 Teilaufträge\n\n<details")
    assert _summary(started) == "Teile und Positionen"
    assert ("- AAP01 und Tuning: Alpha, Beta&#10;- Zubehör: Gamma&#10;- Ersatzteile: Delta, Eps") in started
    # a part's bubble is one block: the status in its title (visible collapsed), the transcript inside, no text around it
    for content in (first, second, third):
        assert content.startswith("<details") and content.endswith("</details>") and content.count("<details") == 1
    assert _summary(first) == "Teil 1/3 · AAP01 und Tuning: 2 von 2 Positionen, 2 Einträge, 10:45 min"
    assert "Protokoll AAP01 und Tuning" in first and "Zeilen" in first
    assert _summary(second) == "Teil 2/3 · Zubehör: gescheitert: Modell weg, 10:45 min"
    assert _summary(third) == "Teil 3/3 · Ersatzteile: 1 von 2 Positionen, 1 Einträge, 10:45 min, nicht geprüft: Eps"


def _summary(bubble: str) -> str:
    match = re.search(r"<summary[^>]*>(.*?)</summary>", bubble, re.DOTALL)
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize(("seconds", "text"), [(45.0, "45s"), (59.4, "59s"), (60.0, "1:00 min"), (645.0, "10:45 min")])
def test_the_duration_is_in_seconds_below_a_minute_else_minutes_and_seconds(monkeypatch, seconds, text) -> None:
    run = Run(monkeypatch)
    run.seconds = seconds
    asyncio.run(sched._execute_job(_job()))
    assert f", {text}" in _summary(run.bubbles[1][3])


def test_the_bubbles_stand_in_the_history_before_the_closing_request_and_the_model_never_sees_them(monkeypatch) -> None:
    run = Run(monkeypatch, record_bubbles=False)
    run.scenarios = {"t1": "full", "t2": "full", "t3": "full"}
    published: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr("aifred.lib.session_events.publish",
                        lambda session_id, kind, data, **kwargs: published.append((session_id, kind, data)))
    monkeypatch.setattr("aifred.lib.message_processor.session_for_channel", session_for_channel)
    history_at_closing: list[Any] = []
    llm_at_closing: list[Any] = []
    channel_ids: list[str] = []

    async def closing(msg: Any) -> Any:
        channel_ids.append(msg.channel_id)
        data = (load_session(session_for_channel("scheduler", msg.channel_id)) or {})["data"]
        history_at_closing.extend(data["chat_history"])
        llm_at_closing.extend(data.get("llm_history", []))
        return SimpleNamespace(text="Bericht.", metadata={"session_id": SESSION_ID})

    monkeypatch.setattr("aifred.lib.message_processor.process_inbound", closing)
    asyncio.run(sched._execute_job(_job(**_three_parts())))

    contents = [entry["content"] for entry in history_at_closing]
    assert len(contents) == 4 and contents[0].startswith("Job „Preise“ gestartet")
    assert [_summary(content).split(": ")[0] for content in contents[1:]] == [
        "Teil 1/3 · AAP01 und Tuning", "Teil 2/3 · Zubehör", "Teil 3/3 · Ersatzteile"]
    assert llm_at_closing == []
    # every bubble reached the open tabs of the session at once, as the turn service's bubbles do
    added = [(session, data["entry"]["content"]) for session, kind, data in published if kind == "message_added"]
    assert [content for _session, content in added][:-1] == contents   # the last: the coverage line, after the closing turn
    assert {session for session, _content in added} == {session_for_channel("scheduler", channel_ids[0])}


def _loop_job(job: sched.Job) -> None:
    assert sched.claim_job(job.job_id)


def test_during_the_parts_the_session_is_a_running_pipeline_and_a_message_waits_until_the_run_ends(monkeypatch) -> None:
    run = Run(monkeypatch)
    create_empty_session(SESSION_ID, owner="test")
    started: list[str] = []
    monkeypatch.setattr(turn_service, "start_turn", lambda request: started.append(request.text))

    async def during() -> None:
        run.seen["running"] = pipeline_registry.is_pipeline_running(SESSION_ID)
        run.seen["queued"] = turn_service.submit_turn(turn_service.TurnRequest(SESSION_ID, "Hallo"))
        run.seen["started_during"] = list(started)

    run.during_parts = during
    asyncio.run(sched._execute_job(_job()))

    assert run.seen["running"] is True and run.seen["queued"] is True
    assert run.seen["started_during"] == []                 # it waits, it does not run in parallel
    assert not pipeline_registry.is_pipeline_running(SESSION_ID)
    assert started == ["Hallo"]                              # the run's end starts it
    assert run.delivered != []


def test_the_pipeline_is_registered_for_the_whole_run_including_the_closing_turn_and_the_delivery(monkeypatch) -> None:
    run = Run(monkeypatch)
    seen: dict[str, bool] = {}
    closing = run.process_inbound
    delivery = run.deliver

    async def closing_turn(msg: Any) -> Any:
        seen["closing"] = pipeline_registry.is_pipeline_running(SESSION_ID)
        return await closing(msg)

    async def deliver(job: sched.Job, text: str, session_id: str) -> None:
        seen["delivery"] = pipeline_registry.is_pipeline_running(SESSION_ID)
        await delivery(job, text, session_id)

    monkeypatch.setattr("aifred.lib.message_processor.process_inbound", closing_turn)
    monkeypatch.setattr(sched, "_deliver_result", deliver)
    asyncio.run(sched._execute_job(_job()))
    assert seen == {"closing": True, "delivery": True}
    assert not pipeline_registry.is_pipeline_running(SESSION_ID)


def test_a_stop_during_the_parts_ends_the_run_marks_it_failed_starts_the_waiting_message_and_the_loop_lives(monkeypatch) -> None:
    run = Run(monkeypatch)
    create_empty_session(SESSION_ID, owner="test")
    started: list[str] = []
    monkeypatch.setattr(turn_service, "start_turn", lambda request: started.append(request.text))

    async def during() -> None:
        assert turn_service.submit_turn(turn_service.TurnRequest(SESSION_ID, "Hallo"))
        assert pipeline_registry.cancel_pipeline(SESSION_ID)      # the stop button
        await asyncio.sleep(1)                                # the cancellation lands here

    run.during_parts = during
    job = _job()

    async def scenario() -> None:
        _loop_job(job)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.ensure_future(sched._run_due_job(sched.get_job_store(), job))

    asyncio.run(scenario())
    failed = sched.get_job_store().get(job.job_id)
    assert failed is not None and failed.retry_count == 1
    assert "closing" not in run.order and run.delivered == []
    assert started == ["Hallo"]
    assert not pipeline_registry.is_pipeline_running(SESSION_ID)
    assert sched._running_job_ids == {}


def _real_session(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real session store instead of the bubble double: what stands in the session is read back from it."""
    monkeypatch.setattr("aifred.lib.message_processor.record_chat_only_bubble", record_chat_only_bubble)
    monkeypatch.setattr("aifred.lib.message_processor.session_for_channel", session_for_channel)
    monkeypatch.setattr("aifred.lib.session_events.publish", lambda *args, **kwargs: None)


def _session_of(job_run_session: str) -> dict[str, Any]:
    data: dict[str, Any] = (load_session(job_run_session) or {})["data"]
    return data


def _routed_session(run: Run) -> str:
    return session_for_channel("scheduler", run.seen["msg"].channel_id)


COVERAGE = "1 von 2 Positionen geprüft\nNicht geprüft: Teil 1 / Magazin"


def test_the_coverage_line_stands_in_the_session_as_a_bubble_the_model_does_not_see(monkeypatch) -> None:
    run = Run(monkeypatch, record_bubbles=False)
    _real_session(run, monkeypatch)
    asyncio.run(sched._execute_job(_job()))
    data = _session_of(_routed_session(run))
    assert data["chat_history"][-1]["content"] == COVERAGE
    assert data.get("llm_history", []) == []


@pytest.mark.parametrize("closing", ["raises", "empty"])
def test_a_failed_closing_delivers_notice_results_and_coverage_and_the_model_sees_them(monkeypatch, closing) -> None:
    run = Run(monkeypatch, record_bubbles=False)
    _real_session(run, monkeypatch)

    async def failing(msg: Any) -> Any:
        run.seen["msg"] = msg
        if closing == "raises":
            raise ValueError("Modell weg")
        return SimpleNamespace(text="", metadata={"session_id": SESSION_ID})

    monkeypatch.setattr("aifred.lib.message_processor.process_inbound", failing)
    job = _job()

    async def scenario() -> None:
        sched.claim_job(job.job_id)
        await sched._run_due_job(sched.get_job_store(), job)

    asyncio.run(scenario())

    [(text, session_id)] = run.delivered
    assert session_id == _routed_session(run)
    assert "Abschluss" in text and "ok, billig" in text and text.endswith(COVERAGE)
    assert ("Modell weg" in text) == (closing == "raises") and ("Engine returned no response" in text) == (closing == "empty")
    assert [stored for _, stored in sched.get_job_store().recent_runs(job.job_id, 5)] == [text]
    llm = _session_of(session_id)["llm_history"]
    assert len(llm) == 1 and "ok, billig" in llm[0]["content"] and "Abschluss" in llm[0]["content"]
    failed = sched.get_job_store().get(job.job_id)
    assert failed is not None and failed.retry_count == 1


def test_a_stop_after_the_parts_writes_the_results_to_the_session_without_delivery_or_run_entry(monkeypatch) -> None:
    run = Run(monkeypatch, record_bubbles=False)
    _real_session(run, monkeypatch)

    async def stopped(msg: Any) -> Any:
        run.seen["msg"] = msg
        raise asyncio.CancelledError

    monkeypatch.setattr("aifred.lib.message_processor.process_inbound", stopped)
    job = _job()

    async def scenario() -> None:
        sched.claim_job(job.job_id)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.ensure_future(sched._run_due_job(sched.get_job_store(), job))

    asyncio.run(scenario())
    assert run.delivered == []
    assert sched.get_job_store().recent_runs(job.job_id, 5) == []
    llm = _session_of(_routed_session(run))["llm_history"]
    assert len(llm) == 1 and "ok, billig" in llm[0]["content"] and "abgebrochen" in llm[0]["content"]
