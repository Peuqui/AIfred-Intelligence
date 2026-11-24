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
                    │  (random ID)        │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   AIfred Engine     │
                    │   (with max_tier +  │
                    │ channel="scheduler")│
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

Optionally the plan fields `frame`, `parts`, `columns` (see "Jobs with sub-tasks").

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
  runs per job; the jobs page shows them. Only the times of those runs go into
  the prompt ("Previous runs of this job (newest first): …",
  `scheduler/history.txt`, `build_job_prompt`), never the delivered texts; the
  first run gets `scheduler/history_empty.txt` there. Whatever a job must keep
  exactly across runs, the job prompt tells it to store in the notes or in
  memory. Each run has its own session.
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
- **Memory:** jobs run as the owner and may write memories; memory depends on
  the owner context, not on the job's tier (a job's default:
  `JOB_DEFAULT_TIER = TIER_WRITE_DATA`, see security.md, memory for the owner
  only).
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

Besides `message` (for a plan, the closing task) the payload has three optional fields, all in `lib/task_plan.py`
(`TaskPlan`, `PLAN_KEYS`):

- `frame`: text that applies to all parts (what it is about, what a good finding is, what to avoid). Sources named
  there are starting points, not limits.
- `parts`: list of sub-tasks with `id`, `title`, `task` (complete, the part does not see the conversation) and
  `positions`, a list of `id` and `name`. Every position gets at least one entry during the run.
- `columns`: the typed fields of this job's entries, each with `name` and `type` (`text`, `zahl` or `link`), for values
  every entry records (e.g. shop, price, availability).

`job_problem` checks the payload before it is stored and calls `plan_problem` for any of these fields, the one check for
the Horarium and the chat tool: shape of the fields, unique part, position and column names, no part without positions,
the position id `zusatz` is reserved (finds outside the list), column names must not be `position_id`, `status`,
`befund`, `quelle` or `neue_quellen`, known column types, and parts exclude
`fixed_text`. A frame or columns without parts are refused by the check (`plan_no_parts`), the run would ignore them
silently otherwise. The plan only runs when `parts` is not empty.

### Flow of a run

`_execute_job` creates the session and makes the whole run its pipeline; for a job with parts, `_run_job` first starts
`_run_plan_parts`, then the closing turn.

1. **Session and pipeline.** The run gets a fresh `channel_id` like any other; `_execute_job` creates the session at once
   through `message_processor.session_for_channel("scheduler", channel_id)`, so the parts work in the same session the
   closing turn later finds through its route, and registers the run's task with `register_pipeline` as the pipeline of
   that session. The whole run (parts, closing turn, delivery) is thus one turn of the session: a chat message in that
   session waits in the queue (`pending_messages`) and starts afterwards (`start_next_turn` at the end of the run), the
   interface shows "running" (`is_pipeline_running`), and the stop (`cancel_pipeline`) cancels the run. A cancelled run
   counts as a failure (`mark_failed`, the next appointment moves on); the scheduler loop goes on. The closing turn runs
   inline in this task and leaves the registration alone (`turn_service.run_turn`).
2. **Parts one after the other.** For each part the plan engine starts a sub-agent with fresh context (frame and part,
   plus, if the job keeps notes, the pointer to the notes file). It runs as the job's
   agent, without persona, with the job's tiers but without sending: all tiers up to the job's `max_tier` except
   `TIER_COMMUNICATE` are allowed. The sub-agent plugin's setting "allowed tiers" does not apply here. Only the Horarium
   delivers.
   Two limits apply per part. The time limit `PLAN_PART_TIMEOUT_SECONDS` (`config.py`, default 1800 s = 30:00 min,
   changeable through the environment) covers the part's run and its reminder together: after it the engine cancels the
   part, which fails with the reason "Time limit of … exceeded" (`task_plan/part_timeout.txt`), the entries made until
   then stay valid, and the next parts run on; a hanging part no longer blocks the serial loop. The chain limit
   `SECURITY_MAX_TOOL_CHAIN_DEPTH` (default 100 tool calls) counts per part: every part has its own toolkit, the run and
   the reminder of the same part share the counter, and every `plan_record` counts. Both also hold for `run_task_plan` in
   the chat.
3. **Entries and read check.** The part reports its results through the tool `plan_record`. An entry with status
   `gefunden` is accepted only if its source was really retrieved in this run.
4. **Exactly one reminder.** If positions remain without an entry, the same sub-agent gets the list of those still open
   once. What is missing after that counts as "not checked".
5. **Part failure.** If a part throws an exception or exceeds the time limit, it appears in the result with its reason
   and the next parts run on. An exception outside the parts (notes) fails the whole run; an engine that ends without a
   result is an error too.
6. **Bubbles during the run.** At the start a bubble appears: one line "Job ... started: N parts" and a collapsed block
   with the parts and their positions (`_plan_started_bubble`). After each part its own bubble follows (the engine's
   `PartDone` event, `_part_bubble_artifacts`): exactly one expandable block with no text around it. Its title line,
   visible when collapsed (phone), carries the part and its state ("Part 1/4 · title: N of M positions, K entries,
   duration", ", not checked: ..." for gaps, "failed: reason" for a failed part); expanded it holds the part's
   transcripts (a collapsible holds text only, so the transcripts are sections inside it; pages and images a sub-agent
   made follow as artifacts of their own below the block). All bubbles go through `record_chat_only_bubble` into the
   chat history only (the model never sees them, `llm_history` is untouched) and reach open tabs at once as
   `message_added`. Durations from 60 s on are shown as minutes:seconds. Order in the history: start, part 1 ... part N,
   the closing turn's request, its answer, the count line.
7. **Notes.** The code enters new sources the parts reported (`neue_quellen`) as a dated section in the job's notes (only
   if the job keeps notes, `_keeps_notes`). `plan_record` accepts only single-line http(s) addresses in `neue_quellen`
   (it rejects anything else, `record_error_new_source`). `_append_new_sources` writes only those the notes do not name
   yet (compared in the form of `normalize_url`), and only to notes that exist; a file that does not exist yet is made by
   the model at the end of its run (`scheduler/notes_new`), because a file holding nothing but addresses would count as
   "notes exist".
8. **Closing turn.** The closing turn is the usual run through `process_inbound`. Its prompt is the job prompt
   (`build_job_prompt`) plus the block from `scheduler/plan_results.txt`: the results of all parts (`render_results`,
   including failed parts, positions not checked, extra finds and reports). The model is to name
   failed parts and unchecked positions and invent nothing.
9. **Count line.** What is delivered and logged is the model's answer, followed by the fixed line from `coverage_line`
    ("N of M positions checked", with a list for gaps and with the reason for failed parts). The code appends the
    line, not the model. Extra finds do not count towards completeness. The model's answer in the session does not have
    the line, so the code puts it there as a bubble of its own after the answer (`record_chat_only_bubble`).

**If the closing turn fails** after the parts (exception, no answer), the results are not lost: `_deliver_unclosed_plan`
delivers them, as the text from `scheduler/plan_closing_failed.txt` (a notice with the reason, below it the results of
the parts) plus the count line. The text goes into the `job_runs` log (`add_run`), takes the usual delivery path
(`_deliver_result`) and stands as a message in the session that the model sees in its history (`record_autonomous_turn`);
one can carry on there, for example write the report from the results. The count line is in the delivery and the log, not
in the session message. The run still counts as failed (the exception is passed on, `mark_failed`), because the closing
step is missing. **If the run is stopped after the parts**, only the session gets a message from
`scheduler/plan_closing_cancelled.txt` with the results; nothing is delivered and there is no log entry. If it is
stopped during the parts, only the bubbles of the finished parts stay in the history, the results are lost.

A part can only delegate further where the recursion depth of the sub-agent plugin allows it (see
[sub-agents](subagent-plugin.md)).

### Every run stands for itself

A part sees only frame, task, positions, columns and the notes pointer, never the findings of earlier runs, and within a
run parts do not see each other; the Horarium does not keep findings across runs. Knowledge needed across
runs (sources that proved good) lives in the job's notes; the Horarium enters the parts' new sources there (step 7). Whoever
wants to collect values across runs (prices, say) says so in the job's task: the closing turn then keeps the table
itself in a file in the job's folder.

### Lock against self-modification

A running job does not change its own job: from a run (`source == "scheduler"`, `job_id` in the metadata)
`horarium_update` and `horarium_delete` refuse the own job with the error message `own_job_locked`. For this the tool call's `job_id` is read as a whole number
in one place (`checked_job_id`, also `"41"` counts), so the comparison with the number from the metadata cannot fail on the
spelling. The run writes change
proposals into its report; the user approves them in the chat. Other jobs are not locked.

### Editing in the chat

`horarium_create` takes `frame`, `parts`, `columns`, fields from the same schema as the chat tool
(`plan_parameters_schema`). `horarium_update` replaces `frame` and `columns` wholly and changes parts precisely: `part_add` appends a part, `part_update` replaces the part with the same `id`
(the model sends it back with just the wanted change), `part_remove` removes a part by `id`. The operations run in a
fixed order (frame, columns, remove, update, add); the finished payload is checked as a whole only afterwards,
and with an invalid payload or an unknown or duplicate part id the job stays unchanged. `horarium_list` shows a job's
plan completely (frame, parts with task and positions, columns).

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
    "session_id": "3f9c0a7e5b1d4c28a6e07b91d5f2c834",
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
| `aifred/lib/plan_engine.py`, `plan_record.py` | Plan engine, recording tool: [task plans](task-plan.md) |
| `prompts/<lang>/scheduler/plan_*.txt` | Texts of the plan flow: `plan_results` (result block), `plan_notes_hint`, `plan_new_sources_heading`, `plan_started_*` (start bubble), `plan_part_*` (a part's title line), `plan_closing_*` (closing turn failed or cancelled) |
| `aifred/lib/message_hub.py` | Worker management with auto-restart |
| `aifred/lib/api/agents.py` | Webhook API endpoint (`/api/agent/trigger`) |
| `aifred/lib/credential_broker.py` | WEBHOOK_API_TOKEN mapping |
| `aifred/plugins/tools/horarium/__init__.py` | Tools and registration of the scheduler loop (`background_parts`) |
| `data/scheduler/jobs.db` | SQLite job database |
