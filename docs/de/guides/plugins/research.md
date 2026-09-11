# Web Research Plugin

**Datei:** `aifred/plugins/tools/research/`

Multi-API-Websuche mit automatischem URL-Ranking und Inhalts-Scraping. Jede
Recherche läuft frisch — es gibt keinen Ergebnis-Cache. Das Tool `web_search`
teilt dieselbe Pipeline (`execute_research`) mit der erzwungenen Recherche der
Modi Schnell/Ausführlich — der Unterschied liegt nur darin, wer die
Suchanfragen erzeugt.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `web_search` | Vollständige Research-Pipeline: Suche → Ranking → Scraping → Kontext. Nimmt 1–3 Queries. | READONLY |
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

1. **Suche** — die Queries werden reihum auf die konfigurierten Such-APIs
   verteilt (Query 1 → SearXNG, 2 → Tavily, 3 → Brave), mit automatischem
   Fallback, wenn eine API ausfällt.
2. **URL-Ranking** — ein LLM sortiert die gesammelten URLs nach Relevanz (mit
   Konversationshistorie) und behält die Top N (7 im Deep-, 3 im Quick-Modus).
3. **Scraping** — die gerankten URLs werden parallel gescrapt, mit einem
   Playwright-Fallback für JS-lastige Seiten.
4. **Context-Building** — die gescrapten Inhalte werden zu einem Kontextblock
   plus einer aufklappbaren Quellenliste für die UI zusammengesetzt.

In den **Modi Schnell/Ausführlich** erzeugt das Automatik-LLM die Queries, bevor
der Agent antwortet (erzwungene Recherche); im Tool-Call-Pfad
(**Automatik-Modus**) liefert das Modell seine eigenen Queries, die
Query-Generierung entfällt. Beide Pfade durchlaufen dasselbe URL-Ranking,
Scraping und Context-Building.

Für den Message Hub (Discord, E-Mail) nutzt `web_search` über `hub_web_search`
dieselben Bausteine; dieser Pfad liest seine Konfiguration aus den Settings statt
aus dem Reflex-State.

## Konfiguration

- Such-APIs via Umgebungsvariablen: `BRAVE_API_KEY`, `TAVILY_API_KEY`.
- SearXNG als selbst-gehostete Alternative ohne API-Key
  (Default `http://localhost:8888`).
- Anzahl gescrapter URLs: `RESEARCH_DEEP_URLS = 7`, `RESEARCH_QUICK_URLS = 3`.
