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

### Fix: Automatik-LLM Double-Loading on Startup
**Problem**: Automatik-LLM wurde beim Start zweimal geladen (Load → Unload → Reload), sichtbar in `nvidia-smi` als VRAM-Thrashing innerhalb von Sekunden.

**Root Cause**:
- `state.py` Line 93 setzte `automatik_model` mit hardcodiertem `config.py` Default bei Class-Init
- `settings.json` wurde erst später in `on_load()` geladen
- Resultat: Falsches Modell initial geladen → richtiges Modell nachgeladen → unnötiger Cycle

**Solution Implemented**:
- Models werden nicht mehr bei Class-Definition initialisiert (leere Strings)
- `settings.json` wird ZUERST geladen in `on_load()`
- `config.py` dient nur als Fallback wenn `settings.json` leer/nicht vorhanden
- Betrifft sowohl `selected_model` als auch `automatik_model`

**Files Modified**:
- [aifred/state.py](aifred/state.py) Lines 89-95, 239-256

**Status**: ✅ Completed (2025-11-14)
**Result**: Keine doppelten Model-Loads mehr beim Start, kein VRAM-Thrashing

---
