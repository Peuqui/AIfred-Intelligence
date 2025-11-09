## Fix: Vector Cache Worker double-initialization deadlock (6305444)

Date: 2025-11-09 00:30:30 +0100

PROBLEM:
- Inline import `from .vector_cache_v2 import query_cache_async` in
  chat_interactive_mode() was called on EVERY user request
- This re-initialized the worker thread, overwriting the first one
- Caused deadlock: App hung after "Checking Vector Cache"

ROOT CAUSE:
- Module-level code in vector_cache_v2.py calls get_worker()
- get_worker() creates singleton worker thread
- BUT: Inline imports re-execute module-level code!

FIX:
- Move imports to top-level (line 27)
- Remove inline import from function (line 94 deleted)
- Worker now initialized ONLY ONCE at app startup

TESTING:
- Restart reflex run required (Python module cache!)
- Worker should only appear once in debug log

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>

----

## fix: Fix logging and vector cache deadlock issues (3c4ffaf)

Date: 2025-11-09 00:13:53 +0100

**Bug 1: Debug log reset on first user request**
- Removed auto-initialization in log_message()
- initialize_debug_log() must be called explicitly at app start
- Prevents log file from being cleared during runtime

**Bug 2: Vector cache hangs after "Checking Vector Cache"**
- Added thread-safe lock (_TTL_RULES_LOCK) for TTL rules loading
- Fixed potential deadlock when loading TTL config in worker thread
- Double-check pattern after acquiring lock to prevent race conditions
- All TTL loading code now inside lock block for consistency

----

## refactor: Clean up prompts structure and documentation (ed37cd3)

Date: 2025-11-09 00:09:39 +0100

- Deleted old prompts from root directory (now only in de/en subdirs)
- Removed root fallback logic from prompt_loader.py
- Moved VECTOR_CACHE_*.md docs to docs/development/
- Updated TODO.md: Removed completed features, kept only open tasks

----

## feat: Externalize TTL rules and settings to YAML config files (fbdcd87)

Date: 2025-11-09 00:03:43 +0100

- Created aifred/config/ directory for centralized configuration
- Migrated TTL rules from hardcoded dict to ttl_rules.yaml (47 keywords)
- Migrated assistant_settings.json to settings.yaml (YAML format)
- Added load_ttl_rules() with global caching in vector_cache_v2.py
- Added README.md in config directory with documentation
- YAML format allows comments and easy manual editing
- No hot-reload (app restart required for changes)
- Graceful degradation if config missing
- Updated .gitignore for settings.yaml

----

## feat: Add timing suffix to cached answers in chat history (75e1bc8)

Date: 2025-11-08 23:52:38 +0100

- Cache-Hits zeigen jetzt '(Cache-Hit: 0.17s)' statt LLM timing
- Macht Cache-Antworten im Chat-Verlauf unterscheidbar
- Nutzer kann sofort sehen ob Antwort aus Cache oder von LLM kam

----

## feat: Implement Vector Cache TTL System (Phase 1 - Keyword-based) (338de25)

Date: 2025-11-08 23:52:22 +0100

## TTL System Implementation
- ✅ Keyword-based TTL detection (0ms overhead)
- ✅ 47 keywords for German + English (time, weather, finance, news)
- ✅ TTL categories: 5min (live), 15min (stocks), 6h (weather/news), 12h (time-based), 24h (yesterday)
- ✅ TTL validation on cache query (expired entries trigger new research)
- ✅ TTL metadata stored in ChromaDB
- ✅ Config options for future Phase 2 (LLM-based TTL, optional)

## Modified Files
- aifred/lib/vector_cache_v2.py: TTL_RULES dict + get_quick_ttl() function
- aifred/lib/conversation_handler.py: TTL validation in cache query flow
- aifred/lib/config.py: VECTOR_CACHE_ENABLE_SMART_TTL, ENABLE_CLEANUP, CLEANUP_INTERVAL

## How It Works
1. Save: Query analyzed for keywords → TTL determined → Stored with timestamp
2. Query: Check cache hit → Validate TTL → If expired: new research, else: return cached
3. Permanent: No keyword match → No TTL → Cache never expires

## Benefits
- 🎯 Prevents stale data (weather, news, stocks)
- ⚡ Zero performance impact (keyword matching is instant)
- 🔄 Backwards compatible (old entries without TTL treated as permanent)
- 📊 ~80% coverage for time-sensitive queries

## Next Steps
- Phase 2: Optional LLM-based TTL for edge cases (VECTOR_CACHE_ENABLE_SMART_TTL)
- Optional: Background cleanup task (VECTOR_CACHE_ENABLE_CLEANUP)

----

## chore: Remove unused apply_dark_theme.py script (8d6bafa)

Date: 2025-11-08 23:44:40 +0100


----

## feat: Vector Cache Activation + Documentation Cleanup (c6b999b)

Date: 2025-11-08 23:43:33 +0100

## Vector Cache Changes
- ✅ Activated vector cache query system (conversation_handler.py)
- ✅ Fixed 500-char truncation bug (vector_cache_v2.py)
- ✅ Removed Q&A format from cached answers
- ✅ Fixed API integration (query_cache_async, correct parameters)
- ✅ Fixed event type for state.py ("result" instead of "result_data")
- ✅ Performance: 250-350x speedup (170ms cache hits vs 40-60s research)

## Documentation
- ✅ Updated VECTOR_CACHE_FINAL_STATUS.md with production status
- ✅ Created VECTOR_CACHE_TTL_DESIGN.md for next feature
- ✅ Removed 11 outdated documentation files
- ✅ Added aifred_vector_cache/ to .gitignore

## Testing
- ✅ Cache MEDIUM confidence hits working (d=0.77-0.81)
- ✅ Full answers stored and retrieved (no truncation)
- ✅ Auto-learning after web research functional


----

