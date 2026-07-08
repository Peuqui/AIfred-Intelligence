# LLM Call Architecture

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
     | _chat_mixin.py  |   | _chat_mixin.py  |    | msg_processor.py|
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
| `tool_result` | `result: str` | Tool execution result |
| `thinking` | `text: str` | Chain-of-thought block |
| `done` | `metrics: dict` | Stream end with backend metrics |
| `pipeline_result` | `result: PipelineResult` | Final aggregated result |

`PipelineResult` contains: full response text, cleaned text (no thinking), thinking HTML, inference metadata, TTFT, timing, tracked URLs, sandbox URLs.

---

## Call Paths in Detail

### 1. Normal Chat (OwnKnowledge)

User sends message with `research_mode="none"`.

```
_chat_mixin.py: send_message()
  |-- Intent Detection (detect_query_intent_and_addressee)
  |     Returns: intent, addressee, detected_language
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
  |     -> Memory tools only (store_memory, recall)
  |-- Messages (build_messages_from_llm_history with perspective)
  |-- Temperature (auto from intent or manual)
  |-- LLM options (build_llm_options)
  |
  v
multi_agent.py: _stream_agent_to_history()
  |-- State setup: set_current_agent, vLLM model ensure, TTS init
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
  |     |-- research/orchestrator.py: perform_agent_research()
  |     |     1. ChromaDB cache check
  |     |     2. Query optimization (LLM generates search terms)
  |     |     3. Web search (DuckDuckGo/Brave/Tavily/SearXNG)
  |     |     4. URL ranking (LLM evaluates relevance)
  |     |     5. Web scraping (top N URLs)
  |     |     6. Context building
  |     |
  |     v
  |     state._research_context = prepared research results
  |
  |-- system_prompt += research_context (injected as knowledge)
  |-- _stream_agent_to_history() -> run_llm_stream()
```

**quick** = top 3 URLs. **deep** = top 8 URLs + deeper analysis.

### 4. Vision Pipeline (Image Upload)

Bypasses `_run_agent_direct_response()` and calls `call_llm()` directly:

```
_chat_mixin.py: _process_vision_request()
  |-- Vision model selection (optional -speed variant for llamacpp)
  |-- call_llm(agent="vision", multimodal_content=[image_parts])
  |     |-- System prompt: get_agent_system_prompt("vision", prompt_key)
  |     |-- temperature = vision_temperature (manual)
  |     |-- enable_thinking = vision_thinking
  |     v
  |     run_llm_stream()  <-- PIPELINE
  |
  |-- Response saved to chat_history + llm_history
  |-- generate_session_title() (async)
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
    |     prompt: sokrates_critic_prompt(round_num=N)
    |     _stream_agent_to_history("sokrates") -> run_llm_stream()
    |
    |-- VOTING: count_lgtm_votes()
    |     [LGTM] = approve, [WEITER] = override (forces re-refinement)
    |     Consensus at 2/3 (majority) or 3/3 (unanimous)
    |
    |-- If consensus -> BREAK
    |
    |-- AIFRED refinement
          prompt: aifred_refinement_prompt
          _stream_agent_to_history("aifred") -> run_llm_stream()
```

#### Tribunal

```
run_tribunal()
  |
  Fixed rounds (no early exit):
    |-- SOKRATES (prosecutor): sokrates_tribunal_prompt
    |-- AIFRED (defense): aifred_defense_prompt
  |
  After all rounds:
    |-- SALOMO (judge): salomo_judge_prompt
```

#### Devil's Advocate

Like Auto-Consensus, but Sokrates uses `get_sokrates_devils_advocate_prompt()`. Response parsed into PRO and CONTRA sections via `parse_pro_contra()`.

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
imap_listener.py: Email received via IMAP IDLE
  |
  v
message_processor.py: process_inbound(InboundMessage)
  |
  |-- 1. detect_target_agent(text) -> "aifred" / "sokrates" / "salomo"
  |-- 2. Find or create session (routing_table)
  |-- 3. Save incoming message to session
  |       Toast notification in browser (even without open session)
  |-- 4. _call_engine(email_context, session_id, agent)
  |       |-- Settings from settings.json (no State)
  |       |-- LLM history from session file
  |       |-- num_ctx: get_model_native_context() (no State)
  |       |-- Toolkit: prepare_agent_toolkit(research_tools_enabled=True)
  |       |       -> Full plugins (web, email, EPIM, documents, etc.)
  |       |-- call_llm(external_toolkit=toolkit, num_ctx_manual=True)
  |       |     |
  |       |     v
  |       |     run_llm_stream()  <-- PIPELINE
  |       |       Chunks collected (no UI streaming)
  |       |       Tool call/result -> debug sink
  |       |
  |       v
  |       response_clean returned
  |
  |-- 5. Save response to session
  |-- 6. Auto-reply via SMTP (if enabled)
  |-- 7. generate_session_title() (if first message)
```

### 8. Session Title Generation

Does **not** use `run_llm_stream()` — simple non-streaming `client.chat()` with 30s timeout.

```
llm_engine.py: generate_session_title()
  |-- Input: user_text (max 500 chars) + ai_response (max 500 chars)
  |-- Prompt: load_prompt("utility/chat_title")
  |-- Model: aifred_model (from settings)
  |-- Options: temperature=0.3, num_predict=300, enable_thinking=False
  |-- llm_client.chat() (NOT streaming, 30s timeout)
  |-- Cleanup: quotes, punctuation, max 80 chars
  |-- update_session_title(session_id, title)
```

### 9. Intent Detection

Also does **not** use `run_llm_stream()` — uses the small automatik model.

```
intent_detector.py: detect_query_intent_and_addressee()
  |-- Model: automatik_model (small, fast)
  |-- Context: AUTOMATIK_LLM_NUM_CTX (4096)
  |-- Returns: (intent, addressee, detected_language)
  |     intent in: FAKTISCH, KREATIV, GEMISCHT
  |     addressee in: None, aifred, sokrates, salomo
  |     language in: de, en
```

---

## Summary: What Goes Through the Pipeline

| Path | Entry Point | Pipeline? | Notes |
|---|---|---|---|
| OwnKnowledge | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Memory-only toolkit |
| Automatik | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Full toolkit, agent decides |
| Quick/Deep | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Research context pre-injected |
| Vision | `_process_vision_request` | `call_llm` -> `run_llm_stream` | Multimodal, own model |
| Direct Agent | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Agent-specific perspective |
| Auto-Consensus | `run_sokrates_analysis` | `_stream_agent_to_history` -> `run_llm_stream` | Multiple rounds, voting |
| Tribunal | `run_tribunal` | `_stream_agent_to_history` -> `run_llm_stream` | 3 agents, Salomo judges |
| Devil's Advocate | `run_sokrates_analysis` | `_stream_agent_to_history` -> `run_llm_stream` | Pro/Contra parsing |
| Symposion | `run_symposion` | `_stream_agent_to_history` -> `run_llm_stream` | N agents sequentially |
| Message Hub | `process_inbound` | `call_llm` -> `run_llm_stream` | Stateless, auto-reply |
| Title Gen | `generate_session_title` | **No** — `client.chat()` | Non-streaming, 30s timeout |
| Intent | `detect_query_intent` | **No** — `client.chat()` | Automatik model |

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
