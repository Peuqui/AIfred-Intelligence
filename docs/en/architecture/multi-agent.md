# Multi-Agent System & Discussion Modes

> **Deutsche Version:** [multi-agent.md](../../de/architecture/multi-agent.md)

How AIfred's agents work together: the built-in and custom agents, the five
discussion modes, how each agent sees the conversation, and which prompt files
drive which step.

For delegating self-contained tasks to a fresh model run, see the
[Sub-Agents plugin](../guides/plugins/subagent.md) and its
[architecture notes](subagent-plugin.md).

---

## Agents

| Agent | Role |
|---|---|
| 🎩 **AIfred** | Main agent — butler & scholar, answers questions (British butler style) |
| 🏛️ **Sokrates** | Critic — questions and challenges with the Socratic method |
| 👑 **Salomo** | Judge — weighs arguments, synthesises, delivers the verdict |
| 📷 **Vision** | Image analyst — OCR and visual Q&A, inherits AIfred's personality |
| 🔥 **Pater Tuck**, 🤖 **Codine**, 🔴 **HAL 9000**, 🕎 **Rabbi Shmuel** | Custom agents shipped as examples in `data/agents.json` (Codine: code + sandbox work) |
| ➕ **Your own** | Created in the Agent Editor — name, emoji, role, bilingual prompts, own memory |

All agents — built-in or custom — are first-class: each has its own row in the
`agent_tuning` map (model, sampling, thinking, reasoning effort, context size,
TTS voice). A new agent automatically gets its settings row, sampling row and
context column in the UI; no code changes, no hardcoded agent lists.

**Customising:**
- All prompts are plain text files in `prompts/de/` and `prompts/en/`
- Agent definitions live in `data/agents.json` (prompt paths, toggles, roles)
- **Agent Editor** (settings modal): create, edit, delete agents; DE/EN prompt editing; emoji picker; tool pills with tier badges (T0–T4)
- **Memory Browser**: inspect and clean up each agent's ChromaDB memory
- Personality can be switched off per agent — identity stays, speaking style goes
- Agents answer in the user's language (German prompts for German, English prompts for every other language)

---

## Prompt layers

`_merge_prompt_layers()` in `aifred/lib/prompt_loader.py` assembles every
system prompt from up to ten layers, in this order:

| # | Layer | When |
|---|---|---|
| 0 | User prefix | always (once, at the top) |
| 1 | Identity — who the agent is | always |
| 2 | Reasoning — how to think | if reasoning is on for the agent |
| 3 | Multi-agent roles — who the others are | multi-agent modes only |
| 4 | Task prompt | always |
| 5 | Security boundary | external channel contexts (Message Hub) |
| 6 | Memory instructions | when agent memory is active (not in incognito) |
| 7 | Personality — how to speak | if personality is on |
| 8 | Tool instructions | when tools are available (near the end so tool use is prioritised) |
| 9 | Disciplines — date grounding, quote/currency discipline, … | always |

The current date and time are part of every prompt, so temporal questions work.

---

## Discussion modes

| Mode | Flow | Who decides? |
|---|---|---|
| **Standard** | One agent answers (selected via the agent pills) | — |
| **Critical Review** | AIfred → Sokrates (critique + pro/contra) → stop | You |
| **Auto-Consensus** | AIfred → Sokrates → Salomo, up to N rounds | Salomo (votes) |
| **Tribunal** | AIfred ↔ Sokrates for N rounds → Salomo | Salomo (verdict) |
| **Symposion** | 2+ freely chosen agents discuss for N rounds, reflection layer from round 2 | Nobody — multiple perspectives |

Rounds: 1–10 (default 3), set in the settings panel. The 💡 icon opens a modal
with an overview of all modes.

### Auto-Consensus

```
┌─────────────┐     ┌──────────────────┐     ┌────────────────────┐
│   User      │────▶│   🎩 AIfred      │────▶│   🏛️ Sokrates       │
│   query     │     │   + [LGTM/WEITER]│     │   + [LGTM/WEITER]  │
└─────────────┘     └──────────────────┘     └─────────┬──────────┘
                                                       │
                              ┌────────────────────────┘
                              ▼
                    ┌─────────────────────┐
                    │   👑 Salomo         │
                    │   + [LGTM/WEITER]   │
                    └──────────┬──────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
     ┌────────────────┐              ┌─────────────────┐
     │ 2/3 or 3/3     │              │ Not enough votes│
     │ = consensus    │              │ = next round    │
     └────────────────┘              └─────────────────┘
```

Consensus type (settings): **majority** (2 of 3 vote `[LGTM]`) or
**unanimous** (all 3). Sokrates only criticises — deciding consensus is
Salomo's job.

### Tribunal

```
┌─────────────┐     ┌─────────────────────────────────────┐
│   User      │────▶│   🎩 AIfred ↔ 🏛️ Sokrates           │
│   query     │     │   debate for N rounds               │
└─────────────┘     └──────────────────┬──────────────────┘
                                       │
                                       ▼
                    ┌─────────────────────────────────────┐
                    │   👑 Salomo — final verdict         │
                    │   weighs both sides, decides        │
                    └─────────────────────────────────────┘
```

### Symposion reflection

From round 2 on, each participant is additionally asked: *which aspects of the
original question are still unanswered, which perspective has been overlooked,
which assumption has gone unchallenged?* This adds depth without forcing
confrontation — agents address gaps instead of restating their view. It is an
additive prompt layer (`prompts/{de,en}/shared/symposion_reflection.txt`), not
a replacement of the discussion prompt (`shared/symposion.txt`).

### Prompt files per step

| Mode / step | Agent | Prompt |
|---|---|---|
| Standard | any | `<agent>/system_minimal` (task layer) |
| Direct addressing | addressed agent | `<agent>/direct` |
| Critical Review, Auto-Consensus — critique | Sokrates | `sokrates/critic` |
| Auto-Consensus — refinement | AIfred | `aifred/refinement` |
| Auto-Consensus — synthesis | Salomo | `salomo/mediator` |
| Tribunal — attack | Sokrates | `sokrates/tribunal` |
| Tribunal — defense | AIfred | `aifred/defense` |
| Tribunal — verdict | Salomo | `salomo/judge` |
| Symposion | every participant | `shared/symposion` (+ `shared/symposion_reflection` from round 2) |

`{round_num}` is filled in so the critic knows which round it is; critics are
told to raise at most one or two points per round.

---

## How each agent sees the conversation

Multi-agent messages are stored once in `llm_history` with speaker labels. When
an agent is called, the history is projected from **its** perspective: its own
messages become `assistant`, everybody else's become `user` with a label. This
keeps agents from mistaking another agent's words for their own.

```
┌─────────────────────────────────────────┐
│          llm_history (stored)           │
│  [AIFRED]: "Answer 1"                   │
│  [SOKRATES]: "Critique"                 │
│  [AIFRED]: "Answer 2"                   │
└─────────────────────────────────────────┘
                    │
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
┌─────────┐   ┌──────────┐   ┌─────────┐
│ AIfred  │   │ Sokrates │   │ Salomo  │
├─────────┤   ├──────────┤   ├─────────┤
│assistant│   │  user    │   │  user   │
│"Answ 1" │   │[AIFRED]: │   │[AIFRED]:│
│  user   │   │assistant │   │  user   │
│[SOKR].. │   │"Critique"│   │[SOKR].. │
│assistant│   │  user    │   │  user   │
│"Answ 2" │   │[AIFRED]: │   │[AIFRED]:│
└─────────┘   └──────────┘   └─────────┘

One source, one view per speaker.
Own messages = assistant (no label), others = user (with label).
```

---

## Addressing agents

- By name, anywhere in the sentence: *"Sokrates, what do you think about …?"*,
  *"Great explanation. Sokrates."* — the addressed agent answers directly.
- Custom agents by ID or display name (detected by the intent detection).
- STT variants are recognised ("Alfred", "Eifred", "AI Fred").
- Works from every channel: browser, FreeEcho.2, Telegram, Discord, e-mail.
- **Active agent pills** pick who answers in Standard mode; a persistent switch
  ("I want to keep talking to Pater Tuck") is recognised in speech too.

### Mode switches by voice or text

The intent detection runs before every message and also recognises mode and
research switches in any language — *"Start tribunal and discuss climate
change"*, *"Schalt auf Tiefrecherche"*, *"Nur Sokrates soll antworten"*. It
returns the new settings plus an `IS_PURE_COMMAND` flag; the message itself is
never rewritten. A pure command is confirmed; a command combined with a
question applies the switch and answers the question in the new mode.

---

## Display

Every message is shown on its own with the agent's emoji; multi-agent steps
carry a label `[<mode>: <step> R<round>]` (built in `_chat_mixin.py`):

| Example | Meaning |
|---|---|
| 🎩 AIfred | Standard answer or directly addressed agent (no label) |
| 🏛️ Sokrates [Critical Review] | Critique in Critical Review |
| 🎩 AIfred [Auto-Consensus: Refinement R2] | Refinement, round 2 |
| 👑 Salomo [Auto-Consensus: Synthesis R2] | Synthesis, round 2 |
| 🏛️ Sokrates [Tribunal R1] | Tribunal exchange, round 1 |
| 👑 Salomo [Tribunal: Verdict R3] | Final verdict |
| 📊 Summary #1 (5 messages) | Collapsible history-compression summary |

`<think>` blocks of all agents appear as collapsibles with model name and
inference time.

---

## Temperature and sampling

- **Auto mode:** the intent detection picks the base temperature
  (FACTUAL 0.2, MIXED 0.5, CREATIVE 1.0); Sokrates and Salomo get an offset
  on top (defaults +0.2 and +0.3, capped at 1.0; `config.py`).
- **Manual mode:** per-agent temperature in the sampling table.
- Top-K, Top-P, Min-P and repeat penalty come from the model's llama-swap YAML
  entry and are reset to it when the backend starts or the model changes;
  temperature survives a restart. The ↺ button resets everything to the YAML
  defaults.

Sokrates, Salomo and every other agent can run on a different model than AIfred.
