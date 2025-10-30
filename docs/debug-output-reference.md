# Debug-Ausgabe Referenz

## Original Gradio-Version - Muster für gewünschte Informationen

Dies ist die Debug-Ausgabe aus der ursprünglichen Gradio-Version, die als Referenz dient für die Informationen, die auch in der Reflex-Version vorhanden sein sollen.

```
13:36:35 | 📨 User Request empfangen
13:36:35 | ⚡ Explizite Recherche erkannt → Web-Suche startet
13:36:35 | 🔧 Query-Optimierung startet
13:36:41 | ⚡ 31 t/s
13:36:43 | 🌐 Web-Suche: SearXNG, Tavily AI, Brave Search (3 APIs)
13:36:43 | 🔄 Deduplizierung: 29 URLs → 24 unique (5 Duplikate)
13:36:43 | ⚖️ KI bewertet URLs mit: qwen2.5:3b
13:36:50 | ⚡ 23 t/s
13:36:50 | 🌐 Web-Scraping startet (parallel)
13:36:52 | ✅ Web-Scraping fertig: 1 URLs erfolgreich
13:36:52 | 🧩 1 Quellen mit Inhalt gefunden
13:36:52 | 📝 Systemprompt wird erstellt
13:36:52 | 📊 Systemprompt: 370217 Zeichen
13:36:52 | 📊 Messages: 2, Gesamt: 370291 Zeichen (~92572 Tokens)
13:36:52 | ⚠️ WARNUNG: Kontext überschritten! (92572 Tokens > 40960 Tokens Limit)
13:36:52 | ⚠️ Ältere Messages werden abgeschnitten!
13:36:52 | 🪟 Context Window: 40960 Tokens (auto)
13:36:52 | 🌡️ Temperature: 0.2 (auto, faktisch)
13:36:52 | 🤖 Haupt-LLM startet: qwen3:8b (mit 1 Quellen)
13:39:12 | ⚡ 12 t/s
13:39:12 | ✅ Haupt-LLM fertig (140.5s, 3626 Zeichen, Agent-Total: 157.4s)
────────────────────────────────────────────────────────────────────────────────
13:39:18 | 📝 Erstelle Cache-Zusammenfassung...
13:39:34 | ⚡ 13 t/s
13:39:34 | ✅ Zusammenfassung erstellt
────────────────────────────────────────────────────────────────────────────────
```

## Wichtige Informationen aus dieser Ausgabe:

### 1. Prozess-Flow mit Timing
- User Request empfangen
- Explizite Recherche erkannt
- Query-Optimierung startet
- Tokens/s für Query-Opt (31 t/s)

### 2. Web-Suche Details
- Verwendete APIs (SearXNG, Tavily AI, Brave Search)
- Deduplizierung: Total URLs → Unique URLs (Duplikate)
- URL-Bewertung mit Model

### 3. Scraping-Fortschritt
- Web-Scraping startet
- Web-Scraping fertig: X URLs erfolgreich
- X Quellen mit Inhalt gefunden

### 4. Context-Management
- Systemprompt: X Zeichen
- Messages: X, Gesamt: X Zeichen (~X Tokens)
- Kontext-Warnungen wenn überschritten
- Context Window: X Tokens (auto/manual)
- Temperature: X (auto/manual, Intent)

### 5. LLM-Execution
- Haupt-LLM startet: Model (mit X Quellen)
- Tokens/s während Generation
- Haupt-LLM fertig (Zeit, Zeichen, Agent-Total)

### 6. Cache-Operationen
- Cache-Zusammenfassung erstellen
- Zusammenfassung erstellt mit Tokens/s

## Aktuelle Reflex-Version

Die aktuelle Reflex-Version hat bereits viele dieser Informationen im Debug-Log.
Einige könnten noch hinzugefügt/verbessert werden:

### ✅ Bereits vorhanden:
- User Request empfangen
- Explizite/Automatik-Entscheidung
- Web-Suche mit API-Namen
- URL-Deduplizierung
- URL-Bewertung
- Scraping-Status
- Context-Größe
- Context Window & Temperature
- LLM Start/Fertig mit Tokens/s
- Cache-Operationen

### ⚠️ Könnte verbessert werden:
- Tokens/s für Automatik-LLM Calls (Query-Opt, URL-Rating, Decision)
- Klarere Separator-Linien zwischen Phasen
- Konsistentere Emoji-Verwendung
- Timing-Informationen für einzelne Schritte

## Notizen:

- Die Separator-Linien (─) helfen, verschiedene Phasen visuell zu trennen
- Tokens/s nach jeder LLM-Operation zeigt Performance
- Die Warnung bei Context-Überschreitung ist wichtig für Debugging
- Agent-Total Zeit zeigt Gesamt-Dauer der Operation
