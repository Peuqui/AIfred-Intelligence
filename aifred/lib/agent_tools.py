"""
Agent Tools für Voice Assistant - Multi-API Version

REFACTORED: This file now serves as a backward-compatibility layer.
All functionality has been moved to the tools/ submodule for better modularity.

Original functionality preserved through re-exports:
- tools/base.py: Base classes and exceptions
- tools/url_utils.py: URL normalization
- tools/search_tools.py: Brave, Tavily, SearXNG, MultiAPI
- tools/scraper_tool.py: Web scraping
- tools/context_builder.py: Context building
- tools/registry.py: Tool registry

Author: AI Assistant
Date: 2025-10-13
Refactored: 2025-11-01
"""

# Re-export everything from tools submodule
from .tools import (
    # Base
    BaseTool,
    RateLimitError,
    APIKeyMissingError,
    # URL Utils
    normalize_url,
    deduplicate_urls,
    # Search Tools
    BraveSearchTool,
    TavilySearchTool,
    SearXNGSearchTool,
    MultiAPISearchTool,
    # Scraper
    WebScraperTool,
    # Context
    build_context,
    # Registry
    ToolRegistry,
    get_tool_registry,
    search_web,
    scrape_webpage,
)

__all__ = [
    # Base
    'BaseTool',
    'RateLimitError',
    'APIKeyMissingError',
    # URL Utils
    'normalize_url',
    'deduplicate_urls',
    # Search Tools
    'BraveSearchTool',
    'TavilySearchTool',
    'SearXNGSearchTool',
    'MultiAPISearchTool',
    # Scraper
    'WebScraperTool',
    # Context
    'build_context',
    # Registry
    'ToolRegistry',
    'get_tool_registry',
    'search_web',
    'scrape_webpage',
]
