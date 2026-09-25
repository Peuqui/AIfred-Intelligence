# Sub-Agents Plugin

> **Deutsche Version:** [subagent.md](../../../de/guides/plugins/subagent.md)

**File:** `aifred/plugins/tools/subagent/`

A main agent (AIfred, Codine, any agent in `data/agents.json`) can, during its
tool loop, delegate a self-contained task to a sub-agent. The sub-agent is a
fresh model run with its own context window, its own toolkit and its own tool
loop. It sees neither the conversation nor the caller's memory and cannot ask
questions back. It has a persona only when it runs as another main agent
(parameter `agent`): it then carries that agent's identity and personality,
that is its role and working method. Only its report comes back, as an ordinary tool
result. The sub-agent's complete transcript, that is its thinking, its text,
every tool call with arguments and result, and the report, appears as a
collapsible block in the main agent's chat bubble. It never reaches the model
and costs no tokens in the history.

Architecture and decisions: [Sub-Agents as a Plugin](../../architecture/subagent-plugin.md).

## Why: lean main agents, shared load

Every tool an agent holds sits in every prompt with its description. A main
agent with all plugins carries tens of thousands of tokens before it has read a
word of the question. With sub-agents it does not have to: it keeps the
everyday tools and `delegate_task`, and specialist work such as code, sandbox or
documents is taken over by another main agent as a sub-agent, with that agent's
identity and personality, model and tool list (setting "sub-agent as another
main agent"). The work is
spread across several agents, each with the toolkit it actually needs and, if
wanted, each with its own model.

Measured on 2026-09-13 with Qwen3.8-27B, prompt shares in tokens from the debug log (total including memory and a short history):

| Agent | Tools | System | Tool schemas | Prompt total |
|---|---|---|---|---|
| AIfred without whitelist | 92 | 10,485 | 20,683 | 31,730 |
| AIfred with whitelist and `delegate_task` | 51 | 5,391 | 11,551 | 17,449 |
| Codine (coding, sandbox, workspace) | 27 | 5,276 | 7,315 | 13,299 |

The lean AIfred saves about 14,000 prefill tokens per turn, 45 % of its prompt.
The system prompt shrinks too, because it only carries the instructions of the
granted plugins. On a cache miss, at about 500 tokens per second of prefill,
that is roughly 28 seconds less until the first token (calculated, not measured
individually). The caller sees which agents with which tool groups are
available in the description of the `agent` parameter; AIfred picks the fitting
agent there on its own, even when the user names none.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `delegate_task` | Hand a sub-task to a sub-agent; the result is its report | READONLY |

### Parameters (`delegate_task`)

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task` | string | yes | The complete, self-contained task: everything the sub-agent must know |
| `expected_result` | string | yes | What the report must contain so the caller can continue |
| `agent` | string | no | Only when the setting "sub-agent as another main agent" is on: the agent the sub-agent runs as: its identity and personality, model and tool list. Every main agent except the caller is offered; if there is none, the parameter is absent. System agents (calibration, vision) are never offered. The parameter description lists, per agent, the tool groups its sub-agent would get (plugins by whitelist and allowed tiers), plus for each group the plugin description from the plugin's `i18n.json` in the language of the turn |

The tool itself has tier READONLY. What the sub-agent may do is bounded by the
caller's ceiling (source and `max_tier`; a Telegram channel without write
tools does not get them through a sub-agent either) and by the plugin setting
"allowed tiers".

## Who may delegate

Like every tool, `delegate_task` has to be in the agent's tool list in
`data/agents.json`. Agents without a whitelist (like AIfred) have it
automatically, Codine has it listed. Sokrates and Salomo do not.

## Settings (gear icon in the plugin modal, `settings.json` next to the plugin)

| Setting | Key | Default | Meaning |
|---|---|---|---|
| Allowed tiers | `SUBAGENT_ALLOWED_TIERS` | `0+2` | Read, research, write files, sandbox. No sending (tier 1), no deleting (tier 3). Sending stays with the main agent, which answers for it in the conversation |
| Recursion depth | `SUBAGENT_MAX_DEPTH` | `1` | Whether a sub-agent may delegate itself. At 1 the sub-agent does not get the tool at all; the bound is structural |
| Sub-agent as another main agent | `SUBAGENT_DELEGATE_TO_OTHER_AGENTS` | off | Enables the `agent` parameter: the sub-agent then runs as another main agent (e.g. Codine), with its identity and personality, model and tool list, but without conversation and memory. A different model means a model swap through llama-swap per delegation, often minutes. The switch is deliberately not an automatism |

Memory is off for sub-agents and not a setting: everything the sub-agent needs
to know comes in the handover from the main agent. A sub-agent that stored
memories would write down things nobody saw in the conversation.

## What the sub-agent gets

- **System prompt**, deliberately lean: the frame from `prompts/<lang>/shared/subagent_frame.txt`,
  the tool instructions of exactly the plugins whose tools it holds, the
  security boundary on external channels, and the shared `disciplines` layer.
  No reminder, no memory context. Without `agent` also no identity and no
  personality. With `agent`, the identity and personality of the chosen agent
  are added, in the same layer order as a normal turn: identity, frame, security
  boundary, personality, tool instructions, `disciplines`. If that agent's
  personality toggle is off in the settings, the personality is missing here
  too.
- **User message**: `task` and `expected_result` from `prompts/<lang>/shared/subagent_task.txt`. History: empty.
- **Model, sampling, thinking and context size** of the caller, or of the agent
  chosen via `agent`, through the same helpers as a normal turn.
- **Toolkit**: the agent's normal kit from the toolkit factory, memory off,
  filtered to the allowed tiers, without `delegate_task` at the last allowed
  depth. Its own chain depth and loop breaker, its own output budget for tool
  results, the backend's context guard as in every turn.

## What the main agent gets

The report, as a `role=tool` message. If the sub-agent created pages or images
in the sandbox, their `SANDBOX_HTML_URL` and `SANDBOX_IMAGE_URL` lines lead the
report, exactly as with an own `execute_code` call, so the main agent can use
them further, e.g. with `render_html`. The result passes through the same cap as
every tool result, which only bites if it would blow the main model's free
context. For the caller, the delegation counts as one tool call.

## What the user sees

While the sub-agent works, the status line shows which tool it is calling.
Afterwards the transcript sits as a collapsible block in the main agent's
bubble, at the point of the turn where it delegated: thinking, text and
transcripts appear in the order they happened. Whatever the sub-agent's tools
produced for the bubble goes up as if the main agent had produced it: sandbox
pages and images, camera images with the VLM description, the fetched web pages
in the sources block, and the transcripts of nested sub-agents. All of it sits
right below the transcript at the point of delegation. The blocks are
collapsed: a sandbox page shows its line with "open in browser" (opens a new tab
without expanding the block), expanded the embedded program and its source code
below. The main agent only summarises the result in its answer and does not
copy code or files again. Through the Message Hub
(Telegram, email) there is no bubble; the run is then in the session's debug
log.

**Metrics:** The transcript ends with the same performance line as an answer,
italic in parentheses: TTFT, prefill, tokens per second, duration, model and
backend. It covers only the sub-agent's work, including sub-agents it delegated
to itself. The line below the main agent's answer covers the whole turn instead:
prefill and tokens per second add up every server request, meaning each tool
round of the main agent and all requests of its sub-agents. The rate is total
tokens over total compute time, not the mean of the single rates, so a short
follow-up prefill does not count as much as a long cold one. The thinking time
adds up every think block of the turn, including those before later tool rounds
and those of the sub-agents. The duration is the
wall clock of the whole turn, including the wait for the sub-agent. If a single
request cannot be measured, prefill shows "n/a" instead of a number that leaves
work out.

## Deliberately not included

- No parallel sub-agents: on single-user hardware parallelism gains nothing,
  the tool loop works sequentially.
- No wall-clock time limit: chain depth and context window bound the
  sub-agent, the tool loop's heartbeat keeps long delegations alive.
- No way to hand the transcript to the main model.

## Example

AIfred is asked: "Compare the three offers in `offers/` and tell me which is
the cheapest over five years." He delegates: `task` with the three file names,
the comparison period and the request to tabulate every cost item;
`expected_result`: "one table per offer with total cost over five years and one
sentence of recommendation". The sub-agent reads the files, calculates in the
sandbox, delivers the table. AIfred answers the user with the recommendation;
the transcript with the files read and the calculations sits collapsed below.
