# Research Pipeline & Pre-Processing

> **Deutsche Version:** [research-pipeline.md](../../de/architecture/research-pipeline.md)

How a message is pre-processed, how an agent goes to the web, and what the
research pipeline does. For the full path of a
single LLM call see [LLM Call Architecture](llm-call.md).

---

## When research happens

Every agent gets its full toolkit in every channel (browser, Message Hub,
scheduler) and decides itself whether to go to the web with `web_search` /
`web_fetch`. Anyone who does not want research says so in the question. Until
October 2026 there were four research modes for this (Automatik, Knowledge,
Web Quick, Web Deep); they were removed (archive: tag
`archive-research-modes-2026-10-09`).

Every research runs fresh — there is deliberately no result cache: how similar
two questions are says nothing about whether an earlier answer still holds
(weather, prices, news). Within a conversation the results stay in the history
anyway.

---

## Pre-processing

```
Intent + addressee detection
├─ One combined call to the Automatik-LLM
├─ Prompt: automatik/intent_detection
├─ Response: "INTENT|ADDRESSEE|LANGUAGE|MODE_SWITCH|IS_PURE_COMMAND"
│  └─ e.g. "FACTUAL||DE||FALSE" or "MIXED|sokrates|DE||FALSE"
├─ Temperature:
│  ├─ Auto mode: FACTUAL=0.2, MIXED=0.5, CREATIVE=1.0
│  └─ Manual mode: intent ignored, manual value used
├─ Addressee: direct agent addressing (sokrates/aifred/salomo/…)
└─ Mode switch: spoken/typed config changes ("start a tribunal")
```

A directly addressed agent is activated immediately, regardless of the
temperature setting. The message itself is never rewritten by the
detector (see [mode switches](multi-agent.md#mode-switches-by-voice-or-text)).

Before every LLM call the history-compression check runs (70 % of the smallest
context window, see [Configuration → History compression](../guides/configuration.md#history-compression)).

---

## The pipeline

The `web_search` tool runs the pipeline (`execute_research()` in
`aifred/lib/research_tools.py`, in the Message Hub `hub_web_search()`); the
agent writes the 1-3 search queries itself in its tool call:

```
1. Multi-API web search
   ├─ Round-robin: query 1 → SearXNG (self-hosted), 2 → Tavily, 3 → Brave
   │  (API keys optional), automatic fallback if an API fails;
   │  a single query goes to all APIs in parallel
   ├─ URL deduplication across APIs
   └─ Non-scrapable domains filtered (data/non_scrapable_domains.txt:
      video platforms, social media)

2. URL ranking
   ├─ Automatik-LLM, prompt: automatik/url_ranking (numeric output)
   ├─ Sees the conversation history (follow-up questions rank correctly)
   └─ Top 7 (RESEARCH_SCRAPE_URLS)

3. Parallel scraping
   ├─ trafilatura first; Playwright (headless Chromium) if it returns
   │  fewer than 800 words (PLAYWRIGHT_FALLBACK_THRESHOLD)
   ├─ PDFs (guidelines, papers) extracted via PyMuPDF
   ├─ No Playwright retry for failed downloads (404, timeout, bot protection)
   ├─ Failed sources are listed with their error reason
   └─ Main model preloads in parallel (not for vLLM — it stays resident)

4. Context building
   ├─ build_context(): scraped text, token-aware
   └─ Sources collapsible for the UI (used + failed sources)
```

SearXNG is the primary search backend and runs as a container next to ChromaDB
(`docker/docker-compose.yml`); Tavily and Brave are optional extras with API
keys in `.env`.

**How the results reach the agent:**
- **Browser:** the `web_search` result, fenced as untrusted data
  (prompt-injection guard), goes back into the tool loop; the agent may search
  again or read a single page with `web_fetch`. The sources box goes above the
  answer.
- **Message Hub** (Discord, e-mail, …): `hub_web_search()` — same building
  blocks without browser state.

---

## Decision flow

```
USER INPUT
    │
    ▼
┌──────────────────────────────────┐
│ Intent + addressee detection     │
│ (Automatik-LLM)                  │
└──────────────────────────────────┘
    │
    ▼
Agent answers with its full toolkit
    │
    └─ web_search call? ──► Research pipeline
         (queries from the agent, top 7)
         └─ result back into the tool loop
```

---

## Automatik-LLM prompts

The Automatik-LLM runs these decisions with thinking disabled (fast, structured
output). Prompts in `prompts/{de,en}/automatik/`:

| Prompt | Language | When | Purpose |
|--------|----------|------|---------|
| `intent_detection.txt` | EN only | Pre-processing | Intent, addressee, language, mode switch |
| `url_ranking.txt` | EN only | Step 2 | Rank URLs by relevance (numeric indices) |

*EN only* because the output is structured or numeric and the language does
not affect it.

**Model choice:** a medium instruct model works best for the Automatik role —
small 4B models struggle with nuanced addressee detection ("What does Alfred
think about Salomo's answer?"). The option **"(same as AIfred-LLM)"** reuses the
main model without extra VRAM.

---

## Code map

**Entry points**
- `aifred/state/_chat_mixin.py` — `send_message()`
- `aifred/lib/multi_agent.py` — `run_generic_agent_direct_response()`: one answer
  path for all agents (toolkit, messages)

**Web research**
- `aifred/lib/research_tools.py` — `execute_research()` (browser) and
  `hub_web_search()` (Message Hub)
- `aifred/plugins/tools/research/` — `web_search` / `web_fetch` tools
- `aifred/lib/research/query_processor.py` — multi-API search
- `aifred/lib/research/url_ranker.py` — LLM-based URL ranking
- `aifred/lib/research/scraper_orchestrator.py` — parallel scraping
- `aifred/lib/tools/` — search APIs, scraper, `build_context()`

**Document RAG**
- `aifred/lib/document_store.py` — ChromaDB documents collection:
  token-accurate chunking, `delete + upsert` for clean re-indexing, dual
  embedding functions (index/query mode), folder filter + chunk-neighbour
  retrieval in `search()`
- `aifred/lib/file_manager.py` — single source of truth for file-system +
  ChromaDB operations (Document UI and Workspace plugin)

**Supporting modules**
- `aifred/lib/embeddings.py` — bge-m3 embedding function (llama-swap embed
  profile first, Ollama second; index mode → GPU, query mode → CPU)
- `aifred/lib/agent_memory.py` — per-agent memory collections and the memory
  tools (`read_memory`, `store_memory`, `update_memory`, `delete_memory`)
- `aifred/lib/tool_output_cap.py` — token budget for tool results (75 % input
  ratio, JSON-aware truncation)
- `aifred/lib/intent_detector.py` — intent, addressee, temperature, mode switch
