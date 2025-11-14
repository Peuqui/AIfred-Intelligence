# AIfred Intelligence - TODO List

## High Priority

### TTL-based Vector Cache System
**Problem**: Semi-volatile queries (news, events) mit Keywords wie "aktuell", "neueste" werden aktuell nicht gecacht, obwohl die Daten für 24h wertvoll wären.

**Example**: "Recherchiere die aktuellen Ereignisse im Israelkrieg" wird als volatile erkannt und nicht gecacht, obwohl die Info für 1 Tag wiederverwendbar wäre.

**Solution Options**:
- **Option A**: Simple TTL parameter per cache entry (24h expiration for volatile queries)
- **Option B**: LLM-based volatility level detection (hourly/daily/permanent)
- **Option C**: User toggle to override volatile detection and force caching

**Implementation Details**:
- ChromaDB hat kein natives TTL → Manual cleanup job needed
- Add `expires_at` timestamp to cache metadata
- Background task to delete expired entries (daily cleanup)
- Modify `vector_cache.py` to support TTL parameter
- Update `cache_decision` prompt to classify volatility level

**Files to modify**:
- `aifred/lib/vector_cache.py` - Add TTL support
- `aifred/lib/research/context_builder.py` - Pass TTL parameter based on LLM decision
- `prompts/cache_decision_de.txt` / `prompts/cache_decision_en.txt` - Add volatility level classification

**Status**: Not started

---

## Medium Priority

(Future tasks go here)

---

## Low Priority

(Future tasks go here)

---

## Completed

(Completed tasks will be moved here)
