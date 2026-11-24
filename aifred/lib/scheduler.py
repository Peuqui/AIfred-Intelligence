"""Scheduler — time-based job execution for AIfred.

Runs as a worker in the Message Hub. Checks every minute for due jobs
and executes them in isolated sessions.

Job types:
- cron: Classic 5-field cron expression in local time (lib/cron.py)
- interval: Fixed intervals in seconds
- once: Single execution at a specific timestamp

Jobs are persisted in SQLite and survive restarts.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import file_manager
from .browser_push import notify_changed
from .bubble import KIND_COLLAPSIBLE, BubbleArtifact
from .config import (
    BASE_AGENT,
    SCHEDULER_CHECK_INTERVAL_SECONDS,
    SCHEDULER_WEBHOOK_TIMEOUT_SECONDS,
)
from .cron import next_cron_run
from .i18n import t
from .logging_utils import log_exception, log_message
from .security import JOB_DEFAULT_TIER
from .task_plan import PLAN_KEYS, TaskPlan, plan_problem
from .user_error import Invalid

if TYPE_CHECKING:
    from .plan_engine import PartDone

# What a job can be: one truth for the agents' scheduler tool (its JSON-schema enums), the jobs page
# (the SQLite CHECK in _ensure_schema stays as the database's own limit).
SCHEDULE_TYPES = ("cron", "interval", "once")
DELIVERY_MODES = ("review", "announce", "webhook")
AUDIO_TYPES = ("notification", "alarm")   # the chime of an announcement in rooms (FreeEcho.2)
# Cron templates: (i18n key, minute, hour, day of month, month, day of week).
CRON_PRESETS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("sched_preset_hourly", "0", "*", "*", "*", "*"),
    ("sched_preset_daily", "0", "8", "*", "*", "*"),
    ("sched_preset_weekdays", "0", "8", "*", "*", "1-5"),
    ("sched_preset_weekly", "0", "8", "*", "*", "1"),
    ("sched_preset_monthly", "0", "8", "1", "*", "*"),
)


# When a recurring job ends: a day ("2026-11-30": that day still runs, to its end) or a moment ("2026-11-30T18:00":
# no run after it). One truth for the tool, the API and the job store.
_ENDS_AT_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_ENDS_AT_MOMENT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?")


def parse_ends_at(ends_at: str) -> datetime:
    """The last moment a job may run. ValueError for anything but the two forms above."""
    if _ENDS_AT_DAY.fullmatch(ends_at):
        return datetime.fromisoformat(ends_at).replace(hour=23, minute=59, second=59)
    if _ENDS_AT_MOMENT.fullmatch(ends_at):
        return datetime.fromisoformat(ends_at)
    raise ValueError(ends_at)


def _runs_after_end(next_run: str, ends_at: str) -> bool:
    """Whether a run at `next_run` falls after the end of the schedule (no end: never)."""
    return bool(ends_at) and datetime.fromisoformat(next_run) > parse_ends_at(ends_at)


def job_problem(schedule_type: str, payload: dict[str, Any], ends_at: str = "") -> Invalid | None:
    """What is wrong with a job before it is stored, None when nothing. The expression itself is checked
    when the next run is computed (JobStore.add / update raise for one that never fires)."""
    if schedule_type not in SCHEDULE_TYPES:
        return Invalid("unknown_schedule_type", value=schedule_type, allowed=", ".join(SCHEDULE_TYPES))
    if ends_at:
        if schedule_type == "once":
            return Invalid("ends_at_not_for_once")
        try:
            parse_ends_at(ends_at)
        except ValueError:
            return Invalid("invalid_ends_at", value=ends_at)
    delivery = payload.get("delivery", "review")
    if delivery not in DELIVERY_MODES:
        return Invalid("unknown_delivery", value=delivery, allowed=", ".join(DELIVERY_MODES))
    # Without a model run there is no session to review a fixed text in.
    if payload.get("fixed_text") and not (delivery == "announce" and payload.get("channel")):
        return Invalid("fixed_text_needs_announce")
    if payload.get("audio_type", "notification") not in AUDIO_TYPES:
        return Invalid("unknown_audio_type", value=payload["audio_type"], allowed=", ".join(AUDIO_TYPES))
    if any(key in payload for key in PLAN_KEYS):
        return plan_problem(payload)
    return None


# ============================================================
# JOB DATACLASS
# ============================================================

@dataclass
class Job:
    """A scheduled job."""

    job_id: int
    name: str
    schedule_type: str          # "cron", "interval", "once"
    schedule_expr: str          # Cron expr, seconds, or ISO timestamp
    payload: dict[str, Any]     # What to do: {"message": "...", "agent": "aifred", ...}
    max_tier: int = JOB_DEFAULT_TIER  # Security tier for this job
    enabled: bool = True
    created_at: str = ""
    last_run: str = ""
    next_run: str = ""
    retry_count: int = 0
    ends_at: str = ""           # "" = no end; see parse_ends_at

    @property
    def agent(self) -> str:
        """The agent that runs the job (the base agent when the payload names none)."""
        return str(self.payload.get("agent", BASE_AGENT))


# ============================================================
# JOB STORE (SQLite)
# ============================================================

class JobStore:
    """SQLite-backed persistent job storage."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _changed() -> None:
        """Every open page shows the jobs anew (the jobs page lists them, with their runs)."""
        notify_changed("jobs")

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    schedule_type TEXT NOT NULL CHECK(schedule_type IN ('cron', 'interval', 'once')),
                    schedule_expr TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    max_tier INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                    last_run TEXT,
                    next_run TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    ends_at TEXT
                )
            """)
            # A database from before the end of a schedule: the column is added once, the jobs stay as they are.
            if "ends_at" not in {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}:
                conn.execute("ALTER TABLE jobs ADD COLUMN ends_at TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    ran_at TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    delivered_text TEXT NOT NULL
                )
            """)
            conn.commit()

    def add_run(self, job_id: int, session_id: str, delivered_text: str) -> None:
        """Record what a run delivered. The full turn stays in its session;
        this keeps only the delivered answer for the next runs' history, and
        only as many runs per job as the history shows."""
        from .config import SCHEDULER_HISTORY_RUNS

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO job_runs (job_id, ran_at, session_id, delivered_text) VALUES (?, ?, ?, ?)",
                (job_id, _now_iso(), session_id, delivered_text),
            )
            conn.execute(
                "DELETE FROM job_runs WHERE job_id = ? AND run_id NOT IN "
                "(SELECT run_id FROM job_runs WHERE job_id = ? ORDER BY run_id DESC LIMIT ?)",
                (job_id, job_id, SCHEDULER_HISTORY_RUNS),
            )
            conn.commit()
            self._changed()

    def recent_runs(self, job_id: int, limit: int) -> list[tuple[str, str]]:
        """(ran_at, delivered_text) of the newest runs, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ran_at, delivered_text FROM job_runs WHERE job_id = ? ORDER BY run_id DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return [(r["ran_at"], r["delivered_text"]) for r in rows]

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        return Job(
            job_id=row["job_id"],
            name=row["name"],
            schedule_type=row["schedule_type"],
            schedule_expr=row["schedule_expr"],
            payload=json.loads(row["payload"]),
            max_tier=row["max_tier"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"] or "",
            last_run=row["last_run"] or "",
            next_run=row["next_run"] or "",
            retry_count=row["retry_count"],
            ends_at=row["ends_at"] or "",
        )

    def _first_run(self, schedule_type: str, schedule_expr: str, after: str, ends_at: str) -> str:
        """The next run of a new or changed schedule. Raises Invalid for one that never fires (get_due_jobs filters
        next_run IS NOT NULL), for an invalid cron expression (a CronError naming what is wrong) and for one whose
        first run already falls after its end."""
        next_run = _calculate_next_run(schedule_type, schedule_expr, after)
        if next_run is None:
            raise Invalid("schedule_never_fires", kind=schedule_type, expression=schedule_expr)
        if _runs_after_end(next_run, ends_at):
            raise Invalid("ends_before_first_run", first=next_run, ends_at=ends_at)
        return next_run

    def add(
        self,
        name: str,
        schedule_type: str,
        schedule_expr: str,
        payload: dict[str, Any],
        max_tier: int = JOB_DEFAULT_TIER,
        ends_at: str = "",
    ) -> Job:
        """Add a new job. Returns the created Job with its ID.

        Raises Invalid for an expression without a next run (the job would never fire) and for a first run after
        the end of the schedule.
        """
        next_run = self._first_run(schedule_type, schedule_expr, _now_iso(), ends_at)
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO jobs (name, schedule_type, schedule_expr, payload, max_tier, next_run, ends_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, schedule_type, schedule_expr, json.dumps(payload), max_tier, next_run, ends_at or None),
            )
            conn.commit()
            self._changed()
            job_id = cursor.lastrowid or 0
        log_message(f"Horarium: added job '{name}' (type={schedule_type}, next={next_run})")
        return self.get(job_id)  # type: ignore[return-value]

    def update(
        self,
        job_id: int,
        name: str,
        schedule_type: str,
        schedule_expr: str,
        payload: dict[str, Any],
        max_tier: int = JOB_DEFAULT_TIER,
        ends_at: str = "",
    ) -> Job | None:
        """Update a job in place.

        Keeps job_id, enabled, last_run and created_at (a delete+add would
        lose them and — worse — lose the job entirely if the add fails
        after the delete). retry_count resets like a fresh add. A job that
        ran to its end stays off after a later end is set: it is switched on
        like any other (enable).

        Raises Invalid like add: for an expression without a next run, and
        for a next run after the end of the schedule.
        """
        next_run = self._first_run(schedule_type, schedule_expr, _now_iso(), ends_at)
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE jobs SET name = ?, schedule_type = ?, schedule_expr = ?,
                       payload = ?, max_tier = ?, next_run = ?, retry_count = 0, ends_at = ?
                   WHERE job_id = ?""",
                (name, schedule_type, schedule_expr, json.dumps(payload),
                 max_tier, next_run, ends_at or None, job_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
        self._changed()
        log_message(f"Horarium: updated job '{name}' (id={job_id}, next={next_run})")
        return self.get(job_id)

    def get(self, job_id: int) -> Job | None:
        """Get a single job by ID."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_all(self, enabled_only: bool = False) -> list[Job]:
        """List all jobs."""
        query = "SELECT * FROM jobs"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY job_id"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_due_jobs(self, now_iso: str) -> list[Job]:
        """Get all enabled jobs whose next_run is at or before now."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM jobs
                   WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?
                   ORDER BY next_run""",
                (now_iso,),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def lapse_missed_runs(self) -> int:
        """Let every appointment that is due now lapse without a run: a recurring job moves to its next regular
        appointment (or ends, if that falls after its end), a one-shot job is off. ``last_run`` and the run history
        stay as they are: nothing ran. Used when the scheduler plugin is switched on again after a time off.
        Returns how many jobs it moved."""
        now = _now_iso()
        lapsed = self.get_due_jobs(now)
        with self._connect() as conn:
            for job in lapsed:
                next_run = None if job.schedule_type == "once" else _calculate_next_run(job.schedule_type, job.schedule_expr, now)
                ended = next_run is None or _runs_after_end(next_run, job.ends_at)
                conn.execute(
                    "UPDATE jobs SET next_run = ?, enabled = ? WHERE job_id = ?",
                    (None if ended else next_run, 0 if ended else 1, job.job_id),
                )
            conn.commit()
        if lapsed:
            self._changed()
            log_message(f"Horarium: {len(lapsed)} appointment(s) of the time off lapsed: {[job.name for job in lapsed]}")
        return len(lapsed)

    def mark_executed(self, job_id: int) -> None:
        """Update last_run and calculate next_run after execution."""
        now = _now_iso()
        job = self.get(job_id)
        if not job:
            return

        next_run: str | None = None
        if job.schedule_type == "once":
            # One-shot: disable after execution
            with self._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET last_run = ?, next_run = NULL, enabled = 0 WHERE job_id = ?",
                    (now, job_id),
                )
                conn.commit()
                self._changed()
            return

        next_run = _calculate_next_run(job.schedule_type, job.schedule_expr, now)
        if next_run is not None and _runs_after_end(next_run, job.ends_at):
            # The schedule has run to its end: off, like a one-shot job after its run.
            with self._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET last_run = ?, next_run = NULL, enabled = 0, retry_count = 0 WHERE job_id = ?",
                    (now, job_id),
                )
                conn.commit()
                self._changed()
            log_message(f"Horarium: job '{job.name}' (id={job_id}) ended after its last run ({job.ends_at})")
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET last_run = ?, next_run = ?, retry_count = 0 WHERE job_id = ?",
                (now, next_run, job_id),
            )
            conn.commit()
            self._changed()

    def mark_failed(self, job_id: int) -> None:
        """Increment retry count and advance next_run to prevent infinite retries."""
        now = _now_iso()
        job = self.get(job_id)
        if not job:
            return

        if job.schedule_type == "once":
            # One-shot job failed — disable it
            with self._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET retry_count = retry_count + 1, enabled = 0 WHERE job_id = ?",
                    (job_id,),
                )
                conn.commit()
                self._changed()
            return

        # Recurring job: advance next_run so it doesn't retry every minute
        next_run = _calculate_next_run(job.schedule_type, job.schedule_expr, now)
        if next_run is not None and _runs_after_end(next_run, job.ends_at):
            with self._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET retry_count = retry_count + 1, next_run = NULL, enabled = 0 WHERE job_id = ?",
                    (job_id,),
                )
                conn.commit()
                self._changed()
            log_message(f"Horarium: job '{job.name}' (id={job_id}) ended with a failed last run ({job.ends_at})")
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET retry_count = retry_count + 1, next_run = ? WHERE job_id = ?",
                (next_run, job_id),
            )
            conn.commit()
            self._changed()

    def delete(self, job_id: int) -> bool:
        """Delete a job and its run history. Returns True if deleted."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM job_runs WHERE job_id = ?", (job_id,))
            conn.commit()
            self._changed()
        return cursor.rowcount > 0

    def enable(self, job_id: int, enabled: bool = True) -> None:
        """Enable or disable a job.

        Enabling recalculates next_run from now: a job that was off keeps the
        next_run of its last run, so switching it on would fire at once for
        a slot that fell due while it was disabled. A job whose next run lies
        after the end of its schedule is not switched on (Invalid).
        """
        job = self.get(job_id)
        if not job:
            return
        next_run = (
            _calculate_next_run(job.schedule_type, job.schedule_expr, _now_iso())
            if enabled else job.next_run
        )
        if enabled and next_run is not None and _runs_after_end(next_run, job.ends_at):
            raise Invalid("job_ended", ends_at=job.ends_at)
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET enabled = ?, next_run = ? WHERE job_id = ?",
                (1 if enabled else 0, next_run, job_id),
            )
            conn.commit()
            self._changed()


# ============================================================
# SCHEDULING HELPERS
# ============================================================

def _now_iso() -> str:
    """Current local time as ISO string."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


async def delete_job(job_id: int) -> bool:
    """Delete a job with its run history and its folder in the workspace (lib/job_notes.py). Returns True if deleted."""
    from . import job_notes

    await job_notes.remove(job_id)
    return get_job_store().delete(job_id)


def next_run_of(schedule_type: str, schedule_expr: str) -> str | None:
    """When an expression fires next from now on; None for a one-shot time that is none, a CronError for a cron
    expression that is invalid or never fires."""
    return _calculate_next_run(schedule_type, schedule_expr, _now_iso())


def _calculate_next_run(schedule_type: str, schedule_expr: str, after: str) -> str | None:
    """Calculate next run time based on schedule type.

    Args:
        schedule_type: "cron", "interval", or "once"
        schedule_expr: Cron expression, seconds (int), or ISO timestamp
        after: Calculate next run after this ISO timestamp
    """
    if schedule_type == "once":
        # An ISO timestamp — run at that time (one past runs at the next check); anything else never fires.
        try:
            datetime.fromisoformat(schedule_expr)
        except ValueError:
            return None
        return schedule_expr

    if schedule_type == "interval":
        seconds = int(schedule_expr)
        base = datetime.fromisoformat(after)
        next_dt = base + timedelta(seconds=seconds)
        return str(next_dt.strftime("%Y-%m-%dT%H:%M:%S"))

    if schedule_type == "cron":
        return _next_cron_run(schedule_expr, after)

    return None


def _next_cron_run(cron_expr: str, after: str) -> str:
    """The next cron run time after `after`. A CronError (Invalid) for an expression that is invalid or never fires
    names what is wrong; a stored job never raises it, its expression was checked when it was saved."""
    return next_cron_run(cron_expr, datetime.fromisoformat(after)).strftime("%Y-%m-%dT%H:%M:%S")


# ============================================================
# SCHEDULER WORKER
# ============================================================

_job_store: JobStore | None = None


def get_job_store() -> JobStore:
    """Get the global JobStore singleton."""
    global _job_store
    if _job_store is None:
        from .config import DATA_DIR
        _job_store = JobStore(DATA_DIR / "scheduler" / "jobs.db")
    return _job_store


# Who holds a job's claim right now (the loop's task or a run-now). The claim is taken BEFORE the
# task is scheduled, so the hub thread's loop and the server thread's run-now endpoint cannot start
# the same job twice: claim_job is one atomic step (setdefault under the GIL, only the caller whose
# own marker comes back is the holder) and only the holder releases it.
_running_job_ids: dict[int, object] = {}


def claim_job(job_id: int) -> bool:
    """Take a job's claim in one step: True only when our own marker landed (setdefault returns
    what was already there — a different object means someone else holds it)."""
    marker = object()
    return _running_job_ids.setdefault(job_id, marker) is marker


def release_job(job_id: int) -> None:
    """Give a claim back. Only the holder calls this."""
    _running_job_ids.pop(job_id, None)


async def _run_due_job(store: JobStore, job: Job) -> None:
    """One job from its start to its mark in the store. The loop claimed the job before scheduling
    this task (an earlier loop's task or a run-now may still hold it)."""
    log_message(f"Horarium: executing job '{job.name}' (id={job.job_id})")
    try:
        await _execute_job(job)
        store.mark_executed(job.job_id)
        log_message(f"Horarium: job '{job.name}' completed")
    except Exception as exc:  # noqa: BLE001 — one failing job must not end the loop
        log_exception(f"Horarium: job '{job.name}' failed", exc)
        store.mark_failed(job.job_id)
    finally:
        release_job(job.job_id)


async def run_job_now(job: Job) -> None:
    """One immediate run ("run now"): the same execution path as the loop's, but without its
    schedule marks — the plan keeps its next_run, last_run and enabled state, so a recurring job's
    next appointment stays where it is and a one-shot job is not consumed by the trial run. The
    caller (the run-now endpoint) claimed the job; released in the finally. Success and failure
    go to the log only; the run itself lands in the job's run history (add_run, by _execute_job)."""
    log_message(f"Horarium: running job '{job.name}' (id={job.job_id}) now")
    try:
        await _execute_job(job)
        log_message(f"Horarium: job '{job.name}' completed (run now)")
    except Exception as exc:  # noqa: BLE001 — the failure shows in the log, nothing to mark
        log_exception(f"Horarium: job '{job.name}' failed (run now)", exc)
    finally:
        release_job(job.job_id)


async def scheduler_loop() -> None:
    """Main scheduler loop — runs as a Message Hub worker, while the scheduler plugin is on.

    Checks for due jobs every minute and executes them. Cancelling the loop (the plugin is switched off) does
    not cancel the job that is running: it runs to its end and marks itself; the loop only stops starting new ones.
    """
    store = get_job_store()
    log_message("Horarium: started")

    while True:
        try:
            now = _now_iso()
            for job in store.get_due_jobs(now):
                if not claim_job(job.job_id):     # claimed elsewhere: an earlier loop's task or a run-now is on it
                    continue
                await asyncio.shield(asyncio.ensure_future(_run_due_job(store, job)))

        except asyncio.CancelledError:
            log_message("Horarium: shutting down")
            return
        except Exception as exc:  # noqa: BLE001 — the loop outlives every error
            log_message(f"Horarium: loop error: {exc}", "error")

        await asyncio.sleep(SCHEDULER_CHECK_INTERVAL_SECONDS)


async def _execute_job(job: Job) -> None:
    """Execute a scheduled job through the SSOT message-processing pipeline.

    Hands the job off to ``process_inbound`` as a synthetic InboundMessage,
    so chat-history / llm-history / hub-notifications / title generation
    are persisted the same way as for any other channel. Only the final
    delivery (review / announce / webhook) stays scheduler-specific.
    """
    import secrets
    from datetime import datetime

    from .envelope import InboundMessage
    from .message_processor import process_inbound
    from .security import DELIVERY_CHANNEL_KEY

    message_text = job.payload.get("message", "")
    agent = job.agent

    if not message_text:
        log_message(f"Horarium: job '{job.name}' has no message, skipping", "warning")
        return
    from .agent_config import selectable_agents
    if agent not in selectable_agents():   # also a fixed text: it speaks in the agent's name
        raise RuntimeError(f"Job '{job.name}': agent '{agent}' is switched off or no chat agent")

    store = get_job_store()

    if job.payload.get("fixed_text"):
        await _deliver_fixed_text(job, message_text, store)
        return

    # Fresh channel_id per run → routing_table allocates a new session,
    # so every job execution lives in its own conversation. What earlier
    # runs delivered comes in through the job prompt instead.
    channel_id = secrets.token_hex(8)
    metadata = {
        "job_name": job.name,
        "job_id": job.job_id,
        # wake_agent pins the target agent so the LLM-based intent
        # detection in process_inbound doesn't reroute the request.
        "wake_agent": agent,
        # Honored by resolve_tier_for_sender for the scheduler channel
        # only — see security.py.
        "max_tier": job.max_tier,
        DELIVERY_CHANNEL_KEY: _delivery_channel(job),
    }
    # A job with parts: the parts run first (each a sub-agent, in the session the closing turn finds by its route),
    # then the closing turn gets what they found.
    plan_run = await _run_plan_parts(job, TaskPlan.from_payload(job.payload), channel_id, metadata) \
        if job.payload.get("parts") else None
    msg = InboundMessage(
        channel="scheduler",
        channel_id=channel_id,
        sender=job.name,
        text=build_job_prompt(job, store, plan_run.prompt_block if plan_run else ""),
        timestamp=datetime.now(),
        metadata=metadata,
        target_agent=agent,
        task=True,
    )

    outbound = await process_inbound(msg)

    if outbound is None or not outbound.text:
        raise RuntimeError(f"Engine returned no response for job '{job.name}'")

    session_id = outbound.metadata.get("session_id", "")
    if not session_id:
        raise RuntimeError(f"process_inbound returned no session_id for job '{job.name}'")

    # What goes out is what the model wrote after its last tool call, without thinking — the same text
    # every channel reply gets (PipelineResult.text_clean; 20.10.2026: a message written before the tools
    # and once more after them went out twice). The job prompt asks for the tools first, then the message once.
    # The coverage line of a plan is appended by the code, not written by the model.
    text = f"{outbound.text}\n\n{plan_run.coverage}" if plan_run else outbound.text
    store.add_run(job.job_id, session_id, text)
    await _deliver_result(job, text, session_id)


@dataclass
class _PlanRun:
    """What the parts of a plan job leave for the closing turn and the message."""

    prompt_block: str   # results for the closing prompt
    coverage: str       # the fixed line appended to the message


def _ui_language() -> str:
    from .settings import load_settings

    return str((load_settings() or {}).get("ui_language", "de"))


async def _run_plan_parts(job: Job, plan: TaskPlan, channel_id: str, metadata: dict[str, Any]) -> _PlanRun:
    """The parts of a plan job (lib/plan_engine.py): a start bubble, then one bubble per part as it ends (status line and
    transcripts); they go to the chat history only, never to the model. The new sources go in the notes. Every run
    stands for itself. Parts never send: the Horarium delivers. An exception outside the parts (the notes) fails the
    run; a failing part is in the result."""
    from . import job_notes, message_processor
    from .bubble import render_bubble
    from .debug_bus import debug, session_scope
    from .plan_engine import PlanResult, coverage_line, render_results, run_plan
    from .plugin_base import PluginContext
    from .prompt_loader import load_prompt
    from .security import TIER_COMMUNICATE, TIER_I18N_KEYS, resolve_trust_label

    lang = _ui_language()
    # The route of the session exists before the parts run: the closing turn finds the same session by it.
    session_id = message_processor.session_for_channel("scheduler", channel_id)
    _folder, notes_file = job_notes.sync(job.job_id, job.name)
    keeps_notes = _keeps_notes(job)
    ctx = PluginContext(
        agent_id=job.agent, lang=lang, session_id=session_id, user_query=job.payload.get("message", ""),
        max_tier=job.max_tier, source="scheduler", trust=resolve_trust_label("scheduler", job.name, metadata),
        metadata=dict(metadata),
    )
    # The parts only gather; sending is the Horarium's delivery.
    allowed_tiers = frozenset(tier for tier, _label, _description in TIER_I18N_KEYS
                              if tier <= job.max_tier and tier != TIER_COMMUNICATE)

    def post(text: str, artifacts: list[BubbleArtifact]) -> None:
        message_processor.record_chat_only_bubble(session_id, "scheduler", job.name, render_bubble(text, artifacts))

    head, parts_block = _plan_started_bubble(job.name, plan, lang)
    post(head, [BubbleArtifact(KIND_COLLAPSIBLE, parts_block, offset=len(head))])
    parts_done = 0
    result: PlanResult | None = None
    with session_scope(session_id):
        async for event in run_plan(
            plan, ctx, allowed_tiers=allowed_tiers, depth=0,
            notes_hint=load_prompt("scheduler/plan_notes_hint", lang=lang, file=notes_file).strip() if keeps_notes else "",
        ):
            if "progress" in event:
                debug(event["progress"])
            elif "part_done" in event:
                done: PartDone = event["part_done"]
                parts_done += 1
                post("", _part_bubble_artifacts(done, parts_done, len(plan.parts), lang))
            elif "plan_result" in event:
                result = event["plan_result"]
    if result is None:
        raise RuntimeError(f"Plan of job '{job.name}' ended without a result")

    now = datetime.now()
    if keeps_notes and result.new_sources:
        _append_new_sources(file_manager.DOCUMENTS_DIR / notes_file, result.new_sources, now, lang)
    return _PlanRun(
        prompt_block=load_prompt("scheduler/plan_results", lang=lang, results=render_results(result, lang)).strip(),
        coverage=coverage_line(result, lang),
    )


def _plan_started_bubble(job_name: str, plan: TaskPlan, lang: str) -> tuple[str, dict[str, Any]]:
    """The first bubble of a plan run: one line (job, number of parts) and a collapsed block with every part and its
    positions (the data of a collapsible artifact)."""
    from .formatting import format_number
    from .prompt_loader import load_prompt

    head = load_prompt("scheduler/plan_started_head", lang=lang, name=job_name,
                       count=format_number(len(plan.parts), locale=lang)).strip()
    part_lines = [
        load_prompt("scheduler/plan_started_part", lang=lang, title=part.title,
                    positions=", ".join(position.name for position in part.positions)).strip()
        for part in plan.parts
    ]
    block = {"title": load_prompt("scheduler/plan_started_parts_title", lang=lang).strip(), "content": "\n".join(part_lines)}
    return head, block


def _part_title(done: PartDone, index: int, count: int, lang: str) -> str:
    """The title line of a part's block, visible when it is collapsed: which part, how many positions it recorded, how
    long it took, or why it failed."""
    from .formatting import format_duration_s, format_number
    from .prompt_loader import load_prompt

    outcome, part = done.outcome, done.outcome.part
    shown = {
        "index": format_number(index, locale=lang), "count": format_number(count, locale=lang), "title": part.title,
        "duration": format_duration_s(done.seconds, decimals=0),
    }
    if outcome.failure is not None:
        return load_prompt("scheduler/plan_part_failed", lang=lang, reason=" ".join(outcome.failure.split()), **shown).strip()
    title = load_prompt(
        "scheduler/plan_part_status", lang=lang,
        checked=format_number(len(part.positions) - len(outcome.not_checked), locale=lang),
        total=format_number(len(part.positions), locale=lang),
        entries=format_number(len(outcome.records.entries), locale=lang), **shown,
    ).strip()
    if outcome.not_checked:
        names = {position.id: position.name for position in part.positions}
        title += load_prompt("scheduler/plan_part_missing", lang=lang,
                             positions=", ".join(names[position_id] for position_id in outcome.not_checked)).strip()
    return title


def _part_bubble_artifacts(done: PartDone, index: int, count: int, lang: str) -> list[BubbleArtifact]:
    """The one block of a part's bubble: the status as its title, the part's transcripts (every collapsible the
    sub-agents made, one under the other) as its content. What is no transcript (pages, images) follows as it is:
    a collapsible cannot hold it."""
    artifacts = [BubbleArtifact.from_dict(raw) for raw in done.artifacts]
    transcripts = [artifact.data for artifact in artifacts if artifact.kind == KIND_COLLAPSIBLE]
    content = "\n\n".join(
        "\n".join(filter(None, [f"=== {data['title']} ===", data["content"], f"( {data['footer']} )" if data.get("footer") else ""]))
        for data in transcripts
    )
    block = BubbleArtifact(KIND_COLLAPSIBLE, {"title": _part_title(done, index, count, lang), "content": content})
    return [block, *(artifact for artifact in artifacts if artifact.kind != KIND_COLLAPSIBLE)]


def _append_new_sources(notes_path: Path, sources: set[str], when: datetime, lang: str) -> None:
    """The sources the parts found beyond the given ones, as a section at the end of the job's notes."""
    from .prompt_loader import load_prompt

    heading = load_prompt("scheduler/plan_new_sources_heading", lang=lang, when=when.strftime("%Y-%m-%d %H:%M")).strip()
    section = "\n".join([heading, *(f"- {source}" for source in sorted(sources))])
    with notes_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n\n{section}\n")


def build_job_prompt(job: Job, store: JobStore, plan_block: str = "") -> str:
    """The message a job run starts with: job context, delivery, the times of the
    previous runs, where the job's notes are and the task (prompts/<lang>/scheduler/).
    ``plan_block``: what the parts of a plan job found, behind the task."""
    from .config import SCHEDULER_HISTORY_RUNS
    from .message_processor import announces_in_rooms, channel_display_label
    from .prompt_loader import load_prompt

    lang = _ui_language()
    delivery = job.payload.get("delivery", "review")
    channel = _delivery_channel(job)
    if delivery == "announce" and announces_in_rooms(channel):
        delivery_text = load_prompt(
            "scheduler/delivery_announce_spoken", lang=lang, channel=channel_display_label(channel),
        )
    elif delivery == "announce" and job.payload.get("recipient"):
        delivery_text = load_prompt(
            "scheduler/delivery_announce", lang=lang,
            channel=channel_display_label(channel), recipient=job.payload["recipient"],
        )
    elif delivery == "announce":   # no recipient: the channel's owner (message_processor._resolve_channel_recipient)
        delivery_text = load_prompt("scheduler/delivery_announce_owner", lang=lang, channel=channel_display_label(channel))
    else:
        delivery_text = load_prompt(f"scheduler/delivery_{delivery}", lang=lang)

    runs = store.recent_runs(job.job_id, SCHEDULER_HISTORY_RUNS)
    if runs:
        times = ", ".join(ran_at[:16].replace("T", " ") for ran_at, _text in runs)
        history = load_prompt("scheduler/history", lang=lang, runs=times)
    else:
        history = load_prompt("scheduler/history_empty", lang=lang)

    prompt = load_prompt(
        "scheduler/job_context", lang=lang,
        job_name=job.name, delivery=delivery_text.strip(),
        history=history.strip(), notes=_notes_text(job, lang), task=job.payload.get("message", ""),
    )
    return f"{prompt.rstrip()}\n\n{plan_block}" if plan_block else prompt


def _keeps_notes(job: Job) -> bool:
    """Does the job keep notes? Not a job that may not write data (below the tier of the owner's), nor one whose agent
    lacks the tools to read and write them."""
    from . import job_notes
    from .security import TIER_WRITE_DATA

    return job.max_tier >= TIER_WRITE_DATA and job_notes.agent_can_keep_notes(job.agent)


def _notes_text(job: Job, lang: str) -> str:
    """What the run is told of the job's notes (lib/job_notes.py): where they are and that it reads and continues them.
    A job without notes (``_keeps_notes``) is not told of any."""
    from . import job_notes
    from .prompt_loader import load_prompt
    from .upload_files import DOCUMENTS_URL

    if not _keeps_notes(job):
        return ""
    folder, notes_file = job_notes.sync(job.job_id, job.name)
    key = "scheduler/notes" if job_notes.has_notes(notes_file) else "scheduler/notes_new"
    # The folder's web address: without it the model guessed it, and a chart linked as /_upload/jobs/… did not
    # resolve, so the job's mail was refused (23.11.2026).
    return load_prompt(key, lang=lang, folder=folder, file=notes_file, url=f"{DOCUMENTS_URL}/{folder}").strip()


# ============================================================
# DELIVERY MODES
# ============================================================

async def _deliver_result(job: Job, response_text: str, session_id: str) -> None:
    """Deliver a job result based on the configured delivery mode.

    Modes (logging always happens regardless of mode):
    - "review":   Write to session + notification (user reviews in UI, default)
    - "announce": Send to a channel (discord, telegram, email)
    - "webhook":  HTTP POST to an external URL
    """
    delivery = job.payload.get("delivery", "review")
    log_message(f"Horarium: delivering job '{job.name}' result via '{delivery}'")

    # Always show toast notification (user should know a job ran)
    _deliver_review(job, response_text, session_id)

    # Additional delivery based on mode
    if delivery == "announce":
        await _deliver_announce(job, response_text, session_id)
    elif delivery == "webhook":
        await _deliver_webhook(job, response_text)


def _delivery_channel(job: Job) -> str:
    """The channel a job's answer goes out on, empty if it goes to none."""
    return job.payload.get("channel", "") if job.payload.get("delivery") == "announce" else ""


async def _send_announcement(job: Job, text: str, session_id: str | None) -> bool:
    """Send a job's text on its channel, through the paths everything else uses:
    in rooms like an agent's announcement or the announce API (all connected
    pucks without a room), to a recipient like an alert (recipient resolution and
    allowlist fallback live in ``announce_to_channel``). Returns whether it arrived."""
    from .agent_config import agent_display_name
    from .message_processor import (
        announce_into_rooms,
        announce_to_channel,
        announces_in_rooms,
        resolve_announce_targets,
    )

    channel = _delivery_channel(job)
    recipient = job.payload.get("recipient", "")
    # The job name is the subject of channels that have one (email). The source
    # opens what a job may link, which a reply to an outside sender may not.
    metadata = {"subject": job.name, **job.payload.get("metadata", {}), "source": "scheduler"}
    if not announces_in_rooms(channel):
        return await announce_to_channel(channel, recipient, text, session_id=session_id, metadata=metadata)
    speaker = agent_display_name(job.agent)
    metadata |= {"audio_type": job.payload.get("audio_type", "notification"), "proactive": True}
    rooms = resolve_announce_targets(channel, recipient)
    return bool(await announce_into_rooms(channel, rooms, text, speaker, metadata))


async def _deliver_fixed_text(job: Job, text: str, store: JobStore) -> None:
    """A job without a model: the text goes out word for word at its time, punctual
    and without a graphics card. Nothing to review in a session, so a text that does
    not arrive fails the job, which the scheduler loop logs and counts."""
    if not await _send_announcement(job, text, None):
        raise RuntimeError(f"Fixed text of job '{job.name}' did not arrive (channel {_delivery_channel(job)!r})")
    store.add_run(job.job_id, "", text)


async def _deliver_announce(job: Job, response_text: str, session_id: str) -> None:
    """Send the result of a run to its channel; a failure shows up in the UI."""
    channel_name = _delivery_channel(job)
    if not channel_name:
        log_message(f"Horarium: job '{job.name}' announce has no channel configured", "warning")
        return
    ok = await _send_announcement(job, response_text, session_id)
    if not ok:
        # Nicht nur ins Debug-Log (das beim Neustart geleert wird): der Auftrag
        # hat seinen Bericht erzeugt, ihn aber nicht zugestellt — das muss
        # sichtbar werden.
        log_message(
            f"Horarium: announce for job '{job.name}' did not deliver "
            f"(channel {channel_name!r})", "error",
        )
        from .message_processor import push_hub_notification
        push_hub_notification(
            session_id=session_id,
            session_title=t("job_delivery_failed_title", name=job.name),
            channel=channel_name,
            sender="system",
            status="error",
        )


def _deliver_review(job: Job, response_text: str, session_id: str) -> None:
    """Write notification for UI review."""
    from .message_processor import push_hub_notification
    push_hub_notification(
        session_id=session_id,
        session_title=f"Job: {job.name}",
        channel="scheduler",
        sender="system",
        status="done",
    )
    log_message(f"Horarium: job '{job.name}' result ready for review in session {session_id[:8]}")


async def _deliver_webhook(job: Job, response_text: str) -> None:
    """POST result to an external URL."""
    import aiohttp

    from .security import UnsafeURLError, validate_external_url

    url = job.payload.get("webhook_url", "")
    if not url:
        log_message(f"Horarium: job '{job.name}' webhook has no URL configured", "warning")
        return

    # SSRF protection: schema + private/loopback/reserved-IP rejection.
    try:
        validate_external_url(url)
    except UnsafeURLError as e:
        log_message(f"Horarium: webhook URL rejected: {e}", "error")
        return

    payload = {
        "job_name": job.name,
        "job_id": job.job_id,
        "result": response_text,
        "timestamp": _now_iso(),
    }

    try:
        # allow_redirects=False: validate_external_url only vetted THIS url.
        # Following a 302 would let a public host redirect us to an internal
        # address (cloud metadata, llama-swap, ChromaDB) — SSRF. The webhook
        # is fire-and-forget, so there's no need to follow redirects anyway.
        async with aiohttp.ClientSession() as session, session.post(
            url, json=payload, allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=SCHEDULER_WEBHOOK_TIMEOUT_SECONDS),
        ) as resp:
            log_message(f"Horarium: webhook POST to {url} → {resp.status}")
    except Exception as exc:  # noqa: BLE001 — reported in the log, the job itself succeeded
        log_message(f"Horarium: webhook POST to {url} failed: {exc}", "error")
