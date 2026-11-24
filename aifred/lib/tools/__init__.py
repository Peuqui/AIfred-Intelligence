"""
Tools Module - Modular Tool Architecture

This module contains all agent tools split into logical components. The package imports nothing itself: a caller
imports the module it needs, so that e.g. ``url_utils`` (used by the core) does not pull in the scraper and the
search tools with their heavy dependencies.

- base.py: Base classes and exceptions
- url_utils.py: URL normalization and deduplication
- search_tools.py: Web search tool implementations
- scraper_tool.py: Web scraping tool
- context_builder.py: Context building for LLM
- registry.py: Tool registry and wrapper functions
"""
