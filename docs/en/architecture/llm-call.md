# LLM Call Architecture

> **Deutsche Version:** [llm-call.md](../../de/architecture/llm-call.md)

**All LLM inference paths in AIfred Intelligence and how they connect.**

---

## Overview

Every answering call in AIfred flows through a unified chunk-processing pipeline (`run_llm_stream()` in `llm_pipeline.py`). This pipeline handles TTFT measurement, tool-call tracking, URL collection, sandbox output extraction, thinking block processing, and inference metadata, regardless of whether the turn comes from the Vue interface's chat or from a channel (email, scheduler, alert), and whether one agent answers alone or several debate. The turn itself is `run_turn()` in `turn_service.py`; the multi-agent modes live in `turn_debate.py`.

```
                +----------------------------+
                |   Chat (Vue)   |  Channel  |
                | submit_turn()  | process_  |
                |                | inbound() |
                +-------+--------+-----+-----+
                        |              |
                        +------+-------+
                               |
                      +--------v---------+
                      |   run_turn()     |   one turn: message up to
                      | turn_service.py  |   the finished reply
                      +--------+---------+
                               |
        intent (Automatik model), mode switch, routing,
        images (_look_at_images), compression (_compress)
                               |
              +----------------+-----------------+
              |                                  |
     +--------v---------+              +---------v----------+
     |   _respond()     |              |  run_symposion()   |
     |  direct answer   |              |  turn_debate.py    |
     +--------+---------+              +---------+----------+
              |                                  |
     +--------v---------+                        |
     |   run_debate()   |  (debate modes         |
     |  turn_debate.py  |   only)                |
     +--------+---------+                        |
              |                                  |
              +----------------+-----------------+
                               |
                      +--------v---------+
                      |  stream_call()   |   every model call of a turn
                      | turn_service.py  |
                      +--------+---------+
                               |
                      +--------v---------+
                      | run_llm_stream() |   <-- UNIFIED PIPELINE
                      | llm_pipeline.py  |
                      +--------+---------+
                               |
                      +--------v---------+
                      |  chat_stream()   |
                      |  llm_client.py   |
                      +--------+---------+
                               |
                      +--------v---------+
                      |  Backend (LLM)   |
                      | Ollama / llama.cpp|
                      | vLLM / Cloud API |
                      +------------------+
```

Every model call of a turn runs through the same three steps: build (`AgentCall`), stream (`stream_call`), finish (`finish_call`: save the bubble and the `llm_history`, tell the clients). This holds for the direct answer (`_respond`) and for every speaker of a debate.

---

## Pipeline Events

`run_llm_stream()` yields these event types:

| Event Type | Payload | Description |
|---|---|---|
| `content` | `text: str` | Token from LLM response |
| `ttft` | `value: float` | Time-to-first-token (seconds) |
| `tool_call_start` | `name: str` | Tool name known, arguments still streaming |
| `tool_call` | `name: str, arguments: str` | Complete tool call with arguments |
| `tool_progress` | `message: str` | Progress line of a streaming tool (web_search, search_documents, ...) |
| `tool_artifacts` | `artifacts: list` | Bubble artifacts delivered by a tool (placed where the tool ran) |
| `tool_result` | `name: str, result: str` | Tool execution result |
| `thinking` | `text: str` | Chain-of-thought block |
| `debug` | `message: str` | Backend debug item (truncation warning, context guard, ...) |
| `progress` | `phase: str` | Backend work between rounds: `compress` while the tool loop compresses, `""` ends it |
| `pipeline_result` | `result: PipelineResult` | Final aggregated result |

`PipelineResult` contains: full response text (`text`, incl. `<think>` blocks), cleaned text (`text_clean`), inference metadata (`metadata_dict`, `metadata_display`, `debug_msg`, `metrics`), `ttft`, `inference_time`, the measured work (`work`), bubble artifacts (`artifacts` — sources from `web_fetch`, sandbox output, VLM output), `answer_text` (answer after the last tool call), `load_time` (model load before the answer, 0 = warm), `history_notes`, plus the flags `silent_reply` and `truncated`. The pipeline does not pass the backend's `done` on; its metrics end up in `PipelineResult.metrics`.

`history_notes` are lines that sit behind the answer in the `llm_history` only, not in the chat. The tool calls themselves do not survive the turn. So that the model knows in the next turn what it did, `run_llm_stream()` appends the line from `ToolKit.call_note()` here (text: `prompts/{de,en}/shared/tool_calls_marker.txt`): write calls with their arguments (secret values redacted), read calls only counted, `✗` = failed. Long values are cut one by one and marked `…` (`TOOL_CALL_NOTE_VALUE_CHARS`), never the call as a whole: the model reads the note as its own call, and a value cut in the middle of a date made it take back an entry that had worked. `shared/tool_instructions.txt` forbids the model to write such lines itself.

---

## Call Paths in Detail

### 1. A Turn (Chat and Channels)

A turn is one message of the session up to the finished reply. It runs as a task of the server, independent of any open tab; the tabs read the session's event stream (`session_events`). A message in the chat (`submit_turn`) and one from a channel (`process_inbound`, see section 5) take the same path. If a turn is already running in the session, a chat message waits in the queue (`pending_messages`) and starts when it ends; a channel's message replaces the running turn (`pipeline_registry.register_pipeline`, the newest wins).

```
turn_service.py: run_turn() -> _run()
  |-- the message's images (session_images), checks: empty, calibration, model set
  |-- the user entry goes into the session at once (chat_history + llm_history)
  |-- _detect_intent(): addressee, language, mode switch (one Automatik call, section 7)
  |     a system's task (job, alert): no intent; URL-only or image-only: skipped
  |-- apply the mode switch; a pure command -> confirmation instead of an answer, turn ends
  |-- _route(): pinned agent (wake word, job) > addressee > the session's active agent
  |     (an explicit address sticks as the active agent)
  |-- turn_debate.plan_turn(): who speaks (TurnPlan)
  |-- _look_at_images(): who looks at the images (section 2)
  |-- _compress(): history compression before the turn (section 3)
  |-- no responder (symposion)          -> turn_debate.run_symposion()
  |   else _respond(responder)          -> direct answer
  |     then, in a debate mode          -> turn_debate.run_debate()
  |-- _start_title(): session title in the background (section 6)
```

The direct answer:

```
turn_service.py: _respond()
  |-- _direct_call(): builds the AgentCall
  |     |-- the agent's model (agent_model: its own, else AIfred's)
  |     |-- num_ctx (agent_context)
  |     |-- agent_toolkit() -> prepare_agent_toolkit(research_tools_enabled=True)
  |     |     -> memory tools + all plugin tools; recalled memories are injected
  |     |        as context right before the question (inject_before_question)
  |     |-- the agent's thinking choice for this model (thinking_mode, thinking_fields)
  |     |-- system prompt: prompt_loader.get_agent_direct_prompt(agent, lang, memory)
  |     |-- messages: build_messages_from_llm_history(..., perspective=agent)
  |     |-- the agent's temperature (resolve_agent_temperature, its own per agent)
  |     |-- LLM options: build_llm_options(view, agent, model, temperature, num_ctx)
  |-- stream_call(): run_llm_stream()  <-- PIPELINE
  |-- finish_call(mode="direct")
```

When several agents speak, the **perspective transformation** in `build_messages_from_llm_history()` ensures each sees the conversation from its own point of view: its own replies are `role: "assistant"` (without a name label), everything else is `role: "user"`, other agents' replies under their display name. The judge of a debate reads as an observer (`observer=True`): every reply is someone else's.

When the user addresses an agent ("Sokrates, what do you think?"), the intent detection returns that agent's id as addressee; `_route()` picks it, and the same turn runs with that agent as the responder.

### 2. Images of a Turn

Who looks at the images is decided by `turn_service._look_at_images()` with `vision_routing.chat_describer()` (rule A, see [Vision routing](vision-routing.md)):

- **VL Direct**: when a single agent answers without a debate and its model can see, it gets the images in its request. `_direct_call()` builds the question (`turn_images.seeing_question`), the content (`seeing_content`, images first, then one text part) and the task in the system prompt (`seeing_task`). The images themselves never enter the history; it holds only the URL (`image_marker`), so a later turn can look again with `vision_analyze`.
- **Description**: in every other case (debate, symposion, a model that cannot see) `turn_images.describe_images()` describes the images once; the description (`description_block`) is appended to the user turn and overwritten in the `llm_history` (`_rewrite_user_turn`). All agents of the turn thus work from the same basis. A describer of another machine loads there (`remote_describers`).
- If no describer is found (`NoVisionModelError`), the turn goes on without a description, with a notice to the user.

An uploaded image can be turned or cut before it is sent (`rotate_upload`, `crop_upload`).

### 3. History Compression

Two places compress, both from `HISTORY_COMPRESSION_TRIGGER` (85% of the context) down to `HISTORY_COMPRESSION_TARGET` (30%):

- **Before the turn** (`turn_service._compress` → `compress_history` → `context_manager.summarize_history_if_needed`): measured against the smallest context of the turn's agents and the largest of their system prompts. The first speaker's model summarizes, so no second model has to load. In the debate modes `turn_debate._compress_before` checks again before every speaker, since the debate itself can fill the next one's context.
- **Inside the tool loop** (`context_manager.compress_tool_loop`, `LoopCompression`): `stream_call` hands `run_llm_stream` a `LoopCompression` (client, model, language); when the loop's context reaches the trigger, it summarizes the earlier conversation and, if that is not enough, the oldest finished tool rounds. The question, the system messages and the last rounds stay. The session's histories are left untouched; the pipeline event `progress` reports the phase `compress`.

Both report the phase through `debug_bus.report_phase` as a `turn_phase` event to the tabs.

### 4. Multi-Agent Modes (`turn_debate.py`)

The modes work with **seats**, not names (`debate_seats.py`): the author (`SEAT_MAIN`, the agent that answers first), the critic (`SEAT_CRITIC`) and the judge (`SEAT_JUDGE`). The session chooses critic and judge (default from `settings.json`, `debate_seats`; `session_seats`). Each agent speaks with its own persona; the seat's task is in `prompts/<lang>/debate/<key>.txt` (`seat_prompt`) and names the other seats, never fixed names. The code picks the round rules, not the model: `round_rules()` loads `<key>_first` for round 1 and `<key>_later` for every further one. If the agent of a seat is gone, `plan_turn` rejects the turn before anyone speaks.

Flow: first the author's direct answer (`_respond`), then `run_debate(ctx, plan, answer)`:

```
run_debate()
  |-- mode == "tribunal"  -> _tribunal()
  |-- mode == "recensio"  -> _recensio()
  |-- else                -> _critique()   (critical_review and auto_consensus)
```

Every speaker builds its call with `_call()` (system prompt, history from its view, the round's question for author tasks) and runs through `stream_call` → `run_llm_stream()` and `finish_call`. The system prompt is the agent's multi-agent task prompt (`_base_system`, role description `DEBATE_ROLES`) plus the seat's task (`_seat_system`) for critic, mediator, prosecutor and judge. The author's tasks (refinement, defense, correctura) come as the question after the history instead, in the recensio under `REVIEW_ROLES`.

#### Critical Review (critical_review)

Direct answer, then exactly one critique by the critic (`_critique`, one round). The user decides afterwards.

#### Auto-Consensus

```
_critique()   (mode = auto_consensus, at most max_debate_rounds rounds)
  |
  Loop per round N:
    |-- CRITIC: task critic + round_rules("critic", N == 1) + critic_voting
    |-- JUDGE (observer): task mediator + round_rules("mediator", N == 1)
    |
    |-- VOTING: _agrees() per seat, _consensus(votes, consensus_type)
    |     [LGTM] = approve; [WEITER] outweighs [LGTM]
    |     Round 1: only critic and judge vote (the author's direct answer was
    |     never asked for a vote); from round 2 the author votes too
    |     "majority": more than half agree; "unanimous": all
    |
    |-- consensus or last round -> end
    |
    |-- AUTHOR: refinement (task refinement as the next round's question)
```

The voting rule (`critic_voting`) is only in the critic's prompt in auto-consensus, not in `critical_review`. `consensus_type` and `max_debate_rounds` come from the settings.

#### Tribunal

```
_tribunal()
  |
  Fixed number of rounds max_debate_rounds (no early exit):
    |-- CRITIC (prosecutor): task tribunal + round_rules("tribunal", N == 1)
    |-- AUTHOR (defense): task defense as the question (may hold or change position),
    |     not in the last round
  |
  After all rounds:
    |-- JUDGE (observer): task judge, the verdict
```

#### Recensio (Code Review)

The censor (the critic's seat) reads and tests the files the auctor (the author) changed in this turn (`_author_files`, from `ToolKit.changed_documents`; lists new/changed/deleted via `_file_lists`).

```
_recensio()
  |-- censor toolkit: toolkit.restricted(RECENSIO_CENSOR_TOOLS)  (read and test only)
  |-- _review(round 0): review of the answer, belongs to no round
  |     system prompt: get_seat_system_prompt(..., REVIEW_ROLES, tools=RECENSIO_CENSOR_TOOLS):
  |     the review replaces the agent's everyday task, identity and personality stay
  |
  Loop per round N (at most max_debate_rounds):
    |-- [LGTM] of the review (and no [WEITER]) -> passed, end
    |-- AUTHOR: correction (task review_fix), changes the files itself
    |-- _review(round N): new review
```

#### Symposion

```
run_symposion()
  |
  Per round up to max_debate_rounds, per agent of the round table (re-read from the session every round):
    |-- system prompt (direct) + rules shared/symposion (+ shared/symposion_reflection from round 2)
    |-- messages: the whole conversation so far, own contributions as assistant
    |-- memories in round 1 only
    |-- stream_call() -> run_llm_stream()
```

No winner, no vote. An agent switched off leaves the round; if only one remains, it answers directly (`plan_turn`).

### 5. Message Hub (Inbound Email, Scheduler, Alerts)

Completely stateless. The message runs as a turn of the turn service (`lib/turn_service.py`), the one the Vue interface uses for the chat: intent, mode switch, compression, multi-agent modes and the same bubble as in the browser.

```
email_channel/__init__.py: listener_loop() — email received via IMAP IDLE
  |
  v
message_processor.py: dispatch_inbound() -> process_inbound(InboundMessage)
  |
  |-- 1. Calibration gate (refusal through the channel), the sender's tier,
  |       sanitizing, user mapping
  |-- 2. Find or create session (routing_table)
  |       hub_notification_scope: toast "received" in the browser
  |-- 3. _turn_origin(): the bubble (build_inbound_chat_entry), what the model
  |       reads (the plugin's context, wrapped by wrap_external_message — the
  |       fence stays in the llm_history), tier, trust, channel metadata,
  |       metadata["wake_agent"] as the pinned agent (only when configured)
  |-- 4. turn_service.run_turn(TurnRequest(origin=...)) in the channel's task
  |       |-- flow as in section 1; per agent agent_toolkit() ->
  |       |     prepare_agent_toolkit with the origin's tier, trust and metadata
  |       |-- run_llm_stream()  <-- PIPELINE (token events to open tabs; not
  |       |     read aloud in the browser, the channel speaks itself)
  |       |-- replies saved as bubbles, title in the background
  |       v
  |       TurnOutcome: status, agent, replies (TurnReply: text_clean, silent)
  |
  |-- 5. outbound_text(): every reply of the turn, sanitized; with a name line
  |       when another agent than AIfred, or several, spoke
  |-- 6. Auto-reply via plugin.send_reply() (if the channel has always_reply
  |       or its auto-reply toggle is on — email: SMTP)
```

### 6. Session Title

Does **not** use `run_llm_stream()`: simple non-streaming `client.chat()` with a hard timeout (`SESSION_TITLE_TIMEOUT_SECONDS`, 300 s). After the first exchange `turn_service._start_title()` starts a background task (`_title`) if the session has no title yet; the finished title arrives as a `session_title` event.

```
session_title.py: first_exchange() + generate_session_title()
  |-- Input: first user message + first reply (max 500 chars each; HTML tags of the
  |     user text and thinking blocks of the reply removed; the speaker label "[AIFRED]: " is dropped)
  |-- Prompt: load_prompt("utility/chat_title")
  |-- Model: the model of the turn's last call with its num_ctx
  |     (it is the loaded one, nothing reloads for the title);
  |     without an override the automatik model from settings
  |-- Options: temperature=0.3, num_predict=SESSION_TITLE_NUM_PREDICT (2000),
  |     enable_thinking=False, num_ctx
  |-- llm_client.chat() (NOT streaming, SESSION_TITLE_TIMEOUT_SECONDS timeout)
  |-- Cleanup: thinking blocks, quotes, trailing punctuation, max 80 chars
  |-- update_session_title(session_id, title)
```

### 7. Intent Detection

Also does **not** use `run_llm_stream()`: uses the small automatik model, called by `turn_service._detect_intent()`.

```
intent_detector.py: detect_query_intent_and_addressee()
  |-- Model: automatik model (effective_automatik_id), prompt always in English
  |-- Context: AUTOMATIK_LLM_NUM_CTX (12288) if the automatik model differs
  |     from AIfred's; with the same model AIfred's max_context (no reload)
  |-- Options: temperature=0.2, enable_thinking=False
  |-- Returns: (addressee, detected_language, mode_switch_updates,
  |            is_pure_command, raw_response, load_time)
  |     addressee: an agent id or None
  |     language in: de, en
  |     mode_switch_updates: requested config change ({} if none)
  |     is_pure_command: message is ONLY a mode-switch command
```

The call is skipped for a message that is only a URL or only images, and for a system's task (job, alert): the interface language applies then.

---

## Thinking Modes & Reasoning-Effort Levels

Each agent has a thinking-mode dropdown (🧠) instead of a plain on/off
toggle. The option list shows only what the model's chat template can do:
`off` where thinking can be turned off, `on` (the template's default level)
and the template's levels where the model thinks, nothing but `off` for a
model without a thinking phase. A model that cannot stop thinking has no `on`:
its default stands as a level at its own place, marked "default". Examples:
GLM-5.3 always thinks → `low`, `high`, `max (default)`; DeepSeek-V4 → `off`, `on`, `max`; Qwen3-VL Instruct → `off`.
Models without a llama-swap entry (Ollama, cloud) offer `off`/`on`.

The mechanism is model-agnostic with one exception: the llama.cpp path turns thinking off for models with "instruct" in their name, even against the user's choice (`backends/llamacpp.py`; a special case by name, proposed for removal). Otherwise:

```
GGUF (tokenizer.chat_template)
  |-- gguf_utils.detect_reasoning_levels()
  |     Scans the embedded Jinja template for comparisons against the
  |     `reasoning_effort` variable (== / != / in [...]). Whatever the
  |     template compares against IS the supported level set, because
  |     chat_template_kwargs are passed 1:1 as Jinja variables.
  |
  |-- gguf_utils.detect_reasoning_info() -> ReasoningInfo
  |     levels + default, plus `thinks` (the template has a thinking phase:
  |     <think>, levels or a switch) and `switchable` (the template reads
  |     the switch AIfred sends: `enable_thinking`; a bundled prompt encoder
  |     branches on `thinking_mode`, which vLLM's tokenizer derives from it).
  |
  |-- model_vram_cache.json: reasoning_levels, reasoning_default, thinks,
  |     thinking_switchable (persisted; resolve_reasoning_info, cache-first)
  |     Filled lazily on first use (header read, ms) and force-refreshed
  |     during calibration (re-download may change it).
  |
  |-- Setting: agent_tuning[agent].thinking_by_model {model: choice}
  |     One choice per row AND model: "off", "on" (the level the template
  |     picks itself) or a level. The model counts as its base model
  |     (agent_settings.thinking_model_key: without llama-swap variants such
  |     as -visiond/-tts-…/-vlm-…/-speed, without a machine prefix). A model
  |     without an entry thinks as the agent's toggles.thinking in
  |     agents.json says. Read only via agent_settings.thinking_mode /
  |     persisted_thinking_mode (with the model the request goes to), written
  |     only via set_thinking_mode (the dropdown). A model switch copies
  |     nothing: the other model has its own entry. effective_thinking_mode
  |     turns the stored choice into one the model can carry out (no thinking
  |     phase → off; off on an always-thinking model → its default; an
  |     unknown level → its default) without rewriting what is stored.
  |
  v
build_llm_options(view, agent, model, temperature, num_ctx) -> LLMOptions;
thinking_fields(choice) -> enable_thinking, reasoning_effort
  |-- llamacpp/_build_extra_body(): chat_template_kwargs =
  |     {"enable_thinking": ..., "reasoning_effort": "<level>"}
  |-- ollama: ignores effort (bool `think` only)
```

Any model whose template compares `reasoning_effort` against string
literals (DeepSeek-V4 `max`, gpt-oss-style `low/medium/high`, future
models) gets its levels detected automatically. Templates using a
different variable name would need an extension in
`detect_reasoning_levels()` — deliberately conservative to avoid false
positives.

---

## Summary: What Goes Through the Pipeline

| Path | Entry Point | Pipeline? | Notes |
|---|---|---|---|
| Direct answer (chat, channel, address) | `turn_service._respond` | `stream_call` -> `run_llm_stream` | Full toolkit, agent decides |
| Image, main model sees | `turn_service._look_at_images` + `_direct_call` | `stream_call` -> `run_llm_stream` | Images in the request (VL Direct) |
| Image, other describer | `turn_images.describe_images` | **No** — `llm_client.chat()` | One description in the user turn, then a normal turn |
| Critical review, auto-consensus | `turn_debate._critique` | `stream_call` -> `run_llm_stream` | Critic, judge, author; voting only in auto-consensus |
| Tribunal | `turn_debate._tribunal` | `stream_call` -> `run_llm_stream` | Rounds of attack and defense, judge last |
| Recensio | `turn_debate._recensio` | `stream_call` -> `run_llm_stream` | Censor with a restricted toolkit, auctor corrects |
| Symposion | `turn_debate.run_symposion` | `stream_call` -> `run_llm_stream` | N agents sequentially |
| Message Hub | `process_inbound` | `turn_service.run_turn` -> `stream_call` -> `run_llm_stream` | Stateless, the same turn as the Vue chat, auto-reply |
| Compression | `turn_service.compress_history`, `compress_tool_loop` | **No** — summary by the next speaker's model | Before the turn, between speakers, in the tool loop |
| Title | `session_title.generate_session_title` | **No** — `client.chat()` | Non-streaming, 300 s timeout |
| Intent | `detect_query_intent_and_addressee` | **No** — `client.chat()` | Automatik model |

---

## Key Files

| File | Role |
|---|---|
| `aifred/lib/turn_service.py` | The server's turn (Vue chat, channels, scheduler, alerts): `run_turn`, `TurnOrigin`, `TurnOutcome`, `stream_call`, `finish_call`, `compress_history` |
| `aifred/lib/turn_debate.py` | Multi-agent modes: `plan_turn`, `run_debate`, `run_symposion` |
| `aifred/lib/debate_seats.py` | Seats (author, critic, judge): `seat_prompt`, `round_rules`, `session_seats` |
| `aifred/lib/turn_images.py` | Images of a turn: the seeing model's request, description, turning and cutting |
| `aifred/lib/llm_pipeline.py` | Unified chunk-processing pipeline (`run_llm_stream`, `PipelineResult`) |
| `aifred/lib/context_manager.py` | History compression (`summarize_history_if_needed`, `compress_tool_loop`, `LoopCompression`) |
| `aifred/lib/session_title.py` | Session title (`first_exchange`, `generate_session_title`) |
| `aifred/lib/message_processor.py` | Message Hub pipeline (`process_inbound`, `outbound_text`) |
| `aifred/lib/llm_client.py` | `LLMClient`, `build_llm_options()`, backend abstraction |
| `aifred/lib/message_builder.py` | `build_messages_from_llm_history()` — perspective transformation |
| `aifred/lib/prompt_loader.py` | Prompt layers: `get_agent_direct_prompt`, `get_agent_system_prompt`, `get_seat_system_prompt` |
| `aifred/lib/agent_memory.py` | `prepare_agent_toolkit()` — memory + plugin tools |
| `aifred/lib/agent_config.py` | Agent configuration (display names, emojis, models) |
| `aifred/lib/intent_detector.py` | Language, addressee and mode switch detection |
