# Web Research Plugin

**Datei:** `aifred/plugins/tools/research/`

Multi-API-Websuche mit automatischem URL-Ranking, Inhalts-Scraping und
semantischem Caching. Das Tool `web_search` teilt dieselbe Pipeline
(`execute_research`) mit dem Automatik-Modus — der Unterschied liegt nur darin,
wer die Suchanfragen erzeugt.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `web_search` | Vollständige Research-Pipeline: Suche → Ranking → Scraping → Cache. Nimmt 1–3 Queries. | READONLY |
| `web_fetch` | Einzelne URL abrufen und Inhalt extrahieren (keine Such-/Ranking-Pipeline). | READONLY |

### `web_search`-Parameter

| Parameter | Typ | Pflicht | Beschreibung |
|-----------|-----|---------|--------------|
| `queries` | Array aus String (1–3) | ja | Suchanfragen; jede wird an eine andere Suchmaschine geschickt. Mehr als 3 werden abgeschnitten. |

### `web_fetch`-Parameter

| Parameter | Typ | Pflicht | Beschreibung |
|-----------|-----|---------|--------------|
| `url` | String | ja | Vollständige URL zum Abrufen (muss mit `http://` oder `https://` beginnen). Wird vor dem Abruf gegen einen SSRF-Schutz validiert. |

## Pipeline (`web_search`)

`web_search` ruft die vollständige Research-Pipeline im `deep`-Modus ab
(7 gescrapte URLs):

1. **Cache-Check** — die Query wird im ChromaDB-Vector-Cache nachgeschlagen; bei
   einem ausreichend frischen semantischen Treffer (Distanz unter dem
   volatilitätsabhängigen Schwellwert) wird der gecachte Kontext sofort
   zurückgegeben.
2. **Suche** — die Queries werden parallel an alle konfigurierten Such-APIs
   geschickt (Brave, Tavily, SearXNG).
3. **URL-Ranking** — ein LLM sortiert die gesammelten URLs nach Relevanz (mit
   Konversationshistorie) und behält die Top N (7 im Deep-, 3 im Quick-Modus).
4. **Scraping** — die gerankten URLs werden parallel gescrapt, mit einem
   Playwright-Fallback für JS-lastige Seiten.
5. **Context-Building** — die gescrapten Inhalte werden zu einem Kontextblock
   plus einer aufklappbaren Quellenliste für die UI zusammengesetzt.
6. **Vector-Cache-Write** — Ergebnisse werden in ChromaDB gespeichert, mit einer
   TTL je nach Volatilität der Query.

Im **Automatik-Modus** generiert das Automatik-LLM die Queries selbst; im
Tool-Call-Pfad liefert das Modell seine eigenen Queries (die Query-Generierung
entfällt). Beide Pfade führen das URL-Ranking aus und schreiben in denselben
Vector-Cache.

Für den Message Hub (Discord, E-Mail) nutzt `web_search` über `_hub_web_search`
dieselben Bausteine; dieser Pfad liest seine Konfiguration aus den Settings statt
aus dem Reflex-State.

## Konfiguration

- Such-APIs via Umgebungsvariablen: `BRAVE_API_KEY`, `TAVILY_API_KEY`.
- SearXNG als selbst-gehostete Alternative ohne API-Key
  (Default `http://localhost:8888`).
- ChromaDB-Collection `research_cache` für den Vector-Cache; die Cache-Treffer-
  Schwellwerte je Volatilitätsklasse sind über `CACHE_DISTANCE_PER_VOLATILITY` in
  `config.py` definiert.
- Anzahl gescrapter URLs: `RESEARCH_DEEP_URLS = 7`, `RESEARCH_QUICK_URLS = 3`.
