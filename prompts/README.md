# AIfred Intelligence - Prompt Templates

This directory contains all AI prompts organized by language for easy maintenance and i18n support.

## Directory Structure

```
prompts/
├── de/ , en/              # one tree per language, the same files in both
│   ├── <agent>/           # per agent: aifred, sokrates, salomo, codine, hal, pater, rabbi, vision
│   ├── agent-template/    # base prompts of a new agent (its name filled in)
│   ├── debate/            # tasks of the debate seats and the per-round blocks
│   ├── shared/            # roles, channels, markers, memory, tools, disciplines, Symposion, sub-agent
│   ├── utility/           # history summarization, context guard, reasoning, chat title, session memory
│   ├── scheduler/         # prompts of scheduled jobs
│   ├── vision/            # image analysis and camera events (besides the vision agent's own files)
│   ├── calibration/       # the calibration agent
│   └── automatik/         # en/ only: intent detection and URL ranking (always asked in English)
└── tools/                 # tool descriptions + parameter schemas of the toolsets in
                           # aifred/lib (memory tools), same layout as a plugin's prompts/tools/
```

Which file belongs to which agent and role (`identity`, `personality`, `task`, `direct`, ...) is
set in `data/agents.json` (`prompts` of each agent) — with one exception today: the direct answer
loads `<agent>/direct.txt` by its path (`prompt_loader.get_agent_direct_prompt`), past the `direct`
entry; that entry is read by the Symposion only.

## Prompt Layers

An agent's system prompt is assembled by `_merge_prompt_layers()` in `aifred/lib/prompt_loader.py`
from ten layers plus the plugins' tool instructions; the table of the layers is in
[Multi-Agent System → Prompt layers](../docs/en/architecture/multi-agent.md#prompt-layers).
The three that belong to the agent itself:

| Layer | File | Purpose | Toggleable |
|-------|------|---------|------------|
| Identity | `identity.txt` | WHO am I (name, role) | No (always loaded) |
| Personality | `personality.txt` | HOW do I speak (style) | Yes (via settings) |
| Task | `direct.txt` (direct answer) / `system_minimal.txt` or `task.txt` (debate seats) | WHAT should I do | No (situational) |

In the debate modes the task of a seat (`debate/`) follows the agent's everyday task; only for the
censor of the Recensio it takes its place (`get_seat_system_prompt`). `shared/multi_agent_roles`
(dialectic) or `shared/multi_agent_roles_review` (Recensio) describes the seats.

## Agent Prompts

| Agent | Files |
|-------|-------|
| `aifred` | `identity`, `personality`, `reminder` (style reminder), `system_minimal` (task), `direct` (addressed directly), `memory_context` |
| `sokrates` | `identity`, `personality`, `reminder`, `system_minimal`, `direct`, `memory_context` |
| `salomo` | `identity`, `personality`, `reminder`, `system_minimal`, `direct`, `memory_context` |
| `hal` | `identity`, `personality`, `reminder`, `task` (debate seats and, via `direct` in agents.json, the Symposion), `direct.txt` (direct answer), `system_minimal` (loaded nowhere), `memory_context` |
| `codine`, `pater`, `rabbi` | `identity`, `personality`, `task` (debate seats and, via `direct` in agents.json, the Symposion), `direct.txt` (direct answer), `system_minimal` (loaded nowhere), `memory_context` |
| `vision` | `identity`, `memory_context`, `task_adaptive` and the files under `vision/` (the personality is AIfred's) |
| `agent-template` | `identity`, `personality`, `system_minimal`, `direct`, `memory_context` |

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
| `shared/multi_agent_roles.txt`, `shared/multi_agent_roles_review.txt` | Description of the seats: dialectic (author, critic, judge) and Recensio (auctor, censor) |
| `shared/symposion.txt`, `shared/symposion_reflection.txt` | Symposion discussion; reflection layer from round 2 |
| `shared/user_prefix.txt`, `shared/disciplines.txt`, `shared/memory_instructions.txt`, `shared/tool_instructions.txt` | User name prefix, date/quote/currency disciplines, long-term memory, tool use |
| `shared/security_boundary.txt`, `shared/retrieved_data_owner.txt`, `shared/retrieved_data_external.txt` | Trust boundary for external messages and retrieved content |
| `shared/channel_*.txt` | Framing of an incoming message per channel (AI-Connect, Discord, e-mail, FreeEcho.2, Telegram) |
| `shared/agent_message.txt`, `shared/autonomous_event.txt` | Message from another agent, automatic notification |
| `shared/subagent_frame.txt`, `shared/subagent_task.txt` | Work order of a sub-agent |
| `shared/*_marker.txt`, `shared/email_readonly_refused.txt` | Notes the system adds to an answer or a tool result |
| `utility/history_summarization.txt` | Compress conversation history (`{conversation}`, `{max_tokens}`, `{max_words}`) |
| `utility/context_guard.txt` | Note when the context is full and no more tools are allowed |
| `utility/reasoning.txt` | Reasoning layer |
| `utility/chat_title.txt` | Chat title (`{user_message}`, `{ai_response}`) |
| `utility/session_memory.txt`, `utility/session_memory_user.txt` | Session summary for the agent's memory |
| `scheduler/*.txt` | Frame, delivery notes, run history and notes of a scheduled job |
| `vision/*.txt` | Image analysis tasks, sandbox screenshots, camera events and their context |
| `calibration/system.txt` | The calibration agent |
| `en/automatik/intent_detection.txt` | Intent, addressee, language and mode switch of a message (EN only; `{user_query}`, `{agent_list}`) |
| `en/automatik/url_ranking.txt` | Rank search-result URLs by relevance for `web_search` (EN only; `{user_question}`, `{url_list}`, `{top_n}`, `{conversation_context}`) |

## Usage

```python
from aifred.lib.prompt_loader import get_agent_system_prompt, load_prompt

# An agent's system prompt, assembled from its layers (key "task" = system_minimal)
system_prompt = get_agent_system_prompt("aifred", lang="de")

# One prompt file with placeholders (prompts/<lang>/<name>.txt)
prompt = load_prompt("utility/history_summarization", lang="de",
                     conversation="...", max_tokens=500, max_words=375)
```

## Language

Every call takes the language as `lang` (`"de"` or `"en"`); without it `load_prompt()` uses
`get_language()`, the `ui_language` of the persisted settings. A turn passes the language the
intent detection found in the message (`intent_detector.py`).

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

1. Create `.txt` file in both `de/` and `en/` directories
2. Use `{placeholder}` syntax for dynamic values
3. Use `load_prompt('name', placeholder=value)` in code (the name is the path under `prompts/<lang>/` without `.txt`)

## Best Practices

1. **Both languages required** - No fallback; prompts must exist in both `de/` and `en/`
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
