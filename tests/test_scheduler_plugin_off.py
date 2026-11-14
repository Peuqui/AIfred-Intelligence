"""Off means off for the scheduler (14.11.2026): while the plugin is off no job starts, a job that is running finishes,
and the appointments of the time off lapse — nothing is made up when it is switched on again."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import aifred.lib.scheduler as sched
from aifred.lib.scheduler import JobStore, _now_iso


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JobStore:
    job_store = JobStore(tmp_path / "jobs.db")
    monkeypatch.setattr(sched, "_job_store", job_store)
    monkeypatch.setattr(sched, "_running_job_ids", {})
    return job_store


def _due(store: JobStore, job_id: int, ago: timedelta = timedelta(hours=2)) -> None:
    past = (datetime.now() - ago).strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(store._db_path) as conn:
        conn.execute("UPDATE jobs SET next_run = ? WHERE job_id = ?", (past, job_id))


def test_missed_appointments_lapse_without_a_run(store: JobStore) -> None:
    recurring = store.add("morning", "cron", "0 7 * * *", {"message": "x"})
    ended = store.add("ends", "interval", "600", {"message": "x"}, ends_at=(datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S"))
    once = store.add("once", "once", (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"), {"message": "x"})
    later = store.add("later", "interval", "86400", {"message": "x"})
    for job in (recurring, ended, once):
        _due(store, job.job_id)
    with sqlite3.connect(store._db_path) as conn:           # the end of "ends" fell into the time off
        conn.execute("UPDATE jobs SET ends_at = ? WHERE job_id = ?", ((datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"), ended.job_id))
    untouched_next_run = store.get(later.job_id).next_run         # type: ignore[union-attr]

    assert store.lapse_missed_runs() == 3

    moved = store.get(recurring.job_id)
    assert moved is not None and moved.enabled and moved.next_run > _now_iso() and moved.last_run == ""
    assert store.recent_runs(recurring.job_id, 10) == []         # nothing ran
    over = store.get(ended.job_id)
    assert over is not None and not over.enabled and over.next_run == ""   # its end came during the time off
    one_shot = store.get(once.job_id)
    assert one_shot is not None and not one_shot.enabled and one_shot.last_run == ""
    assert store.get(later.job_id).next_run == untouched_next_run          # type: ignore[union-attr]
    assert store.get_due_jobs(_now_iso()) == []


def test_the_plugin_lets_missed_appointments_lapse_when_switched_on(store: JobStore) -> None:
    from aifred.plugins.tools.scheduler_tool import plugin

    job = store.add("morning", "cron", "0 7 * * *", {"message": "x"})
    _due(store, job.job_id)
    plugin.on_switched_on()
    assert store.get_due_jobs(_now_iso()) == []


def test_the_scheduler_is_a_background_part_of_its_plugin() -> None:
    from aifred.plugins.tools.scheduler_tool import plugin

    parts = plugin.background_parts()
    assert list(parts) == ["scheduler"] and parts["scheduler"].in_hub and parts["scheduler"].factory is sched.scheduler_loop


def test_a_running_job_finishes_when_the_loop_is_cancelled(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """The plugin is switched off while a job runs: the loop stops starting jobs, the job runs to its end and marks
    itself. (Until 14.11.2026 the cancellation went into the job.)"""
    job = store.add("long", "interval", "3600", {"message": "x"})
    _due(store, job.job_id)
    started, release = asyncio.Event(), asyncio.Event()
    runs: list[int] = []

    async def execute(running: Any) -> None:
        runs.append(running.job_id)
        started.set()
        await release.wait()

    monkeypatch.setattr(sched, "_execute_job", execute)

    async def scenario() -> None:
        loop_task = asyncio.create_task(sched.scheduler_loop())
        await started.wait()
        loop_task.cancel()
        await loop_task                                      # the loop is over, the job is not
        assert set(sched._running_job_ids) == {job.job_id}
        # A new loop (the plugin switched on again) does not start the job a second time.
        second = asyncio.create_task(sched.scheduler_loop())
        await asyncio.sleep(0.05)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):          # cancelled while it waits for the next minute
            await second
        release.set()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    done = store.get(job.job_id)
    assert runs == [job.job_id]
    assert done is not None and done.last_run != "" and sched._running_job_ids == {}


def test_claim_takes_a_job_once_and_frees_it() -> None:
    """The claim is one step: taken before the task is scheduled, so the hub thread's loop and the
    server thread's run-now endpoint cannot both start the same job."""
    assert sched.claim_job(1) is True
    assert sched.claim_job(1) is False
    assert sched.claim_job(2) is True
    sched.release_job(1)
    assert sched.claim_job(1) is True       # free again after the holder gave it back
    sched.release_job(1)
    sched.release_job(2)
    assert sched._running_job_ids == {}


def test_the_loop_skips_a_job_claimed_elsewhere(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """The run-now endpoint holds the claim while the job is due: the loop does not start it again."""
    job = store.add("evening", "interval", "3600", {"message": "x"})
    _due(store, job.job_id)
    assert sched.claim_job(job.job_id)
    runs: list[int] = []

    async def execute(running: Any) -> None:
        runs.append(running.job_id)

    monkeypatch.setattr(sched, "_execute_job", execute)

    async def scenario() -> None:
        loop_task = asyncio.create_task(sched.scheduler_loop())
        await asyncio.sleep(0.05)
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task

    asyncio.run(scenario())
    assert runs == []
    assert set(sched._running_job_ids) == {job.job_id}    # the endpoint's claim still holds
    sched.release_job(job.job_id)
