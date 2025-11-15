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

### Enhanced Debug Logging & Query Visibility (2025-11-15)
**Achievement**: Consistent, detailed debug output across all research modes with precise timing measurements and optimized query visibility.

**Problems Solved**:
1. **Inconsistent Debug Output** - "Eigenes Wissen" mode had sparse logging compared to "Automatik" mode
2. **Missing Preload Timing** - "Haupt-LLM vorgeladen" showed no time measurement
3. **Hidden Optimized Queries** - Users couldn't see what search terms the LLM generated for web research

**Solutions Implemented**:

#### 1. Comprehensive Debug Logging for "Eigenes Wissen" Mode
- **File**: [aifred/state.py](aifred/state.py) Lines 1014-1093
- **Added Messages**:
  - `🚀 Haupt-LLM (model) wird vorgeladen...`
  - `✅ Haupt-LLM vorgeladen (X.Xs)` - with precise timing
  - `✅ System-Prompt erstellt`
  - `📊 Haupt-LLM: input / num_ctx Tokens (max: limit)`
  - `🌡️ Temperature: X.X (manual)`
  - `🤖 Haupt-LLM startet: model_name`
  - `⚡ TTFT: X.XXs` - Time To First Token
  - `✅ Haupt-LLM fertig (X.Xs, Y tokens, Z.Z tok/s)`
- **Result**: Debug output now matches Automatik mode quality

#### 2. Precise Preload Time Measurement
- **Files**: [aifred/state.py](aifred/state.py#L1019-L1030), [aifred/lib/conversation_handler.py](aifred/lib/conversation_handler.py#L395-L412)
- **Implementation**:
  - **Ollama Backend**: Calls `backend.preload_model()` for actual model loading time (1-5 seconds)
  - **vLLM/TabbyAPI**: Measures preparation time only (models stay in VRAM, 0.1-0.5 seconds)
  - Backend-aware timing ensures accurate performance metrics
- **Result**: All "vorgeladen" messages show precise timing in parentheses

#### 3. Optimized Query Display in Debug Console
- **File**: [aifred/lib/research/query_processor.py](aifred/lib/research/query_processor.py#L61)
- **Added Message**: `🔎 Optimierte Query: [optimized search terms]`
- **Visible in**: All web research modes (Automatik, Websuche schnell, Websuche ausführlich)
- **Result**: Users can now assess quality of LLM-generated search queries

**Files Modified**:
- [aifred/state.py](aifred/state.py): Lines 982-1030, 1036-1093 (Eigenes Wissen mode logging)
- [aifred/lib/conversation_handler.py](aifred/lib/conversation_handler.py): Lines 356-417 (Automatik mode logging)
- [aifred/lib/research/query_processor.py](aifred/lib/research/query_processor.py): Line 61 (optimized query display)

**Testing Results**:
- ✅ **Eigenes Wissen**: Shows all debug messages with precise timing
- ✅ **Automatik (Eigenes Wissen branch)**: Shows preload timing (previously missing)
- ✅ **Web-Recherche Modi**: Show optimized query + all existing debug info
- ✅ **Backend-Awareness**: Different timing for Ollama (actual load) vs vLLM/TabbyAPI (prep only)

**Status**: ✅ Completed & Tested (2025-11-15)
**Impact**: Professional debug output across all modes, easier performance optimization and web search quality assessment

---

### Fix: Automatic Date/Time Injection in All Prompts (2025-11-15)
**Problem**: LLM hatte kein Bewusstsein für aktuelles Datum/Zeit, konnte nicht unterscheiden ob Trainingsdaten veraltet sind oder welches Jahr/Monat aktuell ist.

**Root Cause**:
- Kein System-Prompt mit Datum/Zeit im "Eigenes Wissen"-Modus
- Manuelle Datum-Injektion in einigen Prompts (system_rag, query_optimization), aber inkonsistent
- Keine zentrale Stelle für Timestamp-Injection

**Solution Implemented**:
**Zentrale Timestamp-Injection in `load_prompt()`** (Option 2 - User-approved):
1. ✅ `load_prompt()` injiziert automatisch Datum/Zeit **vor** jeden geladenen Prompt
2. ✅ Neue minimale System-Prompts für "Eigenes Wissen"-Modus (`system_minimal.txt`)
3. ✅ Alle manuellen `{current_date}` / `{current_year}` Platzhalter aus Prompts entfernt
4. ✅ Convenience-Functions cleanup (get_query_optimization_prompt, get_decision_making_prompt, get_system_rag_prompt)

**Implementation Details**:

**1. Central Timestamp Injection** - [aifred/lib/prompt_loader.py](aifred/lib/prompt_loader.py) Lines 123-152
```python
# Inject current date/time (always, for all prompts)
now = datetime.now()
if lang == "de":
    timestamp_prefix = f"""AKTUELLES DATUM UND UHRZEIT:
- Datum: {weekday_de}, {now.strftime('%d.%m.%Y')}
- Uhrzeit: {now.strftime('%H:%M:%S')} Uhr
- Jahr: {now.year}

"""
# Prepend to every loaded prompt
prompt_template = timestamp_prefix + prompt_template
```

**2. Minimal System Prompts** - [prompts/de/system_minimal.txt](prompts/de/system_minimal.txt), [prompts/en/system_minimal.txt](prompts/en/system_minimal.txt)
```
Du bist ein hilfreicher AI-Assistent.

Beantworte Fragen präzise und nutze dein Trainingswissen. Wenn deine Trainingsdaten veraltet sind oder du dir unsicher bist, weise den Nutzer darauf hin.
```
(Timestamp wird automatisch von `load_prompt()` vorangestellt)

**3. Eigenes Wissen Mode Update** - [aifred/lib/conversation_handler.py](aifred/lib/conversation_handler.py) Lines 360-363
```python
# Inject minimal system prompt with timestamp
from .prompt_loader import load_prompt
system_prompt_minimal = load_prompt('system_minimal', lang=detected_user_language)
messages.insert(0, {"role": "system", "content": system_prompt_minimal})
```

**4. Cleanup - Removed Manual Date Injection**:
- [aifred/lib/prompt_loader.py](aifred/lib/prompt_loader.py) Lines 195-230: Removed `current_date`/`current_year` parameters from convenience functions
- [aifred/lib/research/context_builder.py](aifred/lib/research/context_builder.py) Lines 102-108: Removed manual date params
- **All prompt files**: Removed `{current_date}` and `{current_year}` placeholders (10 files updated)

**Files Modified**:
- [aifred/lib/prompt_loader.py](aifred/lib/prompt_loader.py) - Central timestamp injection + cleanup
- [aifred/lib/conversation_handler.py](aifred/lib/conversation_handler.py) - System prompt for "Eigenes Wissen"
- [aifred/lib/research/context_builder.py](aifred/lib/research/context_builder.py) - Removed manual date params
- [prompts/de/system_minimal.txt](prompts/de/system_minimal.txt) - New minimal system prompt (German)
- [prompts/en/system_minimal.txt](prompts/en/system_minimal.txt) - New minimal system prompt (English)
- 10 prompt files updated (system_rag, query_optimization, decision_making, cache_decision, etc.)

**How it works**:
1. **Every `load_prompt()` call** automatically prepends timestamp (Date, Time, Weekday, Year)
2. **"Eigenes Wissen" mode** loads `system_minimal.txt` → gets timestamp automatically
3. **Web-Recherche modes** load `system_rag.txt` → gets timestamp automatically
4. **Decision-Making & Query-Optimization** → get timestamp automatically
5. **All modes** now have consistent date/time awareness

**Result**:
✅ LLM knows current date/time in ALL modes (Eigenes Wissen, Quick, Deep, Automatik)
✅ Can determine if training data is outdated
✅ Can use current year for query expansion ("neueste Ereignisse" → adds current year)
✅ Central maintenance - only one place to update timestamp format
✅ No duplicate timestamp injection

**Additional Fixes (2025-11-15 Evening)**:

**Fix 1: Missing Import in state.py**
- **Problem**: "Eigenes Wissen" mode crashed with `ModuleNotFoundError: No module named 'aifred.lib.language_detection'`
- **Root Cause**: [aifred/state.py](aifred/state.py#L990) tried to import `detect_language` from non-existent module
- **Solution**: Changed import to `from .lib.prompt_loader import load_prompt, detect_language` (Lines 990)
- **Files Modified**: [aifred/state.py](aifred/state.py#L989-L993)

**Fix 2: Research Mode Settings Not Persisting**
- **Problem**: Research mode reset to "Automatik" on every restart, even though `settings.json` stored `"research_mode": "none"`
- **Root Cause**: [aifred/state.py](aifred/state.py#L219) loaded `research_mode` from settings, but `research_mode_display` (UI value) was never updated → UI always showed default "🤖 Automatik"
- **Solution**: Added automatic `research_mode_display` sync when loading settings (Lines 221-223)
- **Implementation**:
```python
# Update research_mode_display to match loaded research_mode
from .lib import TranslationManager
self.research_mode_display = TranslationManager.get_research_mode_display(self.research_mode)
```
- **Files Modified**: [aifred/state.py](aifred/state.py#L219-L223)

**Status**: ✅ Completed & Tested (2025-11-15)
- ✅ Timestamp injection working in all 4 research modes
- ✅ "Eigenes Wissen" mode no longer crashes
- ✅ Research mode settings persist across restarts
- ✅ LLM correctly knows current date (tested: November 15, 2024)

---

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
