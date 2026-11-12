"""Scheduler — time-based job execution for AIfred.

Runs as a worker in the Message Hub. Checks every minute for due jobs
and executes them in isolated sessions.

Job types:
- cron: Standard 5-field cron expression with timezone
- interval: Fixed intervals in seconds
- once: Single execution at a specific timestamp

Jobs are persisted in SQLite and survive restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .browser_push import notify_changed
from .logging_utils import log_message
from .security import JOB_DEFAULT_TIER
from .user_error import Invalid

logger = logging.getLogger(__name__)

# Check interval: how often the scheduler looks for due jobs
_CHECK_INTERVAL_SECONDS = 60

# What a job can be: one truth for the agents' scheduler tool (its JSON-schema enums), the jobs page and the
# Reflex editor (the SQLite CHECK in _ensure_schema stays as the database's own limit).
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
        next_run IS NOT NULL) and for one whose first run already falls after its end."""
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
        log_message(f"Scheduler: added job '{name}' (type={schedule_type}, next={next_run})")
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
        log_message(f"Scheduler: updated job '{name}' (id={job_id}, next={next_run})")
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
            log_message(f"Scheduler: job '{job.name}' (id={job_id}) ended after its last run ({job.ends_at})")
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
            log_message(f"Scheduler: job '{job.name}' (id={job_id}) ended with a failed last run ({job.ends_at})")
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
    """When an expression fires next from now on; None for one that never does."""
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


def _next_cron_run(cron_expr: str, after: str) -> str | None:
    """Calculate next cron run time.

    Uses croniter if available, otherwise falls back to simple interval.
    """
    # Fail-loud statt stillem 1h-Intervall-Fallback: croniter ist eine
    # harte Abhängigkeit — ein Cron-Job, der heimlich stündlich statt zum
    # konfigurierten Zeitpunkt läuft, wäre ein versteckter Fehler.
    from croniter import croniter
    try:
        base = datetime.fromisoformat(after)
        cron = croniter(cron_expr, base)
        next_dt = cron.get_next(datetime)
        return str(next_dt.strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception as exc:  # noqa: BLE001 — a job with a broken schedule must not stop the others
        logger.error("Invalid cron expression '%s': %s", cron_expr, exc)
        return None


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


async def scheduler_loop() -> None:
    """Main scheduler loop — runs as a Message Hub worker.

    Checks for due jobs every minute and executes them.
    """
    store = get_job_store()
    log_message("Scheduler: started")

    while True:
        try:
            now = _now_iso()
            due_jobs = store.get_due_jobs(now)

            for job in due_jobs:
                log_message(f"Scheduler: executing job '{job.name}' (id={job.job_id})")
                try:
                    await _execute_job(job)
                    store.mark_executed(job.job_id)
                    log_message(f"Scheduler: job '{job.name}' completed")
                except Exception as exc:  # noqa: BLE001 — one failing job must not end the loop
                    store.mark_failed(job.job_id)
                    log_message(f"Scheduler: job '{job.name}' failed: {exc}", "error")

        except asyncio.CancelledError:
            log_message("Scheduler: shutting down")
            return
        except Exception as exc:  # noqa: BLE001 — the loop outlives every error
            log_message(f"Scheduler: loop error: {exc}", "error")

        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)


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
    agent = job.payload.get("agent", "aifred")

    if not message_text:
        log_message(f"Scheduler: job '{job.name}' has no message, skipping", "warning")
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
    msg = InboundMessage(
        channel="scheduler",
        channel_id=secrets.token_hex(8),
        sender=job.name,
        text=build_job_prompt(job, store),
        timestamp=datetime.now(),
        metadata={
            "job_name": job.name,
            "job_id": job.job_id,
            # wake_agent pins the target agent so the LLM-based intent
            # detection in process_inbound doesn't reroute the request.
            "wake_agent": agent,
            # Honored by resolve_tier_for_sender for the scheduler channel
            # only — see security.py.
            "max_tier": job.max_tier,
            DELIVERY_CHANNEL_KEY: _delivery_channel(job),
        },
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
    store.add_run(job.job_id, session_id, outbound.text)
    await _deliver_result(job, outbound.text, session_id)


def build_job_prompt(job: Job, store: JobStore) -> str:
    """The message a job run starts with: job context, delivery, the times of the
    previous runs, where the job's notes are and the task (prompts/<lang>/scheduler/)."""
    from .config import SCHEDULER_HISTORY_RUNS
    from .message_processor import announces_in_rooms, channel_display_label
    from .prompt_loader import load_prompt
    from .settings import load_settings

    lang = (load_settings() or {}).get("ui_language", "de")
    delivery = job.payload.get("delivery", "review")
    channel = _delivery_channel(job)
    if delivery == "announce" and announces_in_rooms(channel):
        delivery_text = load_prompt(
            "scheduler/delivery_announce_spoken", lang=lang, channel=channel_display_label(channel),
        )
    elif delivery == "announce":
        delivery_text = load_prompt(
            "scheduler/delivery_announce", lang=lang,
            channel=channel_display_label(channel),
            recipient=job.payload.get("recipient", ""),
        )
    else:
        delivery_text = load_prompt(f"scheduler/delivery_{delivery}", lang=lang)

    runs = store.recent_runs(job.job_id, SCHEDULER_HISTORY_RUNS)
    if runs:
        times = ", ".join(ran_at[:16].replace("T", " ") for ran_at, _text in runs)
        history = load_prompt("scheduler/history", lang=lang, runs=times)
    else:
        history = load_prompt("scheduler/history_empty", lang=lang)

    return load_prompt(
        "scheduler/job_context", lang=lang,
        job_name=job.name, delivery=delivery_text.strip(),
        history=history.strip(), notes=_notes_text(job, lang), task=job.payload.get("message", ""),
    )


def _notes_text(job: Job, lang: str) -> str:
    """What the run is told of the job's notes (lib/job_notes.py): where they are and that it reads and continues them.
    A job that may not write data (below the tier of the owner's) has none, and the run is not told of any."""
    from . import job_notes
    from .prompt_loader import load_prompt
    from .security import TIER_WRITE_DATA

    if job.max_tier < TIER_WRITE_DATA:
        return ""
    folder, notes_file = job_notes.sync(job.job_id, job.name)
    key = "scheduler/notes" if job_notes.has_notes(notes_file) else "scheduler/notes_new"
    return load_prompt(key, lang=lang, folder=folder, file=notes_file).strip()


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
    log_message(f"Scheduler: delivering job '{job.name}' result via '{delivery}'")

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
    speaker = agent_display_name(job.payload.get("agent", "aifred"))
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
        log_message(f"Scheduler: job '{job.name}' announce has no channel configured", "warning")
        return
    ok = await _send_announcement(job, response_text, session_id)
    if not ok:
        # Nicht nur ins Debug-Log (das beim Neustart geleert wird): der Auftrag
        # hat seinen Bericht erzeugt, ihn aber nicht zugestellt — das muss
        # sichtbar werden.
        log_message(
            f"Scheduler: announce for job '{job.name}' did not deliver "
            f"(channel {channel_name!r})", "error",
        )
        from .message_processor import push_hub_notification
        push_hub_notification(
            session_id=session_id,
            session_title=f"Job: {job.name} — Zustellung fehlgeschlagen",
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
    log_message(f"Scheduler: job '{job.name}' result ready for review in session {session_id[:8]}")


async def _deliver_webhook(job: Job, response_text: str) -> None:
    """POST result to an external URL."""
    import aiohttp

    from .security import UnsafeURLError, validate_external_url

    url = job.payload.get("webhook_url", "")
    if not url:
        log_message(f"Scheduler: job '{job.name}' webhook has no URL configured", "warning")
        return

    # SSRF protection: schema + private/loopback/reserved-IP rejection.
    try:
        validate_external_url(url)
    except UnsafeURLError as e:
        log_message(f"Scheduler: webhook URL rejected: {e}", "error")
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
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            log_message(f"Scheduler: webhook POST to {url} → {resp.status}")
    except Exception as exc:  # noqa: BLE001 — reported in the log, the job itself succeeded
        log_message(f"Scheduler: webhook POST to {url} failed: {exc}", "error")
