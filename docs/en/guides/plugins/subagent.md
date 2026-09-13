# Sub-Agents Plugin

**File:** `aifred/plugins/tools/subagent/`

A main agent (AIfred, Codine, any agent in `data/agents.json`) can, during its
tool loop, delegate a self-contained task to a sub-agent. The sub-agent is a
fresh model run with its own context window, its own toolkit and its own tool
loop. It has no persona, sees neither the conversation nor the caller's memory
and cannot ask questions back. Only its report comes back, as an ordinary tool
result. The sub-agent's complete transcript, that is its thinking, its text,
every tool call with arguments and result, and the report, appears as a
collapsible block in the main agent's chat bubble. It never reaches the model
and costs no tokens in the history.

Architecture and decisions: [Sub-Agents as a Plugin](../../architecture/subagent-plugin.md).

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `delegate_task` | Hand a sub-task to a sub-agent; the result is its report | READONLY |

### Parameters (`delegate_task`)

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task` | string | yes | The complete, self-contained task: everything the sub-agent must know |
| `expected_result` | string | yes | What the report must contain so the caller can continue |
| `agent` | string | no | Only when the setting "sub-agent as another main agent" is on: the agent whose model and tool list the sub-agent gets. Only agents whose model or tool list differs from the caller are offered; if there is none, the parameter is absent |

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
| Sub-agent as another main agent | `SUBAGENT_DELEGATE_TO_OTHER_AGENTS` | off | Enables the `agent` parameter: the sub-agent then runs with the model and tool list of another main agent (e.g. Codine), still without persona. A different model means a model swap through llama-swap per delegation, often minutes. The switch is deliberately not an automatism |

Memory is off for sub-agents and not a setting: everything the sub-agent needs
to know comes in the handover from the main agent. A sub-agent that stored
memories would write down things nobody saw in the conversation.

## What the sub-agent gets

- **System prompt**, deliberately lean: the frame from `prompts/<lang>/shared/subagent_frame.txt`,
  the tool instructions of exactly the plugins whose tools it holds, the
  security boundary on external channels, and the shared `disciplines` layer.
  No identity, no personality, no reminder, no memory context.
- **User message**: `task` and `expected_result` from `prompts/<lang>/shared/subagent_task.txt`. History: empty.
- **Model, sampling, thinking and context size** of the caller, or of the agent
  chosen via `agent`, through the same helpers as a normal turn.
- **Toolkit**: the agent's normal kit from the toolkit factory, memory off,
  filtered to the allowed tiers, without `delegate_task` at the last allowed
  depth. Its own chain depth and loop breaker, its own output budget for tool
  results, the backend's context guard as in every turn.

## What the main agent gets

Only the report, as a `role=tool` message. It passes through the same cap as
every tool result, which only bites if it would blow the main model's free
context. For the caller, the delegation counts as one tool call.

## What the user sees

While the sub-agent works, the status line shows which tool it is calling.
Afterwards the transcript sits as a collapsible block in the main agent's
bubble, next to the thinking process and the sources. Through the Message Hub
(Telegram, email) there is no bubble; the run is then in the session's debug
log.

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
