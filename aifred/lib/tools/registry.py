"""
Tool Registry - Central registry and wrapper functions

Extracted from agent_tools.py for better modularity.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseTool
from .search_tools import MultiAPISearchTool
from .scraper_tool import WebScraperTool

# Logging Setup
logger = logging.getLogger(__name__)

class ToolRegistry:
    """Manages all available tools"""

    def __init__(self):
        self.tools = {}
        self._register_default_tools()

    def _register_default_tools(self):
        """Register default tools"""
        # Multi-API Search as primary
        self.register(MultiAPISearchTool())
        self.register(WebScraperTool())

    def register(self, tool: BaseTool):
        """Register a tool"""
        self.tools[tool.name] = tool
        logger.info(f"Tool registered: {tool.name}")

    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name"""
        return self.tools.get(name)

# ============================================================
# GLOBAL TOOL REGISTRY
# ============================================================

_tool_registry = None

def get_tool_registry() -> ToolRegistry:
    """Get global tool registry (singleton)"""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry


# ============================================================
# CONVENIENCE FUNCTIONS
# ============================================================

def search_web(query: str) -> Dict[str, Any]:
    """
    Convenience function for web search with multi-API fallback
    (Legacy: Single query to ALL APIs in parallel)
    """
    registry = get_tool_registry()
    search_tool = registry.get("Multi-API Search")
    assert search_tool is not None, "Multi-API Search tool not registered"
    return search_tool.execute(query)


def search_web_multi(queries: List[str]) -> Dict[str, Any]:
    """
    Multi-query web search: Distributes queries across different APIs

    Each query is sent to a different API (1:1 mapping):
    - Query 1 → Tavily (Primary)
    - Query 2 → Brave
    - Query 3 → SearXNG

    FALLBACK: If fewer than 3 queries, use execute() instead to ensure
    ALL APIs are used in parallel for the single/few queries.

    Args:
        queries: List of optimized search queries

    Returns:
        Dict with aggregated URLs from all queries
    """
    registry = get_tool_registry()
    search_tool = registry.get("Multi-API Search")
    assert search_tool is not None, "Multi-API Search tool not registered"

    # Fallback: If only 1 query, use execute() for ALL APIs in parallel
    # This ensures all 3 APIs are used even when LLM returns just one query
    # For 2 queries: Round-robin works fine (Query1→Tavily, Query2→Brave)
    if len(queries) == 1:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("⚠️ Only 1 query - using ALL APIs in parallel")

        result = search_tool.execute(queries[0])  # All 3 APIs parallel
        return result

    assert isinstance(search_tool, MultiAPISearchTool)
    return search_tool.execute_multi_query(queries)


def scrape_webpage(url: str) -> Dict[str, Any]:
    """
    Convenience function for web scraping

    Scrapes complete articles without length limit.
    Ollama's dynamic num_ctx handles context size control!
    """
    registry = get_tool_registry()
    scraper_tool = registry.get("Web Scraper")
    assert scraper_tool is not None, "Web Scraper tool not registered"
    return scraper_tool.execute(url)


# ============================================================
# MAIN (Testing)
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    print("=" * 60)
    print("MULTI-API SEARCH TEST")
    print("=" * 60)

    # Test Multi-API Search
    print("\n[TEST] Multi-API Search: Trump News")
    result = search_web("latest Trump news")

    print(f"\n✅ Success: {result['success']}")
    print(f"📡 Source: {result.get('source', 'N/A')}")
    print(f"🔗 URLs: {len(result.get('related_urls', []))}")

    if result.get('related_urls'):
        print("\nErste 3 URLs:")
        for i, url in enumerate(result['related_urls'][:3], 1):
            print(f"  {i}. {url}")

    print("\n" + "=" * 60)
