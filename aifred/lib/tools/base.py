"""
Base Tool Classes and Exceptions

Extracted from agent_tools.py for better modularity.
"""

import time
import logging
from typing import Dict, List

# Logging Setup
logger = logging.getLogger(__name__)


# ============================================================
# EXCEPTION CLASSES
# ============================================================

class RateLimitError(Exception):
    """Raised when API rate limit is reached"""


class APIKeyMissingError(Exception):
    """Raised when API key is missing"""


# ============================================================
# BASE TOOL CLASS
# ============================================================

class BaseTool:
    """Base class for all agent tools"""

    # Class-level attributes (must be set by subclasses)
    name: str
    description: str
    min_call_interval: float

    def __init__(self):
        # Subclasses must set name, description and min_call_interval
        self.last_call_time: float = 0.0

    def execute(self, query: str, **kwargs) -> Dict:
        """
        Execute tool and return result

        Args:
            query: Search query or URL
            **kwargs: Additional parameters

        Returns:
            Dict with tool result
        """
        raise NotImplementedError

    def _rate_limit_check(self):
        """Simple rate limiting"""
        now = time.time()
        time_since_last_call = now - self.last_call_time

        if time_since_last_call < self.min_call_interval:
            wait_time = self.min_call_interval - time_since_last_call
            logger.debug(f"{self.name}: Rate limit, waiting {wait_time:.1f}s")
            time.sleep(wait_time)

        self.last_call_time = time.time()

    def _extract_urls_from_results(self, results: List[Dict], url_key='url', title_key='title', content_key='description', max_results=10) -> tuple:
        """
        Extract URLs, titles and snippets from search results

        Args:
            results: List of search result dictionaries
            url_key: Key for URL in result dict
            title_key: Key for title in result dict
            content_key: Key for content/description in result dict
            max_results: Maximum number of results

        Returns:
            (related_urls, titles, snippets, content): Tuple with extracted data
        """
        related_urls = []
        titles = []
        snippets = []

        for result in results[:max_results]:
            url = result.get(url_key, '')
            title = result.get(title_key, '')
            snippet = result.get(content_key, '')

            if url:
                related_urls.append(url)
                titles.append(title)
                snippets.append(snippet)

        # Build content for context (first 5 results)
        content_parts = []
        for i, (title, snippet) in enumerate(zip(titles[:5], snippets[:5]), 1):
            content_parts.append(f"{i}. **{title}**: {snippet}")

        content = "\n\n".join(content_parts)

        return related_urls, titles, snippets, content
