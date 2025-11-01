# Changelog - 2025-11-01

## UI Improvements & Bug Fixes

### 1. AI-Antwortfenster & Eingabefenster Clearing ✅

**Problem:**
- Fenster wurden nicht gelöscht nach Übertragung in Chat-History
- Oder zeigten History als Fallback (verwirrendes Verhalten)

**Lösung:**
- Beide Fenster zeigen nur noch ihre jeweiligen State-Variablen (kein History-Fallback)
- Clearing beim "result" Event mit expliziten yields
- `is_generating` wird sofort auf False gesetzt um UI-Flackern zu vermeiden

**Dateien geändert:**
- `aifred/aifred.py` - UI-Komponenten vereinfacht
- `aifred/state.py` - Clearing-Logik beim result-Event

**Einschränkung:**
Fenster werden nach Cache-Metadata-Generierung gelöscht (2-3s Verzögerung) aufgrund Reflex Framework Limitation (Event-Handler buffert Updates).

---

### 2. Temperature-Labels (faktisch/gemischt/kreativ) ✅

**Problem:**
- Debug-Ausgabe zeigte nur Zahlen: `🌡️ Temperature: 0.2 (auto)`
- Schwer zu verstehen welcher Intent erkannt wurde

**Lösung:**
- Neue Funktion `get_temperature_label()` in `intent_detector.py`
- Labels in allen Modi: "faktisch", "gemischt", "kreativ"
- Ausgabe jetzt: `🌡️ Temperature: 0.2 (auto, faktisch)`

**Dateien geändert:**
- `aifred/lib/intent_detector.py` - get_temperature_label() Funktion
- `aifred/lib/conversation_handler.py` - Labels für "Eigenes Wissen"
- `aifred/lib/research/cache_handler.py` - Labels für Cache-Hits
- `aifred/lib/research/context_builder.py` - Labels für RAG

---

### 3. RAG Intent-Detection ✅

**Problem:**
- RAG-Recherche verwendete hardcoded Temperature 0.7 (später 0.5)
- Wetterfragen (faktisch) liefen mit 0.5 statt 0.2
- Keine Unterscheidung zwischen faktischen/kreativen Recherchen

**Lösung:**
- RAG nutzt jetzt KI-basierte Intent-Detection (wie "Eigenes Wissen" Modus)
- Automatik-LLM analysiert User-Frage und wählt passende Temperature:
  - FAKTISCH → 0.2 (Wetter, News, Fakten)
  - KREATIV → 0.8 (Gedichte, Geschichten)
  - GEMISCHT → 0.5 (Kombination)

**Dateien geändert:**
- `aifred/lib/research/context_builder.py` - Intent-Detection Integration

**Beispiel:**
```
User: "Wie wird das Wetter morgen?"
→ Intent: FAKTISCH → Temperature: 0.2 ✅

User: "Schreibe ein Gedicht über Regen"
→ Intent: KREATIV → Temperature: 0.8 ✅
```

---

### 4. Cache-Initialisierung Fix ✅

**Problem:**
- Cache wurde nicht initialisiert bei Hot-Reload
- Fehler: "⚠️ DEBUG Cache-Speicherung fehlgeschlagen: Cache nicht initialisiert"

**Lösung:**
- Cache wird direkt beim Module-Import initialisiert
- `_research_cache` und `_research_cache_lock` nicht mehr `None`
- `on_load()` setzt Cache immer (auch bei bestehender Session)

**Dateien geändert:**
- `aifred/lib/cache_manager.py` - Cache bei Import initialisieren
- `aifred/state.py` - Cache immer in on_load() setzen

---

### 5. Debug-Messages bereinigt ✅

**Problem:**
- Doppelte Messages: "🔧 Cache-Metadata wird generiert..." + "📝 Starte Cache-Metadata Generierung..."

**Lösung:**
- Erste Message entfernt
- Nur noch eine klare Message bleibt

**Dateien geändert:**
- `aifred/lib/research/context_builder.py` - Redundante Message entfernt

---

## Commits

```
80e0a63 - Fix: UI clearing timing + Temperature labels + RAG intent detection
7f2e092 - Fix: Add explicit yields after clearing to force immediate UI update
897d8bd - Fix: Set is_generating=False immediately after result to prevent UI flicker
397c546 - Fix: Add 100ms delay after clearing to ensure UI renders
b8cb3ba - Fix: Eingabefenster zeigt nur current_user_message (kein History-Fallback)
6d374e1 - Fix: Remove loop break + Remove duplicate cache metadata message
```

---

## Technische Details

### Intent-Detection Flow

1. **User stellt Frage** (z.B. "Wie wird das Wetter morgen?")
2. **Automatik-Mode**: KI entscheidet ob Web-Recherche nötig
3. **Falls JA → RAG-Recherche:**
   - Web-Suche + Scraping
   - **Intent-Detection läuft** mit Automatik-LLM (qwen2.5:3b)
   - Prompt aus `prompts/intent_detection.txt`
   - LLM antwortet: "FAKTISCH" / "KREATIV" / "GEMISCHT"
   - Temperature wird entsprechend gesetzt (0.2 / 0.8 / 0.5)
4. **Haupt-LLM generiert Antwort** mit adaptiver Temperature

### Cache-Metadata Generation

- Läuft NACH Haupt-Antwort (nicht blockierend für User)
- Verwendet Automatik-LLM (qwen2.5:3b)
- Erstellt kurze Zusammenfassung (max 60 Wörter)
- Wird für spätere Context-Integration genutzt
- Temperature: 0.1 (sehr konsistent)
- num_ctx: min(2048, haupt_llm_limit // 2)

---

## Breaking Changes

Keine Breaking Changes - alle Änderungen sind abwärtskompatibel.

---

## Known Issues

1. **UI-Clearing Verzögerung:**
   - Fenster werden nach Cache-Metadata gelöscht (2-3s Verzögerung)
   - Grund: Reflex Framework buffert State-Updates
   - Workaround: Akzeptieren oder separate Background-Task (komplex)

---

## Testing Notes

Getestet mit:
- Reflex 0.8.17
- Python 3.12
- Ollama Backend
- qwen3:8b (Haupt-LLM)
- qwen2.5:3b (Automatik-LLM)

Test-Szenarien:
- ✅ Wetter-Frage (faktisch → 0.2)
- ✅ "Hallo bist du da" (kein Research, eigenes Wissen)
- ✅ Cache-Metadata Generierung
- ✅ Hot-Reload (Cache bleibt initialisiert)
- ✅ Debug-Messages korrekt

---

## Future Improvements

1. **UI-Clearing sofort nach Result** (erfordert Reflex Framework Änderung oder Background-Task)
2. **Cache-Metadata Fortschrittsanzeige** (optional, wenn User es sehen will)
3. **Intent-Detection Caching** (wenn gleiche Frage mehrfach gestellt wird)

---

**Erstellt:** 2025-11-01
**Autor:** Claude (AI Assistant)
**Commits:** 80e0a63 bis 6d374e1
