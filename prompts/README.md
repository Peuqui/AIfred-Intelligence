# AIfred Intelligence - Prompt Templates

This directory contains all AI prompts organized by language for easy maintenance and i18n support.

## Directory Structure

```
prompts/
├── de/ , en/              # one tree per language, the same files in both
│   ├── <agent>/           # per agent: aifred, sokrates, salomo, codine, hal, pater, rabbi
│   ├── agent-template/    # base prompts of a new agent (its name filled in)
│   ├── debate/            # tasks of the debate seats and the per-round blocks
│   ├── shared/            # situation, roles, output format, formatting, markers, memory, tools, disciplines, Symposion, sub-agent
│   ├── utility/           # history summarization, context guard, reasoning, chat title, session memory
│   ├── scheduler/         # prompts of scheduled jobs
│   ├── vision/            # the vision agent's files: image analysis, sandbox screenshots, camera events
│   ├── calibration/       # en/ only: the calibration agent (system prompt and building blocks)
│   └── automatik/         # en/ only: intent detection and URL ranking (always asked in English)
└── tools/                 # tool descriptions + parameter schemas of the toolsets in
                           # aifred/lib (memory tools), same layout as a plugin's prompts/tools/
```

The plugins keep their own prompts next to their code: `aifred/plugins/<kind>/<plugin>/prompts/`
(tool descriptions in `tools/`, instructions for the model in `<lang>/`). A channel plugin also
holds the framing of its incoming messages in `<lang>/context/inbound.txt` and its other notes in
`<lang>/messages/` (loaded by `plugin_base.load_plugin_prompt`).

Which file belongs to which agent and role (`identity`, `personality`, `reminder`,
`memory_context`) is set in `data/agents.json` (`prompts` of each agent). What an agent is to do
is not in its files: it comes from the situation (see below).

## Prompt Layers

An agent's system prompt is assembled by `_merge_prompt_layers()` in `aifred/lib/prompt_loader.py`
from layers 0 to 11 including the plugins' tool instructions, on every path (`agent_system_prompt()`:
direct answer, channels, debate seats, Symposion, sub-agent); the table of the layers is in
[Multi-Agent System → Prompt layers](../docs/en/architecture/multi-agent.md#prompt-layers).
The three that belong to the agent itself:

| Layer | File | Purpose | Toggleable |
|-------|------|---------|------------|
| Identity | `identity.txt` | WHO am I (name, role) | No (always loaded) |
| Personality | `personality.txt` | HOW do I speak (style) | Yes (via settings) |
| Situation | not the agent's: `shared/conversation_roles` (direct answer, channels), `debate/*` (a seat's task), `shared/symposion`, `shared/subagent_frame`, or a channel's own (`BaseChannel.situation()`) | WHAT should I do here | No (set by the path) |

`reminder.txt` (the short style reminder) and `memory_context.txt` (how recalled memories are
presented) are also the agent's own files, but no layers of the system prompt: the reminder and the
output language (`shared/output_language`) end the last user message, the memories come as a message
of their own right before the question.

In the debate modes the task of a seat (`debate/`) is the situation of the speaker; the author has
none (its tasks come as the question after the history). The seats are described by
`shared/multi_agent_roles` (tribunal, auto-consensus), `shared/multi_agent_roles_critique`
(critical review: author and critic, no judge) or `shared/multi_agent_roles_review` (Recensio),
chosen per mode in `turn_debate._DEBATE_MODES`.

## Agent Prompts

| Agent | Files |
|-------|-------|
| `aifred`, `sokrates`, `salomo`, `hal`, `codine`, `pater`, `rabbi` | `identity`, `personality`, `reminder` (style reminder), `memory_context` |
| `vision` | no agent folder: the 16 files under `vision/` that the image model really gets (listed in its entry in `agents.json`), no identity and no personality |
| `agent-template` | `identity`, `personality`, `reminder`, `memory_context` (a new agent's base prompts, its name filled in) |

## Debate Prompts (`debate/`)

The task of a seat; the round block (`_first` for the first round, `_later` for every further one)
is chosen by the code (`debate_seats.round_rules`) and goes into `{round_rules}`.

| File | Mode / seat | Purpose |
|------|-------------|---------|
| `critic.txt` (+ `critic_first`, `critic_later`) | Critical Review, Auto-Consensus: critic | Critique of the author's answer |
| `critic_voting.txt` | Auto-Consensus: critic | Vote tag `[LGTM]` / `[WEITER]` |
| `refinement.txt` | Auto-Consensus: author | Revision after critique and synthesis |
| `mediator.txt` (+ `mediator_first`, `mediator_later`) | Auto-Consensus: judge | Synthesis and vote |
| `tribunal.txt` (+ `tribunal_first`, `tribunal_later`) | Tribunal: critic | Attack on the core thesis |
| `defense.txt` | Tribunal: author | Defense or revision |
| `judge.txt` | Tribunal: judge | Final verdict |
| `review.txt` (+ `review_first`, `review_later`) | Recensio: censor | Review of the auctor's files |
| `review_fix.txt` | Recensio: auctor | Correctura of the findings in the files |

## Shared, Utility and Other Prompts

| File | Purpose |
|------|---------|
| `shared/conversation_roles.txt` | The situation of an agent answering the user (direct answer, addressed by name, channels) |
| `shared/multi_agent_roles.txt`, `shared/multi_agent_roles_critique.txt`, `shared/multi_agent_roles_review.txt` | Description of the seats: dialectic (author, critic, judge), critical review (author, critic) and Recensio (auctor, censor) |
| `shared/symposion.txt`, `shared/symposion_reflection.txt` | Symposion rules (the situation); reflection as the question from round 2 |
| `shared/user_prefix.txt`, `shared/output_format.txt`, `shared/formatting.txt`, `shared/output_language.txt`, `shared/disciplines.txt`, `shared/ask_back.txt` | User name prefix, output format, formatting rules (only where the answer is shown as Markdown), output language (end of the last user message), date/quote/currency/appointment disciplines, asking back on decisions |
| `shared/memory_instructions.txt`, `shared/tool_instructions.txt`, `shared/tool_announce.txt` | Long-term memory, tool use, announcing a tool call |
| `shared/security_boundary.txt`, `shared/retrieved_data_owner.txt`, `shared/retrieved_data_external.txt` | Trust boundary (only for a message that is not the owner's) and for retrieved content |
| `shared/channel_header.txt`, `shared/channel_header_routed.txt`, `shared/channel_sender_via.txt` | Generic framing of an incoming message (the channel plugins have their own in `context/inbound.txt`) |
| `shared/agent_message.txt`, `shared/autonomous_event.txt` | Message from another agent, automatic notification |
| `shared/subagent_frame.txt`, `shared/subagent_task.txt` | Work order of a sub-agent |
| `shared/*_marker.txt` | Notes the system adds to an answer or a tool result |
| `utility/history_summarization.txt` | Compress conversation history (`{conversation}`, `{max_tokens}`, `{max_words}`) |
| `utility/context_guard.txt` | Note when the context is full and no more tools are allowed |
| `utility/reasoning.txt` | Reasoning layer |
| `utility/chat_title.txt` | Chat title (`{user_message}`, `{ai_response}`) |
| `utility/session_memory.txt`, `utility/session_memory_user.txt` | Session summary for the agent's memory |
| `scheduler/*.txt` | Frame, delivery notes, run history and notes of a scheduled job |
| `vision/*.txt` | Image analysis tasks, sandbox screenshots, camera events and their context |
| `en/calibration/*.txt` | The calibration agent (EN only): `system.txt` and its building blocks (`start`, `ctx_reduction_*`, `projection_*`, `hybrid_disabled`, `hardware_gpu*`) |
| `en/automatik/intent_detection.txt` | Intent, addressee, language and mode switch of a message (EN only; `{user_query}`, `{agent_list}`) |
| `en/automatik/url_ranking.txt` | Rank search-result URLs by relevance for `web_search` (EN only; `{user_question}`, `{url_list}`, `{top_n}`, `{conversation_context}`) |

## Usage

```python
from aifred.lib.prompt_loader import agent_system_prompt, conversation_situation, load_prompt

# An agent's system prompt, assembled from its layers: the situation says what it is to do
system_prompt = agent_system_prompt(
    "aifred", conversation_situation("de"), "de",
    answer_channel="browser", external=False, tools=None, memory=True,
)

# One prompt file with placeholders (prompts/<lang>/<name>.txt)
prompt = load_prompt("utility/history_summarization", lang="de",
                     conversation="...", max_tokens=500, max_words=375)
```

## Language

Every call takes the language as `lang` (`"de"` or `"en"`); without it `load_prompt()` uses
`prompt_language()`: the language of the turn being answered (`prompt_loader.turn_language`, set
by `turn_service` after the intent detection found it in the message), outside a turn the
`ui_language` of the persisted settings. Jobs and alarms of the system speak the interface's
language. The output language of an answer is stated only in `shared/output_language.txt`, not in
the personalities.

## Personality Toggle

Personality and reasoning can be switched off per agent in the agent settings
(`get_personality_enabled(agent)`, `get_reasoning_enabled(agent)` read the saved tuning, with the
agent's `agents.json` default for a new agent). The identity stays, the speech style goes.

## Automatic Placeholders

Every prompt loaded via `load_prompt()` can use these without the caller passing them
(`_build_standard_placeholders`): `{current_year}`, `{current_date}`, `{current_time}`,
`{current_weekday}`, `{upcoming_week}`, `{previous_years}`, `{user_name}`, `{user_gender}`.
Other `{placeholders}` must be passed by the caller; a missing one raises a `KeyError`. The
user-name prefix is not added by `load_prompt()` but once at the top of a merged system prompt
(`shared/user_prefix.txt`, layer 0).

## Adding New Prompts

1. Create `.txt` file in both `de/` and `en/` directories (a plugin's prompts in the plugin's own `prompts/<lang>/`)
2. Use `{placeholder}` syntax for dynamic values
3. Use `load_prompt('name', placeholder=value)` in code (the name is the path under `prompts/<lang>/` without `.txt`)

## Best Practices

1. **Both languages required** - No fallback; prompts must exist in both `de/` and `en/` (exceptions: `automatik/` and `calibration/` are English only, since the model is always asked in English)
2. **Use placeholders** - Never hardcode dynamic values (dates, user input)
3. **Clear structure** - Use headings and formatting for LLM readability
4. **Include examples** - Concrete examples improve LLM performance
5. **Format specifications** - For structured output (JSON, XML), be explicit

## Troubleshooting

### FileNotFoundError
```
Prompt file not found: .../prompts/en/xyz.txt
```
Ensure the prompt exists in both language directories.

### KeyError for placeholders
```
Missing placeholder in prompt 'decision_making': 'user_text'
```
Pass all required placeholders when calling `load_prompt()`.

### Prompt changes
`load_prompt()` reads the file on every call; a changed `.txt` takes effect with the next request,
without a restart.
