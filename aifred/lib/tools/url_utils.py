"""
URL Utilities - Normalization and Deduplication

Extracted from agent_tools.py for better modularity.
"""

from pathlib import Path
from urllib.parse import urlparse

from ..config import DEBUG_LOG_RAW_MESSAGES
from ..logging_utils import log_message

# ============================================================
# NON-SCRAPABLE DOMAIN FILTER
# ============================================================

# Path to non-scrapable domains file (relative to project root)
_NON_SCRAPABLE_DOMAINS_FILE = Path(__file__).parent.parent.parent.parent / 'data' / 'non_scrapable_domains.txt'

# Cached set of non-scrapable domains (loaded once at first access)
_non_scrapable_domains_cache: set[str] | None = None


def _load_non_scrapable_domains() -> set[str]:
    """
    Load non-scrapable domains from data/non_scrapable_domains.txt.

    These are domains that don't yield useful text when scraped
    (video platforms, JS-heavy social media, login walls).

    File format:
    - One domain per line
    - Lines starting with # are comments
    - Empty lines are ignored

    Returns:
        Set of domain strings
    """
    global _non_scrapable_domains_cache

    if _non_scrapable_domains_cache is not None:
        return _non_scrapable_domains_cache

    domains: set[str] = set()

    if not _NON_SCRAPABLE_DOMAINS_FILE.exists():
        log_message(f"⚠️ Non-scrapable domains file not found: {_NON_SCRAPABLE_DOMAINS_FILE}")
        _non_scrapable_domains_cache = domains
        return domains

    with open(_NON_SCRAPABLE_DOMAINS_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                domains.add(line.lower())

    _non_scrapable_domains_cache = domains
    return domains


def is_non_scrapable_url(url: str) -> bool:
    """
    Check if a URL belongs to a non-scrapable domain.

    Args:
        url: URL to check

    Returns:
        True if URL is non-scrapable, False otherwise
    """
    try:
        parsed = urlparse(url.lower().strip())
        domain = parsed.netloc.replace('www.', '')

        non_scrapable = _load_non_scrapable_domains()

        for ns_domain in non_scrapable:
            if domain == ns_domain or domain.endswith('.' + ns_domain):
                return True
        return False
    except ValueError:
        return False


def filter_non_scrapable_urls(
    urls: list[str],
    titles: list[str],
    snippets: list[str]
) -> tuple[list[str], list[str], list[str], int]:
    """
    Filter out non-scrapable URLs (video platforms, login walls, etc.)

    Args:
        urls: List of URLs
        titles: List of titles (same order as urls)
        snippets: List of snippets (same order as urls)

    Returns:
        Tuple of (filtered_urls, filtered_titles, filtered_snippets, blocked_count)
    """
    filtered_urls = []
    filtered_titles = []
    filtered_snippets = []
    blocked_count = 0

    for i, url in enumerate(urls):
        if is_non_scrapable_url(url):
            blocked_count += 1
            if DEBUG_LOG_RAW_MESSAGES:
                log_message(f"   🚫 Blocked: {url[:60]}...")
        else:
            filtered_urls.append(url)
            filtered_titles.append(titles[i] if i < len(titles) else "")
            filtered_snippets.append(snippets[i] if i < len(snippets) else "")

    return filtered_urls, filtered_titles, filtered_snippets, blocked_count


# ============================================================
# URL DEDUPLICATION UTILITIES
# ============================================================

def normalize_url(url: str) -> str:
    """
    Normalize URL for deduplication

    Handles:
    - www. vs non-www
    - http vs https
    - Trailing slashes
    - URL fragments (#)
    - Query parameters (?) [optional - currently NOT removed!]

    Args:
        url: URL to normalize

    Returns:
        Normalized URL
    """
    try:
        parsed = urlparse(url.lower().strip())

        # Normalize domain (remove www.)
        domain = parsed.netloc.replace('www.', '')

        # Normalize path (remove trailing slash)
        path = parsed.path.rstrip('/')

        # Keep query params (can be important, e.g., ?id=123)
        # Ignore fragments (# anchors)
        query = parsed.query

        # Build normalized URL
        normalized = f"{domain}{path}"
        if query:
            normalized += f"?{query}"

        return normalized

    except ValueError as e:
        log_message(f"⚠️ URL normalization failed for {url}: {e}")
        return url  # Fallback: Original URL


def deduplicate_urls(urls: list[str]) -> list[str]:
    """
    Remove duplicate URLs from list

    Uses normalization to also detect similar URLs:
    - https://www.example.com/path/
    - https://example.com/path
    → Both count as equal!

    Args:
        urls: List of URL strings

    Returns:
        Deduplicated list (preserves order)
    """
    seen = set()
    unique = []

    for url in urls:
        normalized = normalize_url(url)

        if normalized not in seen:
            seen.add(normalized)
            unique.append(url)  # Keep original URL, not normalized!

    duplicates_removed = len(urls) - len(unique)
    if duplicates_removed > 0:
        log_message(f"🔄 Deduplication: {len(urls)} URLs → {len(unique)} unique ({duplicates_removed} duplicates removed)")

    return unique


def deduplicate_urls_with_metadata(
    urls: list[str],
    titles: list[str],
    snippets: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """
    Remove duplicate URLs while preserving associated titles and snippets.

    Uses normalization to detect similar URLs.
    Keeps the first occurrence of each URL along with its metadata.

    Args:
        urls: List of URL strings
        titles: List of titles (same order as urls)
        snippets: List of snippets (same order as urls)

    Returns:
        Tuple of (unique_urls, unique_titles, unique_snippets)
    """
    seen = set()
    unique_urls = []
    unique_titles = []
    unique_snippets = []

    for i, url in enumerate(urls):
        normalized = normalize_url(url)

        if normalized not in seen:
            seen.add(normalized)
            unique_urls.append(url)
            # Use empty string if index out of range
            unique_titles.append(titles[i] if i < len(titles) else "")
            unique_snippets.append(snippets[i] if i < len(snippets) else "")

    duplicates_removed = len(urls) - len(unique_urls)
    if duplicates_removed > 0:
        log_message(f"🔄 Deduplication (with metadata): {len(urls)} URLs → {len(unique_urls)} unique ({duplicates_removed} removed)")

    # Filter non-scrapable domains (video platforms, login walls, etc.)
    filtered_urls, filtered_titles, filtered_snippets, blocked_count = filter_non_scrapable_urls(
        unique_urls, unique_titles, unique_snippets
    )

    if blocked_count > 0:
        log_message(f"🚫 Filtered {blocked_count} non-scrapable URLs (video/social platforms)")

    # Log full URL list when DEBUG_LOG_RAW_MESSAGES is enabled
    if DEBUG_LOG_RAW_MESSAGES and filtered_urls:
        log_message("📋 Full URL list after deduplication + filtering:")
        for i, url in enumerate(filtered_urls, 1):
            log_message(f"   {i:2d}. {url[:80]}{'...' if len(url) > 80 else ''}")

    return filtered_urls, filtered_titles, filtered_snippets
