"""
URL Utilities - Normalization and Deduplication

Extracted from agent_tools.py for better modularity.
"""

import logging
from typing import List
from urllib.parse import urlparse

# Logging Setup
logger = logging.getLogger(__name__)


# ============================================================
# URL DEDUPLICATION UTILITIES
# ============================================================

def normalize_url(url: str) -> str:
    """
    Normalisiert URL für Deduplizierung

    Behandelt:
    - www. vs non-www
    - http vs https
    - Trailing slashes
    - URL fragments (#)
    - Query parameters (?) [optional - aktuell NICHT entfernt!]

    Args:
        url: URL zum Normalisieren

    Returns:
        Normalisierte URL
    """
    try:
        parsed = urlparse(url.lower().strip())

        # Normalisiere Domain (entferne www.)
        domain = parsed.netloc.replace('www.', '')

        # Normalisiere Path (entferne trailing slash)
        path = parsed.path.rstrip('/')

        # Behalte Query-Params (können wichtig sein, z.B. ?id=123)
        # Ignoriere Fragments (# Anker)
        query = parsed.query

        # Baue normalisierte URL
        normalized = f"{domain}{path}"
        if query:
            normalized += f"?{query}"

        return normalized

    except Exception as e:
        logger.warning(f"⚠️ URL-Normalisierung fehlgeschlagen für {url}: {e}")
        return url  # Fallback: Original-URL


def deduplicate_urls(urls: List[str]) -> List[str]:
    """
    Entfernt doppelte URLs aus Liste

    Nutzt Normalisierung um auch ähnliche URLs zu erkennen:
    - https://www.example.com/path/
    - https://example.com/path
    → Beide zählen als gleich!

    Args:
        urls: Liste von URL-Strings

    Returns:
        Deduplizierte Liste (behält Reihenfolge)
    """
    seen = set()
    unique = []

    for url in urls:
        normalized = normalize_url(url)

        if normalized not in seen:
            seen.add(normalized)
            unique.append(url)  # Original-URL behalten, nicht normalisierte!

    duplicates_removed = len(urls) - len(unique)
    if duplicates_removed > 0:
        logger.info(f"🔄 Deduplizierung: {len(urls)} URLs → {len(unique)} unique ({duplicates_removed} Duplikate entfernt)")

    return unique
