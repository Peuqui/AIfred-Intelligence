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
    """Wird geworfen wenn API Rate Limit erreicht ist"""
    pass


class APIKeyMissingError(Exception):
    """Wird geworfen wenn API Key fehlt"""
    pass


# ============================================================
# BASE TOOL CLASS
# ============================================================

class BaseTool:
    """Basis-Klasse für alle Agent-Tools"""

    # Class-level attributes (müssen von Subclasses gesetzt werden)
    name: str
    description: str
    min_call_interval: float

    def __init__(self):
        # Subclasses müssen name, description und min_call_interval setzen
        self.last_call_time = 0

    def execute(self, query: str, **kwargs) -> Dict:
        """
        Führt Tool aus und gibt Ergebnis zurück

        Args:
            query: Such-Query oder URL
            **kwargs: Zusätzliche Parameter

        Returns:
            Dict mit Tool-Ergebnis
        """
        raise NotImplementedError

    def _rate_limit_check(self):
        """Einfaches Rate-Limiting"""
        now = time.time()
        time_since_last_call = now - self.last_call_time

        if time_since_last_call < self.min_call_interval:
            wait_time = self.min_call_interval - time_since_last_call
            logger.debug(f"{self.name}: Rate limit, warte {wait_time:.1f}s")
            time.sleep(wait_time)

        self.last_call_time = time.time()

    def _extract_urls_from_results(self, results: List[Dict], url_key='url', title_key='title', content_key='description', max_results=10) -> tuple:
        """
        Extrahiert URLs, Titel und Snippets aus Suchergebnissen

        Args:
            results: Liste von Suchergebnis-Dictionaries
            url_key: Key für URL in Result-Dict
            title_key: Key für Titel in Result-Dict
            content_key: Key für Content/Description in Result-Dict
            max_results: Maximale Anzahl an Ergebnissen

        Returns:
            (related_urls, titles, snippets, content): Tuple mit extrahierten Daten
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

        # Baue Content für Context (erste 5 Ergebnisse)
        content_parts = []
        for i, (title, snippet) in enumerate(zip(titles[:5], snippets[:5]), 1):
            content_parts.append(f"{i}. **{title}**: {snippet}")

        content = "\n\n".join(content_parts)

        return related_urls, titles, snippets, content
