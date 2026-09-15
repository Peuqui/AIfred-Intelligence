# Scheduler Plugin

**File:** `aifred/plugins/tools/scheduler_tool/`

Scheduled tasks and cron jobs that AIfred executes automatically at defined times.
At the scheduled moment the job's `message` is processed like a normal prompt and the
result is handed to the configured delivery mode.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `scheduler_create` | Create a new scheduled job | WRITE_DATA |
| `scheduler_list` | List all scheduled jobs with status, next run, and delivery mode | READONLY |
| `scheduler_delete` | Delete a scheduled job by its ID | WRITE_DATA |

## `scheduler_create` parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `name` | yes | Short descriptive name for the job |
| `schedule_type` | yes | One of `cron`, `interval`, `once` |
| `schedule_expr` | yes | Cron expression, interval in seconds, or ISO timestamp |
| `message` | yes | The prompt AIfred processes at the scheduled time |
| `agent` | no | Agent to use (default: `aifred`) |
| `delivery` | no | `review`, `announce`, `webhook` (default: `review`) |
| `channel` | no | Target channel for `announce` (e.g. `telegram`, `discord`, `email`) |
| `recipient` | no | Recipient for `announce` (email address, etc.) |
| `webhook_url` | no | URL for `webhook` delivery |

## Features

- **Three schedule types:** `cron` (cron expression, e.g. `0 8 * * *` = daily 8am), `interval` (seconds, e.g. `3600` = every hour), `once` (ISO timestamp, e.g. `2026-03-30T10:00:00`)
- **Delivery modes:** `review` (default, show in UI), `announce` (send to a channel), `webhook` (HTTP POST); only the final answer is delivered, the agent sends nothing itself
- **History per job:** each run sees what the last runs delivered
- **Tier capping:** Jobs run at `TIER_COMMUNICATE`, not at the creating user's tier; they may still write memories because they belong to the owner
- **Isolated execution:** Each job runs from its own stored payload
- **Persistent:** Jobs are stored via the job store and survive service restarts

## Usage examples

Spoken/chat requests AIfred maps onto the tools:

- "Summarise my e-mails every morning at 7 and send it to Telegram"
  → `scheduler_create(name="Morning mail digest", schedule_type="cron", schedule_expr="0 7 * * *", message="Summarise my new e-mails", delivery="announce", channel="telegram")`
- "Remind me tomorrow at 10 about the doctor's appointment"
  → `scheduler_create(name="Doctor reminder", schedule_type="once", schedule_expr="2026-03-31T10:00:00", message="Remind me about the doctor's appointment", delivery="review")`
- "Show me my scheduled jobs" → `scheduler_list()`
- "Delete job 3" → `scheduler_delete(job_id=3)`
