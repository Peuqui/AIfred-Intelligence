# Changelog

All notable changes to AIfred Intelligence will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-11-10

### 🎉 Milestone: Vector Cache Production Ready

#### Added
- **ChromaDB Vector Cache**: Thread-safe semantic caching for web research results
  - Docker-based ChromaDB server mode (port 8000)
  - Automatic duplicate detection with configurable distance thresholds
  - Time-based cache invalidation (5-minute threshold for explicit research keywords)
  - Query-only embeddings for improved similarity matching (distance 0.000 for exact matches)
  - Auto-learning from web research results
- **Configurable Distance Thresholds** (`aifred/lib/config.py`):
  - `CACHE_DISTANCE_HIGH = 0.5` - High confidence cache hits
  - `CACHE_DISTANCE_MEDIUM = 0.85` - Medium confidence cache hits
  - `CACHE_DISTANCE_DUPLICATE = 0.3` - Duplicate detection for explicit keywords
  - `CACHE_TIME_THRESHOLD = 300` - 5 minutes for time-based invalidation
- **Enhanced Cache Logging**: Distance and confidence displayed for all cache operations (hits, misses, duplicates)
- **Docker Compose Consolidation**: Unified `docker/docker-compose.yml` with ChromaDB + optional SearXNG
- **Docker Documentation**: New `docker/README.md` with service management instructions

#### Changed
- **Vector Cache Architecture**: Migrated from PersistentClient to HttpClient (Docker server mode)
  - Fixes: File lock issues and deadlocks in async operations
  - Improvement: Thread-safe by design, no worker threads needed
- **Cache Query Strategy**: Implemented `query_newest()` method
  - Returns most recent match instead of best similarity match
  - Prevents outdated cache entries from being returned
- **Context Window Management**: Generous reserve strategy (8K-16K tokens) to prevent answer truncation
- **Project Structure**:
  - Moved `docker-compose.yml` from root to `docker/` directory
  - Consolidated separate ChromaDB and SearXNG compose files
- **Duplicate Detection**: Time-aware duplicate prevention
  - Skip save if similar entry exists and is < 5 minutes old
  - Allow new entry if existing entry is > 5 minutes old (allows updates)

#### Removed
- **Obsolete Implementations**: Deleted `archive/vector_cache_old/` directory
  - Old PersistentClient implementation (vector_cache.py)
  - Old worker thread implementation (vector_cache_v2.py)
  - Still available in git history if needed

#### Fixed
- **KeyError 'data'**: Fixed cache hit result format for explicit research keywords
- **Missing user_with_time**: Added timestamp generation for history entries
- **Cache Miss on Recent Entries**: Fixed `query_newest()` to find most recent duplicate
- **Duplicate Log Messages**: Proper distinction between saved entries and skipped duplicates
- **Distance Logging**: Distance now displayed for all cache operations (hits and misses)

#### Technical Details
- **ChromaDB Version**: Using latest ChromaDB Docker image
- **API Version**: ChromaDB v2 API (v1 deprecated)
- **Collection Management**:
  - Collection name: `research_cache`
  - Persistent storage: `./aifred_vector_cache/` (Docker volume)
  - Health checks: Automatic with 30s interval
- **Cache Statistics**: Available via `get_cache().get_stats()`

#### Migration Notes
- **Docker Commands Updated**: All docker-compose commands now require `-f docker/docker-compose.yml` or working from `docker/` directory
- **ChromaDB Reset**: Two methods documented in README
  - Option 1: Full restart (stop container, delete data, restart)
  - Option 2: API-based collection deletion (faster, no container restart)
- **Configuration**: All cache thresholds centralized in `aifred/lib/config.py`

---

## [Unreleased]

### Planned Features
- Adjustable cache retention policies
- Cache statistics dashboard in UI
- Export/import cache entries
- Multi-language support for cache queries

---

**Note**: This is the first formal release with changelog tracking. Previous development history is available in git commit history.
