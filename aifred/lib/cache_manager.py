"""
Cache Manager - Research Cache Management with Thread Safety

Handles caching of research results including:
- Thread-safe cache operations
- Session-based cache lookups
"""

import threading
from collections import OrderedDict
from typing import Dict, Optional
from .logging_utils import log_message


# ============================================================
# GLOBAL CACHE STATE (Dependency Injection)
# ============================================================
# IMPORTANT: Initialize cache directly at module import!
# This prevents "Cache not initialized" errors during hot-reloads.
#
# OrderedDict instead of Dict for LRU Cache (oldest entries are deleted first)
_research_cache: OrderedDict = OrderedDict()
_research_cache_lock: threading.Lock = threading.Lock()


def get_cached_research(session_id: Optional[str]) -> Optional[Dict]:
    """
    Gets cached research for a session (thread-safe)

    Args:
        session_id: Session ID to lookup

    Returns:
        Cached research data or None if not found
    """
    # IMPORTANT: An empty dictionary {} is a valid (but empty) cache state!
    if not session_id:
        log_message("🔍 DEBUG Cache-Lookup: No session_id")
        return None

    with _research_cache_lock:
        # DEBUG: Show cache contents (keys) for diagnosis
        cache_keys = list(_research_cache.keys())
        log_message(f"🔍 DEBUG Cache-Lookup: Searching session_id = {session_id[:8]}...")
        log_message(f"   Cache contains {len(cache_keys)} entries: {[k[:8] + '...' for k in cache_keys]}")

        if session_id in _research_cache:
            cache_entry = _research_cache[session_id]
            log_message(f"   ✅ Cache-Hit! Entry found with {len(cache_entry.get('scraped_sources', []))} sources")
            return dict(cache_entry)
        else:
            log_message(f"   ❌ Cache-Miss! session_id '{session_id[:8]}...' not in cache")
    return None


def delete_cached_research(session_id: Optional[str]) -> None:
    """
    Deletes cached research for a session (thread-safe)

    Args:
        session_id: Session ID to delete
    """
    if not session_id:
        return

    with _research_cache_lock:
        if session_id in _research_cache:
            del _research_cache[session_id]
            log_message(f"🗑️ Cache deleted for Session {session_id[:8]}...")
