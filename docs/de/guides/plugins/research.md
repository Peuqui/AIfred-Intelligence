# Web Research Plugin

> **English version:** [research.md](../../../en/guides/plugins/research.md)

**Datei:** `aifred/plugins/tools/research/`

Multi-API-Websuche mit automatischem URL-Ranking und Inhalts-Scraping. Jede
Recherche läuft frisch — es gibt keinen Ergebnis-Cache. Jeder Agent hat die
Tools in jedem Kanal und entscheidet selbst, wann er recherchiert; die
Suchanfragen formuliert er im Tool-Call.

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

**Wachhund** (seit 22.11.2026, neu gefasst 26.11.2026; meldet nur, sperrt nichts): Als bekannt zählt eine Adresse,
die wörtlich in dem stand, was das Modell vor dem Erzeugen des Aufrufs als Eingabe bekam: alle Nachrichten mit Rolle
`system` (Systemprompt, Auftragstext, Notizen), `user` und `tool` (auch `read_file` und andere Werkzeuge) der laufenden
Anfrage, soweit sie vor der letzten Assistent-Nachricht stehen. Eigene Ausgaben des Modells (Text, Aufrufargumente) und
Werkzeugergebnisse derselben Runde, die erst nach dem Erzeugen des Aufrufs vorlagen, zählen nicht. Eine Adresse über den
Leseproxy (`https://r.jina.ai/<Adresse>`) ist bekannt, wenn die eingebettete Adresse es ist. Ist die Adresse unbekannt und
trägt sie einen Teil (Pfadstück, Parameterwert) ab `WEB_FETCH_WATCH_PART_CHARS` (64) Zeichen, könnten darin
mitgeschickte Daten stecken, etwa auf eine untergeschobene Anweisung hin. Dann steht eine Warnung
`🐕 web_fetch watchdog` im Debug-Log und ein gelber Hinweis im Chat der Sitzung; abgerufen wird trotzdem (`carried_part`
im Plugin). Die Eingabe liefert die Werkzeugschleife je Aufruf über `lib/model_input.py`; ein Unteragent sieht seine
eigene Eingabe.

## Pipeline (`web_search`)

`web_search` ruft die vollständige Research-Pipeline ab (`execute_research`,
7 gescrapte URLs):

1. **Suche** — die Queries werden reihum auf die konfigurierten Such-APIs
   verteilt (Query 1 → SearXNG, 2 → Tavily, 3 → Brave), mit automatischem
   Fallback, wenn eine API ausfällt; eine einzelne Query geht parallel an alle.
2. **URL-Ranking** — ein LLM sortiert die gesammelten URLs nach Relevanz (mit
   Konversationshistorie) und behält die Top 7.
3. **Scraping** — die gerankten URLs werden parallel gescrapt, mit einem
   Playwright-Fallback für JS-lastige Seiten.
4. **Context-Building** — die gescrapten Inhalte werden zu einem Kontextblock
   plus einer aufklappbaren Quellenliste für die UI zusammengesetzt.

Für den Message Hub (Discord, E-Mail) nutzt `web_search` über `hub_web_search`
dieselben Bausteine; dieser Pfad liest seine Konfiguration aus den Settings.

## Konfiguration

- Such-APIs via Umgebungsvariablen: `BRAVE_API_KEY`, `TAVILY_API_KEY`.
- SearXNG als selbst-gehostete Alternative ohne API-Key
  (Default `http://localhost:8888`).
- Anzahl gescrapter URLs: `RESEARCH_SCRAPE_URLS = 7` (`aifred/lib/config.py`).
