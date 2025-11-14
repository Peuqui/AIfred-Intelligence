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

### Fix: Research Mode Persistence (2025-11-14)
**Problem**: Research Mode wurde beim Programmstart nicht korrekt wiederhergestellt. Einstellung ging verloren.

**Root Cause**:
- `set_research_mode_display()` setzte die Variable, rief aber `_save_settings()` nicht auf
- Settings-Infrastruktur war komplett da (Laden + Speichern im Dict)
- Nur der **Trigger** zum Speichern fehlte bei Mode-Änderung
- Inkonsistent mit anderen Settern wie `set_automatik_model()` (Zeile 1319), die korrekt speichern

**Solution Implemented**:
- Added `_save_settings()` call in `set_research_mode_display()` (Line 1314)
- Macht Setter konsistent: Jede User-Einstellung ruft `_save_settings()` auf
- Settings werden sofort persistiert wenn Mode geändert wird

**How it works**:
1. **Startup**: Lädt `research_mode` aus `settings.json` (Zeile 219) oder Fallback zu `"automatik"` aus `config.py`
2. **User changes mode**: UI ruft `set_research_mode_display()` → `_save_settings()` → `settings.json` updated
3. **Next startup**: Gespeicherter Mode wird wiederhergestellt

**Files Modified**:
- [aifred/state.py](aifred/state.py) Line 1314

**Status**: ✅ Completed (2025-11-14)
**Result**: Research Mode wird korrekt gespeichert und beim Programmstart wiederhergestellt

---

### 🎯 Milestone: Progress UI System Complete (2025-11-14)
**Achievement**: Vollständig funktionierendes Progress-Feedback-System über alle Research-Modi hinweg

**Problems Solved**:
1. **Progress Bar unsichtbar** - Orange Fill-Bar wurde durch CSS-Override versteckt
2. **Status Text nicht dynamisch** - "Generiere Antwort" wurde im "none"-Modus nicht angezeigt
3. **Quick/Deep Modi ohne Progress-Events** - Web-Scraping und LLM-Phasen wurden nicht angezeigt
4. **Keine Pulsier-Animation** - "Generiere Antwort" pulsierte nicht während LLM-Inferenz

**Solutions Implemented**:

#### 1. CSS Fix - Progress Bar Visibility
- **File**: [assets/custom.css](assets/custom.css) Line 90
- **Change**: Removed `.rt-Box` from dark background CSS selector
- **Reason**: `.rt-Box { background-color: #161b22 !important; }` overwrote orange progress bar color
- **Result**: Progress bar orange fill (#e67700) now visible in all modes

#### 2. Progress Event Handling - Quick/Deep Modes
- **File**: [aifred/state.py](aifred/state.py) Lines 942-960
- **Change**: Added complete progress event handling to Quick/Deep research modes
- **Events Added**: `progress`, `history_update`, `thinking_warning`
- **Logic**: Copied identical event handling from Automatik mode
- **Result**: Web-Scraping progress (1/3, 2/3, 3/3) now displayed correctly

#### 3. Dynamic Status Text - None Mode
- **File**: [aifred/aifred.py](aifred/aifred.py) Lines 449-464
- **Change**: Added `is_generating` state check for idle text display
- **Logic**: Shows "Generiere Antwort" when `is_generating=True`, even if `progress_active=False`
- **Result**: Status text updates correctly in "Eigenes Wissen (schnell)" mode

#### 4. Pulsing Animation - All Modes
- **File**: [aifred/aifred.py](aifred/aifred.py) Lines 526, 539, 543
- **Change**: Animation triggers on `progress_active | is_generating`
- **Before**: Only pulsed when `progress_active=True` (research modes only)
- **After**: Pulses during any LLM activity (research + none mode)
- **Result**: Visual feedback during all LLM inference phases

**Investigation Method**:
1. Git bisecting: Tested commits c282fdc (working) → main (broken)
2. Identified breaking commit: d8e4d55 ("Force dark theme")
3. CSS analysis revealed `.rt-Box` override issue
4. Code comparison: Automatik mode vs Quick/Deep mode event handling
5. Systematic testing of each fix in all 4 research modes

**Technical Details**:
- Progress events flow: `scraper_orchestrator.py` → `orchestrator.py` → `state.py` → `aifred.py`
- Event types: `progress` (scraping, llm, compress), `debug`, `content`, `result`, `history_update`
- State variables: `progress_active`, `progress_phase`, `progress_current`, `progress_total`, `is_generating`
- CSS specificity: `!important` overrides component-specific styling
- Reflex reactive rendering: `rx.cond()` for conditional UI updates

**Files Modified**:
- [assets/custom.css](assets/custom.css) Line 90
- [aifred/state.py](aifred/state.py) Lines 942-960
- [aifred/aifred.py](aifred/aifred.py) Lines 449-464, 526, 539, 543

**Testing Results**:
- ✅ **Automatik Mode**: Progress bar + pulsing "Automatik-Entscheidung" → "Web-Scraping 1/7" → "Generiere Antwort"
- ✅ **Quick Mode**: Progress bar + pulsing "Web-Scraping 1/3" → "Generiere Antwort"
- ✅ **Deep Mode**: Progress bar + pulsing "Web-Scraping 1/7" → "Generiere Antwort"
- ✅ **None Mode**: Pulsing "Generiere Antwort" (no web scraping, as expected)

**Status**: ✅ **MILESTONE COMPLETED** (2025-11-14)
**Impact**: Consistent, professional UI feedback across all research modes. User always knows what's happening.

---

### Fix: Broken Progress Bar in Web-Scraping
**Problem**: Progress bar wurde angezeigt (Box-Container sichtbar), aber der orangene Füllbalken war unsichtbar.

**Root Cause**:
- Commit d8e4d55 ("Force dark theme") fügte CSS-Override hinzu: `.rt-Box { background-color: #161b22 !important; }`
- Dies überschrieb die orangene Progress Bar Farbe (`#e67700`) mit dunklem Grau
- Der innere `rx.box` mit `background_color=COLORS["primary"]` wurde unsichtbar

**Investigation Method**:
1. Systematisches Git-Bisecting: Commit-für-Commit Testing von c282fdc (working) bis main (broken)
2. Identifiziert: d8e4d55 als ersten kaputten Commit
3. CSS-Analyse zeigte `.rt-Box` Override als Problem

**Solution Implemented**:
- Entfernte `.rt-Box` aus dem globalen dark background CSS-Override
- Behält dark theme für andere Container (Card, Container, Section, etc.)
- Progress Bar kann jetzt seine orangene Farbe behalten

**Files Modified**:
- [assets/custom.css](assets/custom.css) Line 90 - Removed `.rt-Box` from selector

**Status**: ✅ Completed (2025-11-14)
**Result**: Progress Bar funktioniert wieder in allen Modi (Automatik, Quick, Deep)

---

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
