# Web Research Plugin

**Datei:** `aifred/plugins/tools/research/`

Multi-API Websuche mit automatischem Scraping und semantischem Caching. Teilt dieselbe
Pipeline (`execute_research`) mit dem Automatik-Modus — der Unterschied liegt nur darin,
wer die Suche auslöst und ob URL-Ranking stattfindet.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `web_search` | Vollständige Research-Pipeline: Suche → Scraping → Cache. Nimmt 1–3 Queries. | READONLY |
| `web_fetch` | Einzelne URL abrufen und Inhalt extrahieren (kein Scraping-Ranking) | READONLY |

## Pipeline (`web_search`)

`web_search` ruft die vollständige Research-Pipeline ab — identisch zum Automatik-Modus,
mit einem Unterschied:

1. **Suche** — alle 3 Queries werden parallel an alle konfigurierten Such-APIs geschickt
   (Brave, Tavily, SearXNG)
2. ~~URL-Ranking~~ — **wird übersprungen** (`skip_url_ranking=True`)
3. **Scraping** — Top-URLs werden parallel gescrapt (3 oder 7 Sites je nach Modus)
4. **Context-Building** — Inhalte werden zusammengefasst und als Kontext aufbereitet
5. **Vector-Cache** — Ergebnisse werden in ChromaDB gespeichert (TTL je nach Volatilität)

Im **Automatik-Modus** generiert das Automatik-LLM die Queries selbst und das URL-Ranking
läuft mit. Beide Pfade schreiben in denselben Vector-Cache.

## Konfiguration

- Such-APIs via `.env`: `BRAVE_API_KEY`, `TAVILY_API_KEY`
- SearXNG als selbst-gehostete Alternative ohne API-Key
- ChromaDB-Collection `research_cache` für den Vector-Cache
