# Scheduler Plugin

**File:** `aifred/plugins/tools/scheduler_tool/`

Scheduled tasks and cron jobs that AIfred executes automatically at defined times.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `scheduler_create` | Create new scheduled job | WRITE_DATA |
| `scheduler_list` | List all scheduled jobs | READONLY |
| `scheduler_delete` | Delete scheduled job | WRITE_DATA |

## Features

- **Three schedule types:** `cron` (cron expression, e.g. `0 8 * * *`), `interval` (seconds, e.g. `3600`), `once` (ISO timestamp)
- **Delivery modes:** `log` (default), `announce` (send to channel), `review` (show in UI), `webhook` (HTTP POST)
- **Isolated sessions:** Each job runs in its own session
- **Auto-restart:** Jobs survive service restarts
