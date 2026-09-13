# Sub-Agents as a Plugin

Architecture design, as of 2026-09-13. All decisions are made, the
implementation is pending. This page describes what will be built and why;
the user guide follows with the implementation under
`docs/en/guides/plugins/subagent.md`. German version:
[docs/de/architecture/subagent-plugin.md](../../de/architecture/subagent-plugin.md).

## Goal

A main agent (AIfred, Codine, any agent in `data/agents.json`) can, during
its tool loop, delegate a self-contained task to a sub-agent. The sub-agent
is a fresh model call with its own context window, its own toolkit and its
own tool loop. Only its final report comes back, as an ordinary tool result.
The inner run stays hidden from the main model and is shown to the user as a
collapsible block inside the chat bubble.

The whole thing is a plugin like Calculator or Workspace: a directory under
`aifred/plugins/tools/`, switchable in the plugin modal, no special
architecture. The guiding pattern is the manager-worker scheme from the
GVS5H paper: planning at the top, sub-tasks with fresh context below, shared
memory through files in the workspace.

## What AIfred already has and what the design reuses

| Building block | Where | Role in the design |
|---|---|---|
| Plugin protocol `ToolPlugin`, `PluginContext` | `aifred/lib/plugin_base.py` | The plugin is an ordinary tool plugin; the context supplies agent, language, session, state, `max_tier`, `source` |
| Per-agent toolkit `prepare_agent_toolkit` | `aifred/lib/agent_memory.py` | Builds the sub-agent's toolkit, including tier filter and agent whitelist |
| Complete agent turn without Reflex state `call_llm` | `aifred/lib/llm_engine.py` | Runs the sub-agent with `external_toolkit`, exactly as Message Hub and Scheduler do today |
| Tool loop with guards | `aifred/backends/base.py`, `aifred/lib/function_calling.py` | The sub-agent gets them for free: chain depth, loop breaker, context guard, forced final round |
| Async-generator executor with `progress` events | `function_calling.py`, precedent `research` | The delegation tool streams progress into the status line without blocking the report |
| Collapsible blocks | `aifred/lib/formatting.py`, `llm_pipeline.PipelineResult` | The inner run is attached to the bubble as HTML next to the text, like the research sources |
| Token estimate ignores `<details>` | `aifred/lib/context_manager.py` | The run block costs nothing in the history and never reaches the model |
| Prompt layers and plugin instructions | `aifred/lib/prompt_loader.py`, `plugin_base.load_plugin_instructions` | The sub-agent gets a lean prompt made of frame, tool instructions and `disciplines`; the caller gets instructions on when to delegate and what belongs in the handover |
| Debug bus with `session_scope` | `aifred/lib/debug_bus.py` | The sub-agent's debug lines land in the same session; ContextVar-based, hence nesting-safe |

Newly built are only the plugin itself, a generic frame prompt in both
languages, and a small, generic pipeline extension so that a tool can
deliver a collapsible block.

## The plugin

Directory `aifred/plugins/tools/subagent/`, name `subagent`, one tool
`delegate_task`.

**Tool parameters**

| Parameter | Required | Meaning |
|---|---|---|
| `task` | yes | The complete task with all context the sub-agent needs. It sees neither the conversation nor the memory. |
| `expected_result` | yes | What the report must contain so the caller can continue |
| `agent` | no | Only when the plugin setting "delegation to other agents" is on: agent id whose model, tuning and tool list the sub-agent gets, still without persona. The allowed values are derived on every turn while the toolkit is built: only agents that differ from the caller in model or whitelist. If there is none, the parameter is absent from the schema. Default: the caller itself |

Without a persona, a sub-agent "after Codine" differs from one "after
AIfred" only in model, tuning and tool list. The parameter therefore makes
sense exactly when agents run different models, say Codine on a coder model,
and AIfred is to hand a programming task there without the user switching
agents. AIfred allows that, so the parameter exists, but behind a switch
that defaults to off: with the switch off, `agent` is not in the tool schema
and the model cannot use it in the first place.

When AIfred calls Codine this way, Codine is treated like any other
sub-agent: no persona, no conversation, no memory, only model, tuning and
tool list. No Codine bubble and no exchange appear in the chat; AIfred stays
the user's counterpart, Codine's report goes to AIfred as a tool result and
her transcript into the collapsible block of AIfred's answer. This is
division of labour in the background and deliberately not the Symposion,
where personas discuss as participants of their own with their own bubbles.

Tool tier: `TIER_READONLY`. The sub-agent's toolkit inherits the caller's
`source` and `max_tier` and is additionally filtered by the plugin setting
"allowed tiers" (see below). A Telegram channel that gets no write tools
today does not get them through a sub-agent either.

**Flow of one call**

1. The executor builds the sub-agent's toolkit through
   `prepare_agent_toolkit` with the caller's agent id (or, with the switch on
   and `agent` set, that of the chosen agent), memory off. `delegate_task`
   and every tool whose tier is not in the setting "allowed tiers" are
   removed from the kit. Without `delegate_task`, the recursion depth is
   structurally 1, with no counter and no lock. The setting "recursion
   depth" (default 1) allows more later; the current depth then travels as a
   field in `PluginContext.metadata`.
2. The sub-agent's system prompt, deliberately lean and without persona: the
   frame from `prompts/<lang>/shared/subagent_frame.txt`, the tool
   instructions of the plugins it actually gets, and the shared layer
   `disciplines`. No identity, no personality, no reminder, no memory
   context. The frame says: you are a sub-agent, you have no access to the
   conversation or the memory, you ask no questions back, you end with a
   report containing exactly what `expected_result` asks for, and you do not
   delegate further.
3. The sub-agent's user message: `task`. History: empty. This is the core of
   the context relief, and it forces the caller into a complete handover.
   The instructions to the caller (`_intro.txt`) say exactly that.
4. Execution through `call_llm` with `external_toolkit`; model, sampling and
   thinking from the tuning of the caller or of the chosen agent, as in any
   of its turns.
5. During execution the executor yields `progress` events: which tools the
   sub-agent is calling right now. The status line therefore shows "Codine
   (sub-agent) calls read_file", not the inner text.
6. Result: the sub-agent's final text, capped by `cap_tool_output` to the
   existing tool-output budget. It goes to the main model as a `role=tool`
   message, and the main model continues with it. No recursion, no jump
   back: the tool loop waits for the result as for any other tool.
7. The sub-agent's complete transcript, that is its thinking, its text,
   every tool call with arguments and full result, and the report, is
   delivered as a collapsible block (see below). Nothing runs under the
   radar; the price is size in the session file, not in the context.

**Budget.** No new budgeting and no switch of its own for the cap. The
sub-agent has its own `ToolKit` and with it its own chain depth and loop
breaker. Its context window is the hard limit; the backend's context guard
applies as in every turn. At the caller, the report passes through the
existing cap, which only bites if it would blow the main model's free
context. For the caller, the delegation counts as one tool call.

**Who may delegate.** Like every tool, `delegate_task` has to be in the
agent's tool list in `agents.json`. No special case in the code, not even
for Sokrates and Salomo; they are to be treated like all other agents one
day anyway.

**Plugin settings**, stored in the plugin's `settings.json` and reachable
through the gear icon in the plugin modal:

| Setting | Default | Meaning |
|---|---|---|
| Allowed tiers | 0 and 2 | Read, research, write files, sandbox. Not 1: no sending of email, Telegram, Discord. Sending stays with the main agent, which answers for it in the conversation. Not 3: no deleting. |
| Recursion depth | 1 | Whether a sub-agent may delegate itself |
| Delegation to other agents | off | Enables the `agent` parameter. No automatism: the switch does not depend on whether another agent runs a different model; the uselessness of an option is avoided per turn in the schema instead (see `agent`). A deliberate choice, because a different model means a model swap through llama-swap per delegation, minutes per call. The instructions to the caller name that cost. |

Memory is off for sub-agents and not a setting: everything the sub-agent
needs to know comes in the handover from the main agent, which has the
conversation and the memory. A sub-agent that stores memories would write
down things nobody saw in the conversation.

## The collapsible block

Today, collapsible blocks reach the bubble in three ways: the model writes a
tag, the pipeline injects a tag, or an HTML field sits next to the text in
`PipelineResult`. The third way fits, but today it is limited to sources and
sandbox.

Proposal, generic and small: a tool may emit, next to `progress` and
`result`, an event `collapsible` with title and content. `llm_pipeline`
collects these events into a new field `PipelineResult.tool_collapsibles`,
and `multi_agent` renders them with a helper in `formatting.py` next to
sources and sandbox into the bubble. The block is a `<details>`, ignored by
the token estimate, never read aloud and never sent to the model.

With that, every future tool has the same way to show a run. Through the
Message Hub (Telegram, email) the block is dropped, there is no bubble
there; the run is then in the session's debug log.

## What Codine makes of it

Codine is an ordinary agent with 37 tools today. She is not rebuilt. Given
`delegate_task` in her whitelist, she can split a task, hand sub-tasks to
workers with fresh context and her own tool list, keep the result in
workspace files and check it with the sandbox. That is the manager-worker
pattern from the paper without Codine becoming a new agent type.

## Files

| File | New or change |
|---|---|
| `aifred/plugins/tools/subagent/__init__.py` | new, plugin and executor |
| `aifred/plugins/tools/subagent/prompts/tools/delegate_task.txt` | new, tool description for the model |
| `aifred/plugins/tools/subagent/prompts/de/_intro.txt`, `en/_intro.txt` | new, instructions to the caller: when to delegate, what belongs in `task` |
| `prompts/de/shared/subagent_frame.txt`, `prompts/en/shared/subagent_frame.txt` | new, frame for the sub-agent |
| `aifred/plugins/tools/subagent/settings.json` | new, allowed tiers, recursion depth, delegation to other agents |
| `aifred/lib/llm_pipeline.py` | collect the `collapsible` event, field `tool_collapsibles` |
| `aifred/lib/formatting.py` | helper that renders `tool_collapsibles` as `<details>` |
| `aifred/lib/multi_agent.py` | attach the block next to sources and sandbox |
| `data/agents.json` | `delegate_task` in the whitelist of the desired agents |
| `tests/test_subagent_plugin.py` | new: name equals folder name, tool and tier, recursion filter, tier filter from the setting, inheritance of `max_tier` and `source`, memory off, result as report, block event; `call_llm` mocked |
| `docs/de/guides/plugins/subagent.md`, `docs/en/guides/plugins/subagent.md` | new, user guide in both languages |
| `README.md`, `README.de.md`, `docs/<lang>/guides/plugins-overview.md` | entry in the Multi-Agent System section and in the plugin overview, in the same commit as the code |

## Deliberately not in the design

- No parallel sub-agents. On single-user hardware, with one llama.cpp
  instance or tight KV memory under vLLM, parallelism gains nothing; the
  tool loop works sequentially anyway.
- No wall-clock time limit. Chain depth and context window bound the
  sub-agent. The tool loop's existing heartbeat, every twenty seconds during
  a silent tool call, covers long delegations too; nothing new needed.
- No passing of conversation or memory. The caller puts what is needed into
  `task`.
- No way to hand the inner run to the main model. It sees the report, the
  user sees everything.
- No switch for the cap, no memory setting (reasons above).

## Decided (2026-09-13)

- Every agent that gets `delegate_task` entered may delegate; no special
  case in the code.
- Sub-agent without persona, without memory; model and tools of the caller,
  or by switch those of another agent (`agent`).
- Recursion depth configurable, default 1.
- Collapsible block with the complete transcript.
- No parallel delegation, no cap switch.
- Allowed tiers default 0 and 2, configurable in the plugin setting.
- Docs: architecture page in both languages now; user guide, plugin overview
  and README entry together with the code.
