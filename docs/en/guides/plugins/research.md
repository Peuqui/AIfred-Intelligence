# Web Research Plugin

**File:** `aifred/plugins/tools/research/`

Multi-API web search with automatic URL ranking, content scraping and semantic
caching. The `web_search` tool shares the same pipeline (`execute_research`) with
the automatic mode — the only difference is who generates the search queries.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `web_search` | Full research pipeline: search → ranking → scraping → cache. Takes 1–3 queries. | READONLY |
| `web_fetch` | Fetch a single URL and extract its content (no search/ranking pipeline). | READONLY |

### `web_search` parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `queries` | array of string (1–3) | yes | Search queries; each is sent to a different search engine. More than 3 are truncated. |

### `web_fetch` parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | Full URL to fetch (must start with `http://` or `https://`). Validated against an SSRF guard before fetching. |

## Pipeline (`web_search`)

`web_search` runs the full research pipeline in `deep` mode (7 scraped URLs):

1. **Cache check** — the query is looked up in the ChromaDB vector cache; on a
   fresh-enough semantic hit (distance below the volatility-dependent threshold)
   the cached context is returned immediately.
2. **Search** — the queries are sent in parallel to all configured search APIs
   (Brave, Tavily, SearXNG).
3. **URL ranking** — an LLM ranks the collected URLs by relevance (with
   conversation history) and keeps the top N (7 in deep mode, 3 in quick mode).
4. **Scraping** — the ranked URLs are scraped in parallel, with a Playwright
   fallback for JS-heavy pages.
5. **Context building** — scraped content is assembled into a context block plus
   a collapsible source list for the UI.
6. **Vector cache write** — results are stored in ChromaDB with a TTL based on
   the query's volatility.

In **automatic mode** the automatik-LLM generates the queries itself; in the
tool-call path the model supplies its own queries (query generation is skipped).
Both paths run URL ranking and write to the same vector cache.

For the Message Hub (Discord, e-mail), `web_search` uses the same building blocks
through `_hub_web_search`, which reads its config from settings instead of the
Reflex state.

## Configuration

- Search APIs via environment variables: `BRAVE_API_KEY`, `TAVILY_API_KEY`.
- SearXNG as a self-hosted alternative without an API key
  (default `http://localhost:8888`).
- ChromaDB collection `research_cache` for the vector cache; cache hit thresholds
  per volatility class are defined by `CACHE_DISTANCE_PER_VOLATILITY` in
  `config.py`.
- Scraped-URL counts: `RESEARCH_DEEP_URLS = 7`, `RESEARCH_QUICK_URLS = 3`.
