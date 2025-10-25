# Changelog

Alle wichtigen Änderungen an AIfred Intelligence werden in dieser Datei dokumentiert.

Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

---

## [2025-10-25] - Cache-Fixes, Debug-Logging & UI-Verbesserungen

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

#### Umfassendes Cache-Debug-Logging
- **Cache-Lookup Logging** in `lib/agent_core.py` (Zeilen 78-90):
  - Zeigt bei jedem Lookup: Gesuchte Session-ID, Cache-Einträge, Hit/Miss Status
  - Format: `🔍 DEBUG Cache-Lookup: Suche session_id = 71ccc280...`
  - Bei Hit: `✅ Cache-Hit! Eintrag gefunden mit 3 Quellen`
  - Bei Miss: `❌ Cache-Miss! session_id '71ccc280...' nicht in Cache`

- **Kompletter Cache-Dump** in `lib/agent_core.py` (Zeilen 228-242):
  - Nach jedem Cache-Save: Zeigt GESAMTEN Cache-Inhalt
  - Format: `📦 KOMPLETTER CACHE-INHALT:`
  - Für jeden Eintrag: Session-ID, User-Text, Timestamp, Mode, URLs aller Quellen
  - Beispiel-Output:
    ```
    Session: 71ccc280-861a-41c2-88e2-1b96657f2d33
      User-Text: Wie wird das Wetter morgen in Niestetal, Hessen?
      Timestamp: 1761348296.2415316
      Mode: deep
      Quellen (3):
        1. https://www.wetter.com/...
        2. https://www.wetterdienst.de/...
        3. https://wetter.tv/...
    ```

- **Cache-Metadata Logging** in `lib/agent_core.py` (Zeilen 1312-1315):
  - Zeigt exakten Inhalt des Cache-Metadata wenn verfügbar
  - Hilft bei Diagnose von Cache-basierten Entscheidungen

#### URL-Rating Parse-Fehler Logging
- **Erweiterte Fehlerausgabe** in `lib/agent_core.py` (Zeilen 742-744):
  - Zeigt problematische Zeile bei Parse-Fehlern
  - Format: `⚠️ Parse-Fehler für URL 3: list index out of range`
  - Zeigt erwartetes Format: `[NUM]. Score: [0-10] - Reasoning: [TEXT]`
  - Hilft bei Diagnose von LLM-Format-Fehlern (aktuell ~15% bei phi3:mini/qwen2.5:3b)

#### Haupt-LLM Message Logging
- **Komplettes Message-Array Logging** in `lib/agent_core.py` (Zeilen 1132-1145)
  - Zeigt alle Messages die an Haupt-LLM (z.B. qwen3:8b) übergeben werden
  - Inklusive System-Prompt Preview (erste 500 Zeichen)
  - Zeigt Gesamt-Token-Count und Context-Window-Größe

#### UI-Verbesserungen
- **Debug-Console Größe erhöht**: 21 → 25 Zeilen (vertikal)
- **Auto-Refresh Toggle** in `aifred_intelligence.py` (Zeilen 647-659, 743-754):
  - Checkbox zum Ein/Ausschalten der automatischen Console-Aktualisierung
  - Bei Deaktivierung: Timer stoppt komplett (kein Scroll-Jump mehr)
  - Erlaubt ruhiges Scrollen und Analysieren während laufender Requests
- **Sofortige Chat-Anzeige** in `aifred_intelligence.py` (Zeilen 1247-1251):
  - Chatbot-Widget wird sofort nach AI-Antwort aktualisiert
  - User sieht Antwort BEVOR Cache-Metadata und TTS laufen
  - Verbesserte Responsiveness der UI

#### Intent-Detection Verbesserungen
- **Verbessertes Logging** in `lib/agent_core.py` (Zeilen 492, 516, 575, 695):
  - Zeigt jetzt explizit welches Modell verwendet wird
  - Format: `🎯 Cache-Followup Intent-Detection mit phi3:mini: ...`
  - Format: `✅ Cache-Followup Intent (phi3:mini): FAKTISCH`
  - Format: `📨 MESSAGES an qwen3:1.7b (Query-Opt):` (vorher: hardcodiert "phi3:mini")
  - Format: `📨 MESSAGES an qwen3:1.7b (URL-Rating):` (vorher: hardcodiert "phi3:mini")
- **Verbesserter Prompt** in `prompts/followup_intent_detection.txt`:
  - "Empfehlungen geben" explizit als KREATIV definiert
  - Neue Beispiele: "Kannst du mir Empfehlungen geben?" → KREATIV
  - "Was könnte ich... unternehmen?" → KREATIV
  - **Test-Ergebnis**: qwen2.5:3b erkennt Empfehlungs-Fragen korrekt als KREATIV

#### URL-Rating Verbesserungen
- **Generische lokale Relevanz** in `prompts/url_rating.txt`:
  - Neue Kategorie "LOKALE RELEVANZ" (+0 bis +2 Punkte)
  - Erkennt automatisch Orts-Fragen (z.B. "Berlin", "München", "Kassel")
  - Bevorzugt URLs mit Ortsnamen (kassel.de, vhs-kassel.de, nordhessen.de) → +2
  - Bestraft allgemeine Blogs ohne Ortsbezug bei Orts-Fragen → -2
  - Funktioniert für JEDE Stadt, nicht hardcodiert
- **Verstärkte Anti-Forum/Social-Media Regel**:
  - Foren (seniorennet.be, random-forum.com) → -3 Punkte
  - Social Media (Pinterest, Instagram) → -2 Punkte
- **Konkrete Beispiele hinzugefügt**:
  - "Aktivitäten Kassel" + kassel.de → Score 10
  - "Aktivitäten Kassel" + vhs-kassel.de → Score 10
  - "Aktivitäten Kassel" + seniorennet.be/forum → Score 2

#### Automatisches URL-Fallback für fehlgeschlagenes Scraping
- **Intelligentes Fallback-System** in `lib/agent_core.py` (Zeilen 1020-1133):
  - **Deep-Modus**: Startet mit 7 URLs statt 5 (Quick-Modus: unverändert 3)
  - **Automatische Nachscraping**: Wenn URLs fehlschlagen (404, Timeout, Bot-Protection, PDF-Fehler):
    - System erkennt zu wenige erfolgreiche Quellen (< 5)
    - Scraped automatisch nächste URLs aus der Rating-Liste
    - Ziel: Immer 5 erfolgreiche Quellen (statt nur 3/5 wie vorher)
  - **Intelligente Stopbedingung**: Hört auf sobald Ziel erreicht (spart Zeit)
  - **Beispiel**: VHS-PDF + TripAdvisor scheitern → System scraped kassel.de + nordhessen.de nach
  - **Logging**: Zeigt Fallback-Fortschritt: `🔄 Fallback: 3/5 erfolgreich → Scrape 2 weitere URLs`
- **Performance-Optimierung**: Fallback-URLs werden ebenfalls parallel gescraped
- **User-Request**: "wenn er Schwierigkeiten hat und geblockt wird, dass er dann einfach die nächsten in der Liste scrapet"

### 🐛 Behoben

#### **KRITISCH: Cache-Lookup & Storage Bug**
- **Root Cause**: `if not _research_cache` behandelt leeres Dict `{}` als `None`
- **Problem**: Cache wurde NIEMALS gespeichert, da bei leerem Dict sofort `return` erfolgte
- **Fix in `lib/agent_core.py`**:
  - Zeile 74 (`get_cached_research`): `if not _research_cache` → `if _research_cache is None`
  - Zeile 210 (`save_cached_research`): `if not _research_cache` → `if _research_cache is None`
- **Impact**: Cache funktioniert jetzt korrekt - Request 1 speichert, Request 2 findet und nutzt Cache
- **Test-Ergebnis**: ✅ Cache-Hit nach 98s für Follow-up-Frage (statt erneuter 98s Web-Scraping)

#### Decision-LLM ignoriert lokale Aktivitäten-Fragen
- **Problem**: Fragen wie "Aktivitäten in Kassel?" wurden als `<search>no</search>` eingestuft
  - LLM dachte, es könnte aus Trainingsdaten antworten (generische Vorschläge)
  - Resultat: Keine Web-Recherche → Veraltete/ungenaue Informationen
- **Fix in `prompts/decision_making.txt`**:
  - Neue Regel: "Lokale Aktivitäten/Empfehlungen/Events → <search>yes</search>"
  - Neue Regel: "Restaurants/Geschäfte/Orte in Stadt → <search>yes</search>"
  - Neue Beispiele:
    - "Aktivitäten in Kassel?" → <search>yes</search>
    - "Was kann ich in München machen?" → <search>yes</search>
    - "VHS-Kurse in Kassel?" → <search>yes</search>
- **User-Feedback**: "Erst die explizite Nachfrage, warum hast du nicht im Internet recherchiert, hat es dann getriggert."
- **Resultat**: Lokale Fragen triggern jetzt korrekt Web-Recherche für aktuelle, lokale Infos

#### URLs in Inline-Zitaten entfernt
- **Prompt-Update** in `prompts/system_rag.txt` (Zeilen 44-48):
  - ✅ RICHTIG: "Quelle 1 berichtet, dass das Wetter morgen regnerisch wird..."
  - ❌ FALSCH: "Quelle 1 (https://www.wetter.com/...) berichtet..."
  - URLs NUR in Quellen-Liste am Ende, NIEMALS im Fließtext
- **Resultat**: Sauberere, lesbarere AI-Antworten ohne URL-Clutter

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

#### URL-Rating Format Enforcement
- **Verstärkter Prompt** in `prompts/url_rating.txt` (Zeilen 37-45):
  - ⚠️ ABSOLUT KRITISCH Sektion hinzugefügt
  - JEDE Zeile MUSS EXAKT beginnen: `[NUM]. Score: [0-10] - Reasoning: [TEXT]`
  - KEINE zusätzlichen Erklärungen, Kommentare oder Abweichungen
  - Ziel: Reduktion der Parse-Fehlerrate von ~15% (3 von 20 URLs)

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

#### Logging Helper Functions
- **Neue Helper-Funktionen** in `lib/logging_utils.py` (Zeilen 93-143):
  - `debug_print_prompt(prompt_type, prompt, model_name)`: Standardisiertes Prompt-Logging
  - `debug_print_messages(messages, model_name, context)`: Standardisiertes Messages-Logging
  - Reduziert Code-Duplikation in agent_core.py
  - Zeigt Message-Arrays, Token-Counts, Ollama-Parameter

#### Import Consolidation
- **aifred_intelligence.py**: Alle `agent_core` Imports in einen Block zusammengefasst (Zeilen 25-32)
- Entfernt: Unused `import sys` aus agent_core.py

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
