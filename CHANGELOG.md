# Changelog

Alle wichtigen Änderungen an AIfred Intelligence werden in dieser Datei dokumentiert.

Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

---

## [2025-10-25] - Logging-System & Intelligent Scraping Optimierungen

### 🎯 Hinzugefügt

#### File-Based Debug Logging System
- **Neues Debug-Log-System** in `lib/logging_utils.py`
  - Alle Debug-Ausgaben gehen jetzt in `logs/aifred_debug.log` (nicht mehr Journal Control)
  - **Automatische Log-Rotation**: Bei >1 MB wird rotiert (alte Datei → `.old`, neue erstellt)
  - **Timestamp-Format**: `HH:MM:SS.mmm` für präzises Timing
  - **Überschreiben bei Service-Start**: Jeder Service-Neustart startet mit leerem Log
  - **Append bei Browser-Reload**: Mehrere Tests hintereinander bleiben sichtbar
  - **Maximale Größe**: 1 MB = ~20-30 Anfragen (überschaubar zum Debuggen)

#### Intelligente Scraping-Strategie
- **Download-Failed Detection** in `lib/agent_tools.py`:
  - Wenn Trafilatura Download fehlschlägt (404, Timeout, Bot-Protection) → **KEIN Playwright Retry**
  - Spart ~10s Timeout pro blockierter Site (z.B. AccuWeather)
  - Nur bei erfolgreichem Download mit wenig Content (< 1000 Wörter) → Playwright Fallback
- **AccuWeather Timeout-Problem gelöst**: Von 43s auf sofortigen Skip reduziert

#### Cache-Metadata Logging
- **Vollständiges Cache-Metadata Logging** in `lib/agent_core.py` (Zeilen 1312-1315)
  - Zeigt exakten Inhalt des Cache-Metadata wenn verfügbar
  - Hilft bei Diagnose von Cache-basierten Entscheidungen

#### Haupt-LLM Message Logging
- **Komplettes Message-Array Logging** in `lib/agent_core.py` (Zeilen 1132-1145)
  - Zeigt alle Messages die an Haupt-LLM (z.B. qwen3:8b) übergeben werden
  - Inklusive System-Prompt Preview (erste 500 Zeichen)
  - Zeigt Gesamt-Token-Count und Context-Window-Größe

### 🐛 Behoben

#### Context Limit Detection
- **Priorisierung korrigiert** in `lib/agent_core.py` (Zeilen 273-285):
  - `original_context_length` wird jetzt VOR `.context_length` geprüft
  - Verhindert falsche Extended-Context-Werte für Modelle mit RoPE-Scaling
  - Beispiel: phi3:mini zeigt jetzt korrekt 4096 statt 131072 Tokens

#### Context Limit Warning Bug
- **is_automatik_llm Parameter korrigiert** in `lib/agent_core.py` (Zeile 1137):
  - Haupt-LLM (qwen3:8b) nutzt jetzt korrekt `is_automatik_llm=False`
  - Verhindert falsche Warnung: "5687 Tokens > 4096 Limit" → jetzt korrekt "5687 < 40960"

#### Sprachliche Mehrdeutigkeit in Decision Prompt
- **"Tag" → "XML-Markierung"** in `prompts/decision_making.txt`:
  - Zeile 5: "ANTWORTE NUR MIT EINEM TAG" → "ANTWORTE NUR MIT EINER DIESER XML-MARKIERUNGEN"
  - Zeile 27: "Antworte NUR mit dem Tag!" → "Antworte NUR mit der XML-Markierung (nichts anderes)!"
  - Verhindert Verwechslung mit deutschem Wort "Tag" (= day)
  - **Resultat**: qwen2.5:3b wählt jetzt korrekt `<search>yes</search>` für Wetter-Fragen

#### External Library Logging Spam
- **Logging-Level auf WARNING gesetzt** in `lib/agent_tools.py` (Zeilen 30-34):
  - Trafilatura "discarding element" Messages unterdrückt
  - Playwright, urllib3, requests Debug-Output deaktiviert
  - Journal Control bleibt sauber

### ⚡ Optimiert

#### Scraping Timeouts reduziert
- **Trafilatura Timeout**: 15s → 10s (`lib/agent_tools.py` Zeile 645)
- **Playwright Timeout**: 15000ms → 10000ms (`lib/agent_tools.py` Zeile 773)
- **Thread Timeout**: 20s → 10s (`lib/agent_core.py` Zeile 1036)
- **Intelligente Strategie**: Download-Fail = Max 10s (kein Playwright), JS-Heavy = Max 20s (Trafilatura + Playwright)

### 🔧 Geändert

#### Architektur-Vereinfachung: Context Limits
- **Dictionary entfernt**, ersetzt durch 2 globale Variablen in `lib/agent_core.py`:
  - `_haupt_llm_context_limit = 4096` (Fallback)
  - `_automatik_llm_context_limit = 4096` (Fallback)
- **Setter-Funktionen**: `set_haupt_llm_context_limit()`, `set_automatik_llm_context_limit()`
- **Call-Sites updated**: `is_automatik_llm=True/False` statt Modell-Namen übergeben

#### Performance-Logging zentralisiert
- **ollama_wrapper.py** loggt jetzt automatisch alle `ollama.chat()` Aufrufe
- `_log_ollama_performance()` extrahiert Metriken und berechnet t/s
- Reduziert Code-Duplikation in agent_core.py

#### Journal Control Ausgabe deaktiviert
- **stdout logging auskommentiert** in `lib/logging_utils.py` (Zeile 58)
- Alle Logs gehen nur noch in Debug-Datei (kein doppelt gemoppelt)
- Spart Zeit und vermeidet journald Rate-Limiting

### 🧪 Getestet

#### Decision-Making Performance Vergleich
**Test:** "Wie wird das Wetter morgen in Niestetal, Hessen?"

| Modell | Entscheidungszeit | t/s Gen | Ergebnis | Gesamtzeit |
|--------|------------------|---------|----------|-----------|
| **qwen2.5:3b** | 2.8s | 113 t/s | `<search>yes</search>` ✅ | 59.5s |
| **phi3:mini** | 2.5s | 111 t/s | `<search>yes</search>` ✅ | 96.2s |

**Fazit:**
- phi3:mini 0.3s schneller bei Entscheidung (11% Zeitersparnis)
- qwen2.5:3b 36.7s schneller gesamt (bessere URL-Rating Performance)
- Beide Modelle wählen korrekt für Wetter-Fragen

#### Intelligent Scraping Test
**AccuWeather Blocking:**
- Trafilatura Download failed → Playwright SKIP ✅
- Zeit: Sofortiger Skip statt 43s Timeout
- Log-Message: "⚠️ trafilatura Download failed → SKIP Playwright (Site blockiert/down)"

**JS-Heavy Sites (wetter.com, proplanta.de):**
- Trafilatura: 27-577 Wörter
- Playwright Retry: 265-1526 Wörter ✅
- Zeit: ~4-6s pro Site (Trafilatura + Playwright)

### 📝 Dokumentation

- **CHANGELOG.md** erstellt (diese Datei)
- Debug-Logging System dokumentiert
- Performance-Metriken für Decision-Making dokumentiert

---

## Frühere Versionen

Siehe Git-History für detaillierte Änderungen vor diesem Datum.
