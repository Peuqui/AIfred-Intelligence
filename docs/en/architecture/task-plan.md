# Task Plans (Plan Engine)

> **Deutsche Version:** [task-plan.md](../../de/architecture/task-plan.md)

As of 2026-11-24. The plan engine splits a large task into parts, has each part worked on by its own sub-agent and makes
sure no position is lost silently. It lives in `aifred/lib/` and knows neither the Horarium nor a channel nor an archive
file; two callers use it (see below).

## Purpose

One prompt for twelve positions, research, table and report drops positions, copies values from earlier runs and does
not notice. The engine turns it into small tasks with fresh context and checks in code what the model would only
promise: every position needs an entry, a found value needs a source the sub-agent really read in this run, and what is
missing appears as "not checked" in the result.

## Building blocks

| Module | Job |
|---|---|
| `lib/task_plan.py` | data model (`TaskPlan`, `PlanPart`, `PlanPosition`, `PlanColumn`), the one check `plan_problem`, the schema of the plan fields for tools (`plan_parameters_schema`, source `prompts/tools/task_plan.params.json`) and the part operations (`parts_with_added`, `parts_with_replaced`, `parts_without`) |
| `lib/plan_record.py` | the recording tool `plan_record` (`make_record_tool`), the collected entries (`PartRecords`, `PlanEntry`) |
| `lib/plan_engine.py` | `run_plan` (parts one after the other), `render_results` (the text for a model), `coverage_line` (the fixed count line) |
| `lib/plan_archive.py` | the archive `plan_archiv.csv`, the history (`last_entries`) and the trend table (`trend_table`); only for the Horarium path |
| `lib/subagent_run.py` | `SubagentSession`, the run of a sub-agent, see [sub-agents](subagent-plugin.md) |
| `prompts/<lang>/task_plan/` | every text the model sees: part prompt, reminder, tool description and rejections of `plan_record`, result rendering, count line, trend table |

A plan has four fields: `frame` (frame for all parts), `parts` (each with `id`, `title`, `task` and the positions with
`id` and `name`), `columns` (archive columns of type `text`, `zahl` or `link`) and `trend` (the name of a `zahl`
column). The same structure is the payload of a Horarium job ([scheduler](scheduler.md#jobs-with-sub-tasks)) and the
parameters of the chat tool, so a plan from the chat can be stored unchanged as a job.

## Flow

`run_plan(plan, ctx, allowed_tiers=…, depth=…, history=…, notes_hint=…)` is an async generator. It runs the parts one
after the other, not in parallel. For each part:

1. `part_task` builds the task from `task_plan/part.txt`: frame, part, positions, archive columns with type, the history
   per position (or the note that there is none), the notes pointer and the working rules (search broadly, sources are
   starting points, enter only freshly retrieved values, enter every position at least once, extra finds allowed, close
   with a short report).
2. A `SubagentSession` with the caller's agent, without persona and with `plan_record` as an extra tool runs the part.
   The allowed tiers and the depth are the caller's.
3. If positions remain without an entry afterwards (whatever the status; `nicht_gefunden` and `fehler` are entries
   too), the engine sends exactly one reminder (`task_plan/missing.txt`) into the same run (`follow_up`). What is still
   missing then is `not_checked` in the part's outcome.
4. If a part throws an exception, it is logged and kept as the part's reason (`failure`); the further parts run. Entries
   the part had accepted before stay valid.

The events for the caller are `progress` (prefixed with the part's title), `work` and `artifacts` (the sub-agents'
transcripts) as with any tool executor, and last `{"plan_result": PlanResult}`.

## Recording tool and read check

`plan_record` (tier `TIER_READONLY`) exists only in the toolkit of a part's sub-agent. Its schema is built per part:
`position_id` is an enumeration of the part's positions plus `zusatz` (a find outside the list), `status` is `gefunden`,
`nicht_gefunden` or `fehler`, plus `befund` (text), `quelle`, one field per archive column (`zahl` as a number) and
`neue_quellen` (list of addresses the part discovered beyond the given ones). Several entries per position are allowed.
Rejections come with a reason, as prompt text in the tool result, so that the sub-agent corrects itself: unknown
position or status, a value of the wrong type (also text in a number column), an unknown column, an empty finding for
`nicht_gefunden` and `fehler`, and for `gefunden` a missing source or a source that was not retrieved in this run.

**Read check.** Every `ToolKit` instance keeps a set `read_urls`. A tool reports pages it read by having its executor
yield `{"read": [urls]}`; the tool loop (`execute_streaming`) normalizes them with `normalize_url` and puts them in the
set, they are never a result. Only the tools of the research plugin report pages: `web_fetch` reports the retrieved
address on success, `web_search` the addresses of the pages it actually read out. For an entry with status `gefunden`,
`plan_record` normalizes the given source and compares it with the set. `normalize_url` drops the scheme (http and
https count the same), lowercases the host, removes a leading `www.`, a trailing slash of the path and the fragment but
keeps the query, and it cuts the prefix of the reading proxy `https://r.jina.ai/` so that the embedded address counts.
What a tool does not report does not count: retrievals from the sandbox (a Python script that loads a page) never appear in
the set. The set belongs to the sub-agent's toolkit; the reminder uses the same one, so pages read earlier stay valid.

## Result and count line

`PlanResult` holds the plan and per part a `PartOutcome`: entries and new sources (`PartRecords`), the sub-agent's
report, the reason of a failure and the positions not checked. `new_sources` merges the new sources of all parts.

`render_results(result, lang)` is the one place for the text for a model (chat and Horarium): per part its positions
with all entries (status, finding, source, column values, numbers in the language's notation), the words "not checked"
for a gap, the reason of a failed part, the extra finds, the report and finally the new sources. `coverage_line(result,
lang)` is the fixed line "N of M positions checked", with a list "part / position" for a gap and a list with reasons for
failed parts. Extra finds do not count; positions of a failed part count as checked as far as they have entries. The
caller appends the line, the model does not write it.

## Two callers

**Horarium.** `_run_plan_parts` in `lib/scheduler.py` calls `run_plan` for a job with `parts`: with the job's tiers
without sending, the history from the archive and the notes pointer, writes the archive, computes the trend table and
then lets the closing turn run with the results. Details: [scheduler](scheduler.md#jobs-with-sub-tasks).

**Chat tool `run_task_plan`.** It belongs to the sub-agent plugin, is available on every channel that uses the plugin
(browser, Telegram, email, Discord, FreeEcho, anything that runs through `process_inbound`) and uses the plugin's tier
and depth settings. As with `delegate_task`, the model decides whether a task is large enough; the criteria are in the
tool description. The chat call has no archive, no history, no notes: the result is `render_results` plus
`coverage_line` as the tool result, which the model answers in the running turn. See [sub-agents](subagent-plugin.md).

## Deliberate limits

- **Parts do not see each other.** Within a run every part knows only the frame, its own task and the history; there
  are no agreements between parts. What is needed in common goes in the frame.
- **The check proves the retrieval, not the value.** `plan_record` knows that the sub-agent read the source, not that
  the entered value is there. An invented or misread value for a real source goes unnoticed.
- **Redirects are not followed.** The set contains the given address, not the target of a redirect; if the sub-agent
  enters the target, the entry is rejected until it has retrieved it.
- **No parallelism.** The parts run one after the other (see also "Deliberately not in the design" in
  [sub-agents](subagent-plugin.md)).
- **Only one reminder.** What is missing afterwards is reported, not chased further.
