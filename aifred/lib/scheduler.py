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
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .logging_utils import log_message

logger = logging.getLogger(__name__)

# Check interval: how often the scheduler looks for due jobs
_CHECK_INTERVAL_SECONDS = 60


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
    max_tier: int = 1           # Security tier for this job
    enabled: bool = True
    created_at: str = ""
    last_run: str = ""
    next_run: str = ""
    retry_count: int = 0


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

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    schedule_type TEXT NOT NULL CHECK(schedule_type IN ('cron', 'interval', 'once')),
                    schedule_expr TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    max_tier INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                    last_run TEXT,
                    next_run TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()

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
        )

    def add(
        self,
        name: str,
        schedule_type: str,
        schedule_expr: str,
        payload: dict[str, Any],
        max_tier: int = 1,
    ) -> Job:
        """Add a new job. Returns the created Job with its ID."""
        now = _now_iso()
        next_run = _calculate_next_run(schedule_type, schedule_expr, now)
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO jobs (name, schedule_type, schedule_expr, payload, max_tier, next_run)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name, schedule_type, schedule_expr, json.dumps(payload), max_tier, next_run),
            )
            conn.commit()
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
        max_tier: int = 1,
    ) -> Job | None:
        """Update a job in place.

        Keeps job_id, enabled, last_run and created_at (a delete+add would
        lose them and — worse — lose the job entirely if the add fails
        after the delete). retry_count resets like a fresh add.
        """
        next_run = _calculate_next_run(schedule_type, schedule_expr, _now_iso())
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE jobs SET name = ?, schedule_type = ?, schedule_expr = ?,
                       payload = ?, max_tier = ?, next_run = ?, retry_count = 0
                   WHERE job_id = ?""",
                (name, schedule_type, schedule_expr, json.dumps(payload),
                 max_tier, next_run, job_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
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
            return

        next_run = _calculate_next_run(job.schedule_type, job.schedule_expr, now)
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET last_run = ?, next_run = ?, retry_count = 0 WHERE job_id = ?",
                (now, next_run, job_id),
            )
            conn.commit()

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
            return

        # Recurring job: advance next_run so it doesn't retry every minute
        next_run = _calculate_next_run(job.schedule_type, job.schedule_expr, now)
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET retry_count = retry_count + 1, next_run = ? WHERE job_id = ?",
                (next_run, job_id),
            )
            conn.commit()

    def delete(self, job_id: int) -> bool:
        """Delete a job. Returns True if deleted."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            conn.commit()
        return cursor.rowcount > 0

    def enable(self, job_id: int, enabled: bool = True) -> None:
        """Enable or disable a job."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET enabled = ? WHERE job_id = ?",
                (1 if enabled else 0, job_id),
            )
            conn.commit()


# ============================================================
# SCHEDULING HELPERS
# ============================================================

def _now_iso() -> str:
    """Current local time as ISO string."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _calculate_next_run(schedule_type: str, schedule_expr: str, after: str) -> str | None:
    """Calculate next run time based on schedule type.

    Args:
        schedule_type: "cron", "interval", or "once"
        schedule_expr: Cron expression, seconds (int), or ISO timestamp
        after: Calculate next run after this ISO timestamp
    """
    if schedule_type == "once":
        return schedule_expr  # ISO timestamp — run at that time

    if schedule_type == "interval":
        seconds = int(schedule_expr)
        base = datetime.fromisoformat(after)
        next_dt = base + __import__("datetime").timedelta(seconds=seconds)
        return str(next_dt.strftime("%Y-%m-%dT%H:%M:%S"))

    if schedule_type == "cron":
        return _next_cron_run(schedule_expr, after)

    return None


def _next_cron_run(cron_expr: str, after: str) -> str | None:
    """Calculate next cron run time.

    Uses croniter if available, otherwise falls back to simple interval.
    """
    try:
        from croniter import croniter
        base = datetime.fromisoformat(after)
        cron = croniter(cron_expr, base)
        next_dt = cron.get_next(datetime)
        return str(next_dt.strftime("%Y-%m-%dT%H:%M:%S"))
    except ImportError:
        logger.warning("croniter not installed — cron jobs will use 1h fallback interval")
        base = datetime.fromisoformat(after)
        next_dt = base + __import__("datetime").timedelta(hours=1)
        return str(next_dt.strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception as exc:
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
                except Exception as exc:
                    store.mark_failed(job.job_id)
                    log_message(f"Scheduler: job '{job.name}' failed: {exc}", "error")

        except asyncio.CancelledError:
            log_message("Scheduler: shutting down")
            return
        except Exception as exc:
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
    from .config import MESSAGE_HUB_OWNER
    from .envelope import InboundMessage
    from .message_processor import process_inbound

    message_text = job.payload.get("message", "")
    agent = job.payload.get("agent", "aifred")

    if not message_text:
        log_message(f"Scheduler: job '{job.name}' has no message, skipping", "warning")
        return

    # Fresh channel_id per run → routing_table allocates a new session,
    # so every job execution lives in its own conversation (no history
    # bleed between daily runs).
    msg = InboundMessage(
        channel="scheduler",
        channel_id=secrets.token_hex(8),
        sender=MESSAGE_HUB_OWNER,
        text=message_text,
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
        },
        target_agent=agent,
    )

    outbound = await process_inbound(msg)

    if outbound is None or not outbound.text:
        raise RuntimeError(f"Engine returned no response for job '{job.name}'")

    session_id = outbound.metadata.get("session_id", "")
    if not session_id:
        raise RuntimeError(f"process_inbound returned no session_id for job '{job.name}'")

    await _deliver_result(job, outbound.text, session_id)


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
        await _deliver_announce(job, response_text)
    elif delivery == "webhook":
        await _deliver_webhook(job, response_text)


async def _deliver_announce(job: Job, response_text: str) -> None:
    """Send result to a channel via the shared autonomous-delivery SSoT
    (``message_processor.announce_to_channel`` — same path the alert pipeline
    uses; recipient resolution + allowlist fallback live there)."""
    channel_name = job.payload.get("channel", "")
    if not channel_name:
        log_message(f"Scheduler: job '{job.name}' announce has no channel configured", "warning")
        return
    from .message_processor import announce_to_channel
    ok = await announce_to_channel(
        channel_name,
        job.payload.get("recipient", ""),
        response_text,
        metadata=job.payload.get("metadata", {}),
    )
    if not ok:
        log_message(f"Scheduler: announce for job '{job.name}' did not deliver", "warning")


def _deliver_review(job: Job, response_text: str, session_id: str) -> None:
    """Write notification for UI review."""
    from .message_processor import write_hub_notification
    write_hub_notification(
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
        async with aiohttp.ClientSession() as session:
            # allow_redirects=False: validate_external_url only vetted THIS url.
            # Following a 302 would let a public host redirect us to an internal
            # address (cloud metadata, llama-swap, ChromaDB) — SSRF. The webhook
            # is fire-and-forget, so there's no need to follow redirects anyway.
            async with session.post(
                url, json=payload, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                log_message(f"Scheduler: webhook POST to {url} → {resp.status}")
    except Exception as exc:
        log_message(f"Scheduler: webhook POST to {url} failed: {exc}", "error")
