# Horarium Plugin

> **Deutsche Version:** [horarium.md](../../../de/guides/plugins/horarium.md)

**File:** `aifred/plugins/tools/horarium/`

Scheduled tasks and cron jobs that AIfred executes automatically at defined times.
At the scheduled moment the job's `message` is processed like a normal prompt and the
result is handed to the configured delivery mode.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `horarium_create` | Create a new scheduled job | WRITE_DATA |
| `horarium_list` | List all scheduled jobs with status, next run, delivery mode, and the stored prompt | READONLY |
| `horarium_update` | Change a scheduled job in place (only the fields passed; also switch on/off; add, change or remove single parts of a plan) | WRITE_DATA |
| `horarium_delete` | Delete a scheduled job by its ID | WRITE_DATA |

## `horarium_create` parameters

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
| `end_date` | no | End of a `cron`/`interval` job: `YYYY-MM-DD` (that day still runs) or `YYYY-MM-DDTHH:MM`; left out, the job runs without limit. `horarium_update` sets or removes it (empty value) |
| `frame` | no | Only for jobs with sub-tasks: the frame that applies to all parts |
| `parts` | no | The sub-tasks, each with `id`, `title`, `task` and `positions` (each `id`, `name`) |
| `columns` | no | Typed fields of the entries, each with `name` and `type` (`text`, `zahl`, `link`) |

## Features

- **Three schedule types:** `cron` (cron expression, e.g. `0 8 * * *` = daily 8am), `interval` (seconds, e.g. `3600` = every hour), `once` (ISO timestamp, e.g. `2026-03-30T10:00:00`)
- **Delivery modes:** `review` (default, show in UI), `announce` (send to a channel), `webhook` (HTTP POST); the whole answer is delivered as the browser shows it (without thinking), the agent sends nothing itself
- **History per job:** each run sees what the last runs delivered
- **Tier limit:** jobs run with the owner's tier by default (`JOB_DEFAULT_TIER`); `max_tier` at creation sets a lower one, at most the creator's own tier. Sub-tasks never send themselves (no `TIER_COMMUNICATE`); the Horarium delivers
- **Isolated execution:** Each job runs from its own stored payload
- **Persistent:** Jobs are stored via the job store and survive service restarts; what is overdue at a restart runs afterwards
- **Off means off:** The execution loop belongs to the plugin. While it is off no job starts; a job that is already running runs to its end. Appointments of the time off lapse: when the plugin is switched on, due jobs move to their next regular appointment without a run and without an entry in the run log (a one-shot job is switched off)

## Jobs with sub-tasks

A large task (many positions, several independent groups, e.g. prices of many products in several shops, anew every
week) is unreliable in a single prompt: positions drop silently, values are copied from earlier runs. So AIfred splits
such a task himself when creating it. You only say what you want; AIfred builds a frame, the parts with their positions
and, if needed, columns for values every entry records from it. Small tasks stay a single task without parts. If AIfred ran the
plan earlier in the conversation with `run_task_plan`, he takes it over unchanged as the job. Parts cannot be combined
with fixed text (`fixed_text`).

**What happens in a run:**

- The parts run one after the other, each as its own sub-agent. They search freely; sources named are starting points,
  not limits. For every position they enter an entry (found, not found, error); a find counts only with a source that
  was really retrieved. New sources they discover are appended by the Horarium to the job's notes.
- If a position is still missing after a part, the part is asked exactly once more; after that it counts as "not checked".
- If a part fails, the rest runs anyway. The message says which part failed and why.
- At the end AIfred writes the message from all results. Below it the Horarium itself appends a line such as "11 of 12
  positions checked", with the missing positions for gaps. The model does not write this line.
- The transcripts of the parts are in the history of the run's session as collapsible blocks, not in what the model reads.

**Every run stands for itself:** The Horarium keeps no archive and no trend table; the parts never see the findings of
earlier runs. Knowledge about sources lives in the job's notes (the Horarium enters new sources there). Whoever wants to
collect values across runs (e.g. prices) says so in the task: the closing turn then keeps the table itself in a file in the
job's folder.

**Editing in the chat:** Tell AIfred what should change; he changes single parts without touching the rest (add, change or
remove a part, and replace frame and columns). "Show me my jobs" lists the plan completely. If a change is
refused (e.g. a duplicate part id), the job stays unchanged.

**A job does not change itself:** A running job may neither change nor delete its own job. Improvements it notices go into
its report; you decide in the chat whether they are adopted.

Structure and flow in detail: [scheduler](../../architecture/scheduler.md#jobs-with-sub-tasks).

## Run now

On the page (the "Run now" button) a job starts immediately, outside its schedule — a switched-off job too (the button
is the explicit wish). Its schedule stays untouched: the next regular run comes at its own appointment, the on/off
state and the last-run stamp do not move, and a one-shot job is not consumed by the trial run. The run appears in the
recent-runs log. Refused with a clear message while Horarium is off or the job is already running; an unknown job gives
404.

## Usage examples

Spoken/chat requests AIfred maps onto the tools:

- "Summarise my e-mails every morning at 7 and send it to Telegram"
  → `horarium_create(name="Morning mail digest", schedule_type="cron", schedule_expr="0 7 * * *", message="Summarise my new e-mails", delivery="announce", channel="telegram")`
- "Remind me tomorrow at 10 about the doctor's appointment"
  → `horarium_create(name="Doctor reminder", schedule_type="once", schedule_expr="2026-03-31T10:00:00", message="Remind me about the doctor's appointment", delivery="review")`
- "Check the Black Week deals three times a day until the end of November"
  → `horarium_create(name="Black Week", schedule_type="cron", schedule_expr="0 8,14,20 * * *", end_date="2026-11-30", message="…")` (ONE job with a list of hours)
- "Show me my scheduled jobs" → `horarium_list()`
- "Move job 3 to 7:05" → `horarium_update(job_id=3, schedule_expr="5 7 * * *")`
- "Delete job 3" → `horarium_delete(job_id=3)`
