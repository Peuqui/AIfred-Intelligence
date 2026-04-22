# Web Research Plugin

**File:** `aifred/plugins/tools/research/`

Multi-API web search with automatic scraping and semantic caching. Shares the same
pipeline (`execute_research`) with the automatic mode — the only difference is who
triggers the search and whether URL ranking is performed.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `web_search` | Full research pipeline: search → scraping → cache. Takes 1–3 queries. | READONLY |
| `web_fetch` | Fetch a single URL and extract its content (no scraping pipeline) | READONLY |

## Pipeline (`web_search`)

`web_search` runs the full research pipeline — identical to the automatic mode,
with one difference:

1. **Search** — all 3 queries are sent in parallel to all configured search APIs
   (Brave, Tavily, SearXNG)
2. ~~URL ranking~~ — **skipped** (`skip_url_ranking=True`)
3. **Scraping** — top URLs are scraped in parallel (3 or 7 sites depending on mode)
4. **Context building** — content is summarised and prepared as context
5. **Vector cache** — results are stored in ChromaDB (TTL based on volatility)

In **automatic mode**, the automatik-LLM generates the queries itself and URL ranking runs.
Both paths write to the same vector cache.

## Configuration

- Search APIs via `.env`: `BRAVE_API_KEY`, `TAVILY_API_KEY`
- SearXNG as self-hosted alternative without API key
- ChromaDB collection `research_cache` for the vector cache
