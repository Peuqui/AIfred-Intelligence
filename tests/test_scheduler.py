"""Tests for aifred.lib.scheduler — job store, scheduling, next_run calculation."""

from datetime import datetime, timedelta

import pytest

from aifred.lib.scheduler import JobStore, _calculate_next_run, _now_iso


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
