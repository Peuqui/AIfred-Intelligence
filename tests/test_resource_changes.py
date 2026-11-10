"""A list the server holds changed: every open page is told which, and loads it anew (lib/browser_push.py: notify_changed).
The scheduler's jobs, the saved chats and the agents each announce a change at their one place of writing."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from aifred.lib import agent_config, browser_push, scheduler, session_events, session_storage

SESSION = "test-resource-changes"


@pytest.fixture(autouse=True)
def _fresh_channel() -> Iterator[None]:
    session_events._channels.pop(SESSION, None)
    yield
    session_events._channels.pop(SESSION, None)
    session_events._home_loop = None


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the places of writing announce, instead of an open page."""
    announced: list[str] = []
    for module in (scheduler, agent_config, session_storage):
        monkeypatch.setattr(module, "notify_changed", announced.append)
    return announced


def test_every_connected_page_is_told_which_list_changed() -> None:
    async def scenario() -> list[str]:
        async with session_events.subscribe(SESSION) as (_, queue):
            browser_push.notify_changed("jobs")
            browser_push.notify_changed("agents")
            return [queue.get_nowait().kind, queue.get_nowait().kind]

    assert asyncio.run(scenario()) == ["jobs_changed", "agents_changed"]


def test_nobody_is_told_who_is_not_connected() -> None:
    browser_push.notify_changed("jobs")
    assert session_events.replay_after(SESSION, 0) == []


def test_the_newest_change_of_a_list_replaces_the_older_ones_for_a_page_that_comes_back() -> None:
    async def scenario() -> list[str]:
        async with session_events.subscribe(SESSION):
            for _ in range(5):
                browser_push.notify_changed("jobs")
            browser_push.notify_changed("sessions")
        return [event.kind for event in session_events.replay_after(SESSION, 0)]

    assert asyncio.run(scenario()) == ["jobs_changed", "sessions_changed"]


def test_an_unknown_list_is_refused() -> None:
    with pytest.raises(ValueError):
        browser_push.notify_changed("recipes")


def test_the_changes_survive_the_clearing_of_a_new_turn() -> None:
    """turn_service keeps the kinds that carry a state: a page that reconnects meanwhile still gets the change."""
    async def scenario() -> list[str]:
        async with session_events.subscribe(SESSION):
            browser_push.notify_changed("jobs")
            session_events.publish(SESSION, "token", {})
            session_events.clear_replay(SESSION, keep=browser_push.LATEST_WINS_KINDS)
        return [event.kind for event in session_events.replay_after(SESSION, 0)]

    assert asyncio.run(scenario()) == ["jobs_changed"]


class TestJobsAnnounceEveryChange:
    @pytest.fixture
    def store(self, tmp_path: Path, told: list[str]) -> scheduler.JobStore:
        return scheduler.JobStore(tmp_path / "jobs.db")

    def test_add_update_enable_run_fail_and_delete(self, store: scheduler.JobStore, told: list[str]) -> None:
        job = store.add("t", "interval", "3600", {})
        assert told == ["jobs"]
        store.update(job.job_id, "t2", "interval", "3600", {})
        store.enable(job.job_id, False)
        store.enable(job.job_id, True)
        store.add_run(job.job_id, "s", "text")
        store.mark_executed(job.job_id)
        store.mark_failed(job.job_id)
        store.delete(job.job_id)
        assert told == ["jobs"] * 8

    def test_a_change_that_did_not_happen_is_not_announced(self, store: scheduler.JobStore, told: list[str]) -> None:
        assert store.update(999, "x", "interval", "3600", {}) is None
        assert told == []


def test_agents_announce_when_the_file_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, told: list[str]) -> None:
    monkeypatch.setattr(agent_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(agent_config, "AGENTS_FILE", tmp_path / "agents.json")
    agent_config.save_agents_raw({"aifred": {}})
    assert told == ["agents"]


class TestSavedChatsAnnounceTheirChanges:
    def test_a_session_written_and_deleted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, told: list[str]) -> None:
        monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
        session_id = session_storage.generate_session_id()
        assert session_storage.create_empty_session(session_id, "ki")
        assert told == ["sessions"]
        assert session_storage.delete_session(session_id)
        assert told == ["sessions", "sessions"]
