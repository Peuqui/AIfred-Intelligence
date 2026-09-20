# Sub-Agents as a Plugin

Architecture, as of 2026-09-20, implemented as the plugin `aifred/plugins/tools/subagent/`.
This page describes what was built and why; the user guide is at
[docs/en/guides/plugins/subagent.md](../guides/plugins/subagent.md). German version:
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
| Event pipeline `run_llm_stream` | `aifred/lib/llm_pipeline.py` | Runs the sub-agent with its own toolkit; model, context and temperature come through the same helpers as the browser turn (`multi_agent`) and the hub turn (`message_processor`). `call_llm` was not an option because it injects the persona reminder into the user message |
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
| `agent` | no | Only when the plugin setting "sub-agent as another main agent" is on: agent id the sub-agent runs as: identity and personality, model, tuning and tool list of that agent. The allowed values are derived on every turn while the toolkit is built: every main agent except the caller. Each brings its own identity and personality, so an agent with the caller's model and whitelist is a sensible choice too (until 2026-09-20 such agents were filtered out). System agents (`role: system`) are excluded. If there is none, the parameter is absent from the schema. The parameter description carries a legend, also derived per turn (`delegation_legend`): for each candidate the tool groups its sub-agent would get (plugins via `collect_plugin_tools`, the same selection rule as the toolkit factory, filtered by whitelist, tier ceiling and allowed tiers), and for each group name and description from the plugin's `i18n.json` in the language of the turn. New, changed or disabled plugins show up by themselves. Default: the caller itself |

A sub-agent "after Codine" differs from one "after AIfred" in four things:
identity and personality, model, tuning, tool list. A main agent's value
often sits in its prompt rather than in its model: Codine's personality
holds her professional role and the mandatory workflow for changing existing
code, HAL's holds the code review method. Handing over the toolkit alone
would be half the point, so a sub-agent with `agent` loads the identity and
personality of the chosen agent (changed on 2026-09-20; before that it ran
without persona). The parameter sits behind a switch that defaults to off:
with the switch off, `agent` is not in the tool schema and the model cannot
use it in the first place.

When AIfred calls Codine this way, the sub-agent works as Codine, but
without conversation and memory, without the reminder and without her task
layer for the conversation. No Codine bubble and no exchange appear in the chat; AIfred stays
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
2. The sub-agent's system prompt, deliberately lean: the frame from
   `prompts/<lang>/shared/subagent_frame.txt`, the tool instructions of the
   plugins it actually gets, and the shared layer `disciplines`. No
   reminder, no memory context. Without `agent` also no identity and no
   personality: a sub-agent of the caller itself is a worker without
   persona. With `agent`, the identity and personality of the chosen agent
   are added, through the same helpers as a normal turn (`load_identity`,
   `load_personality`; the agent's personality toggle therefore applies here
   too) and in the same layer order as `merge_prompt_layers`, with the frame
   in the task slot: identity, frame, security boundary, personality, tool
   instructions, `disciplines`. The frame says: you are a sub-agent, you have no access to the
   conversation or the memory, you ask no questions back, you end with a
   report containing exactly what `expected_result` asks for, without copying
   whole files, code or long output into it (naming the file or URL is enough), and you
   delegate further only if you hold the `delegate_task` tool (that is, only
   below the recursion depth).
3. The sub-agent's user message: `task`. History: empty. This is the core of
   the context relief, and it forces the caller into a complete handover.
   The instructions to the caller (plugin fragment `delegate_task.txt`) say exactly that.
4. Execution through `run_llm_stream` with the own toolkit; model, sampling,
   thinking and context size from the tuning of the caller or of the chosen
   agent, through the same helpers as in any of its turns (`resolve_run_params`
   in the plugin bundles the browser and the hub path).
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
| Sub-agent as another main agent | off | Enables the `agent` parameter. No automatism: the switch does not depend on whether another agent runs a different model. A deliberate choice, because a different model means a model swap through llama-swap per delegation, minutes per call. The instructions to the caller name that cost. |

Memory is off for sub-agents and not a setting: everything the sub-agent
needs to know comes in the handover from the main agent, which has the
conversation and the memory. A sub-agent that stores memories would write
down things nobody saw in the conversation.

## The collapsible block

Everything a turn's tools deliver for the bubble is a bubble artifact
(`aifred/lib/bubble.py`): sub-agent transcripts, sources, sandbox pages and
images, camera images (whole bursts too) and the VLM description.
`llm_pipeline` collects them in `PipelineResult.artifacts`, each with an anchor
at the point of the turn where its tool ran. `render_bubble` is the one place
that turns text and artifacts into the bubble in turn order; the browser path
(`multi_agent`), `call_llm` and the Message Hub use it. The hub shows no tag
blocks such as thinking or the VLM description, but all other artifacts.

A tool delivers artifacts as an `artifacts` event; the tool loop forwards it
as `tool_artifacts`. The sub-agent uses exactly this path: its transcript and
all artifacts of its own turn go to the caller as one list and appear where
the caller delegated, like the caller's own. Each camera image and sandbox
page appears once per turn.

Camera images also stay as a Markdown reference at the start of the text:
tool results are not stored, and only through that reference in the history
can a later turn analyse an image again. `render_bubble` hides the references;
the image is shown by its artifact.

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
| `aifred/plugins/tools/subagent/prompts/de/delegate_task.txt`, `en/delegate_task.txt` | new, instructions to the caller: when to delegate, what belongs in `task` (fragment, only when `delegate_task` is granted) |
| `prompts/de/shared/subagent_frame.txt`, `prompts/en/shared/subagent_frame.txt` | new, frame for the sub-agent |
| `aifred/plugins/tools/subagent/settings.json` | created on the first save through the gear icon: allowed tiers, recursion depth, sub-agent as another main agent (`credential_fields`, not secret) |
| `aifred/lib/bubble.py` | bubble artifacts and `render_bubble`, the one place that builds the bubble |
| `aifred/lib/function_calling.py`, `aifred/backends/base.py`, `aifred/lib/llm_pipeline.py` | executor event `artifacts` → `tool_artifacts` through the tool loop, collected with anchors in `PipelineResult.artifacts` |
| `aifred/lib/multi_agent.py`, `aifred/lib/llm_engine.py`, `aifred/lib/message_processor.py` | bubble through `render_bubble` |
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
- Sub-agent without memory; model and tools of the caller and then without
  persona, or by switch as another agent (`agent`). Since 2026-09-20,
  `agent` also brings the identity and personality of the chosen agent;
  before that only model, tuning and tool list.
- Recursion depth configurable, default 1.
- Collapsible block with the complete transcript.
- No parallel delegation, no cap switch.
- Allowed tiers default 0 and 2, configurable in the plugin setting.
- Docs: architecture page in both languages now; user guide, plugin overview
  and README entry together with the code.
