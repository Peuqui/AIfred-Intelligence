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
| `horarium_list` | List all scheduled jobs with status, next run, delivery mode, and the stored prompt; for a job with a plan also frame, parts (with task and positions) and columns | READONLY |
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
| `channel` | no | Target channel for `announce` (e.g. `telegram`, `discord`, `email`, `freeecho2`; on `freeecho2` the answer is read out loud in the rooms) |
| `recipient` | no | Recipient for `announce` (email address, etc.; for `freeecho2` a room, `@group` or `*` for all connected pucks, the default) |
| `webhook_url` | no | URL for `webhook` delivery |
| `fixed_text` | no | `true` = no model: `message` goes out word for word at the exact time. Needs `delivery` `announce`; excludes parts. For alarms and plain reminders |
| `audio_type` | no | For `freeecho2`: the sound before the announcement, `notification` (default, gentle chime) or `alarm` |
| `max_tier` | no | Security tier of the job (0 read only, 1 communicate, 2 write data, 3 system). Left out, your own tier, at most 2 (`JOB_DEFAULT_TIER`); you cannot grant more than you have |
| `end_date` | no | End of a `cron`/`interval` job: `YYYY-MM-DD` (that day still runs) or `YYYY-MM-DDTHH:MM`; left out, the job runs without limit. `horarium_update` sets or removes it (empty value) |
| `frame` | no | Only for jobs with sub-tasks: the frame that applies to all parts |
| `parts` | no | The sub-tasks, each with `id`, `title`, `task` and `positions` (each `id`, `name`) |
| `columns` | no | Typed fields of the entries, each with `name` and `type` (`text`, `zahl`, `link`) |

## `horarium_update` parameters

Only `job_id` is required; what is left out stays as it is. The same fields as `horarium_create` (`name`, `schedule_type`,
`schedule_expr`, `message`, `agent`, `delivery`, `channel`, `recipient`, `webhook_url`, `fixed_text`, `audio_type`,
`end_date`, `max_tier`) with these differences and additions:

| Parameter | Description |
|-----------|-------------|
| `job_id` | ID of the job (required; "41" as text is read as a number) |
| `schedule_type` | New type; then also pass `schedule_expr` |
| `enabled` | `true` switches the job on, `false` off |
| `end_date` | Sets or moves the end; an empty value removes it |
| `max_tier` | New tier; left out, it stays |
| `frame` | Replaces the plan's frame completely |
| `columns` | Replaces the columns completely (empty list = no columns) |
| `part_add` | Adds one part (`id`, `title`, `task`, `positions`); the `id` must not exist yet |
| `part_update` | Replaces the part of the same `id` completely; the `id` must exist |
| `part_remove` | `id` of a part (text) to remove; it must exist |

The operations run in the order frame, columns, remove, update, add; then the whole plan is checked, and nothing is saved
when it is wrong.

## Features

- **Three schedule types:** `cron` (cron expression, e.g. `0 8 * * *` = daily 8am), `interval` (seconds, e.g. `3600` = every hour), `once` (ISO timestamp, e.g. `2026-03-30T10:00:00`)
- **Delivery modes:** `review` (default, show in UI), `announce` (send to a channel), `webhook` (HTTP POST); the whole answer is delivered as the browser shows it (without thinking), the agent sends nothing itself
- **History per job:** the run log keeps what the last runs delivered (the jobs page shows it); the run itself learns only the times of the last runs, not the texts
- **Tier limit:** jobs run with the owner's tier by default (`JOB_DEFAULT_TIER`); `max_tier` sets another one at creation and when changing, lower or higher than the default, but at most the creator's own tier (`_job_tier`). Sub-tasks never send themselves (no `TIER_COMMUNICATE`); the Horarium delivers
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
- If a part fails, the rest runs anyway. The message says which part failed and why. A part has a time limit of 30:00 min
  (`PLAN_PART_TIMEOUT_SECONDS`, changeable through the environment); if it exceeds it, it fails with the reason "Time
  limit … exceeded", the entries made until then stay. The number of tool calls is limited per part
  (`SECURITY_MAX_TOOL_CHAIN_DEPTH`, default 100, shared by the part's run and its reminder).
- New sources a part reports (`neue_quellen`) are accepted only as single-line http(s) addresses; the Horarium enters only
  those the notes do not name yet, and only into notes that already exist.
- At the end AIfred writes the message from all results. Below it the Horarium itself appends a line such as "11 of 12
  positions checked", with the missing positions for gaps. The model does not write this line; it is also in the run's
  session, as a bubble of its own after the answer.
- If the closing turn fails after the parts, the results are not lost: they are delivered with a notice of the reason and
  the count line, are in the run log and stand as a message in the session that AIfred sees in his history (you can carry
  on there, for example write the report). The run still counts as failed. If you stop the run after the parts, the results
  are only in the session; nothing is delivered.
- The whole run (parts, closing turn, delivery) is one turn of the session: if you write there meanwhile, your message
  waits and starts afterwards; the interface shows "running". The stop cancels the run, it counts as a failure, the
  scheduler goes on.
- In the session history a bubble "Job "…" started: N parts" appears at the start, with the parts and their positions
  (collapsed), and after each part a bubble of its own: an expandable block whose title line, even collapsed, shows the
  part and its state ("N of M positions, K entries, duration", "not checked: …" for gaps, "failed: reason" for an error);
  expanded it holds the part's transcripts. Only the interface sees these bubbles, not the model.

**Every run stands for itself:** The parts never see the findings of earlier runs; the Horarium does not keep them. Knowledge about sources lives in the job's notes (the Horarium enters new sources there). Whoever wants to
collect values across runs (e.g. prices) says so in the task: the closing turn then keeps the table itself in a file in the
job's folder.

**Editing in the chat:** Tell AIfred what should change; he changes single parts without touching the rest (add, change or
remove a part, and replace frame and columns). "Show me my jobs" lists the plan completely. If a change is
refused (e.g. a duplicate part id), the job stays unchanged.

**A job does not change itself:** A running job may neither change nor delete its own job. Improvements it notices go into
its report; you decide in the chat whether they are adopted.

**On the jobs page:** In a job's form "Add sub-tasks" adds the plan block: frame, parts (ID, title, task, positions with ID
and name) and columns (name, type). With parts the message field is called "Closing": what the closing turn is to do with
the results (summarize, compare, what belongs in the message). "Fixed text" and parts exclude each other; the other switch
is greyed out, with the reason beside it. The job list shows how many parts and positions a job with a plan has. It is
saved through the jobs API (`frame`, `parts`, `columns`), under the same check as the tool.

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
