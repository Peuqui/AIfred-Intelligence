"""Tests for aifred.lib.scheduler — job store, scheduling, next_run calculation."""

from datetime import datetime, timedelta

import pytest

from aifred.lib.scheduler import JobStore, _calculate_next_run, _now_iso, job_problem, parse_ends_at
from aifred.lib.user_error import Invalid

# ── Helpers ───────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    """Fresh JobStore with temp database."""
    return JobStore(tmp_path / "test_jobs.db")


# ── Job Store CRUD ────────────────────────────────────────────

class TestJobStoreCRUD:
    def test_add_and_get(self, store):
        job = store.add("test", "interval", "300", {"message": "hi"}, max_tier=1)
        assert job.job_id is not None
        assert job.name == "test"
        assert job.schedule_type == "interval"
        assert job.enabled is True

        fetched = store.get(job.job_id)
        assert fetched is not None
        assert fetched.name == "test"
        assert fetched.payload == {"message": "hi"}

    def test_get_nonexistent(self, store):
        assert store.get(9999) is None

    def test_list_all(self, store):
        store.add("job1", "interval", "60", {})
        store.add("job2", "cron", "0 8 * * *", {})
        store.add("job3", "once", "2026-04-01T10:00:00", {})
        assert len(store.list_all()) == 3

    def test_list_enabled_only(self, store):
        store.add("active", "interval", "60", {})
        j2 = store.add("disabled", "interval", "60", {})
        store.enable(j2.job_id, enabled=False)
        enabled = store.list_all(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0].name == "active"

    def test_delete(self, store):
        job = store.add("to_delete", "interval", "60", {})
        assert store.delete(job.job_id) is True
        assert store.get(job.job_id) is None
        assert store.delete(job.job_id) is False  # Already deleted

    def test_enable_disable(self, store):
        job = store.add("toggle", "interval", "60", {})
        assert job.enabled is True

        store.enable(job.job_id, enabled=False)
        assert store.get(job.job_id).enabled is False

        store.enable(job.job_id, enabled=True)
        assert store.get(job.job_id).enabled is True


# ── Due Jobs ──────────────────────────────────────────────────

class TestDueJobs:
    def test_no_due_jobs_initially(self, store):
        store.add("future", "interval", "3600", {"message": "later"})
        due = store.get_due_jobs(_now_iso())
        assert len(due) == 0

    def test_due_job_detected(self, store):
        # Add job with next_run in the past
        job = store.add("past", "interval", "60", {"message": "now"})
        past = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
        # Manually set next_run to past
        import sqlite3
        conn = sqlite3.connect(str(store._db_path))
        conn.execute("UPDATE jobs SET next_run = ? WHERE job_id = ?", (past, job.job_id))
        conn.commit()
        conn.close()

        due = store.get_due_jobs(_now_iso())
        assert len(due) == 1
        assert due[0].name == "past"

    def test_disabled_jobs_not_due(self, store):
        job = store.add("disabled", "interval", "60", {})
        past = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
        import sqlite3
        conn = sqlite3.connect(str(store._db_path))
        conn.execute("UPDATE jobs SET next_run = ?, enabled = 0 WHERE job_id = ?", (past, job.job_id))
        conn.commit()
        conn.close()

        assert len(store.get_due_jobs(_now_iso())) == 0


# ── Mark Executed / Failed ────────────────────────────────────

class TestMarkExecuted:
    def test_interval_reschedules(self, store):
        job = store.add("repeat", "interval", "300", {})
        store.mark_executed(job.job_id)
        updated = store.get(job.job_id)
        assert updated.last_run != ""
        assert updated.next_run != ""  # Has a next run scheduled
        assert updated.enabled is True  # Still enabled (unlike 'once')
        assert updated.retry_count == 0

    def test_once_disables_after_execution(self, store):
        job = store.add("onetime", "once", "2026-04-01T10:00:00", {})
        store.mark_executed(job.job_id)
        updated = store.get(job.job_id)
        assert updated.enabled is False
        assert updated.next_run == ""

    def test_mark_failed_increments_retry(self, store):
        job = store.add("failing", "interval", "60", {})
        assert job.retry_count == 0
        store.mark_failed(job.job_id)
        assert store.get(job.job_id).retry_count == 1
        store.mark_failed(job.job_id)
        assert store.get(job.job_id).retry_count == 2


# ── Schedule Calculation ──────────────────────────────────────

class TestCalculateNextRun:
    def test_once_returns_expr(self):
        result = _calculate_next_run("once", "2026-04-01T10:00:00", "2026-03-29T12:00:00")
        assert result == "2026-04-01T10:00:00"

    def test_interval_adds_seconds(self):
        result = _calculate_next_run("interval", "300", "2026-03-29T12:00:00")
        assert result == "2026-03-29T12:05:00"

    def test_cron_calculates_next(self):
        # "every day at 8:00"
        result = _calculate_next_run("cron", "0 8 * * *", "2026-03-29T09:00:00")
        # Next 8:00 should be tomorrow
        assert result is not None
        assert "2026-03-30T08:00:00" == result

    def test_cron_same_day(self):
        # "every day at 14:00", current is 09:00
        result = _calculate_next_run("cron", "0 14 * * *", "2026-03-29T09:00:00")
        assert result == "2026-03-29T14:00:00"

    def test_invalid_schedule_type(self):
        result = _calculate_next_run("invalid", "foo", "2026-03-29T12:00:00")
        assert result is None


# ── Job Payload ───────────────────────────────────────────────

class TestJobPayload:
    def test_payload_round_trip(self, store):
        payload = {
            "message": "Check my emails and summarize",
            "agent": "aifred",
            "delivery": "announce",
            "channel": "discord",
        }
        job = store.add("complex", "cron", "0 8 * * *", payload, max_tier=1)
        fetched = store.get(job.job_id)
        assert fetched.payload == payload
        assert fetched.max_tier == 1

    def test_max_tier_stored(self, store):
        job = store.add("admin_job", "interval", "60", {}, max_tier=4)
        assert store.get(job.job_id).max_tier == 4


class TestEnableRecalculatesNextRun:
    def test_enable_moves_stale_next_run_to_future(self, store):
        job = store.add("gebet", "cron", "45 6 * * *", {"message": "x"})
        store.enable(job.job_id, enabled=False)
        with store._connect() as conn:  # stale slot from before the job was switched off
            conn.execute("UPDATE jobs SET next_run = '2026-10-06T06:45:00' WHERE job_id = ?", (job.job_id,))
            conn.commit()
        store.enable(job.job_id, enabled=True)
        enabled = store.get(job.job_id)
        assert enabled is not None and enabled.enabled is True
        assert enabled.next_run > _now_iso()
        assert store.get_due_jobs(_now_iso()) == []

    def test_disable_keeps_next_run(self, store):
        job = store.add("gebet", "cron", "45 6 * * *", {"message": "x"})
        store.enable(job.job_id, enabled=False)
        disabled = store.get(job.job_id)
        assert disabled is not None and disabled.next_run == job.next_run


# ── End of a schedule ─────────────────────────────────────────

def _days(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


class TestParseEndsAt:
    def test_a_day_counts_to_its_end(self):
        assert parse_ends_at("2026-11-30") == datetime(2026, 11, 30, 23, 59, 59)

    def test_a_moment_is_taken_as_it_is(self):
        assert parse_ends_at("2026-11-30T18:00") == datetime(2026, 11, 30, 18, 0)
        assert parse_ends_at("2026-11-30T18:00:30") == datetime(2026, 11, 30, 18, 0, 30)

    @pytest.mark.parametrize("text", ["30.11.2026", "2026-11-30 18:00", "2026-11", "bald", "20261130"])
    def test_anything_else_is_refused(self, text):
        with pytest.raises(ValueError):
            parse_ends_at(text)

    def test_job_problem_names_a_bad_end_and_a_one_shot_with_an_end(self):
        bad = job_problem("cron", {}, "30.11.2026")
        assert isinstance(bad, Invalid) and bad.key == "invalid_ends_at"
        once = job_problem("once", {}, "2026-11-30")
        assert isinstance(once, Invalid) and once.key == "ends_at_not_for_once"
        assert job_problem("cron", {}, "2026-11-30") is None and job_problem("cron", {}) is None


class TestScheduleEnd:
    def test_the_end_is_stored_and_read_back(self, store):
        job = store.add("t", "cron", "0 8,14,20 * * *", {}, ends_at=_days(30))
        assert store.get(job.job_id).ends_at == _days(30)
        assert store.add("open", "cron", "0 8 * * *", {}).ends_at == ""

    def test_a_first_run_after_the_end_is_refused(self, store):
        with pytest.raises(Invalid) as refused:
            store.add("t", "cron", "0 8 * * *", {}, ends_at=_days(-1))
        assert refused.value.key == "ends_before_first_run"
        assert store.list_all() == []

    def test_update_sets_moves_and_removes_the_end(self, store):
        job = store.add("t", "cron", "0 8 * * *", {})
        assert store.update(job.job_id, "t", "cron", "0 8 * * *", {}, ends_at=_days(10)).ends_at == _days(10)
        assert store.update(job.job_id, "t", "cron", "0 8 * * *", {}, ends_at="").ends_at == ""
        with pytest.raises(Invalid):
            store.update(job.job_id, "t", "cron", "0 8 * * *", {}, ends_at=_days(-1))
        assert store.get(job.job_id).ends_at == ""      # the refused change left it as it was

    @staticmethod
    def _time_passes_to(store, job_id, ends_at):
        """The end is now only 30 minutes away: as if the days since the job was set up had gone by."""
        with store._connect() as conn:
            conn.execute("UPDATE jobs SET ends_at = ? WHERE job_id = ?", (ends_at, job_id))
            conn.commit()

    @staticmethod
    def _in_half_an_hour() -> str:
        return (datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")

    def test_the_last_run_before_the_end_switches_the_job_off(self, store):
        job = store.add("t", "interval", "3600", {}, ends_at=_days(5))
        self._time_passes_to(store, job.job_id, self._in_half_an_hour())
        store.mark_executed(job.job_id)
        ended = store.get(job.job_id)
        assert (ended.enabled, ended.next_run, bool(ended.last_run)) == (False, "", True)

    def test_a_run_inside_the_schedule_goes_on(self, store):
        job = store.add("t", "interval", "3600", {}, ends_at=_days(5))
        store.mark_executed(job.job_id)
        assert store.get(job.job_id).enabled is True and store.get(job.job_id).next_run

    def test_a_failed_last_run_ends_the_job_too(self, store):
        job = store.add("t", "interval", "3600", {}, ends_at=_days(5))
        self._time_passes_to(store, job.job_id, self._in_half_an_hour())
        store.mark_failed(job.job_id)
        ended = store.get(job.job_id)
        assert (ended.enabled, ended.next_run, ended.retry_count) == (False, "", 1)

    def test_an_ended_job_is_not_switched_on_before_a_later_end(self, store):
        job = store.add("t", "interval", "3600", {}, ends_at=_days(5))
        self._time_passes_to(store, job.job_id, self._in_half_an_hour())
        store.mark_executed(job.job_id)
        with pytest.raises(Invalid) as refused:
            store.enable(job.job_id, True)
        assert refused.value.key == "job_ended"
        store.update(job.job_id, "t", "interval", "3600", {}, ends_at=_days(5))
        store.enable(job.job_id, True)
        assert store.get(job.job_id).enabled is True

    def test_a_database_from_before_the_end_gets_the_column_and_keeps_its_jobs(self, tmp_path):
        import sqlite3

        path = tmp_path / "old.db"
        with sqlite3.connect(path) as conn:
            conn.execute("""CREATE TABLE jobs (job_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                schedule_type TEXT NOT NULL CHECK(schedule_type IN ('cron', 'interval', 'once')), schedule_expr TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}', max_tier INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT, last_run TEXT, next_run TEXT, retry_count INTEGER NOT NULL DEFAULT 0)""")
            conn.execute("INSERT INTO jobs (name, schedule_type, schedule_expr, next_run) VALUES ('alt', 'cron', '0 8 * * *', '2030-01-01T08:00:00')")
        reopened = JobStore(path)
        assert [(job.name, job.ends_at) for job in reopened.list_all()] == [("alt", "")]
        JobStore(path)      # a second start does not add the column again
