"""
Tool Registry - Central registry and wrapper functions

Extracted from agent_tools.py for better modularity.
"""

import logging
from typing import Any

from .base import BaseTool
from .scraper_tool import WebScraperTool
from .search_tools import MultiAPISearchTool

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

    def get(self, name: str) -> BaseTool | None:
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

def search_web_multi(queries: list[str]) -> dict[str, Any]:
    """Web search over every configured API: one query goes to all of them, several are distributed
    (MultiAPISearchTool.execute_multi_query); the hits come with titles and snippets."""
    search_tool = get_tool_registry().get("Multi-API Search")
    assert isinstance(search_tool, MultiAPISearchTool), "Multi-API Search tool not registered"
    return search_tool.execute_multi_query(queries)


def scrape_webpage(url: str) -> dict[str, Any]:
    """
    Convenience function for web scraping

    Scrapes complete articles without length limit.
    Ollama's dynamic num_ctx handles context size control!
    """
    registry = get_tool_registry()
    scraper_tool = registry.get("Web Scraper")
    assert scraper_tool is not None, "Web Scraper tool not registered"
    return scraper_tool.execute(url)
