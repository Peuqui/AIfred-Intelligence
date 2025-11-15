"""
Query Processor - Query optimization and web search

Handles:
- Query optimization using Automatik-LLM
- Web search with fallback (Brave → Tavily → SearXNG)
- URL extraction from search results
"""

import time
from typing import Dict, List, AsyncIterator

from ..query_optimizer import optimize_search_query
from ..agent_tools import search_web
from ..logging_utils import log_message


async def process_query_and_search(
    user_text: str,
    history: List[tuple],
    automatik_model: str,
    automatik_llm_client
) -> AsyncIterator[Dict]:
    """
    Process query optimization and perform web search

    Args:
        user_text: User's question
        history: Chat history for context
        automatik_model: Automatik LLM model name
        automatik_llm_client: Automatik LLM client

    Yields:
        Dict: Debug messages and search results

    Returns (via last yield):
        Tuple[str, str, float, List[str], List[Dict]]:
        (optimized_query, query_reasoning, query_opt_time, related_urls, tool_results)
    """
    tool_results = []

    # 1. Query Optimization
    yield {"type": "debug", "message": "🔍 Query-Optimierung läuft..."}

    query_opt_start = time.time()

    # Query Automatik-Model Context Limit (silent - already shown in decision phase)
    automatik_limit = await automatik_llm_client.get_model_context_limit(automatik_model)

    optimized_query, query_reasoning = await optimize_search_query(
        user_text=user_text,
        automatik_model=automatik_model,
        history=history,
        llm_client=automatik_llm_client,
        automatik_llm_context_limit=automatik_limit
    )
    query_opt_time = time.time() - query_opt_start

    # Show query optimization completion AND optimized query
    yield {"type": "debug", "message": f"✅ Query-Optimierung fertig ({query_opt_time:.1f}s)"}
    yield {"type": "debug", "message": f"🔎 Optimierte Query: {optimized_query}"}

    # 2. Web-Suche
    log_message("=" * 60)
    log_message("🔍 Web-Suche mit optimierter Query")
    log_message("=" * 60)

    search_result = search_web(optimized_query)
    tool_results.append(search_result)

    # Console Log: Welche API wurde benutzt?
    api_source = search_result.get('source', 'Unbekannt')
    stats = search_result.get('stats', {})
    apis_used = search_result.get('apis_used', [])

    if stats and apis_used:
        # Multi-API Search mit Stats
        total_urls = stats.get('total_urls', 0)
        unique_urls = stats.get('unique_urls', 0)
        duplicates = stats.get('duplicates_removed', 0)

        yield {"type": "debug", "message": f"🌐 Web-Suche: {', '.join(apis_used)} ({len(apis_used)} APIs)"}
        if duplicates > 0:
            yield {"type": "debug", "message": f"🔄 Deduplizierung: {total_urls} URLs → {unique_urls} unique ({duplicates} Duplikate)"}
    else:
        yield {"type": "debug", "message": f"🌐 Web-Suche mit: {api_source}"}

    # Extract URLs
    related_urls = search_result.get('related_urls', [])

    # Return results as last yield (includes query_reasoning for debug accordion)
    yield {"type": "query_result", "data": (optimized_query, query_reasoning, query_opt_time, related_urls, tool_results)}
