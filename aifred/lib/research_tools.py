"""Research pipeline (Search → Ranking → Scraping → Context) behind the web_search tool, whose facade lives in the
research plugin (aifred/plugins/tools/research): ``run_web_search``.

Every research runs fresh — deliberately no result cache: question
similarity says nothing about whether an answer still holds (the former
semantic cache served yesterday's weather forecast). Within a conversation
the results stay in the history anyway.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)


# SSOT for the tool result when the search layer could not reach ANY search
# engine (DNS/connection/timeout). Crucially distinct from an empty result:
# the agent must NOT treat this as "the topic doesn't exist" — it was offline.
WEB_SEARCH_NETWORK_ERROR = json.dumps({
    "error": "network_unavailable",
    "message": (
        "Web search could not reach any search engine (DNS or network "
        "failure). This is NOT an empty result — do not conclude the topic "
        "does not exist or draw any factual conclusion from it. Tell the user "
        "the network/search is temporarily unavailable and offer to retry."
    ),
})


def _is_network_outage(tool_results: list[dict[str, Any]]) -> bool:
    """True if the web-search step failed purely because no search server was
    reachable (``error_type == 'network'``), as opposed to returning zero hits.
    Lets callers report an outage instead of a misleading 'no results'."""
    return any(
        isinstance(r, dict) and r.get("error_type") == "network"
        for r in tool_results
    )


def research_note(source_count: int, queries: Sequence[str], lang: str) -> str:
    """The llm_history line of a web search: how many sources were read and
    for which queries. A count alone did not tell the model in a follow-up
    WHAT it had researched, and it accepted the user's "you did not research"
    (18.09.: Kuanda searched as Kondo). Text in prompts/<lang>/shared."""
    from .prompt_loader import load_prompt
    return load_prompt(
        "shared/research_marker",
        lang=lang,
        source_count=source_count,
        queries=", ".join(f'"{query}"' for query in queries),
    )


async def run_web_search(queries: list[str], llm_history: list[dict]) -> tuple[str, int]:
    """The web search of a turn:
    - Multi-API search (Brave, Tavily, SearXNG)
    - URL ranking (LLM-based, with the conversation for context)
    - Parallel scraping with Playwright fallback
    - Context building

    Reads backend and models from settings. Debug messages go through the Debug Bus (session_scope must be active).

    Returns (context, number of sources read); the caller builds the
    llm_history line from the count (research_note).
    """
    from .config import BACKEND_DEFAULT_MODELS, BACKEND_URLS, DEFAULT_SETTINGS
    from .debug_bus import debug
    from .llm_client import LLMClient
    from .research.query_processor import process_query_and_search
    from .research.scraper_orchestrator import orchestrate_scraping
    from .research.url_ranker import rank_urls_by_relevance
    from .settings import load_settings
    from .tools import build_context

    debug(f"🔍 Web search: {', '.join(queries)}")

    # Resolve backend config from settings
    from .config import get_effective_model_from_settings
    settings = load_settings() or {}
    backend_type = settings.get("backend_type", DEFAULT_SETTINGS["backend_type"])
    backend_url = BACKEND_URLS.get(backend_type, "")
    # Automatik für Query-Generation und URL-Ranking (Hilfsaufgaben), aifred nur für den
    # Modell-Preload zum Haupt-Inferenz-Call. Bei „Automatik = (wie Alfred-LLM)“ fällt
    # automatik intern auf aifred zurück — gleiches Verhalten, aber die Einstellung wird
    # respektiert, wenn der User sie ändert.
    automatik_model_id = get_effective_model_from_settings("automatik")
    aifred_model_id = get_effective_model_from_settings("aifred")
    if not aifred_model_id:
        backend_models = settings.get("backend_models", {}).get(backend_type, {})
        defaults = BACKEND_DEFAULT_MODELS.get(backend_type, {})
        aifred_model_id = backend_models.get("aifred", defaults.get("aifred", ""))

    llm_client = LLMClient(backend_type=backend_type, base_url=backend_url)

    try:
        # ── Phase 1: Multi-API search ─────────────────────────
        related_urls: list[str] = []
        titles: list[str] = []
        snippets: list[str] = []
        tool_results: list[dict[str, Any]] = []

        async for item in process_query_and_search(
            user_text=queries[0],
            llm_history=[],
            automatik_model=automatik_model_id,
            automatik_llm_client=llm_client,
            llm_options={},
            vision_json_context=None,
            pre_generated_queries=queries,
        ):
            if item["type"] == "query_result":
                _, _, _, related_urls, titles, snippets, tool_results = item["data"]
            elif item["type"] == "debug":
                debug(item["message"])

        if not related_urls:
            if _is_network_outage(tool_results):
                debug("⚠️ Search unreachable — network/DNS failure (not 'no results')")
                return WEB_SEARCH_NETWORK_ERROR, 0
            debug("⚠️ No URLs found")
            return json.dumps({"error": "No results found"}), 0

        # ── Phase 2: URL ranking ──────────────────────────────
        from .research.context_utils import get_model_native_context
        num_ctx = get_model_native_context(automatik_model_id, backend_type)

        from .config import RESEARCH_SCRAPE_URLS
        top_n = RESEARCH_SCRAPE_URLS

        if related_urls and titles and snippets:
            debug(f"🎯 Ranking {len(related_urls)} URLs by relevance...")
            ranked_urls, _, debug_summary = await rank_urls_by_relevance(
                user_question=queries[0],
                urls=related_urls,
                titles=titles,
                snippets=snippets,
                automatik_llm_client=llm_client,
                automatik_model=automatik_model_id,
                llm_history=llm_history,
                top_n=top_n,
                llm_options={},
                automatik_num_ctx=num_ctx if num_ctx > 0 else 32768,
            )
            if debug_summary:
                debug(f"📋 {debug_summary}")
            related_urls = ranked_urls
        else:
            debug(f"⏭️ URL ranking skipped, using top {min(top_n, len(related_urls))} URLs")
            related_urls = related_urls[:top_n]

        # ── Phase 3: Parallel scraping ────────────────────────
        # model_choice = aifred (Haupt-LLM wird vorgeladen, da nach dem
        # Scraping sein Inferenz-Call kommt). Kein LLM-Call zur
        # Verarbeitung der Scrape-Daten — die werden direkt durchgereicht.
        # Kontext wie die Hub-Inferenz (message_processor), sonst laedt
        # Ollama beim echten Aufruf neu.
        from .research.context_utils import get_stateless_num_ctx
        preload_num_ctx, _ = get_stateless_num_ctx(aifred_model_id, backend_type)
        async for item in orchestrate_scraping(
            related_urls=related_urls,
            llm_client=llm_client,
            model_choice=aifred_model_id,
            preload_num_ctx=preload_num_ctx,
        ):
            if item["type"] == "scraping_result":
                _, scraping_tool_results = item["data"]
                tool_results.extend(scraping_tool_results)
            elif item["type"] == "debug":
                debug(item["message"])

        if not tool_results:
            debug("⚠️ No sources available")
            return json.dumps({"error": "No results found"}), 0

        # ── Phase 4: Build context ────────────────────────────
        context = build_context(queries[0], tool_results)
        scraped_only = [r for r in tool_results if r.get("success") and r.get("content")]
        debug(f"✅ Research: {len(context)} chars, {len(scraped_only)} sources")
        return context, len(scraped_only)

    finally:
        await llm_client.close()
