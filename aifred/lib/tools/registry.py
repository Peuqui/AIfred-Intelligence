"""
Tool Registry - Central registry and wrapper functions

Extracted from agent_tools.py for better modularity.
"""

import logging
from typing import Dict, List, Optional

from .base import BaseTool
from .search_tools import MultiAPISearchTool
from .scraper_tool import WebScraperTool

# Logging Setup
logger = logging.getLogger(__name__)

class ToolRegistry:
    """Verwaltet alle verfügbaren Tools"""

    def __init__(self):
        self.tools = {}
        self._register_default_tools()

    def _register_default_tools(self):
        """Registriert Standard-Tools"""
        # Multi-API Search als Primary
        self.register(MultiAPISearchTool())
        self.register(WebScraperTool())

    def register(self, tool: BaseTool):
        """Registriert ein Tool"""
        self.tools[tool.name] = tool
        logger.info(f"Tool registriert: {tool.name}")

    def get(self, name: str) -> Optional[BaseTool]:
        """Holt ein Tool nach Name"""
        return self.tools.get(name)

    def list_tools(self) -> List[str]:
        """Listet alle Tools"""
        return list(self.tools.keys())


# ============================================================
# GLOBALE TOOL REGISTRY
# ============================================================

_tool_registry = None

def get_tool_registry() -> ToolRegistry:
    """Holt globale Tool Registry (Singleton)"""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry


# ============================================================
# CONVENIENCE FUNCTIONS
# ============================================================

def search_web(query: str) -> Dict:
    """
    Convenience-Funktion für Web-Suche mit Multi-API Fallback
    """
    registry = get_tool_registry()
    search_tool = registry.get("Multi-API Search")
    return search_tool.execute(query)


def scrape_webpage(url: str) -> Dict:
    """
    Convenience-Funktion für Web-Scraping

    Scraped komplette Artikel ohne Längenlimit.
    Ollama's dynamisches num_ctx übernimmt die Context-Größen-Kontrolle!
    """
    registry = get_tool_registry()
    scraper_tool = registry.get("Web Scraper")
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
