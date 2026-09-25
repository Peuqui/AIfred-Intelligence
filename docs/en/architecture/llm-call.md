# LLM Call Architecture

> **Deutsche Version:** [llm-call.md](../../de/architecture/llm-call.md)

**All LLM inference paths in AIfred Intelligence and how they connect.**

---

## Overview

Every LLM call in AIfred flows through a unified chunk-processing pipeline (`run_llm_stream()` in `llm_pipeline.py`). This pipeline handles TTFT measurement, tool-call tracking, URL collection, sandbox output extraction, thinking block processing, and inference metadata — regardless of whether the call comes from the chat UI, a multi-agent debate, the vision pipeline, or the Message Hub.

```
                          +---------------------+
                          |    User / E-Mail     |
                          +----------+----------+
                                     |
              +----------------------+----------------------+
              |                      |                      |
     +--------v--------+   +--------v--------+    +--------v--------+
     |   Chat (UI)     |   |  Vision (Image) |    |  Message Hub    |
     |  send_message() |   | _process_vision |    | process_inbound |
     | _chat_mixin.py  |   | _chat_mixin.py  |    |message_processor|
     +--------+--------+   +--------+--------+    +--------+--------+
              |                      |                      |
              |               +------v------+        +------v------+
              |               |  call_llm() |        |_call_engine |
              |               |llm_engine.py|        |  > call_llm |
              |               +------+------+        +------+------+
              |                      |                      |
     +--------v----------+          |                      |
     |_run_agent_direct   |         |                      |
     |   _response()      |         |                      |
     | multi_agent.py     |         |                      |
     +--------+-----------+         |                      |
              |                     |                      |
     +--------v----------+         |                      |
     |_stream_agent_to   |         |                      |
     |   _history()      |         |                      |
     | multi_agent.py    |         |                      |
     +--------+----------+         |                      |
              |                     |                      |
              +---------------------+----------------------+
                                    |
                          +---------v---------+
                          | run_llm_stream()  |   <-- UNIFIED PIPELINE
                          | llm_pipeline.py   |
                          +---------+---------+
                                    |
                          +---------v---------+
                          | chat_stream()     |
                          | llm_client.py     |
                          +---------+---------+
                                    |
                          +---------v---------+
                          |  Backend (LLM)    |
                          | Ollama / llama.cpp|
                          | vLLM              |
                          | Cloud API         |
                          +-------------------+
```

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
| `done` | `metrics: dict` | Stream end with backend metrics |
| `pipeline_result` | `result: PipelineResult` | Final aggregated result |

`PipelineResult` contains: full response text (`text`, incl. `<think>` blocks), cleaned text (`text_clean`), inference metadata (`metadata_dict`, `metadata_display`, `debug_msg`, `metrics`), `ttft`, `inference_time`, the measured work (`work`), bubble artifacts (`artifacts` — sources from `web_fetch`, sandbox output, VLM output), `final_text` (answer after the last tool call), `history_notes`, plus the flags `silent_reply` and `truncated`.

---

## Call Paths in Detail

### 1. Normal Chat (OwnKnowledge)

User sends message with `research_mode="none"`.

```
_chat_mixin.py: send_message()
  |-- Intent Detection (detect_query_intent_and_addressee)
  |     Returns: intent, addressee, detected_language,
  |              mode_switch_updates, is_pure_command, raw_response
  |-- History Compression (if >70% context utilization)
  |-- run_generic_agent_direct_response("aifred", research_mode="none")
  |
  v
multi_agent.py: _run_agent_direct_response()
  |-- LLMClient init (backend + URL from State)
  |-- Agent model selection (state._effective_model_id)
  |-- num_ctx (get_agent_num_ctx)
  |-- System prompt (get_agent_direct_prompt)
  |-- Toolkit: prepare_agent_toolkit(research_tools_enabled=False)
  |     -> Memory tools only (store_memory, update_memory, delete_memory,
  |        read_memory); recalled memories are injected as context
  |-- Messages (build_messages_from_llm_history with perspective)
  |-- Temperature (auto from intent or manual)
  |-- LLM options (build_llm_options)
  |
  v
multi_agent.py: _stream_agent_to_history()
  |-- State setup: state._set_current_agent, vLLM model ensure, TTS init
  |
  v
llm_pipeline.py: run_llm_stream()  <-- PIPELINE
  |-- chat_stream_with_retry() -> llm_client.chat_stream()
  |-- Chunk processing with tracking
  |-- PipelineResult
```

### 2. Automatik Mode

Identical to OwnKnowledge, except:

- `research_tools_enabled=True` in `prepare_agent_toolkit()`
- Toolkit includes: web_search, web_fetch, calculate, execute_code, document tools, etc.
- System prompt includes tool descriptions
- **The LLM autonomously decides** whether to use tools

### 3. Quick/Deep Research

Forced web research **before** the LLM call:

```
_run_agent_direct_response(..., research_mode="quick")
  |-- _execute_forced_research(state, query, "quick")
  |     |-- research_tools.py: execute_research()
  |     |     1. Query generation (Automatik-LLM generates search terms)
  |     |     2. Web search (SearXNG/Tavily/Brave, round-robin per query)
  |     |     3. URL ranking (LLM evaluates relevance)
  |     |     4. Web scraping (top N URLs)
  |     |     5. Context building
  |     |
  |     v
  |     state._research_context = prepared research results
  |
  |-- inject_before_question(messages, wrap_untrusted_data(research_context, 'web_research'))
  |-- _stream_agent_to_history() -> run_llm_stream()
```

**quick** = top 3 URLs (`RESEARCH_QUICK_URLS`). **deep** = top 7 URLs (`RESEARCH_DEEP_URLS`). Every research runs fresh (no result cache). The toolkit is the same as in Automatik mode (`research_tools_enabled` is true for every `research_mode` other than `"none"`).

### 4. Vision Pipeline (Image Upload)

Bypasses `_run_agent_direct_response()` and calls `call_llm()` directly:

```
_chat_mixin.py: _process_vision_request()
  |-- Model selection: _vl_choice() -> (model_id, settings bucket)
  |     A vision-capable main (AIfred) model handles the image itself;
  |     the vision model only steps in for non-vision main models
  |-- Acting agent = active agent (memory, tools, personality)
  |-- Toolkit: prepare_agent_toolkit(research_tools_enabled=True)
  |-- call_llm(agent=<acting agent>, multimodal_content=content_parts,
  |            external_toolkit=..., vision_task_addon=...)
  |     |-- System prompt: get_agent_system_prompt(agent, "task")
  |     |     + vision_task_addon
  |     |-- temperature_mode="manual", temperature from the bucket
  |     |     of the model that runs the turn
  |     |-- LLM options: build_llm_options(state, agent, ...)
  |     v
  |     run_llm_stream()  <-- PIPELINE
  |
  |-- Response saved to chat_history + llm_history
  |-- _generate_session_title() (fire-and-forget task)
```

### 5. Multi-Agent Debate Modes

All debate modes use `_stream_agent_to_history()` -> `run_llm_stream()` for each agent turn. The **perspective transformation** in `build_messages_from_llm_history()` ensures each agent sees the conversation from its own point of view:
- Own messages -> `role: "assistant"`
- Other agents' messages -> `role: "user"` (with labels like `[SOKRATES]:`)

#### Auto-Consensus

```
run_sokrates_analysis(mode="auto_consensus")
  |
  Loop (max_debate_rounds):
    |-- SOKRATES critique
    |     prompt: get_sokrates_critic_prompt(round_num=N)
    |     _stream_agent_to_history("sokrates") -> run_llm_stream()
    |
    |-- SALOMO synthesis
    |     prompt: get_salomo_mediator_prompt(round_num=N)
    |
    |-- VOTING: count_lgtm_votes(aifred, sokrates, salomo)
    |     [LGTM] = approve, [WEITER] = override (forces re-refinement)
    |     Consensus at 2/3 (majority) or 3/3 (unanimous)
    |
    |-- If consensus -> BREAK
    |
    |-- AIFRED refinement
          prompt: get_aifred_refinement_prompt(...)
          _stream_agent_to_history("aifred") -> run_llm_stream()
```

#### Tribunal

```
run_tribunal()
  |
  Fixed rounds (no early exit):
    |-- SOKRATES (prosecutor): get_sokrates_tribunal_prompt(round_num=N)
    |-- AIFRED (defense): get_aifred_defense_prompt(...)
  |
  After all rounds:
    |-- SALOMO (judge): get_salomo_judge_prompt()
```

#### Devil's Advocate

Runs through `run_sokrates_analysis()` like Auto-Consensus, but with a single round, and Sokrates uses `get_sokrates_devils_advocate_prompt()`. Response parsed into PRO and CONTRA sections via `parse_pro_contra()`.

#### Symposion

```
run_symposion()
  |
  For each agent in state.symposion_agents:
    |-- System prompt + toolkit for this agent
    |-- Messages with agent-specific perspective
    |-- _stream_agent_to_history(agent_id) -> run_llm_stream()
```

### 6. Direct Agent Addressing

User writes "Sokrates, what do you think?" -> Intent detection recognizes `addressee="sokrates"`.

```
run_generic_agent_direct_response(agent_id="sokrates", ...)
  |-- Agent config: get_agent_config("sokrates")
  |     -> display_name="Sokrates", emoji, model, temperature
  |-- _run_agent_direct_response(agent="sokrates")
  |     -> perspective="sokrates" (own messages = assistant)
  |     -> _stream_agent_to_history("sokrates") -> run_llm_stream()
```

Same for Salomo and custom agents.

### 7. Message Hub (Inbound Email)

Completely stateless — no Reflex State needed, only settings file and session storage.

```
email_channel/__init__.py: listener_loop() — email received via IMAP IDLE
  |
  v
message_processor.py: dispatch_inbound() -> process_inbound(InboundMessage)
  |
  |-- 1. Find or create session (routing_table) — before intent
  |       detection, so session_scope is active for the LLM calls
  |       hub_notification_scope: toast "received" in the browser
  |-- 2. detect_target_agent_via_llm(text)
  |       -> (agent_id, intent, detected_language, mode_switch_updates)
  |       Wraps detect_query_intent_and_addressee() (automatik model);
  |       then routing priority: metadata["wake_agent"] > addressee
  |       > session active_agent > "aifred"
  |-- 3. Save incoming message to session (save_user_to_session)
  |-- 4. _call_engine(user_text, session_id, agent, ...)
  |       |-- Settings from settings.json (no State)
  |       |-- LLM history from session file
  |       |-- num_ctx: get_stateless_num_ctx() (no State)
  |       |-- Toolkit: prepare_agent_toolkit(research_tools_enabled=True)
  |       |       -> Full plugins (web, email, EPIM, documents, etc.)
  |       |-- call_llm(external_toolkit=toolkit,
  |       |            num_ctx_manual_enabled=True, num_ctx_manual_value=num_ctx)
  |       |     |
  |       |     v
  |       |     run_llm_stream()  <-- PIPELINE
  |       |       Chunks collected (no UI streaming)
  |       |       Tool call/result -> debug sink
  |       |
  |       v
  |       response_clean (+ display, final, metadata, history_notes) returned
  |
  |-- 5. Save response to session (_append_response)
  |-- 6. Auto-reply via plugin.send_reply() (if the channel has always_reply
  |       or its auto-reply toggle is on — email: SMTP)
  |-- 7. generate_session_title() (if the session has no title yet)
```

### 8. Session Title Generation

Does **not** use `run_llm_stream()` — simple non-streaming `client.chat()` with a hard timeout (`SESSION_TITLE_TIMEOUT_SECONDS`, 300 s).

```
llm_engine.py: generate_session_title()
  |-- Input: user_text (max 500 chars) + ai_response (max 500 chars)
  |-- Prompt: load_prompt("utility/chat_title")
  |-- Model: model_override, else the automatik model from settings
  |     (falls back to aifred_model when "(same as AIfred-LLM)" is selected)
  |-- Options: temperature=0.3, num_predict=SESSION_TITLE_NUM_PREDICT (2000),
  |     enable_thinking=False, num_ctx=AUTOMATIK_LLM_NUM_CTX
  |-- llm_client.chat() (NOT streaming, SESSION_TITLE_TIMEOUT_SECONDS timeout)
  |-- Cleanup: thinking blocks, quotes, trailing punctuation, max 80 chars
  |-- update_session_title(session_id, title)
```

### 9. Intent Detection

Also does **not** use `run_llm_stream()` — uses the small automatik model.

```
intent_detector.py: detect_query_intent_and_addressee()
  |-- Model: automatik_model (small, fast)
  |-- Context: AUTOMATIK_LLM_NUM_CTX (12288) if the automatik model differs
  |     from AIfred's; with the same model AIfred's max_context (no reload)
  |-- Options: temperature=0.2, enable_thinking=False
  |-- Returns: (intent, addressee, detected_language,
  |            mode_switch_updates, is_pure_command, raw_response)
  |     intent in: FAKTISCH, KREATIV, GEMISCHT
  |     addressee in: None, aifred, sokrates, salomo
  |     language in: de, en
  |     mode_switch_updates: requested config change ({} if none)
  |     is_pure_command: message is ONLY a mode-switch command
```

---

## Thinking Modes & Reasoning-Effort Levels

Each agent has a thinking-mode dropdown (🧠) instead of a plain on/off
toggle. The option list is model-specific: `off` and `on` always exist;
models whose chat template supports graded thinking depth contribute
extra entries (e.g. DeepSeek-V4: `max` = Think Max — `on` is the
template's default level, Think High).

The mechanism is fully model-agnostic — no model names are hardcoded:

```
GGUF (tokenizer.chat_template)
  |-- gguf_utils.detect_reasoning_levels()
  |     Scans the embedded Jinja template for comparisons against the
  |     `reasoning_effort` variable (== / != / in [...]). Whatever the
  |     template compares against IS the supported level set, because
  |     chat_template_kwargs are passed 1:1 as Jinja variables.
  |
  |-- model_vram_cache.json: "reasoning_levels" (persisted)
  |     Filled lazily on model switch/startup (header read, ms) and
  |     force-refreshed during calibration (re-download may change it).
  |
  |-- State: agent_tuning[agent].thinking (bool)
  |     + agent_tuning[agent].reasoning_effort (str)
  |     Dropdown maps: off -> thinking=False; on -> thinking=True,
  |     effort=""; <level> -> thinking=True, effort=<level>.
  |
  v
LLMOptions.reasoning_effort
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
| OwnKnowledge | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Memory-only toolkit |
| Automatik | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Full toolkit, agent decides |
| Quick/Deep | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Research context pre-injected |
| Vision | `_process_vision_request` | `call_llm` -> `run_llm_stream` | Multimodal, acts as the active agent |
| Direct Agent | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Agent-specific perspective |
| Auto-Consensus | `run_sokrates_analysis` | `_stream_agent_to_history` -> `run_llm_stream` | Multiple rounds, voting |
| Tribunal | `run_tribunal` | `_stream_agent_to_history` -> `run_llm_stream` | 3 agents, Salomo judges |
| Devil's Advocate | `run_sokrates_analysis` | `_stream_agent_to_history` -> `run_llm_stream` | Pro/Contra parsing |
| Symposion | `run_symposion` | `_stream_agent_to_history` -> `run_llm_stream` | N agents sequentially |
| Message Hub | `process_inbound` | `call_llm` -> `run_llm_stream` | Stateless, auto-reply |
| Title Gen | `generate_session_title` | **No** — `client.chat()` | Non-streaming, 300 s timeout |
| Intent | `detect_query_intent_and_addressee` | **No** — `client.chat()` | Automatik model |

---

## Key Files

| File | Role |
|---|---|
| `aifred/lib/llm_pipeline.py` | Unified chunk-processing pipeline (`run_llm_stream`, `PipelineResult`) |
| `aifred/lib/multi_agent.py` | Chat streaming (`_stream_agent_to_history`), debate modes, agent dispatch |
| `aifred/lib/llm_engine.py` | `call_llm()` (Vision + Hub entry), `generate_session_title()` |
| `aifred/lib/message_processor.py` | Message Hub pipeline (`process_inbound`, `_call_engine`) |
| `aifred/lib/llm_client.py` | `LLMClient`, `build_llm_options()`, backend abstraction |
| `aifred/lib/message_builder.py` | `build_messages_from_llm_history()` — perspective transformation |
| `aifred/lib/agent_memory.py` | `prepare_agent_toolkit()` — memory + plugin tools |
| `aifred/lib/agent_config.py` | Agent configuration (display names, emojis, models) |
| `aifred/lib/intent_detector.py` | Intent/language/addressee detection |
| `aifred/state/_chat_mixin.py` | UI entry point (`send_message`, `_process_vision_request`) |
