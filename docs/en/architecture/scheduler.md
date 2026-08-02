# Scheduler & Proactive Features

**Date:** 2026-03-29

AIfred can act autonomously on a schedule — without a user sending a message.
Jobs run in isolated sessions with security tier enforcement.

---

## Architecture

```
                    ┌─────────────────────────────────┐
                    │          Message Hub             │
                    │   (Background Worker Manager)    │
                    └──────────┬──────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
   Channel Workers      Scheduler Worker     Webhook API
   (Email, Discord)     (checks every min)   (/api/agent/trigger)
          │                    │                    │
          └────────────────────┼────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Isolated Session   │
                    │  (sched_ID_random)  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   AIfred Engine     │
                    │   (with max_tier +  │
                    │    source="cron")   │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Delivery Mode     │
                    │   log/announce/     │
                    │   review/webhook    │
                    └─────────────────────┘
```

---

## Job Types

| Type | Expression | Description |
|------|-----------|-------------|
| `cron` | `0 8 * * *` | Standard 5-field cron (via croniter), with timezone |
| `interval` | `300` | Fixed intervals in seconds |
| `once` | `2026-04-01T10:00:00` | Single execution at ISO timestamp |

### Cron Examples

| Expression | Meaning |
|-----------|---------|
| `0 8 * * *` | Daily at 8:00 |
| `*/30 * * * *` | Every 30 minutes |
| `0 9 * * 1-5` | Weekdays at 9:00 |
| `0 0 1 * *` | Monthly on the 1st at midnight |

---

## Job Store (SQLite)

Persisted in `data/scheduler/jobs.db`. Jobs survive restarts.

```sql
CREATE TABLE jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    schedule_type TEXT NOT NULL,     -- 'cron', 'interval', 'once'
    schedule_expr TEXT NOT NULL,     -- Cron expr, seconds, or ISO timestamp
    payload TEXT NOT NULL,           -- JSON: message, agent, delivery, channel, ...
    max_tier INTEGER NOT NULL,       -- Security tier (see security-architecture.md)
    enabled INTEGER NOT NULL,
    created_at TEXT,
    last_run TEXT,
    next_run TEXT,
    retry_count INTEGER
);
```

### Job Payload

```json
{
    "message": "Summarize my unread emails",
    "agent": "aifred",
    "delivery": "announce",
    "channel": "discord",
    "recipient": "",
    "webhook_url": "",
    "metadata": {}
}
```

### API

```python
from aifred.lib.scheduler import get_job_store

store = get_job_store()

# Add job
job = store.add(
    name="morning_summary",
    schedule_type="cron",
    schedule_expr="0 8 * * *",
    payload={"message": "Summarize my emails", "delivery": "announce", "channel": "discord"},
    max_tier=1,
)

# List jobs
all_jobs = store.list_all()
active_jobs = store.list_all(enabled_only=True)

# Delete job
store.delete(job.job_id)

# Enable/disable
store.enable(job.job_id, enabled=False)
```

---

## Delivery Modes

Where does the job result go?

| Mode | Description |
|------|-------------|
| `log` | Audit log and session only (default) |
| `announce` | Send to a channel (Discord, Telegram, Email) |
| `review` | UI notification, user reviews in session |
| `webhook` | HTTP POST to external URL (e.g. Home Assistant) |

### announce

Requires `channel` and optionally `recipient` in payload:
```json
{"delivery": "announce", "channel": "discord", "recipient": ""}
```

### webhook

Requires `webhook_url` in payload:
```json
{"delivery": "webhook", "webhook_url": "http://homeassistant.local:8123/api/webhook/aifred"}
```

POST body:
```json
{
    "job_name": "morning_summary",
    "job_id": 1,
    "result": "You have 3 unread emails...",
    "timestamp": "2026-03-30T08:00:05"
}
```

---

## Webhook API (External Triggers)

External systems can trigger AIfred actions:

```
POST /api/agent/trigger
```

### Auth

Token-based via credential broker (`WEBHOOK_API_TOKEN` in `.env`).

### Request

```json
{
    "message": "What's the weather today?",
    "agent": "aifred",
    "token": "your-secret-token",
    "max_tier": 0,
    "delivery": "webhook",
    "webhook_url": "http://homeassistant.local:8123/api/webhook/result"
}
```

### Response

```json
{
    "success": true,
    "session_id": "webhook_a1b2c3d4",
    "message": "Agent triggered, running in background"
}
```

### Security

- Token checked against `WEBHOOK_API_TOKEN` (via broker, never in LLM context)
- `max_tier` capped at `DEFAULT_TIER_BY_SOURCE["webhook"]` (default: 0 = read-only)
- Each trigger gets an isolated session
- Rate limiting applies (from security layer S6)

---

## Worker Auto-Restart

All workers (channel listeners, scheduler) auto-restart on crash:

- **Exponential backoff:** 5s → 10s → 20s → 40s → 80s → max 300s
- **Max 5 attempts**, then permanent stop
- **UI notification** on permanent death (no silent failure)
- **Health API:** `message_hub.health()` returns running status + restart_count

---

## Files

| File | Purpose |
|------|---------|
| `aifred/lib/scheduler.py` | Job store, scheduler loop, delivery modes |
| `aifred/lib/message_hub.py` | Worker management with auto-restart |
| `aifred/lib/api/agents.py` | Webhook API endpoint (`/api/agent/trigger`) |
| `aifred/lib/credential_broker.py` | WEBHOOK_API_TOKEN mapping |
| `aifred/aifred.py` | Scheduler registration at app startup |
| `data/scheduler/jobs.db` | SQLite job database |
