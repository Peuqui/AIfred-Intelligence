# Vector Cache Architecture - Visual Guide

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                       REFLEX WEB UI                                 │
│  Chat Interface | Debug Console | Settings | Progress Indicators    │
└────────────────────────────┬────────────────────────────────────────┘
                             │ User Input (text, research mode)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     AISTATE (state.py)                              │
│  - Manages UI state (is_generating, chat_history, debug_messages)   │
│  - Routes to chat_interactive_mode or perform_agent_research        │
│  - Has lazy initialized vector cache (unused)                       │
└────────────────┬──────────────────────────┬────────────────────────┘
                 │                          │
                 │ Automatik Mode           │ Quick/Deep Mode
                 ▼                          ▼
      ┌──────────────────────┐   ┌─────────────────────────────────┐
      │ CONVERSATION HANDLER │   │ RESEARCH ORCHESTRATOR           │
      │ (conversation_       │   │ (research/orchestrator.py)      │
      │  handler.py)         │   │                                 │
      │                      │   │ 4-Phase Pipeline:               │
      │ 1. Explicit Keywords?│   │ 1. Cache Handler                │
      │ 2. Vector Cache?     │   │ 2. Query Processor              │
      │    (DISABLED ❌)    │   │ 3. Scraper Orchestrator         │
      │ 3. LLM Decision?     │   │ 4. Context Builder + Auto-Learn │
      │ 4. Research if Yes   │   └──────────┬────────────────────┘
      │    (calls Phase 4)   │              │
      └────────────┬─────────┘              │
                   │                        │
                   └────────────┬───────────┘
                                │
                    ┌───────────▼──────────────┐
                    │ CONTEXT BUILDER         │
                    │ (context_builder.py)    │
                    │                         │
                    │ - Build context         │
                    │ - LLM inference         │
                    │ - Format response       │
                    │ - Auto-learn to cache   │
                    └───────────┬──────────────┘
                                │
                    ┌───────────▼──────────────┐
                    │ VECTOR CACHE v2         │
                    │ (vector_cache_v2.py)    │
                    │                         │
                    │ - Worker thread         │
                    │ - Queue communication   │
                    │ - Auto-learning         │
                    │ - Non-blocking          │
                    └───────────┬──────────────┘
                                │
                    ┌───────────▼──────────────┐
                    │ CHROMADB STORAGE        │
                    │ ./aifred_vector_cache/  │
                    │                         │
                    │ SQLite database with    │
                    │ semantic embeddings     │
                    └────────────────────────┘
```

---

## Vector Cache Implementations Comparison

### v1 (vector_cache.py) - BASIC

```
SYNCHRONOUS ARCHITECTURE
┌─────────────────┐
│ Calling Code    │
│ (async)         │
└────────┬────────┘
         │ await asyncio.to_thread()
         ▼
    ┌─────────────────────────────┐
    │ asyncio Thread Pool         │
    │ (BOTTLENECK!)               │
    └────────┬────────────────────┘
             │ blocks thread
             ▼
    ┌─────────────────────────────┐
    │ VectorCache (v1)            │
    │ - Direct ChromaDB calls     │
    │ - Initialization: 500-1500ms│
    │ - Query: ~20ms              │
    └────────┬────────────────────┘
             │
             ▼
    ┌─────────────────────────────┐
    │ ChromaDB (NOT thread-safe)  │
    │ - Can cause hangs           │
    │ - Blocks event loop         │
    └─────────────────────────────┘

PROBLEMS:
- Thread pool exhaustion → timeout
- Not thread-safe → potential crashes
- Blocks event loop → Reflex hangs
- Unused in practice
```

### v2 (vector_cache_v2.py) - ADVANCED ⭐

```
ASYNCHRONOUS ARCHITECTURE (RECOMMENDED)
┌──────────────────┐
│ Calling Code     │
│ (async/await)    │
└────────┬─────────┘
         │ await asyncio.to_thread(worker.submit_request)
         │ (only 1-5ms for queue communication)
         ▼
    ┌──────────────────────────────────┐
    │ asyncio Thread Pool              │
    │ (minimal blocking)               │
    └────────┬─────────────────────────┘
             │ quick handoff
             ▼
    ┌──────────────────────────────────┐
    │ VectorCacheWorker (daemon thread)│
    │ - Queue.put() / Queue.get()      │
    │ - Timeout: 10s max               │
    └────────┬─────────────────────────┘
             │ dedicated thread (isolated)
             ▼
    ┌──────────────────────────────────┐
    │ ChromaDB Operations              │
    │ - Runs in isolation              │
    │ - Takes 20-100ms (doesn't block) │
    │ - Thread-safe queue access       │
    └────────┬─────────────────────────┘
             │
             ▼
    ┌──────────────────────────────────┐
    │ ChromaDB (single-threaded)       │
    │ - Proper isolation               │
    │ - No event loop blocking         │
    │ - No Reflex timeouts             │
    └──────────────────────────────────┘

BENEFITS:
✅ Event loop never blocked
✅ Fast queue communication (1-5ms)
✅ Proper thread isolation
✅ 10s timeout protection
✅ Non-blocking from caller perspective
✅ Used for auto-learning (works!)
```

---

## Vector Cache Data Flow

### Storage Layout

```
./aifred_vector_cache/
│
├── chroma.sqlite3 (~184KB)
│   └─ ChromaDB database file
│      ├─ Collections metadata
│      ├─ Document store
│      └─ Embedding vectors (384-dim)
│
└── 6bd34b83-a161-4948-a9e2-a693ce9b2f47/
    └─ Collection storage
       ├─ parquet files (document chunks)
       └─ index files (vector embeddings)
```

### Document Format in Collection

```
ChromaDB Collection: "research_cache"

Each Entry:
{
    "id": "uuid-12345-abcde",
    
    "document": """Q: Was ist künstliche Intelligenz?
                  A: Künstliche Intelligenz ist...""",
    
    "embedding": [0.123, -0.456, 0.789, ...384 dims...],
    
    "metadata": {
        "timestamp": "2025-11-08T08:30:45.123456",
        "num_sources": 3,
        "source_urls": "https://wiki.de, https://news.de, https://...",
        "mode": "quick"  # or "deep"
    }
}

Semantic Similarity Search:
┌─ User Query: "Was ist KI?"
├─ Embed: [0.125, -0.450, 0.795, ...384 dims...]
├─ Compare with all stored embeddings
├─ Cosine distance: [0.35, 0.67, 1.2, ...]
├─ Sort by distance
└─ Return best matches
```

---

## Request Flow Detailed

### Flow 1: Quick/Deep Research Mode (4 Phases)

```
User Input (Quick or Deep mode selected)
    │
    ▼
perform_agent_research() [orchestrator.py]
    │
    ├──────────────────────────────────────────────────┐
    │  PHASE 1: CACHE HANDLER (cache_handler.py)       │
    │                                                   │
    │  ┌─────────────────────────────────────────┐    │
    │  │ Session-based Cache Check               │    │
    │  │ (Different from vector cache!)           │    │
    │  ├─ Get cached research from cache_manager │    │
    │  ├─ If follow-up detected: Use cached data │    │
    │  ├─ Return answer directly                 │    │
    │  └─ If miss: Continue to Phase 2           │    │
    │  Latency: Instant (no web)                 │    │
    └──────────────────────────────────────────────────┘
    │
    ├──────────────────────────────────────────────────┐
    │  PHASE 2: QUERY PROCESSOR (query_processor.py)   │
    │                                                   │
    │  ┌─────────────────────────────────────────┐    │
    │  │ 1. Query Optimization                   │    │
    │  │    - Automatik LLM refines user query   │    │
    │  │    - Improves search relevance          │    │
    │  │    Latency: ~2-5s                       │    │
    │  ├─────────────────────────────────────────┤    │
    │  │ 2. Web Search                           │    │
    │  │    - Brave API (primary)                │    │
    │  │    - Tavily API (fallback)              │    │
    │  │    - SearXNG (fallback)                 │    │
    │  │    - Extract URLs from results          │    │
    │  │    Latency: ~3-5s                       │    │
    │  └─────────────────────────────────────────┘    │
    │  Output: list of URLs + relevance scores        │
    └──────────────────────────────────────────────────┘
    │
    ├──────────────────────────────────────────────────┐
    │  PHASE 3: SCRAPER ORCHESTRATOR                   │
    │           (scraper_orchestrator.py)              │
    │                                                   │
    │  ┌──────────────────────────────────────────┐   │
    │  │ Parallel Web Scraping                    │   │
    │  │ + Main LLM Preloading (parallel)         │   │
    │  │                                          │   │
    │  │  ThreadPoolExecutor(max_workers=5)       │   │
    │  │  ├─ scrape_webpage(url1)  ──────┐       │   │
    │  │  ├─ scrape_webpage(url2)  ──────┤       │   │
    │  │  ├─ scrape_webpage(url3)  ──────┼─wait  │   │
    │  │  ├─ scrape_webpage(url4)  ──────┤       │   │
    │  │  └─ scrape_webpage(url5)  ──────┘       │   │
    │  │                                          │   │
    │  │  + llm_client.preload_model() [parallel]│   │
    │  │    ├─ Loads main LLM into VRAM          │   │
    │  │    └─ Ready for Phase 4 inference       │   │
    │  │                                          │   │
    │  │  Latency: ~10-20s (parallel)            │   │
    │  │  Progress updates: current/total/failed  │   │
    │  └──────────────────────────────────────────┘   │
    │  Output: list of scraped documents               │
    └──────────────────────────────────────────────────┘
    │
    ├──────────────────────────────────────────────────┐
    │  PHASE 4: CONTEXT BUILDER                        │
    │           (context_builder.py)                   │
    │                                                   │
    │  ┌──────────────────────────────────────────┐   │
    │  │ 1. Build Context                         │   │
    │  │    - Filter successful sources           │   │
    │  │    - Truncate to MAX_RAG_CONTEXT_TOKENS  │   │
    │  │    - Create RAG prompt                   │   │
    │  └──────────────────────────────────────────┘   │
    │  ┌──────────────────────────────────────────┐   │
    │  │ 2. Build Messages                        │   │
    │  │    - Add system prompt (with context)    │   │
    │  │    - Add chat history                    │   │
    │  │    - Add current user question           │   │
    │  └──────────────────────────────────────────┘   │
    │  ┌──────────────────────────────────────────┐   │
    │  │ 3. LLM Streaming Inference               │   │
    │  │    - Dynamic context window calculation  │   │
    │  │    - Intent-based temperature selection  │   │
    │  │    - Stream response tokens              │   │
    │  │    Latency: ~15-30s                      │   │
    │  └──────────────────────────────────────────┘   │
    │  ┌──────────────────────────────────────────┐   │
    │  │ 4. AUTO-LEARNING TO VECTOR CACHE ✅      │   │
    │  │                                          │   │
    │  │  result = await add_to_cache_async(      │   │
    │  │      query=user_text,                    │   │
    │  │      answer=ai_text[:500],               │   │
    │  │      sources=scraped_sources,            │   │
    │  │      metadata={'mode': mode}             │   │
    │  │  )                                       │   │
    │  │                                          │   │
    │  │  ├─ Calls worker.submit_request()        │   │
    │  │  ├─ Non-blocking (returns immediately)   │   │
    │  │  ├─ Worker processes in background       │   │
    │  │  │  ├─ Create embeddings                 │   │
    │  │  │  ├─ Store in ChromaDB                 │   │
    │  │  │  └─ Index semantically                │   │
    │  │  └─ No user-visible latency              │   │
    │  │                                          │   │
    │  │  Latency: 0ms (non-blocking)             │   │
    │  └──────────────────────────────────────────┘   │
    │  Output: Final answer + updated history          │
    └──────────────────────────────────────────────────┘
    │
    ▼
Return to send_message()
    │
    ├─ Update UI with streamed content
    ├─ Update chat history
    ├─ Clear progress indicator
    └─ Ready for next input

TOTAL LATENCY: ~30-60 seconds (web research)
              vs. ~20ms (cache hit)
```

### Flow 2: Automatik Mode (with Vector Cache DISABLED)

```
User Input (Automatik mode)
    │
    ▼
chat_interactive_mode() [conversation_handler.py]
    │
    ├─ Explicit Keywords Check?
    │  ├─ "recherchiere", "google", "suche im internet"
    │  └─ If yes: Skip decision, go to Deep Research
    │     (calls perform_agent_research with mode="deep")
    │
    ├─ Vector Cache Check
    │  │
    │  ├─ Code Status: ❌ DISABLED (line 92)
    │  │  # TEMPORARILY DISABLED - Causes Reflex timeout issues
    │  │  # TODO: Re-implement using Reflex Background Events
    │  │
    │  └─ Would have queried cache for semantic match
    │     but is commented out
    │
    ├─ LLM Decision
    │  ├─ Ask Automatik LLM: "Do we need to research this?"
    │  ├─ Decision prompt analyzes user question
    │  ├─ Considers chat history
    │  └─ Returns: research_needed = True/False
    │
    └─ If Research Needed
       └─ Call perform_agent_research(mode="deep")
          └─ Execute full 4-phase pipeline above

PROBLEM: No cache check before LLM decision
RESULT: Even if cache has answer, will do research
```

---

## Vector Cache Statistics

### Timing Breakdown

```
RESEARCH WITHOUT CACHE (current)
┌─────────────────────────────────────────┐
│ Phase 2: Query Optimization      2-5s   │
│ Phase 2: Web Search              3-5s   │
│ Phase 3: Parallel Scraping      10-20s  │
│ Phase 4: LLM Inference          15-30s  │
├─────────────────────────────────────────┤
│ TOTAL                           30-60s  │
└─────────────────────────────────────────┘

WITH VECTOR CACHE HIT
┌─────────────────────────────────────────┐
│ Query Cache              20ms + 1-5ms   │
│ Return Cached Answer                    │
├─────────────────────────────────────────┤
│ TOTAL                           ~25ms   │
└─────────────────────────────────────────┘

SAVINGS: 30-60s → ~25ms = 99.95% faster
BENEFIT: 2400x speedup for cache hits
```

### Memory Usage

```
ChromaDB Storage:
├─ Database file:        ~184KB (current, grows with entries)
├─ Embeddings (384-dim):  ~1.5MB per 1000 entries
└─ Total overhead:        <10MB for typical usage

Worker Thread Memory:
├─ Python overhead:       ~1-2MB
├─ ChromaDB instance:     ~20-30MB
└─ Total:                 ~30-50MB (when active)

Total System Impact: <100MB (negligible)
```

---

## Integration Points

### Where Vector Cache is Used

```
1. AUTO-LEARNING (Active) ✅
   Location: context_builder.py:210
   ├─ After LLM inference succeeds
   ├─ Calls: add_to_cache_async()
   ├─ Stores: Q&A + sources + metadata
   └─ Latency: 0ms (non-blocking)

2. MANUAL QUERIES (Disabled) ❌
   Location: conversation_handler.py:92
   ├─ Would query cache before LLM decision
   ├─ Would enable instant answer for similar questions
   ├─ Was disabled due to Reflex timeout issues
   └─ Ready for re-implementation

3. LAZY INITIALIZATION (Unused) ⚠️
   Location: state.py:36
   ├─ get_vector_cache() function
   ├─ Uses asyncio.to_thread() (blocking!)
   ├─ Never called in practice
   └─ Not recommended (use v2 instead)
```

---

## Future Improvements

### Short-term (Enable v2)
```
conversation_handler.py lines 88-93:

BEFORE (DISABLED):
    # TEMPORARILY DISABLED - Causes Reflex timeout issues
    log_message("⚠️ Vector Cache DISABLED (temporary)")

AFTER (ENABLED):
    from ..vector_cache_v2 import query_cache_async
    
    cache_result = await query_cache_async(user_text, n_results=1)
    if cache_result['source'] == 'CACHE' and cache_result['confidence'] == 'high':
        log_message(f"✅ Vector Cache HIT")
        # Return cached answer directly
    else:
        log_message(f"Cache miss or low confidence")
        # Continue to LLM decision...
```

### Medium-term (Configuration)
```
config.py additions:

VECTOR_CACHE_ENABLED = True
VECTOR_CACHE_PERSIST_DIR = "./aifred_vector_cache"
VECTOR_CACHE_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
VECTOR_CACHE_SIMILARITY_THRESHOLD_HIGH = 0.5
VECTOR_CACHE_SIMILARITY_THRESHOLD_MEDIUM = 0.85
VECTOR_CACHE_ENABLE_AUTO_LEARNING = True
VECTOR_CACHE_MAX_ENTRIES = 10000
VECTOR_CACHE_EXPIRATION_DAYS = 90
```

### Long-term (Advanced)
```
1. Multi-collection support
   - By research mode (quick/deep)
   - By user/session
   - By language

2. Cache expiration
   - Timestamp-based removal
   - Configurable TTL

3. Hybrid caching
   - Exact match cache (fast, deterministic)
   - Vector cache (semantic, flexible)
   - Fallback chain

4. Analytics & monitoring
   - Cache hit rates
   - Average speedup
   - Query latency distribution
```

---

## Troubleshooting Guide

### Issue: Vector Cache Not Working

**Check 1: Is it enabled in conversation_handler.py?**
```python
# Line 92 should NOT be commented out
# Should call query_cache_async() instead of logging disabled message
```

**Check 2: Do entries exist in storage?**
```bash
ls -la ./aifred_vector_cache/
# Should see chroma.sqlite3 and subdirectory
# If empty: cache hasn't auto-learned yet (no successful research)
```

**Check 3: Is worker thread running?**
```python
# Add debug logging in vector_cache_v2.py
# Should see: "VectorCache Worker Thread started"
```

**Check 4: Are there Reflex timeouts?**
```python
# Check Reflex logs for request cancellations
# If yes: vector cache is blocking event loop (use v2 for queries)
```

### Issue: Auto-Learning Not Working

**Check 1: Did research complete successfully?**
```python
# context_builder.py:210 only runs if LLM inference succeeds
# Check debug console for errors
```

**Check 2: Is worker thread initialized?**
```python
# First call to add_to_cache_async() triggers initialization
# May take 500-1500ms for ChromaDB setup
```

**Check 3: Check database integrity**
```bash
cd ./aifred_vector_cache/
sqlite3 chroma.sqlite3 "SELECT COUNT(*) FROM embeddings;"
# Shows number of stored vectors
```

---

## Summary

Vector Cache v2 is the recommended implementation:
- Thread-safe with dedicated worker
- Non-blocking queue communication
- Auto-learning already works
- Ready to re-enable manual queries

Replace v1 usage with v2:
- Use `query_cache_async()` from vector_cache_v2
- Remove blocking `asyncio.to_thread()` calls
- Enable cache in conversation_handler.py (replace lines 88-93)

Expected performance gain:
- Cache hits: 30-60s → ~25ms (99.95% faster)
- No latency impact on cache misses
- Minimal memory overhead (~30-50MB)
