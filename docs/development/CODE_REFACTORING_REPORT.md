# AIfred Intelligence - Code Refactoring Bericht

**Datum:** 2025-10-17
**Analysiert von:** Claude Code Agent
**Code-Basis:** ~3500 Zeilen (aifred_intelligence.py + lib/*)

---

## Executive Summary - Top 5 Refactoring-Prioritäten

### 1. ⚠️ KRITISCH: Duplizierte History-Parsing Logik (6+ Stellen)
**Impact:** Hoch | **Aufwand:** Mittel | **Risiko:** Niedrig
**Status:** ✅ PHASE 1 - In Bearbeitung

- Identisches History-Parsing an 6+ Stellen im Code
- 65-143 Zeilen pro Duplikat = ~400 Zeilen duplizierten Code!
- Bug: Timing-Parse-Pattern inkonsistent (fehlt "(Agent:" an manchen Stellen)

**Lösung:** Zentrale Funktion `lib/message_builder.py:build_messages_from_history()`

**Einsparung:** ~60 Zeilen Code, 1 Bug-Fix

---

### 2. ⚠️ KRITISCH: Fehlende GPU-Mode Calls in Agent-Code
**Impact:** Mittel | **Aufwand:** Niedrig | **Risiko:** Niedrig
**Status:** ✅ PHASE 1 - In Bearbeitung

- `lib/agent_core.py`: 5 ollama.chat() Calls OHNE set_gpu_mode()
- GPU-Toggle wird bei Agent-Research ignoriert
- Nutzt immer GPU Auto-Detect (Standard)

**Locations:**
- `agent_core.py:81` (optimize_search_query)
- `agent_core.py:183` (ai_rate_urls)
- `agent_core.py:392` (perform_agent_research - finale Antwort)
- `agent_core.py:477` (chat_interactive_mode - Entscheidung)
- `agent_core.py:511` (chat_interactive_mode - finale Antwort)

**Lösung:** `set_gpu_mode(enable_gpu, llm_options)` am Anfang von `perform_agent_research()` und `chat_interactive_mode()`

**Einsparung:** Konsistenz, User-Kontrolle über GPU-Nutzung

---

### 3. 🧹 Ungenutzter Code (Tote Funktionen)
**Impact:** Mittel | **Aufwand:** Niedrig | **Risiko:** Niedrig
**Status:** ✅ PHASE 1 - In Bearbeitung

**Ungenutzter Code:**
1. `clear_gpu_mode()` - lib/ollama_wrapper.py:441-451 - DEFINIERT ABER NIE AUFGERUFEN
2. `debug_log()` - lib/logging_utils.py:38-48 - DEFINIERT ABER NIE GENUTZT
3. Doppelter `import os` - aifred_intelligence.py:4 und 1026

**Lösung:** Löschen aller ungenutzten Funktionen und Imports

**Einsparung:** ~30 Zeilen Code, weniger Verwirrung

---

### 4. 📊 Duplizierte Chat-Step Funktionen (Audio vs. Text)
**Impact:** Hoch | **Aufwand:** Mittel | **Risiko:** Niedrig
**Status:** 🔄 PHASE 2 - Geplant

**Duplikate:**
- `chat_audio_step2_ai` vs. `chat_text_step1_ai`: 95% identisch
- `chat_audio_step2_with_mode` vs. `chat_text_step1_with_mode`: 100% identisch
- Einziger Unterschied: STT-Zeit (für Text immer 0.0)

**Lösung:** Zentrale Funktion `lib/chat_core.py:generate_ai_response()`

**Einsparung:** ~200 Zeilen Code

---

### 5. 📏 Zu lange Funktionen (>50 Zeilen)
**Impact:** Mittel | **Aufwand:** Hoch | **Risiko:** Mittel
**Status:** 🔄 PHASE 2-3 - Geplant

**Problem-Funktionen:**
1. `perform_agent_research()` - 178 ZEILEN! (lib/agent_core.py:235-412)
2. `chat_interactive_mode()` - 121 ZEILEN (lib/agent_core.py:415-536)

**Lösung:** Aufteilen in Sub-Funktionen
- `_build_agent_system_prompt()` (45 Zeilen)
- `_scrape_top_urls()` (25 Zeilen)
- `should_use_web_search()` (20 Zeilen)
- `_generate_standard_response()` (30 Zeilen)

**Einsparung:** Bessere Wartbarkeit, Testbarkeit

---

## Phase 1: Quick Wins (✅ In Bearbeitung)

**Zeitaufwand:** 1-2 Stunden
**Risiko:** Niedrig
**Impact:** Hoch

### ✅ Schritt 1.1: Entferne toten Code
- [ ] Entferne `clear_gpu_mode()` aus lib/ollama_wrapper.py
- [ ] Entferne `debug_log()` aus lib/logging_utils.py
- [ ] Entferne doppelten `import os` aus aifred_intelligence.py

### ✅ Schritt 1.2: Erstelle lib/message_builder.py
- [ ] Neue Datei mit `build_messages_from_history()`
- [ ] Robustes Timing-Pattern-Parsing (STT, Agent, Inferenz, TTS, Entscheidung)

### ✅ Schritt 1.3: Ersetze History-Parsing Duplikate
- [ ] aifred_intelligence.py (2 Stellen)
- [ ] lib/agent_core.py (4 Stellen)
- [ ] Teste Multi-Turn Conversations

### ✅ Schritt 1.4: Fixe Agent GPU-Mode
- [ ] Füge `enable_gpu` und `llm_options` Parameter zu `perform_agent_research()` hinzu
- [ ] Füge `enable_gpu` und `llm_options` Parameter zu `chat_interactive_mode()` hinzu
- [ ] Füge `set_gpu_mode()` am Anfang beider Funktionen hinzu
- [ ] Update alle Caller in aifred_intelligence.py
- [ ] Teste GPU-Toggle bei Agent-Research

**Einsparung Phase 1:** ~90 Zeilen Code, 2 Bugs gefixt

---

## Phase 2: Mittlere Refactorings (🔄 Geplant)

**Zeitaufwand:** 3-4 Stunden
**Risiko:** Mittel
**Impact:** Sehr Hoch

### Schritt 2.1: Erstelle lib/chat_core.py
- [ ] `generate_ai_response()` - vereinigt audio/text chat functions
- [ ] `generate_ai_response_with_mode()` - vereinigt with_mode functions
- [ ] Wrapper-Funktionen in aifred_intelligence.py

### Schritt 2.2: Splitte perform_agent_research()
- [ ] Extrahiere `_build_agent_system_prompt()` (45 Zeilen)
- [ ] Extrahiere `_scrape_top_urls()` (25 Zeilen)
- [ ] Update `perform_agent_research()` zu nutzen von Sub-Funktionen

### Schritt 2.3: Splitte chat_interactive_mode()
- [ ] Erstelle lib/agent_prompts.py mit `AUTOMATIK_DECISION_PROMPT`
- [ ] Extrahiere `should_use_web_search()` (20 Zeilen)
- [ ] Extrahiere `_generate_standard_response()` (30 Zeilen)

**Einsparung Phase 2:** ~250 Zeilen Code

---

## Phase 3: Große Refactorings (📋 Optional)

**Zeitaufwand:** 5-8 Stunden
**Risiko:** Hoch
**Impact:** Mittel

### Schritt 3.1: Move reload_model() Logic zu lib/
- [ ] Erstelle `reload_model_with_config()` in lib/memory_manager.py
- [ ] Wrapper in aifred_intelligence.py

### Schritt 3.2: Move model_changed() Logic zu lib/
- [ ] Erstelle `handle_model_change()` in lib/settings_manager.py

### Schritt 3.3: Docstrings hinzufügen
- [ ] Alle Funktionen in aifred_intelligence.py (Google-Style)
- [ ] Fehlende Docstrings in agent_tools.py

### Schritt 3.4: Umbenennen für Konsistenz (OPTIONAL)
- [ ] `chat_audio_step2_ai` → `chat_audio_ai_inference`
- [ ] `chat_text_step1_ai` → `chat_text_ai_inference`

**Einsparung Phase 3:** +100 Zeilen bessere Struktur

---

## Code-Metriken Vorher/Nachher

| Metrik | Vorher | Nach Phase 1 | Nach Phase 2 | Nach Phase 3 |
|--------|--------|--------------|--------------|--------------|
| Gesamt-Zeilen | ~3.500 | ~3.410 (-90) | ~3.160 (-340) | ~3.060 (-440) |
| Längste Funktion | 178 Zeilen | 178 Zeilen | 70 Zeilen | 70 Zeilen |
| Duplizierter Code | ~400 Zeilen | ~340 Zeilen | 0 Zeilen | 0 Zeilen |
| Funktionen >50 Zeilen | 3 | 3 | 0 | 0 |
| Funktionen >10 Params | 3 | 3 | 0 | 0 |
| Ungenutzte Funktionen | 2 | 0 | 0 | 0 |

---

## Risiko-Bewertung

| Phase | Risiko | Zeitaufwand | Impact | Empfehlung |
|-------|--------|-------------|--------|------------|
| Phase 1 (Quick Wins) | **Niedrig** ✅ | 1-2h | **Hoch** | **SOFORT** |
| Phase 2 (Mittlere Refactorings) | Mittel | 3-4h | Sehr Hoch | BALD |
| Phase 3 (Große Refactorings) | Hoch | 5-8h | Mittel | OPTIONAL |

---

## Detaillierte Findings

### 1. Duplizierter Code - History-Parsing

**Locations:**
```python
# aifred_intelligence.py:60-69 (chat_audio_step2_ai)
messages = []
for h in history:
    user_msg = h[0].split(" (STT:")[0] if " (STT:" in h[0] else h[0]
    ai_msg = h[1].split(" (Inferenz:")[0] if " (Inferenz:" in h[1] else h[1]
    messages.extend([
        {'role': 'user', 'content': user_msg},
        {'role': 'assistant', 'content': ai_msg}
    ])
messages.append({'role': 'user', 'content': user_text})

# aifred_intelligence.py:136-145 (chat_text_step1_ai) - IDENTISCH!
# lib/agent_core.py:376-382 (perform_agent_research) - ÄHNLICH, fehlt "(Agent:"
# lib/agent_core.py:496-503 (chat_interactive_mode) - ÄHNLICH
```

**Problem:**
- Code existiert 6+ Mal
- Inkonsistent: Manche Stellen parsen "(Agent:", andere nicht
- Bug-Anfällig: Änderung muss an 6 Stellen gemacht werden

**Lösung - lib/message_builder.py:**
```python
def build_messages_from_history(history, current_user_text):
    """
    Konvertiert Gradio-History zu Ollama-Messages Format

    Entfernt Timing-Info wie "(STT: 2.5s)", "(Inferenz: 1.3s)", "(Agent: 45.2s)"
    aus History-Einträgen für sauberen Context.

    Args:
        history: List[Tuple[str, str]] - Gradio Chatbot History
        current_user_text: str - Aktuelle User-Nachricht

    Returns:
        List[Dict] - Ollama messages Format
    """
    messages = []

    # Alle bekannten Timing-Pattern
    timing_patterns = [
        " (STT:",
        " (Agent:",
        " (Inferenz:",
        " (TTS:",
        " (Entscheidung:"
    ]

    for user_turn, ai_turn in history:
        # Entferne ALLE Timing-Pattern vom User-Text
        clean_user = user_turn
        for pattern in timing_patterns:
            if pattern in clean_user:
                clean_user = clean_user.split(pattern)[0]

        # Entferne ALLE Timing-Pattern vom AI-Text
        clean_ai = ai_turn
        for pattern in timing_patterns:
            if pattern in clean_ai:
                clean_ai = clean_ai.split(pattern)[0]

        messages.extend([
            {'role': 'user', 'content': clean_user},
            {'role': 'assistant', 'content': clean_ai}
        ])

    # Aktuelle User-Nachricht anhängen
    messages.append({'role': 'user', 'content': current_user_text})

    return messages
```

**Usage:**
```python
# Vorher (10 Zeilen):
messages = []
for h in history:
    user_msg = h[0].split(" (STT:")[0] if " (STT:" in h[0] else h[0]
    ai_msg = h[1].split(" (Inferenz:")[0] if " (Inferenz:" in h[1] else h[1]
    messages.extend([
        {'role': 'user', 'content': user_msg},
        {'role': 'assistant', 'content': ai_msg}
    ])
messages.append({'role': 'user', 'content': user_text})

# Nachher (1 Zeile):
messages = build_messages_from_history(history, user_text)
```

---

### 2. Ungenutzter Code

#### 2.1 `clear_gpu_mode()` - NIE AUFGERUFEN

**Location:** lib/ollama_wrapper.py:441-451

```python
def clear_gpu_mode():
    """Räumt GPU-Einstellung auf (nach Request)"""
    if hasattr(_thread_local, 'enable_gpu'):
        was_gpu = _thread_local.enable_gpu
        del _thread_local.enable_gpu
        debug_print(f"🔧 [GPU Mode] Cleanup - {'GPU' if was_gpu else 'CPU'} Modus beendet")
```

**Problem:**
- Funktion wird NIRGENDWO im gesamten Codebase aufgerufen
- Thread-local wird bei `set_gpu_mode()` überschrieben → Cleanup unnötig
- Dokumentation sagt "nach Request", aber kein Cleanup in Gradio-Callbacks

**Lösung:** ENTFERNEN

---

#### 2.2 `debug_log()` - NIE GENUTZT

**Location:** lib/logging_utils.py:38-48

```python
def debug_log(message):
    """Logging-basierte Debug-Ausgabe"""
    if DEBUG_ENABLED:
        logger.debug(message)
```

**Problem:**
- NIEMALS verwendet im gesamten Codebase
- Alle verwenden `debug_print()` stattdessen (direkt zu stdout)
- Toter Code

**Lösung:** ENTFERNEN

---

#### 2.3 Doppelter Import

**Location:** aifred_intelligence.py

```python
# Zeile 4:
import os

# ... viel später ...

# Zeile 1026:
import os  # DOPPELT!
```

**Lösung:** Entferne Zeile 1026

---

### 3. Agent GPU-Mode Bug

**Problem:**
- Agent-Research nutzt IMMER GPU Auto-Detect
- Ignoriert User-Toggle für GPU on/off
- Inkonsistent mit Rest der Anwendung

**Betroffene Funktionen:**
1. `perform_agent_research()` - lib/agent_core.py:235
2. `chat_interactive_mode()` - lib/agent_core.py:415

**Lösung:**

```python
# lib/agent_core.py

def perform_agent_research(user_text, stt_time, mode, model_choice, automatik_model, history, enable_gpu=True, llm_options=None):
    """
    Führt Agent-Research durch (Web-Suche + Scraping + AI-Antwort)

    Args:
        ...
        enable_gpu: bool - GPU-Beschleunigung aktivieren (default: True)
        llm_options: Dict - Custom LLM-Parameter (z.B. num_ctx)
    """
    # WICHTIG: Setze GPU-Modus am ANFANG!
    set_gpu_mode(enable_gpu, llm_options or {})

    agent_start = time.time()

    # Rest der Funktion bleibt gleich...
    # Alle ollama.chat() Calls nutzen jetzt automatisch enable_gpu!
```

**Caller Update (aifred_intelligence.py):**

```python
# In generate_ai_response_with_mode():

elif "Schnell" in research_mode:
    debug_print(f"⚡ Modus: Web-Suche Schnell (Agent)")
    return perform_agent_research(
        user_text, stt_time, "quick",
        model_choice, automatik_model, history,
        enable_gpu,  # ← NEU: durchreichen!
        llm_options  # ← NEU: durchreichen!
    )

elif "Ausführlich" in research_mode:
    debug_print(f"🔍 Modus: Web-Suche Ausführlich (Agent)")
    return perform_agent_research(
        user_text, stt_time, "deep",
        model_choice, automatik_model, history,
        enable_gpu,  # ← NEU: durchreichen!
        llm_options  # ← NEU: durchreichen!
    )

elif "Automatik" in research_mode:
    debug_print(f"🤖 Modus: Automatik (KI entscheidet)")
    return chat_interactive_mode(
        user_text, stt_time,
        model_choice, automatik_model, history,
        enable_gpu,  # ← NEU: durchreichen!
        llm_options  # ← NEU: durchreichen!
    )
```

---

## Zusammenfassung

### Gesamt-Einsparungen (alle Phasen)

| Kategorie | Zeilen entfernt | Bugs gefixt |
|-----------|----------------|-------------|
| Duplizierter Code (History-Parsing) | ~60 | 1 |
| Duplizierte Chat-Funktionen | ~200 | 0 |
| Ungenutzter Code | ~30 | 0 |
| Funktion-Splitting | ~150 | 0 |
| **GESAMT** | **~440 Zeilen** | **1 Bug** |

### Wichtigste Erkenntnisse

**Stärken des aktuellen Codes:**
- ✅ Gute Modul-Aufteilung (lib/* ist sinnvoll strukturiert)
- ✅ Konsistente Nutzung von debug_print()
- ✅ Kein auskommentierter Code (sauber!)
- ✅ Gute Abstraktion (ollama_wrapper ist elegant!)

**Größte Schwächen:**
- ❌ Massive Duplikation (History-Parsing 6x!)
- ❌ Fehlende GPU-Mode Aufrufe in Agent-Code
- ❌ Zu lange Funktionen (perform_agent_research: 178 Zeilen!)
- ❌ Ungenutzter Code (clear_gpu_mode, debug_log)

**Empfohlene Reihenfolge:**
1. **SOFORT:** Phase 1 (Quick Wins) - Höchster ROI, geringes Risiko
2. **BALD:** Phase 2 (Mittlere Refactorings) - Größte Code-Reduktion
3. **OPTIONAL:** Phase 3 (Große Refactorings) - Nur für Wartbarkeit

---

**Ende des Berichts**
