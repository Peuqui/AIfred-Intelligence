# Recherche-Pipeline & Vorverarbeitung

> **English version:** [research-pipeline.md](../../en/architecture/research-pipeline.md)

Wie eine Nachricht vorverarbeitet wird, wie der Recherche-Modus entscheidet, ob
ein Agent ins Web geht, und was die Recherche-Pipeline tut. Den vollständigen
Weg eines einzelnen LLM-Calls beschreibt [LLM-Call-Architektur](llm-call.md).

---

## Recherche-Modi

Der Recherche-Modus wird pro Session gesetzt (UI-Toggle, REST API oder per
Sprache). Jede Recherche läuft frisch — es gibt bewusst keinen Ergebnis-Cache:
Wie ähnlich sich zwei Fragen sind, sagt nichts darüber aus, ob eine frühere
Antwort noch gilt (Wetter, Preise, Nachrichten). Innerhalb einer Konversation
bleiben die Ergebnisse ohnehin in der History.

| Modus | Was passiert | Tools für den Agenten |
|------|--------------|---------------------|
| **Eigenes Wissen** (`none`) | Direkte Antwort vom Modell | ❌ keine |
| **Automatik** (`automatik`, Standard) | Der Agent entscheidet per Tool-Calls, ob und was gesucht wird | ✅ inkl. `web_search`, `web_fetch` |
| **Schnelle Websuche** (`quick`) | Recherche-Pipeline läuft vor der Antwort, Top-3-URLs | ✅ weiterhin verfügbar |
| **Tiefe Websuche** (`deep`) | Recherche-Pipeline läuft vor der Antwort, Top-7-URLs | ✅ weiterhin verfügbar |

---

## Vorverarbeitung (alle Modi)

```
Intent- + Adressaten-Erkennung
├─ Ein kombinierter Call an das Automatik-LLM
├─ Prompt: automatik/intent_detection
├─ Antwort: "INTENT|ADDRESSEE|LANGUAGE|MODE_SWITCH|IS_PURE_COMMAND"
│  └─ z.B. "FACTUAL||DE||FALSE" oder "MIXED|sokrates|DE||FALSE"
├─ Temperatur:
│  ├─ Auto-Modus: FACTUAL=0.2, MIXED=0.5, CREATIVE=1.0
│  └─ Manual-Modus: Intent ignoriert, manueller Wert verwendet
├─ Adressat: direkte Agenten-Ansprache (sokrates/aifred/salomo/…)
└─ Moduswechsel: gesprochene/getippte Konfigurationsänderungen ("starte ein Tribunal")
```

Ein direkt angesprochener Agent wird sofort aktiviert, unabhängig von
Recherche-Modus oder Temperatur-Einstellung. Die Nachricht selbst wird vom
Detektor nie umgeschrieben (siehe
[Moduswechsel](multi-agent.md#moduswechsel-per-sprache-oder-text)).

Vor jedem LLM-Call läuft die Prüfung auf History-Kompression (70 % des
kleinsten Kontextfensters, siehe
[Konfiguration → History-Kompression](../guides/configuration.md#history-kompression)).

---

## Die Pipeline

Eine Pipeline (`execute_research()` in `aifred/lib/research_tools.py`) bedient
sowohl den erzwungenen Pfad (quick/deep) als auch das `web_search`-Tool:

```
1. Query-Generierung (nur quick/deep)
   ├─ Automatik-LLM, Prompt: automatik/query_generation (+ Vision-JSON, falls vorhanden)
   ├─ 3 Queries: #1 immer Englisch, #2-3 in der Sprache der Frage
   └─ Tool-Pfad: übersprungen — der Agent übergibt 1-3 Queries in seinem web_search-Call

2. Multi-API-Websuche
   ├─ Round-Robin: Query 1 → SearXNG (selbst gehostet), 2 → Tavily, 3 → Brave
   │  (API-Keys optional), automatischer Fallback, wenn eine API ausfällt;
   │  eine einzelne Query geht parallel an alle APIs
   ├─ URL-Deduplizierung über alle APIs
   └─ Nicht scrapbare Domains gefiltert (data/non_scrapable_domains.txt:
      Video-Plattformen, Social Media)

3. URL-Ranking
   ├─ Automatik-LLM, Prompt: automatik/url_ranking (numerische Ausgabe)
   ├─ Sieht die Konversations-History (Folgefragen werden korrekt gerankt)
   └─ Top 3 (quick) oder Top 7 (deep und jeder web_search-Tool-Call)

4. Paralleles Scraping
   ├─ zuerst trafilatura; Playwright (Headless-Chromium), wenn es
   │  weniger als 800 Wörter liefert (PLAYWRIGHT_FALLBACK_THRESHOLD)
   ├─ PDFs (Leitlinien, Paper) per PyMuPDF extrahiert
   ├─ Kein Playwright-Retry bei fehlgeschlagenen Downloads (404, Timeout, Bot-Schutz)
   ├─ Fehlgeschlagene Quellen werden mit Fehlergrund aufgelistet
   └─ Hauptmodell lädt parallel vor (nicht bei vLLM — das bleibt resident)

5. Kontext-Aufbau
   ├─ build_context(): gescrapter Text, token-bewusst
   └─ Quellen-Collapsible für die UI (genutzte + fehlgeschlagene Quellen)
```

SearXNG ist das primäre Such-Backend und läuft als Container neben ChromaDB
(`docker/docker-compose.yml`); Tavily und Brave sind optionale Extras mit
API-Keys in `.env`.

**Wie die Ergebnisse beim Agenten ankommen:**
- **Quick/Deep:** Der Kontext wird als eigene Nachricht direkt vor der
  User-Frage eingefügt, eingezäunt als nicht vertrauenswürdige Daten (Schutz
  gegen Prompt Injection). Dass er nicht im System-Prompt steht, hält außerdem
  den Prompt-Präfix für den KV-Cache stabil.
- **Automatik:** Das `web_search`-Ergebnis (genauso eingezäunt) geht zurück in
  die Tool-Schleife; der Agent darf erneut suchen oder mit `web_fetch` eine
  einzelne Seite lesen.
- **Message Hub** (Discord, E-Mail, …): `hub_web_search()` — dieselben
  Bausteine ohne Browser-State.

---

## Entscheidungsablauf

```
USER-EINGABE
    │
    ▼
┌──────────────────────────────────┐
│ Intent- + Adressaten-Erkennung   │
│ (Automatik-LLM)                  │
└──────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────┐
│ Recherche-Modus?                 │
└──────────────────────────────────┘
    │
    ├── none ────────────► Agent antwortet (ohne Tools)
    │
    ├── automatik ───────► Agent antwortet mit Tools
    │                          │
    │                          └─ web_search-Call? ──► Recherche-Pipeline
    │                               (Queries vom Agenten, Top 7)
    │                               └─ Ergebnis zurück in die Tool-Schleife
    │
    └── quick / deep ────► Recherche-Pipeline
                           (Queries vom Automatik-LLM, Top 3 / 7)
                               │
                               ▼
                           Kontext vor der Frage
                               │
                               ▼
                           Agent antwortet (Tools weiterhin verfügbar)
```

---

## Automatik-LLM-Prompts

Das Automatik-LLM trifft diese Entscheidungen mit ausgeschaltetem Thinking
(schnell, strukturierte Ausgabe). Prompts in `prompts/{de,en}/automatik/`:

| Prompt | Sprache | Wann | Zweck |
|--------|----------|------|---------|
| `intent_detection.txt` | nur EN | Vorverarbeitung | Intent, Adressat, Sprache, Moduswechsel |
| `query_generation.txt` | DE + EN | Quick/Deep, Schritt 1 | 3 Suchanfragen erzeugen |
| `url_ranking.txt` | nur EN | Schritt 3 | URLs nach Relevanz ranken (numerische Indizes) |

*Nur EN*, wo die Ausgabe strukturiert oder numerisch ist und die Sprache sie
nicht beeinflusst; *DE + EN*, wo die Ausgabe von der Sprache des Users abhängt.

**Modellwahl:** Für die Automatik-Rolle eignet sich ein mittleres
Instruct-Modell am besten — kleine 4B-Modelle tun sich mit feinerer
Adressaten-Erkennung schwer („Was hält Alfred von Salomos Antwort?"). Die
Option **„(wie AIfred-LLM)"** nutzt das Hauptmodell ohne zusätzlichen VRAM
mit.

---

## Code-Landkarte

**Einstiegspunkte**
- `aifred/state/_chat_mixin.py` — `send_message()`, Dispatch nach Recherche-Modus
- `aifred/lib/multi_agent.py` — `run_generic_agent_direct_response()`: ein
  Antwortpfad für alle Agenten (erzwungene Recherche, Toolkit, Nachrichten)

**Web-Recherche**
- `aifred/lib/research_tools.py` — `execute_research()` (Browser) und
  `hub_web_search()` (Message Hub)
- `aifred/plugins/tools/research/` — `web_search`- / `web_fetch`-Tools
- `aifred/lib/conversation_handler.py` — `generate_web_search_queries()`
- `aifred/lib/research/query_processor.py` — Multi-API-Suche
- `aifred/lib/research/url_ranker.py` — LLM-basiertes URL-Ranking
- `aifred/lib/research/scraper_orchestrator.py` — paralleles Scraping
- `aifred/lib/tools/` — Such-APIs, Scraper, `build_context()`

**Dokument-RAG**
- `aifred/lib/document_store.py` — ChromaDB-Dokument-Collection:
  token-genaues Chunking, `delete + upsert` für sauberes Re-Indexieren, zwei
  Embedding-Functions (Index-/Query-Modus), Ordner-Filter +
  Chunk-Nachbar-Retrieval in `search()`
- `aifred/lib/file_manager.py` — Single Source of Truth für Dateisystem- +
  ChromaDB-Operationen (Dokument-UI und Workspace-Plugin)

**Unterstützende Module**
- `aifred/lib/embeddings.py` — bge-m3-Embedding-Function (zuerst das
  llama-swap-Embed-Profil, dann Ollama; Index-Modus → GPU, Query-Modus → CPU)
- `aifred/lib/agent_memory.py` — Gedächtnis-Collections pro Agent und die
  Gedächtnis-Tools (`read_memory`, `store_memory`, `update_memory`, `delete_memory`)
- `aifred/lib/tool_output_cap.py` — Token-Budget für Tool-Ergebnisse (75 %
  Input-Anteil, JSON-aware Truncation)
- `aifred/lib/intent_detector.py` — Intent, Adressat, Temperatur, Moduswechsel
