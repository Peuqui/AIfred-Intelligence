# Web Research Plugin

> **Deutsche Version:** [research.md](../../../de/guides/plugins/research.md)

**File:** `aifred/plugins/tools/research/`

Multi-API web search with automatic URL ranking and content scraping. Every
research runs fresh — there is no result cache. Every agent has the tools in
every channel and decides itself when to research; it writes the search
queries in the tool call.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `web_search` | Full research pipeline: search → ranking → scraping → context. Takes 1–3 queries. | READONLY |
| `web_fetch` | Fetch a single URL and extract its content (no search/ranking pipeline). | READONLY |

### `web_search` parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `queries` | array of string (1–3) | yes | Search queries; each is sent to a different search engine. More than 3 are truncated. |

### `web_fetch` parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | Full URL to fetch (must start with `http://` or `https://`). Validated against an SSRF guard before fetching. |

**Watchdog** (since 2026-11-22, reports only, never blocks): when an agent fetches an address that neither the user
wrote nor a search result or fetched page of this turn showed, and one part of it (path piece, parameter value) is at
least `WEB_FETCH_WATCH_PART_CHARS` (64) characters long, it could carry data out, for instance on an injected
instruction. Then a `🐕 web_fetch watchdog` warning goes to the debug log and a yellow notice to the session's chat;
the page is fetched all the same (`carried_part` in the plugin).

## Pipeline (`web_search`)

`web_search` runs the full research pipeline (`execute_research`, 7 scraped URLs):

1. **Search** — the queries are distributed round-robin across the configured
   search APIs (query 1 → SearXNG, 2 → Tavily, 3 → Brave), with automatic
   fallback if an API fails; a single query goes to all of them in parallel.
2. **URL ranking** — an LLM ranks the collected URLs by relevance (with
   conversation history) and keeps the top 7.
3. **Scraping** — the ranked URLs are scraped in parallel, with a Playwright
   fallback for JS-heavy pages.
4. **Context building** — scraped content is assembled into a context block plus
   a collapsible source list for the UI.

For the Message Hub (Discord, e-mail), `web_search` uses the same building blocks
through `hub_web_search`, which reads its config from settings.

## Configuration

- Search APIs via environment variables: `BRAVE_API_KEY`, `TAVILY_API_KEY`.
- SearXNG as a self-hosted alternative without an API key
  (default `http://localhost:8888`).
- Scraped-URL count: `RESEARCH_SCRAPE_URLS = 7` (`aifred/lib/config.py`).
