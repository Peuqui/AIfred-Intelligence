"""
Query Processor - Query optimization and web search

Handles:
- Direct URL detection and scraping
- Query optimization using Automatik-LLM
- Web search with fallback (Brave → Tavily → SearXNG)
- URL extraction from search results
"""

import re
from typing import Dict, List, AsyncIterator, Optional
from urllib.parse import urlparse

from ..tools import search_web_multi
from ..logging_utils import log_message
from ..tools.url_utils import deduplicate_urls, deduplicate_urls_with_metadata


# ============================================================
# URL DETECTION & INTENT ANALYSIS
# ============================================================

# Compiled Regex for URL detection (performance optimization)
URL_PATTERN = re.compile(
    r'https?://[^\s]+|'  # https://... or http://...
    r'www\.[^\s]+|'  # www....
    r'\b[a-zA-Z][-a-zA-Z0-9]*\.(?:com|de|org|net|io|co|gov|ai|tech|dev|app|info|me)(?:/[^\s]*)?',  # domain.tld/path (starts with letter, not digit)
    re.IGNORECASE
)

# Search intent keywords (multilingual)
SEARCH_INTENT_KEYWORDS = [
    # German
    'finde', 'find heraus', 'suche', 'such', 'ähnliche', 'vergleiche', 'vergleich',
    'alternative', 'alternativen', 'gibt es',
    # English
    'find', 'search', 'similar', 'compare', 'comparison', 'alternative', 'alternatives',
    'are there', 'is there'
]


def detect_urls_in_text(text: str, max_urls: int = 7) -> List[str]:
    """
    Detects URLs in user text using regex

    Args:
        text: User input text
        max_urls: Maximum number of URLs to return (default: 7 for Deep-Modus)

    Returns:
        List of validated and normalized URLs (up to max_urls)
    """
    matches = URL_PATTERN.findall(text)
    validated_urls = []

    for match in matches:
        # Extract URL string from tuple (regex with groups returns tuples)
        url = match[0] if isinstance(match, tuple) else match

        # Normalize: Add https:// if missing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        # Validate with urlparse
        try:
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:
                validated_urls.append(url)
        except ValueError:
            continue  # Skip invalid URLs

        # Limit to max_urls
        if len(validated_urls) >= max_urls:
            break

    # Deduplicate URLs (using existing utility)
    return deduplicate_urls(validated_urls)


def detect_search_intent(text: str) -> bool:
    """
    Detects if user wants additional web search beyond URL scraping

    Keywords: finde, suche, ähnliche, vergleiche, alternative, etc.

    Args:
        text: User input text

    Returns:
        True if search intent detected, False otherwise
    """
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in SEARCH_INTENT_KEYWORDS)


def remove_urls_from_text(text: str, urls: List[str]) -> str:
    """
    Removes detected URLs from user text for query optimization

    Args:
        text: Original user text
        urls: List of URLs to remove

    Returns:
        Text with URLs removed and whitespace normalized
    """
    result = text

    for url in urls:
        # Remove original URL (may not have https://)
        result = result.replace(url, '')

        # Also remove normalized version (with https://)
        if url.startswith('https://'):
            result = result.replace(url.replace('https://', ''), '')

    # Normalize whitespace
    result = ' '.join(result.split())

    return result.strip()


async def process_query_and_search(
    user_text: str,
    llm_history: List[Dict[str, str]],
    automatik_model: str,
    automatik_llm_client,
    llm_options: Dict | None = None,
    vision_json_context: Optional[Dict] = None,
    pre_generated_queries: Optional[List[str]] = None
) -> AsyncIterator[Dict]:
    """
    Process query optimization and perform web search

    Args:
        user_text: User's question
        llm_history: LLM history for context (not chat_history!)
        automatik_model: Automatik LLM model name
        automatik_llm_client: Automatik LLM client
        llm_options: Optional Dict with enable_thinking toggle
        vision_json_context: Optional Vision JSON from image extraction (for query context)
        pre_generated_queries: Pre-generated queries from research_decision (skips LLM call)

    Yields:
        Dict: Debug messages and search results

    Returns (via last yield):
        Tuple[str, str, float, List[str], List[Dict]]:
        (optimized_query, query_reasoning, query_opt_time, related_urls, tool_results)
    """
    tool_results: List[Dict] = []

    # ============================================================
    # STEP 0: URL DETECTION & INTENT ANALYSIS
    # ============================================================
    detected_urls = detect_urls_in_text(user_text, max_urls=7)
    has_search_intent = detect_search_intent(user_text)

    # Determine processing mode
    if detected_urls and not has_search_intent:
        # MODE A: Direct-URL mode (no web search)
        mode = "direct"
    elif detected_urls and has_search_intent:
        # MODE B: Hybrid mode (URL + web search)
        mode = "hybrid"
    else:
        # MODE C: Normal mode (web search only)
        mode = "normal"

    # ============================================================
    # MODE A: DIRECT-URL MODE (Skip Query-Opt & Search)
    # ============================================================
    if mode == "direct":
        log_message("=" * 60)
        log_message("⚡ Direct-Scraping mode activated")
        log_message("=" * 60)

        yield {"type": "debug", "message": f"🔗 {len(detected_urls)} URL(s) detected → Direct-Scraping"}
        for i, url in enumerate(detected_urls, 1):
            yield {"type": "debug", "message": f"   {i}. {url}"}

        yield {"type": "debug", "message": "⚡ Direct-Scraping mode (Skip Query-Opt & Search)"}

        # Set results directly (no titles/snippets for direct URLs)
        optimized_query = detected_urls[0] if len(detected_urls) == 1 else f"{len(detected_urls)} URLs"
        query_reasoning = "Direct URL detected - skipped optimization and search"
        query_opt_time = 0.0
        related_urls = detected_urls

        # Return results (empty titles/snippets - direct URLs skip ranking)
        yield {"type": "query_result", "data": (optimized_query, query_reasoning, query_opt_time, related_urls, [], [], tool_results)}
        return

    # ============================================================
    # MODE B: HYBRID MODE (URL + Web-Search)
    # ============================================================
    elif mode == "hybrid":
        log_message("=" * 60)
        log_message("🔀 Hybrid mode activated (URL + Web-Search)")
        log_message("=" * 60)

        yield {"type": "debug", "message": f"🔗 {len(detected_urls)} URL(s) detected"}
        for i, url in enumerate(detected_urls, 1):
            yield {"type": "debug", "message": f"   {i}. {url}"}
        yield {"type": "debug", "message": "🔍 Search intent detected → Hybrid mode"}

        # Remove URLs from text for query optimization
        text_without_urls = remove_urls_from_text(user_text, detected_urls)

        if not text_without_urls:
            # Edge case: Only URLs in text, no search keywords
            log_message("⚠️ No text after URL removal, fallback to direct mode")
            yield {"type": "debug", "message": "⚠️ Only URLs in text, fallback to Direct-Scraping"}

            optimized_query = detected_urls[0] if len(detected_urls) == 1 else f"{len(detected_urls)} URLs"
            query_reasoning = "Only URLs in text, no additional search terms"
            query_opt_time = 0.0
            related_urls = detected_urls

            yield {"type": "query_result", "data": (optimized_query, query_reasoning, query_opt_time, related_urls, [], [], tool_results)}
            return

        # Query Optimization for text without URLs
        # pre_generated_queries MUST be provided by caller (via research_decision)
        if not pre_generated_queries:
            error_msg = "pre_generated_queries is required - research_decision must be called first"
            log_message(f"❌ {error_msg}")
            yield {"type": "error", "message": error_msg}
            raise ValueError(error_msg)

        # Use pre-generated queries from research_decision
        log_message("⚡ Using pre-generated queries from research_decision")
        optimized_queries = pre_generated_queries
        query_reasoning = "Pre-generated by research_decision"
        query_opt_time = 0.0

        # Show API assignments for each query — read the actual order
        # from the live MultiAPISearchTool instance so this debug log can
        # never drift out of sync with the real round-robin in
        # search_tools.py (which used to be the case when we had a
        # hardcoded ["Tavily", "Brave", "SearXNG"] list here).
        from ..tools.registry import get_tool_registry
        from ..tools.search_tools import MultiAPISearchTool
        _search_tool = get_tool_registry().get("Multi-API Search")
        if isinstance(_search_tool, MultiAPISearchTool) and _search_tool.apis:
            api_names = [a.name for a in _search_tool.apis]
        else:
            api_names = ["SearXNG", "Tavily", "Brave"]  # safe default
        for i, q in enumerate(optimized_queries):
            api_name = api_names[i % len(api_names)]
            yield {"type": "debug", "message": f"   {i+1}. [{api_name}] {q}"}

        # Web search with multi-query (distributed across different APIs)
        log_message("=" * 60)
        log_message("🔍 Multi-Query Web Search (Hybrid)")
        log_message("=" * 60)

        search_result = search_web_multi(optimized_queries)
        tool_results.append(search_result)

        # Log Multi-Query Stats (File-Log + UI)
        api_source = search_result.get('source', 'Unknown')
        stats = search_result.get('stats', {})
        apis_used = search_result.get('apis_used', [])
        query_results = search_result.get('query_results', [])

        if stats and apis_used:
            total_urls = stats.get('total_urls', 0)
            unique_urls = stats.get('unique_urls', 0)
            duplicates = stats.get('duplicates_removed', 0)

            # File-Log: Detailed Query→API mapping
            log_message("📋 Query → API mapping:")
            for qr in query_results:
                status = "✅" if qr.get('success') else "❌"
                log_message(f"   {status} {qr.get('api')}: \"{qr.get('query', '')[:50]}...\" → {qr.get('urls_found', 0)} URLs")
            log_message(f"🔄 Deduplication: {total_urls} URLs → {unique_urls} unique ({duplicates} duplicates)")

            # UI Debug console
            yield {"type": "debug", "message": f"🌐 Multi-Query Search: {', '.join(apis_used)} ({len(apis_used)} APIs)"}
            yield {"type": "debug", "message": f"🔄 Deduplication: {total_urls} URLs → {unique_urls} unique ({duplicates} duplicates)"}
        else:
            log_message(f"🌐 Web search with: {api_source}")
            yield {"type": "debug", "message": f"🌐 Web search with: {api_source}"}

        # Combine detected URLs with search results
        search_urls = search_result.get('related_urls', [])
        search_titles = search_result.get('titles', [])
        search_snippets = search_result.get('snippets', [])

        # Detected URLs have no titles/snippets from search
        all_urls = detected_urls + search_urls
        all_titles = [""] * len(detected_urls) + search_titles
        all_snippets = [""] * len(detected_urls) + search_snippets

        # Deduplicate combined list with metadata
        related_urls, titles, snippets = deduplicate_urls_with_metadata(
            all_urls, all_titles, all_snippets
        )

        yield {"type": "debug", "message": f"🔀 Total: {len(related_urls)} URLs (direct + search)"}

        # Return results with titles/snippets for ranking
        yield {"type": "query_result", "data": (optimized_queries[0], query_reasoning, query_opt_time, related_urls, titles, snippets, tool_results)}
        return

    # ============================================================
    # MODE C: NORMAL MODE (web search only, as before)
    # ============================================================
    else:  # mode == "normal"
        # 1. Query Optimization
        # pre_generated_queries MUST be provided by caller (via research_decision)
        if not pre_generated_queries:
            error_msg = "pre_generated_queries is required - research_decision must be called first"
            log_message(f"❌ {error_msg}")
            yield {"type": "error", "message": error_msg}
            raise ValueError(error_msg)

        # Use pre-generated queries from research_decision
        log_message("⚡ Using pre-generated queries from research_decision")
        optimized_queries = pre_generated_queries
        query_reasoning = "Pre-generated by research_decision"
        query_opt_time = 0.0

        # Show API assignments for each query — read the actual order
        # from the live MultiAPISearchTool instance so this debug log can
        # never drift out of sync with the real round-robin in
        # search_tools.py (which used to be the case when we had a
        # hardcoded ["Tavily", "Brave", "SearXNG"] list here).
        from ..tools.registry import get_tool_registry
        from ..tools.search_tools import MultiAPISearchTool
        _search_tool = get_tool_registry().get("Multi-API Search")
        if isinstance(_search_tool, MultiAPISearchTool) and _search_tool.apis:
            api_names = [a.name for a in _search_tool.apis]
        else:
            api_names = ["SearXNG", "Tavily", "Brave"]  # safe default
        for i, q in enumerate(optimized_queries):
            api_name = api_names[i % len(api_names)]
            yield {"type": "debug", "message": f"   {i+1}. [{api_name}] {q}"}

        # 2. Web search with multi-query
        log_message("=" * 60)
        log_message("🔍 Multi-Query Web Search")
        log_message("=" * 60)

        search_result = search_web_multi(optimized_queries)
        tool_results.append(search_result)

    # Log Multi-Query Stats (File-Log + UI)
    api_source = search_result.get('source', 'Unknown')
    stats = search_result.get('stats', {})
    apis_used = search_result.get('apis_used', [])
    query_results = search_result.get('query_results', [])

    if stats and apis_used:
        # Multi-API Search with Stats
        total_urls = stats.get('total_urls', 0)
        unique_urls = stats.get('unique_urls', 0)
        duplicates = stats.get('duplicates_removed', 0)

        # File-Log: Detailed Query→API mapping
        log_message("📋 Query → API mapping:")
        for qr in query_results:
            status = "✅" if qr.get('success') else "❌"
            log_message(f"   {status} {qr.get('api')}: \"{qr.get('query', '')[:50]}...\" → {qr.get('urls_found', 0)} URLs")
        log_message(f"🔄 Deduplication: {total_urls} URLs → {unique_urls} unique ({duplicates} duplicates)")

        # UI Debug console
        yield {"type": "debug", "message": f"🌐 Multi-Query Search: {', '.join(apis_used)} ({len(apis_used)} APIs)"}
        yield {"type": "debug", "message": f"🔄 Deduplication: {total_urls} URLs → {unique_urls} unique ({duplicates} duplicates)"}
    else:
        log_message(f"🌐 Web search with: {api_source}")
        yield {"type": "debug", "message": f"🌐 Web search with: {api_source}"}

    # Extract URLs and metadata
    related_urls = search_result.get('related_urls', [])
    titles = search_result.get('titles', [])
    snippets = search_result.get('snippets', [])

    # Return results as last yield (first query for display, all queries in search_result)
    # Extended with titles/snippets for LLM-based URL ranking
    yield {"type": "query_result", "data": (optimized_queries[0], query_reasoning, query_opt_time, related_urls, titles, snippets, tool_results)}
