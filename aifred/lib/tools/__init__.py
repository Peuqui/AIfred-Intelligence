"""
Tools Module - Modular Tool Architecture

This module contains all agent tools split into logical components:
- base.py: Base classes and exceptions
- url_utils.py: URL normalization and deduplication
- search_tools.py: Web search tool implementations
- scraper_tool.py: Web scraping tool
- context_builder.py: Context building for LLM
- registry.py: Tool registry and wrapper functions
"""

# Base classes and exceptions
from .base import APIKeyMissingError, BaseTool, RateLimitError

# Context builder
from .context_builder import build_context

# Registry and wrappers
from .registry import (
    ToolRegistry,
    get_tool_registry,
    scrape_webpage,
    search_web,
    search_web_multi,
)

# Scraper tool
from .scraper_tool import WebScraperTool

# Search tools
from .search_tools import (
    BraveSearchTool,
    MultiAPISearchTool,
    SearXNGSearchTool,
    TavilySearchTool,
)

# URL utilities
from .url_utils import deduplicate_urls, normalize_url

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
    'search_web_multi',
    'scrape_webpage',
]
