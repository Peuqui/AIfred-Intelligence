# Recherche-Pipeline & Vorverarbeitung

> **English version:** [research-pipeline.md](../../en/architecture/research-pipeline.md)

Wie eine Nachricht vorverarbeitet wird, wie ein Agent ins Web geht und was die
Recherche-Pipeline tut. Den vollständigen
Weg eines einzelnen LLM-Calls beschreibt [LLM-Call-Architektur](llm-call.md).

---

## Wann recherchiert wird

Jeder Agent bekommt in jedem Kanal (Browser, Message Hub, Scheduler) seinen
vollen Werkzeugsatz und entscheidet selbst, ob er mit `web_search` /
`web_fetch` ins Web geht. Wer keine Recherche will, sagt das in der Frage. Bis
Oktober 2026 gab es dafür vier Recherche-Modi (Automatik, Wissen, Web Quick,
Web Deep); sie sind abgeschafft (Archiv: Tag `archive-research-modes-2026-10-09`).

Jede Recherche läuft frisch — es gibt bewusst keinen Ergebnis-Cache: Wie
ähnlich sich zwei Fragen sind, sagt nichts darüber aus, ob eine frühere
Antwort noch gilt (Wetter, Preise, Nachrichten). Innerhalb einer Konversation
bleiben die Ergebnisse ohnehin in der History.

---

## Vorverarbeitung

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

Ein direkt angesprochener Agent wird sofort aktiviert, unabhängig von der
Temperatur-Einstellung. Die Nachricht selbst wird vom
Detektor nie umgeschrieben (siehe
[Moduswechsel](multi-agent.md#moduswechsel-per-sprache-oder-text)).

Vor jedem LLM-Call läuft die Prüfung auf History-Kompression (70 % des
kleinsten Kontextfensters, siehe
[Konfiguration → History-Kompression](../guides/configuration.md#history-kompression)).

---

## Die Pipeline

Das `web_search`-Tool ruft die Pipeline auf (`execute_research()` in
`aifred/lib/research_tools.py`, im Message Hub `hub_web_search()`); die 1-3
Suchanfragen formuliert der Agent selbst in seinem Tool-Call:

```
1. Multi-API-Websuche
   ├─ Round-Robin: Query 1 → SearXNG (selbst gehostet), 2 → Tavily, 3 → Brave
   │  (API-Keys optional), automatischer Fallback, wenn eine API ausfällt;
   │  eine einzelne Query geht parallel an alle APIs
   ├─ URL-Deduplizierung über alle APIs
   └─ Nicht scrapbare Domains gefiltert (data/non_scrapable_domains.txt:
      Video-Plattformen, Social Media)

2. URL-Ranking
   ├─ Automatik-LLM, Prompt: automatik/url_ranking (numerische Ausgabe)
   ├─ Sieht die Konversations-History (Folgefragen werden korrekt gerankt)
   └─ Top 7 (RESEARCH_SCRAPE_URLS)

3. Paralleles Scraping
   ├─ zuerst trafilatura; Playwright (Headless-Chromium), wenn es
   │  weniger als 800 Wörter liefert (PLAYWRIGHT_FALLBACK_THRESHOLD)
   ├─ PDFs (Leitlinien, Paper) per PyMuPDF extrahiert
   ├─ Kein Playwright-Retry bei fehlgeschlagenen Downloads (404, Timeout, Bot-Schutz)
   ├─ Fehlgeschlagene Quellen werden mit Fehlergrund aufgelistet
   └─ Hauptmodell lädt parallel vor (nicht bei vLLM — das bleibt resident)

4. Kontext-Aufbau
   ├─ build_context(): gescrapter Text, token-bewusst
   └─ Quellen-Collapsible für die UI (genutzte + fehlgeschlagene Quellen)
```

SearXNG ist das primäre Such-Backend und läuft als Container neben ChromaDB
(`docker/docker-compose.yml`); Tavily und Brave sind optionale Extras mit
API-Keys in `.env`.

**Wie die Ergebnisse beim Agenten ankommen:**
- **Browser:** Das `web_search`-Ergebnis geht, eingezäunt als nicht
  vertrauenswürdige Daten (Schutz gegen Prompt Injection), zurück in die
  Tool-Schleife; der Agent darf erneut suchen oder mit `web_fetch` eine
  einzelne Seite lesen. Die Quellen-Box kommt über die Antwort.
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
Agent antwortet mit vollem Werkzeugsatz
    │
    └─ web_search-Call? ──► Recherche-Pipeline
         (Queries vom Agenten, Top 7)
         └─ Ergebnis zurück in die Tool-Schleife
```

---

## Automatik-LLM-Prompts

Das Automatik-LLM trifft diese Entscheidungen mit ausgeschaltetem Thinking
(schnell, strukturierte Ausgabe). Prompts in `prompts/{de,en}/automatik/`:

| Prompt | Sprache | Wann | Zweck |
|--------|----------|------|---------|
| `intent_detection.txt` | nur EN | Vorverarbeitung | Intent, Adressat, Sprache, Moduswechsel |
| `url_ranking.txt` | nur EN | Schritt 2 | URLs nach Relevanz ranken (numerische Indizes) |

*Nur EN*, weil die Ausgabe strukturiert oder numerisch ist und die Sprache sie
nicht beeinflusst.

**Modellwahl:** Für die Automatik-Rolle eignet sich ein mittleres
Instruct-Modell am besten — kleine 4B-Modelle tun sich mit feinerer
Adressaten-Erkennung schwer („Was hält Alfred von Salomos Antwort?"). Die
Option **„(wie AIfred-LLM)"** nutzt das Hauptmodell ohne zusätzlichen VRAM
mit.

---

## Code-Landkarte

**Einstiegspunkte**
- `aifred/state/_chat_mixin.py` — `send_message()`
- `aifred/lib/multi_agent.py` — `run_generic_agent_direct_response()`: ein
  Antwortpfad für alle Agenten (Toolkit, Nachrichten)

**Web-Recherche**
- `aifred/lib/research_tools.py` — `execute_research()` (Browser) und
  `hub_web_search()` (Message Hub)
- `aifred/plugins/tools/research/` — `web_search`- / `web_fetch`-Tools
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
