# AIfred Intelligence - Architecture & Vector Cache Integration Analysis

## Executive Summary

AIfred Intelligence is a sophisticated AI assistant with a Reflex-based web UI that implements a multi-phase research pipeline. The system has **TWO vector cache implementations**: 
- **vector_cache.py** - Basic ChromaDB wrapper (currently used in state.py with lazy initialization)
- **vector_cache_v2.py** - Advanced thread-safe implementation with dedicated worker thread (used in context_builder.py)

**The hanging issue occurs because vector cache is DISABLED in conversation_handler.py (line 92)** with a comment indicating it causes Reflex timeout issues. The current flow uses lazy initialization but the cache check is commented out.

---

## 1. Overall System Architecture

### 1.1 Application Flow Diagram

```
┌─────────────────────────────────────────────────────────┐
│  Reflex Web UI (aifred/aifred.py)                      │
│  - Single column layout                                 │
│  - Chat history, settings, debug console               │
└────────────────┬────────────────────────────────────────┘
                 │ User Input: send_message()
                 ▼
┌─────────────────────────────────────────────────────────┐
│ AIState.send_message() (aifred/state.py:290)            │
│ - Routes to chat_interactive_mode (Automatik)           │
│ - Or perform_agent_research (Quick/Deep)                │
└────────┬──────────────────────────┬─────────────────────┘
         │ Automatik Mode           │ Quick/Deep Mode
         ▼                          ▼
    ┌────────────────┐         ┌──────────────────────┐
    │ chat_          │         │ perform_agent_       │
    │ interactive_   │         │ research() [Phase 1] │
    │ mode()         │         │ Orchestrator         │
    │ [Conv Handler] │         │ (orchestrator.py)    │
    └────────┬───────┘         └──────┬───────────────┘
             │                        │
             ├─ Explicit Research?    │
             ├─ Vector Cache Check    ├─ Cache Handler (cache_handler.py)
             │  (DISABLED - line 92)  │  - Check research cache
             ├─ LLM Decision?         │  - Return cached answer if hit
             │  (Decision LLM)        │
             │                        ├─ Query Processor (query_processor.py)
             └─ Research if needed──┬─┴─ Query optimization + Web search
                                    │
                                    ├─ Scraper Orchestrator (scraper_orchestrator.py)
                                    │  - Parallel web scraping (ThreadPoolExecutor)
                                    │  - LLM preloading during scraping
                                    │
                                    └─ Context Builder (context_builder.py)
                                       - Build context from scraped sources
                                       - LLM inference
                                       ├─ Auto-learn: Vector Cache v2
                                       │  (add_to_cache_async)
                                       └─ Return answer + history
```

### 1.2 Module Organization

```
aifred/
├── aifred.py                    # Reflex UI (main page, components)
├── state.py                     # Reflex State Management (send_message entry point)
├── backends/                    # LLM Backend Adapters
│   ├── base.py                 # Base backend interface
│   ├── ollama.py               # Ollama implementation
│   └── vllm.py                 # vLLM implementation
└── lib/                        # Core Libraries
    ├── __init__.py            # Exports (VectorCache, perform_agent_research)
    ├── config.py              # Global configuration
    ├── llm_client.py           # Unified async LLM client
    ├── conversation_handler.py # Automatik mode (with VectorCache check - DISABLED)
    ├── context_manager.py      # Context window calculation
    ├── message_builder.py      # Message history construction
    ├── prompt_loader.py        # Prompt management (multi-language)
    ├── query_optimizer.py      # Query optimization
    ├── intent_detector.py      # Intent/temperature detection
    ├── cache_manager.py        # Session-based research cache
    ├── vector_cache.py         # BASIC ChromaDB wrapper (lazy init in state.py)
    ├── vector_cache_v2.py      # ADVANCED thread-safe worker implementation
    ├── agent_tools.py          # Web search, scraping tools
    └── research/               # Research Pipeline Modules
        ├── __init__.py        # Exports
        ├── orchestrator.py    # Top-level orchestration (perform_agent_research)
        ├── cache_handler.py   # Cache hit handling
        ├── query_processor.py # Query optimization + web search
        ├── scraper_         
        │ orchestrator.py    # Parallel scraping
        └── context_builder.py # Context building + LLM inference + vector cache auto-learning
```

---

## 2. Request Processing Flow

### 2.1 Detailed Flow: Quick/Deep Research Mode

```
User Input
    ↓
state.py:send_message()
    ↓
perform_agent_research() [orchestrator.py]
    │
    ├─→ PHASE 1: handle_cache_hit() [cache_handler.py]
    │   ├─ Session ID lookup in cache_manager
    │   ├─ If HIT: Return cached answer + history
    │   └─ If MISS: Continue to Phase 2
    │
    ├─→ PHASE 2: process_query_and_search() [query_processor.py]
    │   ├─ Query optimization using Automatik LLM
    │   │  └─ Uses query_optimizer.optimize_search_query()
    │   ├─ Web search (Brave → Tavily → SearXNG fallback)
    │   └─ Extract URLs from results
    │
    ├─→ PHASE 3: orchestrate_scraping() [scraper_orchestrator.py]
    │   ├─ Parallel scraping with ThreadPoolExecutor
    │   │  └─ max_workers = min(5, num_urls)
    │   ├─ LLM preloading in parallel
    │   └─ Progress updates
    │
    └─→ PHASE 4: build_and_generate_response() [context_builder.py]
        ├─ Build context from scraped sources
        ├─ Build messages with history
        ├─ LLM streaming inference
        ├─ Format response (thinking tags)
        ├─→ Vector Cache Auto-Learning ✅
        │   └─ add_to_cache_async() [vector_cache_v2.py]
        │       └─ Sends to worker thread via queue
        └─ Return final answer + updated history
```

### 2.2 Detailed Flow: Automatik Mode

```
User Input
    ↓
state.py:send_message()
    ↓
chat_interactive_mode() [conversation_handler.py]
    │
    ├─ Explicit Keywords Check?
    │  └─ If yes: Skip KI decision, go to Deep Research
    │
    ├─ Vector Cache Check (DISABLED - line 92)
    │  ├─ TODO comment: Re-implement using Reflex Background Events
    │  └─ Reason: Causes Reflex timeout issues ⚠️
    │
    ├─ LLM Decision: Do we need research?
    │  └─ Uses decision_making_prompt
    │  └─ get_decision_making_prompt()
    │
    └─ If Research Needed: Call perform_agent_research(mode="deep")
       └─ Full 4-phase pipeline above
```

---

## 3. Vector Cache Implementation Analysis

### 3.1 Vector Cache v1 (vector_cache.py) - BASIC

**Location**: `/home/mp/Projekte/AIfred-Intelligence/aifred/lib/vector_cache.py`

**Purpose**: Semantic similarity search using ChromaDB embeddings

**Key Components**:
- ChromaDB PersistentClient with persistence to `./aifred_vector_cache`
- Default embedding model: `all-MiniLM-L6-v2` (sentence transformers)
- Single collection: `research_cache`

**Architecture**:
```python
VectorCache
├── __init__(persist_directory="./aifred_vector_cache")
│   ├─ Creates persistence directory
│   ├─ Initializes ChromaDB PersistentClient
│   ├─ Creates/gets collection named "research_cache"
│   └─ Logs initialization
│
├── query(user_query: str, n_results: int = 3) → Dict
│   ├─ Semantic similarity search on query text
│   ├─ Returns confidence level (high/medium/low)
│   ├─ Distance thresholds:
│   │  ├─ < 0.5:  HIGH (direct return)
│   │  ├─ 0.5-0.85: MEDIUM (could verify with LLM)
│   │  └─ > 0.85: LOW (web search required)
│   └─ Timing: ~20ms per query
│
├── add(query: str, answer: str, sources: List, metadata: Dict) → None
│   ├─ Adds Q&A document to ChromaDB
│   ├─ Stores source URLs + metadata
│   └─ Auto-generates UUID
│
├── clear() → None
│   └─ Deletes collection and recreates
│
└── get_stats() → Dict
    ├─ total_entries: ChromaDB count
    └─ persist_path: Directory path
```

**Issues**:
- NOT thread-safe (ChromaDB must be called from single thread)
- Blocks asyncio event loop during initialization/queries
- Used in state.py with lazy initialization via `asyncio.to_thread()`
- Can cause request cancellations in Reflex

---

### 3.2 Vector Cache v2 (vector_cache_v2.py) - ADVANCED

**Location**: `/home/mp/Projekte/AIfred-Intelligence/aifred/lib/vector_cache_v2.py`

**Purpose**: Thread-safe vector caching with dedicated worker thread

**Architecture - Worker Thread Pattern**:
```python
VectorCacheWorker (runs in dedicated thread)
├── __init__(persist_directory)
│   ├─ Creates queues for request/response communication
│   ├─ Initializes thread (daemon=True)
│   └─ Worker lifecycle management
│
├── start() → None
│   └─ Spawns worker thread (idempotent)
│
├── _worker_loop() → (runs in worker thread)
│   ├─ Initializes ChromaDB in worker thread
│   ├─ Main loop: processes requests from queue
│   ├─ Operations:
│   │  ├─ 'query': _handle_query()
│   │  ├─ 'add': _handle_add()
│   │  ├─ 'get_stats': _handle_get_stats()
│   │  └─ 'clear': _handle_clear()
│   └─ Queue timeout: 1.0s (checks self.running periodically)
│
├── submit_request(operation: str, **kwargs) → Dict
│   ├─ Async-safe method (returns blocking call)
│   ├─ Create UUID for request tracking
│   ├─ Put request in queue
│   ├─ Wait for response (10s timeout)
│   ├─ Cleanup response queue
│   └─ Return result
│
├── stop() → None
│   ├─ Sets running = False
│   └─ Joins thread (5s timeout)
│
└── Global Module Functions
    ├─ get_worker() → VectorCacheWorker (singleton, thread-safe)
    │  └─ Double-check locking pattern
    │
    ├─ query_cache_async() → Dict
    │  └─ await asyncio.to_thread(worker.submit_request, 'query', ...)
    │
    ├─ add_to_cache_async() → Dict
    │  └─ await asyncio.to_thread(worker.submit_request, 'add', ...)
    │
    └─ shutdown_worker() → None
       └─ Stops worker on app shutdown
```

**Key Advantages over v1**:
- ChromaDB runs in isolated thread (not blocking asyncio)
- Queue-based communication (thread-safe)
- Proper lifecycle management (start/stop)
- Used in context_builder.py for auto-learning

**Timing**:
- Queue communication: ~1-5ms
- ChromaDB operations in worker: ~20-50ms
- Total async overhead: negligible (runs in background)

---

### 3.3 Where Vector Cache is Used

**Currently Active**:
1. **context_builder.py (lines 210-228)** - Auto-learning on successful research
   ```python
   # After LLM generates response from web research:
   from ..vector_cache_v2 import add_to_cache_async
   result = await add_to_cache_async(
       query=user_text,
       answer=ai_text,
       sources=scraped_only,
       metadata={'mode': mode}
   )
   ```
   - Stores Q&A from web research into cache
   - Enables future semantic similarity matching
   - Non-blocking (runs in worker thread)

**Lazy Initialized but Unused**:
2. **state.py (lines 28-86)** - get_vector_cache() function
   - Lazy async initialization
   - Uses `asyncio.to_thread()` with VectorCache v1
   - Called but NOT USED anywhere in practice

**Explicitly Disabled**:
3. **conversation_handler.py (line 92)** - Cache hit checking
   ```python
   # TEMPORARILY DISABLED - Causes Reflex timeout issues
   # TODO: Re-implement using Reflex Background Events
   log_message("⚠️ Vector Cache DISABLED (temporary)")
   ```
   - Would query cache before LLM decision
   - Comment indicates timeout issues in Reflex

---

## 4. Vector Cache Data & Persistence

### 4.1 Storage Location

```
/home/mp/Projekte/AIfred-Intelligence/aifred_vector_cache/
├── chroma.sqlite3              # ChromaDB database file (~184KB from recent access)
└── 6bd34b83-a161-4948-a9e2-a693ce9b2f47/  # Collection storage
```

**Note**: Directory is created by both v1 and v2 implementations

### 4.2 Data Format

**ChromaDB Collection**: `research_cache`

**Document Format**:
```python
{
    "documents": [
        "Q: {user_query}\nA: {answer_first_500_chars}"
    ],
    "metadatas": [{
        "timestamp": "2025-11-08T...",
        "num_sources": 3,
        "source_urls": "url1, url2, url3",
        "mode": "quick"  # or "deep"
    }],
    "ids": ["uuid-v4-string"]
}
```

**Embedding Model**:
- Sentence Transformers: `all-MiniLM-L6-v2`
- Dimensions: 384
- Tokenized automatically by ChromaDB

### 4.3 Distance Thresholds

**Cosine Distance Interpretation**:
- `0.25-0.45`: Exact/very similar questions → HIGH confidence
- `0.5-0.85`: Related questions → MEDIUM confidence  
- `> 0.85`: Unrelated → LOW confidence (web search)

---

## 5. Configuration

### 5.1 Key Settings (config.py)

```python
# DEPLOYMENT
USE_SYSTEMD_RESTART = True  # Production vs Dev mode

# HISTORY COMPRESSION (70% threshold)
HISTORY_COMPRESSION_THRESHOLD = 0.7
HISTORY_MESSAGES_TO_COMPRESS = 6      # 3 Q&A pairs
HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION = 10

# CONTEXT MANAGEMENT
MAX_RAG_CONTEXT_TOKENS = 20000         # Max tokens for web research context
MAX_WORDS_PER_SOURCE = 2000            # Prevents single source dominance
CHARS_PER_TOKEN = 3                    # Token ratio for estimation

# DEFAULT MODELS
DEFAULT_SETTINGS = {
    "model": "qwen3:8b",               # Main LLM
    "automatik_model": "phi3:mini",    # For decisions/optimization
}
```

### 5.2 Vector Cache Configuration

**NOT explicitly configured** - Uses defaults:
- Persist directory: `./aifred_vector_cache` (hardcoded)
- Embedding model: Default (all-MiniLM-L6-v2)
- Collection name: `research_cache` (hardcoded)

**Recommendation**: Add to config.py:
```python
# VECTOR CACHE CONFIGURATION
VECTOR_CACHE_PERSIST_DIR = "./aifred_vector_cache"
VECTOR_CACHE_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
VECTOR_CACHE_COLLECTION_NAME = "research_cache"
VECTOR_CACHE_SIMILARITY_THRESHOLD_HIGH = 0.5
VECTOR_CACHE_SIMILARITY_THRESHOLD_MEDIUM = 0.85
VECTOR_CACHE_ENABLED = True  # Feature flag
```

---

## 6. Initialization Flow

### 6.1 Application Startup

**1. Reflex App Load** → `aifred.py` creates app with Reflex framework

**2. Page Load** → Triggers `AIState.on_load()` (state.py:148)
```python
async def on_load(self):
    initialize_debug_log(force_reset=False)
    set_language(DEFAULT_LANGUAGE)
    # Vector Cache uses lazy initialization (on first use)
    await self.initialize_backend()
```

**3. Backend Initialization** → Checks Ollama/vLLM health
```python
# Preload Automatik LLM in background (non-blocking)
asyncio.create_task(preload_in_background())
```

**4. Vector Cache** → NOT initialized on startup
- Lazy init: `get_vector_cache()` in state.py
- First use: When first message is sent
- NOT called in conversation_handler.py (disabled)

### 6.2 First Message Processing

**Timeline**:
1. `send_message()` called
2. Sets `is_generating = True`
3. Routes to `chat_interactive_mode()` or `perform_agent_research()`
4. **If auto-learn triggered**: `add_to_cache_async()` initializes worker thread
5. Worker thread starts: `get_worker()` with double-check locking
6. ChromaDB initialized in worker thread (~500-1500ms)
7. All subsequent operations use initialized worker

---

## 7. The Hanging Issue - Root Cause Analysis

### 7.1 Symptoms
- System hangs when vector cache operations enabled
- Timeouts in Reflex UI
- Requests cancelled unexpectedly

### 7.2 Root Causes Identified

**1. ChatBot - Blocking Event Loop** (vector_cache.py)
```python
# BAD: Blocks async event loop
_vector_cache = await asyncio.to_thread(init_chromadb)  # state.py:76
```
- ChromaDB initialization can take 500-1500ms
- asyncio.to_thread() blocks the thread pool
- If thread pool is exhausted, event loop hangs
- Reflex requests timeout waiting for responses

**2. Vector Cache Check - Disabled in conversation_handler.py**
```python
# Line 92: TEMPORARILY DISABLED - Causes Reflex timeout issues
log_message("⚠️ Vector Cache DISABLED (temporary)")
```
- Code explicitly disables cache checking
- Indicates previous timeout issues
- No fallback mechanism

**3. ChromaDB Thread Safety**
- ChromaDB is NOT fully thread-safe
- Must run in dedicated thread
- v1 (state.py) violates this with `asyncio.to_thread()`
- v2 (context_builder.py) does it correctly with worker thread

**4. Queue Blocking**
- v2 uses `queue.Queue.get(timeout=10.0)`
- If worker thread hangs, main thread blocks
- No timeout on ChromaDB operations themselves

### 7.3 Why v2 Works Better

```python
# CORRECT: Worker thread, queue-based communication
def submit_request(self, operation: str, **kwargs):
    response_queue = queue.Queue()
    request_queue.put({...})
    result = response_queue.get(timeout=10.0)  # Main thread blocks, but worker is independent
```

- ChromaDB runs in isolated thread
- Queue communication is fast (~1-5ms)
- Event loop NOT blocked
- Timeout prevents infinite hangs

---

## 8. System Performance Metrics

### 8.1 Timing Breakdown

**Web Research (Quick Mode)**:
- Query Optimization: ~2-5s
- Web Search: ~3-5s
- Parallel Scraping (3-5 sources): ~10-20s
- LLM Inference (qwen3:8b): ~15-30s
- **Total**: ~30-60s

**Vector Cache Query**:
- Best case (high confidence): ~20ms
- Queue overhead: ~1-5ms
- Total savings: 30-60s → instant

**Vector Cache Auto-Learn**:
- Queue submission: ~1ms (non-blocking)
- Worker processing: ~50-100ms (happens in background)
- **No user-visible delay**

### 8.2 Resource Usage

- ChromaDB file: ~184KB (for accumulated entries)
- Memory (worker thread): ~50-100MB during operations
- CPU: Negligible when idle

---

## 9. Current Implementation Issues & TODOs

### 9.1 Known Issues

| Issue | Location | Impact | Status |
|-------|----------|--------|--------|
| Vector cache disabled | conversation_handler.py:92 | Can't use cache on Automatik | ⚠️ DISABLED |
| Lazy init blocking | state.py:76 | Can timeout on first use | ❌ UNUSED |
| No config options | config.py | Hard-coded paths/models | 📋 TODO |
| No graceful shutdown | N/A | Worker thread not stopped | 📋 TODO |
| Single collection | vector_cache.py | No separation by mode/user | 📋 TODO |

### 9.2 Code Comments Indicating Issues

```python
# conversation_handler.py:90-92
# TEMPORARILY DISABLED - Causes Reflex timeout issues
# TODO: Re-implement using Reflex Background Events

# context_builder.py:85
# TODO: Replace with Vector DB semantic search in Phase 1

# state.py:29
# set_research_cache removed - cache system deprecated
```

---

## 10. Research Pipeline Details

### 10.1 Cache Handler (cache_handler.py)

**Purpose**: Check if current question is a follow-up to cached research

**Flow**:
1. Get session ID from query
2. Lookup in `cache_manager.get_cached_research(session_id)`
3. If found: Use cached scraped sources instead of web search
4. Build context from cached sources
5. Generate answer without web scraping

**Performance**: Instant (no web scraping)

### 10.2 Query Processor (query_processor.py)

**Purpose**: Optimize user query for better web search results

**Flow**:
1. Get Automatik LLM context limit
2. Call `query_optimizer.optimize_search_query()`
3. Query uses Automatik LLM to refine user's question
4. Search web with optimized query (Brave/Tavily/SearXNG)
5. Return URLs + reasoning

**Performance**: ~2-10s (LLM + web search)

### 10.3 Scraper Orchestrator (scraper_orchestrator.py)

**Purpose**: Scrape URLs in parallel + preload main LLM

**Flow**:
1. Determine scraping strategy (quick=3 URLs, deep=7 URLs)
2. Start LLM preload task in parallel
3. Create ThreadPoolExecutor with max 5 workers
4. Scrape URLs concurrently with `agent_tools.scrape_webpage()`
5. Track progress (current/total/failed)
6. Return scraped content

**Performance**: ~10-20s (parallel with preload)

### 10.4 Context Builder (context_builder.py)

**Purpose**: Build context, generate answer, auto-learn to cache

**Flow**:
1. Filter successfully scraped sources
2. Build context with `build_context()` from sources
3. Load system prompt with context
4. Build messages from history
5. Calculate dynamic context window
6. Detect query intent for temperature
7. Stream LLM inference
8. **AUTO-LEARN**: Save successful research to vector cache (v2)
9. Return formatted answer + updated history

**Vector Cache Integration** (lines 210-228):
```python
# After successful inference:
result = await add_to_cache_async(
    query=user_text,
    answer=ai_text,
    sources=scraped_only,
    metadata={'mode': mode}
)
# Non-blocking - runs in worker thread
```

---

## 11. LLM Client Architecture

### 11.1 LLMClient Interface

```python
class LLMClient:
    def __init__(backend_type="ollama", base_url=None)
    
    # Non-streaming
    async def chat(model, messages, options) → LLMResponse
    
    # Streaming
    async def chat_stream(model, messages, options) → AsyncIterator[Dict]
        ├─ {"type": "content", "text": "..."}
        └─ {"type": "done", "metrics": {...}}
    
    # Model preloading
    async def preload_model(model) → (success, time_taken)
    
    # Context limit
    async def get_model_context_limit(model) → int
    
    # Cleanup
    async def close() → None
```

### 11.2 Streaming Response Format

```python
# During generation
{"type": "content", "text": "chunk"}

# On completion
{
    "type": "done",
    "metrics": {
        "tokens_generated": 150,
        "tokens_per_second": 25.3,
        "generation_time": 5.93
    }
}
```

---

## 12. Recommendations for Fixing Vector Cache

### 12.1 Short-term Fixes

**1. Use v2 for Cache Queries** (conversation_handler.py)
```python
# Replace lines 88-93 with:
from ..vector_cache_v2 import query_cache_async

cache_result = await query_cache_async(user_text, n_results=1)
if cache_result['source'] == 'CACHE' and cache_result['confidence'] in ['high', 'medium']:
    log_message(f"✅ Vector Cache HIT: {cache_result['distance']:.3f}")
    # Return cached answer...
else:
    log_message(f"❌ Vector Cache miss or low confidence")
    # Continue to LLM decision...
```

**2. Add Graceful Shutdown** (state.py)
```python
# Add to AIState or app cleanup:
from .lib.vector_cache_v2 import shutdown_worker

# On app close:
def on_shutdown():
    shutdown_worker()
```

**3. Add Configuration Options** (config.py)
```python
VECTOR_CACHE_ENABLED = True
VECTOR_CACHE_PERSIST_DIR = "./aifred_vector_cache"
VECTOR_CACHE_SIMILARITY_THRESHOLD = 0.5
```

### 12.2 Medium-term Improvements

**1. Reflex Background Events**
- Use Reflex's background event system
- Cache queries run in background
- Non-blocking UI updates

**2. Semantic Pre-filtering**
- Filter scraped sources by semantic similarity to query
- Reduce context size
- Improve answer quality

**3. Multi-Collection Support**
- Separate collections by research mode
- Separate by user/session if multi-user
- Better organization and performance

**4. Cache Expiration**
- Add timestamp checking
- Expire old entries (configurable)
- Prevent stale information

### 12.3 Long-term Architecture

**1. Dedicated Cache Service**
- Separate microservice for vector caching
- HTTP API instead of thread-based
- Shared across multiple instances

**2. Hybrid Caching**
- Vector DB for semantic search
- Traditional cache for exact questions
- Fallback chain: Exact → Semantic → Web

**3. Analytics**
- Track cache hit rates
- Monitor performance metrics
- Identify optimization opportunities

---

## 13. File Reference Guide

### Core Files

| File | Purpose | Key Functions |
|------|---------|---|
| `aifred.py` | Reflex UI | `index()`, component functions |
| `state.py` | State management | `send_message()`, `initialize_backend()`, `get_vector_cache()` |
| `backends/base.py` | Backend interface | `health_check()`, `chat_stream()` |
| `backends/ollama.py` | Ollama adapter | Ollama API integration |
| `lib/config.py` | Configuration | All global settings |
| `lib/llm_client.py` | Async LLM client | `chat()`, `chat_stream()`, `preload_model()` |

### Research Pipeline

| File | Purpose | Key Functions |
|------|---------|---|
| `lib/research/orchestrator.py` | Main orchestration | `perform_agent_research()` |
| `lib/research/cache_handler.py` | Cache checking | `handle_cache_hit()` |
| `lib/research/query_processor.py` | Query optimization | `process_query_and_search()` |
| `lib/research/scraper_orchestrator.py` | Web scraping | `orchestrate_scraping()` |
| `lib/research/context_builder.py` | Context + LLM | `build_and_generate_response()` |

### Vector Cache

| File | Purpose | Key Classes |
|------|---------|---|
| `lib/vector_cache.py` | Basic ChromaDB wrapper | `VectorCache` |
| `lib/vector_cache_v2.py` | Thread-safe worker | `VectorCacheWorker`, async functions |

### Supporting Libraries

| File | Purpose |
|------|---------|
| `lib/conversation_handler.py` | Automatik mode (with disabled cache) |
| `lib/cache_manager.py` | Session-based research cache |
| `lib/context_manager.py` | Context window calculation |
| `lib/message_builder.py` | Message history construction |
| `lib/prompt_loader.py` | Prompt templates + i18n |
| `lib/query_optimizer.py` | Query optimization |
| `lib/intent_detector.py` | Intent/temperature detection |
| `lib/agent_tools.py` | Web search, scraping utilities |

---

## 14. Conclusion

AIfred Intelligence implements a sophisticated multi-phase research pipeline with **two levels of caching**:

1. **Session-based research cache** (`cache_manager.py`) - Tracks researched topics per session
2. **Vector semantic cache** (`vector_cache.py` + `vector_cache_v2.py`) - Enables intelligent reuse

The vector cache is **partially functional**:
- ✅ Auto-learns from successful web research (context_builder.py)
- ✅ Uses thread-safe worker pattern (vector_cache_v2.py)
- ❌ Cache queries disabled in Automatik mode (conversation_handler.py)
- ⚠️ Lazy initialization uses blocking call (state.py)

**Main issue causing hangs**: Vector cache check is explicitly disabled due to Reflex timeout issues. The recommended solution is to use v2's worker thread pattern with proper Reflex integration (background events).

