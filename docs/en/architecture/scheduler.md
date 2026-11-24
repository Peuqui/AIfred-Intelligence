# Scheduler & Proactive Features

> **Deutsche Version:** [scheduler.md](../../de/architecture/scheduler.md)

**Date:** 2026-11-24

The plugin is called **Horarium** everywhere (`plugins/tools/horarium`, tools `horarium_*`); the engine behind it lives in `lib/scheduler.py`.

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
                    │   review/announce/  │
                    │   webhook           │
                    └─────────────────────┘
```

---

## Job Types

| Type | Expression | Description |
|------|-----------|-------------|
| `cron` | `0 8 * * *` | Classic 5-field cron by Vixie rules (`lib/cron.py`), local time |
| `interval` | `300` | Fixed intervals in seconds |
| `once` | `2026-04-01T10:00:00` | Single execution at ISO timestamp |

### Cron Examples

| Expression | Meaning |
|-----------|---------|
| `0 8 * * *` | Daily at 8:00 |
| `*/30 * * * *` | Every 30 minutes |
| `0 9 * * 1-5` | Weekdays at 9:00 |
| `0 0 1 * *` | Monthly on the 1st at midnight |
| `0 8,14,20 * * *` | Three times a day (8, 14, 20): ONE job with a list of hours |
| `0 */4 * * *` | Every 4 hours |

### End of a schedule

`cron` and `interval` jobs can have an end (`ends_at`, optional): a day `YYYY-MM-DD` (that day still runs until the
evening) or a moment `YYYY-MM-DDTHH:MM` (no run after it). If the next run lies after the end, the job is switched off
after its last run (`enabled = 0`, like a one-shot job; history and settings stay). An end before the first run is refused
on add and change, and an ended job can only be switched on again after a later end is set. `once` has no end. The
format is checked by `parse_ends_at` in `lib/scheduler.py`.

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
    retry_count INTEGER,
    ends_at TEXT                     -- NULL = no end; YYYY-MM-DD or YYYY-MM-DDTHH:MM
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

Optionally the plan fields `frame`, `parts`, `columns` and `trend` (see "Jobs with sub-tasks").

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
| `review` | UI notification, user reviews in session (default) |
| `announce` | Send to a channel (Discord, Telegram, Email) or read out loud on the FreeEcho pucks |
| `webhook` | HTTP POST to external URL (e.g. Home Assistant) |

The notification is sent in every mode; `announce` and `webhook` deliver
in addition.

### A job run

- **Job prompt:** `build_job_prompt()` builds the message to the agent from
  `prompts/<lang>/scheduler/`: job name, where the answer goes
  (`delivery_<mode>.txt`), the previous runs and the task. For a job with sub-tasks the
  results of the parts are added (see "Jobs with sub-tasks"); a run then consists of the parts and the closing
  turn, for every other job of exactly this one prompt.
- **History per job:** table `job_runs` keeps what each run delivered,
  independent of the sessions and only for the newest `SCHEDULER_HISTORY_RUNS`
  runs per job. Exactly those go into the prompt as excerpts so the job does
  not repeat itself: whole sentences up to about
  `SCHEDULER_HISTORY_EXCERPT_CHARS` characters, never cut mid-sentence, with
  "…" when shortened (`history_excerpt`). Whatever a job must keep exactly, the
  job prompt tells it to store in memory. Each run still has its own session.
- **The whole answer:** delivered and recorded is the answer as the browser
  shows it, without thinking — the same `outbound.text` every channel reply
  gets. Until 2026-10-04 only the text after the last tool call went out; when
  the agent wrote the message first and updated its memory afterwards, only
  the closing sentence arrived. The job prompt keeps working notes out of the
  delivery, and the own delivery channel has no sending action (the notes of 09/15 came from
  the agent's own sending attempts). A run without text fails.
- **No double delivery:** a scheduler run lacks only the sending tool of its own
  delivery channel (`Tool.outbound_channel`, `security.may_send_outbound`, the
  delivery is in the metadata under `DELIVERY_CHANNEL_KEY`), and the `email` tool
  its `send` action when delivery is by mail. This also holds for the run's
  sub-agents. The other channels are open to the agent, but the job prompt allows
  them only if the task explicitly asks for it.
- **Memory:** jobs run as the owner and may write memories although their
  tier is `TIER_COMMUNICATE` (see security.md, memory for the owner only).
- **Agent settings:** thinking, reasoning effort and sampling come from the
  main window's agent settings.

### announce

The job name becomes the subject (on channels that have one, i.e. email).
Requires `channel` and optionally `recipient` in payload:
```json
{"delivery": "announce", "channel": "discord", "recipient": ""}
```

On `freeecho2` the answer is read out loud in the rooms, on the same path as the
`freeecho2_announce` tool and the announce API (`resolve_announce_targets`,
`announce_into_rooms`). `recipient` is a room, `@group` or `*`; without one all
connected pucks are reached. `audio_type` picks the sound before it
(`notification`, `alarm`); it speaks as the job's agent. The job prompt tells the
agent to write for listening.

**Fixed text:** With `"fixed_text": true` no model starts, `message` goes out word
for word at the exact time (`_deliver_fixed_text`), on any channel, without a card
or a session. For alarms and plain reminders. If the text does not arrive, the job
fails. A job with a model needs its model start first (about 5 minutes when cold),
so for a fixed time it must be scheduled earlier.

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

## Jobs with sub-tasks

A large task (many positions, several independent groups) does not fit one prompt: the model drops positions silently
or copies values from earlier runs. So a job can carry a plan. The code directs the run, not the model; the control
flow is the plan engine, which the chat tool `run_task_plan` also uses and which is described in
[task plans](task-plan.md). A job without `parts` stays a run with exactly one prompt, with no special path.

### Payload fields

Besides `message` (for a plan, the closing task) the payload has four optional fields, all in `lib/task_plan.py`
(`TaskPlan`, `PLAN_KEYS`):

- `frame`: text that applies to all parts (what it is about, what a good finding is, what to avoid). Sources named
  there are starting points, not limits.
- `parts`: list of sub-tasks with `id`, `title`, `task` (complete, the part does not see the conversation) and
  `positions`, a list of `id` and `name`. Every position gets at least one entry during the run.
- `columns`: this job's archive columns, each with `name` and `type` (`text`, `zahl` or `link`), for values compared
  across runs (e.g. shop, price, availability).
- `trend`: `{"value": "<name of a column of type zahl>"}` for the trend table.

`job_problem` checks the payload before it is stored and calls `plan_problem` for any of these fields, the one check for
the Horarium and the chat tool: shape of the fields, unique part, position and column names, no part without positions,
the position id `zusatz` is reserved (finds outside the list), column names must not be `position_id`, `status`,
`befund`, `quelle` or `neue_quellen`, known column types, `trend.value` must be a `zahl` column, and parts exclude
`fixed_text`. The plan only runs when `parts` is not empty.

### Flow of a run

For a job with parts, `_execute_job` first starts `_run_plan_parts`, then the closing turn.

1. **Session in advance.** The run gets a fresh `channel_id` like any other; `_run_plan_parts` creates the session at once
   through `message_processor.session_for_channel("scheduler", channel_id)`, so the parts work in the same session the
   closing turn later finds through its route.
2. **Parts one after the other.** For each part the plan engine starts a sub-agent with fresh context (frame and part,
   plus the history from the archive and, if the job keeps notes, the pointer to the notes file). It runs as the job's
   agent, without persona, with the job's tiers but without sending: all tiers up to the job's `max_tier` except
   `TIER_COMMUNICATE` are allowed. The sub-agent plugin's setting "allowed tiers" does not apply here. Only the Horarium
   delivers.
3. **Entries and read check.** The part reports its results through the tool `plan_record`. An entry with status
   `gefunden` is accepted only if its source was really retrieved in this run.
4. **Exactly one reminder.** If positions remain without an entry, the same sub-agent gets the list of those still open
   once. What is missing after that counts as "not checked".
5. **Part failure.** If a part throws an exception, it appears in the result with its reason and the next parts run on.
   An exception outside the parts (archive, notes) fails the whole run; an engine that ends without a result is an error
   too.
6. **Transcripts.** The transcripts of all parts appear as one bubble in the session's chat history only
   (`record_chat_only_bubble`, title from `scheduler/plan_transcripts_title`); the model never sees it, `llm_history` is
   untouched.
7. **Archive.** The code appends all entries of the run to `plan_archiv.csv` in the job's folder (see below). The model
   never touches the file.
8. **Notes.** New sources the parts reported (`neue_quellen`) are appended by the code as a dated section to the job's
   notes (only if the job keeps notes, `_keeps_notes`).
9. **Closing turn.** The closing turn is the usual run through `process_inbound`. Its prompt is the job prompt
   (`build_job_prompt`) plus the block from `scheduler/plan_results.txt`: the results of all parts (`render_results`,
   including failed parts, positions not checked, extra finds and reports) and the trend table. The model is to name
   failed parts and unchecked positions and invent nothing.
10. **Count line.** What is delivered and logged is the model's answer, followed by the fixed line from `coverage_line`
    ("N of M positions checked", with a list for gaps and with the reason for failed parts). The code appends the
    line, not the model. Extra finds do not count towards completeness.

A part can only delegate further where the recursion depth of the sub-agent plugin allows it (see
[sub-agents](subagent-plugin.md)).

### Archive and history

`plan_archiv.csv` lies in the job's folder (`job_notes.sync`) and is written only by the code and only appended
(`lib/plan_archive.py`). One row per entry with the fixed columns `zeitpunkt`, `teil`, `position`, `status`, `befund`,
`quelle` followed by the job's columns in the plan's order; for each position without an entry a row with status
`nicht_geprueft` (its finding is the reason of the part's failure, if there was one). The `csv` module does all writing
and reading, so commas, quotes and line breaks in a finding survive. When the plan gains a column, the file is rewritten
once with a widened header and old rows stay empty there; a dropped column stays in the header.

The history comes from the archive: `last_entries` gives per position the entries of the last run in which it had any
(`nicht_geprueft` does not count). Each part gets them as a pointer, not as values to copy. Within a run, parts do not
see each other.

### Trend table instead of charts

If the job has `trend`, the code computes a Markdown table from the archive after appending the run (`trend_table`) and
hands it to the closing turn. Per position: the best value of the last run (smallest value of the trend column among the
`gefunden` entries) with its source, the best value of the run before, the best value of all runs with time and source,
and an arrow (↓ smaller, → equal, ↑ larger than in the previous run). There are no charts and no images. Without
`trend`, or without archive rows, the table is empty and left out of the prompt.

### Lock against self-modification

A running job does not change its own job: from a run (`source == "scheduler"`, `job_id` in the metadata)
`horarium_update` and `horarium_delete` refuse the own job with the error message `own_job_locked`. The run writes change
proposals into its report; the user approves them in the chat. Other jobs are not locked.

### Editing in the chat

`horarium_create` takes `frame`, `parts`, `columns` and `trend`, fields from the same schema as the chat tool
(`plan_parameters_schema`). `horarium_update` replaces `frame` and `columns` wholly, sets `trend` (an empty `value`
removes it) and changes parts precisely: `part_add` appends a part, `part_update` replaces the part with the same `id`
(the model sends it back with just the wanted change), `part_remove` removes a part by `id`. The operations run in a
fixed order (frame, columns, trend, remove, update, add); the finished payload is checked as a whole only afterwards,
and with an invalid payload or an unknown or duplicate part id the job stays unchanged. `horarium_list` shows a job's
plan completely (frame, parts with task and positions, columns, trend).

---

## Run now (immediate run)

The Horarium page can start a job immediately, outside its schedule: `POST /api/jobs/{job_id}/run`
(`api/jobs.py`, the "Run now" button). The run takes the same path as the loop's (`_execute_job`), but `run_job_now`
does NOT call `mark_executed`/`mark_failed`: the appointments are fixed — the plan's `next_run`, `last_run` and
on/off state stay as they are (the next regular run comes at its own appointment, a one-shot job is not consumed by
the trial run). The run appears in the run log (`add_run`) and in the log; success and failure are logged only. The
answer comes at once (202 with `job_id` and `name`), the run continues as a FastAPI BackgroundTask on the server's
loop — not on the hub's (the same pattern as `/api/agent/trigger`, whose execution runs there too). Refused (409)
while the plugin is off (`require_plugin_enabled`) or the job is already running; 404 for an unknown job;
switched-off jobs run, because the button is the explicit wish.

The double-start lock across threads (hub loop and server loop): `_running_job_ids` is a dict (job_id → marker of
its holder). `claim_job` checks and takes the claim in ONE step through `dict.setdefault` (atomic under the GIL:
True only when one's own marker comes back), `release_job` gives it back. The loop claims BEFORE it schedules the
task (skips on failure), the endpoint claims within its own turn (no await in between); `_run_due_job` and
`run_job_now` release in the finally.

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
    "sender": "Home Assistant",
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
- `sender` is mandatory (422 without it) and display only: `🪝 Webhook · <sender>` in the bubble. Webhook and scheduler are always owner-level triggers, `max_tier` limits the rights
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
| `aifred/lib/scheduler.py` | Job store, scheduler loop, delivery modes, flow of a job with parts (`_run_plan_parts`) |
| `aifred/lib/task_plan.py` | Plan data model and the one check (`plan_problem`) |
| `aifred/lib/plan_engine.py`, `plan_record.py`, `plan_archive.py` | Plan engine, recording tool, archive and trend table: [task plans](task-plan.md) |
| `prompts/<lang>/scheduler/plan_*.txt` | Texts of the plan flow (result block, trend, notes pointer, new sources) |
| `aifred/lib/message_hub.py` | Worker management with auto-restart |
| `aifred/lib/api/agents.py` | Webhook API endpoint (`/api/agent/trigger`) |
| `aifred/lib/credential_broker.py` | WEBHOOK_API_TOKEN mapping |
| `aifred/aifred.py` | Scheduler registration at app startup |
| `data/scheduler/jobs.db` | SQLite job database |
