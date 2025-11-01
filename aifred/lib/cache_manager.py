"""
Cache Manager - Research Cache Management with Thread Safety

Handles caching of research results including:
- Thread-safe cache operations
- Cache metadata generation
- Session-based cache lookups
"""

import time
import threading
from typing import Dict, List, Optional
from .logging_utils import log_message


# ============================================================
# GLOBAL CACHE STATE (Dependency Injection)
# ============================================================
_research_cache: Optional[Dict] = None
_research_cache_lock: Optional[threading.Lock] = None


def set_research_cache(cache_dict: Dict, lock: threading.Lock) -> None:
    """
    Sets the research cache and lock for dependency injection

    Args:
        cache_dict: Dictionary to store research results by session_id
        lock: threading.Lock() for thread-safe access
    """
    global _research_cache, _research_cache_lock
    _research_cache = cache_dict
    _research_cache_lock = lock


def get_cached_research(session_id: Optional[str]) -> Optional[Dict]:
    """
    Gets cached research for a session (thread-safe)

    Args:
        session_id: Session ID to lookup

    Returns:
        Cached research data or None if not found
    """
    # WICHTIG: Prüfe _research_cache_lock und session_id, aber NICHT ob _research_cache leer ist!
    # Ein leeres Dictionary {} ist ein gültiger (aber leerer) Cache-State!
    if _research_cache is None or _research_cache_lock is None or not session_id:
        log_message("🔍 DEBUG Cache-Lookup: _research_cache oder _research_cache_lock ist None, oder keine session_id")
        return None

    with _research_cache_lock:
        # DEBUG: Zeige Cache-Inhalt (Keys) für Diagnose
        cache_keys = list(_research_cache.keys())
        log_message(f"🔍 DEBUG Cache-Lookup: Suche session_id = {session_id[:8]}...")
        log_message(f"   Cache enthält {len(cache_keys)} Einträge: {[k[:8] + '...' for k in cache_keys]}")

        if session_id in _research_cache:
            cache_entry = _research_cache[session_id]
            log_message(f"   ✅ Cache-Hit! Eintrag gefunden mit {len(cache_entry.get('scraped_sources', []))} Quellen")
            return cache_entry.copy()
        else:
            log_message(f"   ❌ Cache-Miss! session_id '{session_id[:8]}...' nicht in Cache")
    return None


def get_all_metadata_summaries(exclude_session_id: Optional[str] = None, max_entries: int = 10) -> List[Dict]:
    """
    Holt ALLE Metadata-Zusammenfassungen aus dem Cache (außer der aktuellen Session).

    Diese Funktion wird verwendet, um der Haupt-LLM Kontext aus früheren Recherchen zu geben,
    OHNE die vollständigen Quellen zu übergeben (spart Context-Tokens).

    Args:
        exclude_session_id: Session ID, die NICHT zurückgegeben werden soll (aktuelle Recherche)
        max_entries: Maximale Anzahl alter Recherchen (Standard: 10)

    Returns:
        Liste von Dicts mit {session_id, user_text, metadata_summary, timestamp}
        Sortiert nach Timestamp (neueste zuerst), max. max_entries Einträge
    """
    if _research_cache is None or _research_cache_lock is None:
        return []

    result = []
    with _research_cache_lock:
        for session_id, cache_entry in _research_cache.items():
            # Aktuelle Session ausschließen
            if session_id == exclude_session_id:
                continue

            # Nur Einträge MIT Metadata-Zusammenfassung
            metadata_summary = cache_entry.get('metadata_summary')
            if not metadata_summary:
                continue

            result.append({
                'session_id': session_id,
                'user_text': cache_entry.get('user_text', ''),
                'metadata_summary': metadata_summary,
                'timestamp': cache_entry.get('timestamp', 0)
            })

    # Sortiere nach Timestamp (neueste zuerst) und limitiere auf max_entries
    result.sort(key=lambda x: x['timestamp'], reverse=True)
    return result[:max_entries]


def save_cached_research(
    session_id: Optional[str],
    user_text: str,
    scraped_sources: List[Dict],
    mode: str,
    metadata_summary: Optional[str] = None
) -> None:
    """
    Saves research to cache (thread-safe)

    Args:
        session_id: Session ID
        user_text: Original user question
        scraped_sources: List of scraped source dictionaries
        mode: Research mode used
        metadata_summary: Optional KI-generated semantic summary of sources
    """
    if _research_cache is None or _research_cache_lock is None or not session_id:
        log_message("⚠️ DEBUG Cache-Speicherung fehlgeschlagen: Cache nicht initialisiert oder keine session_id")
        return

    with _research_cache_lock:
        _research_cache[session_id] = {
            'timestamp': time.time(),
            'user_text': user_text,
            'scraped_sources': scraped_sources,
            'mode': mode,
            'metadata_summary': metadata_summary
        }
        # DEBUG: Zeige Cache-Status nach Speichern
        cache_size = len(_research_cache)
        log_message(f"💾 Research-Cache gespeichert für Session {session_id[:8]}...")
        log_message(f"   Cache enthält jetzt {cache_size} Einträge: {[k[:8] + '...' for k in _research_cache.keys()]}")
        log_message(f"   Gespeichert: {len(scraped_sources)} Quellen, user_text: '{user_text[:50]}...'")

        # DEBUG: Zeige KOMPLETTEN Cache-Inhalt
        log_message("=" * 80)
        log_message("📦 KOMPLETTER CACHE-INHALT:")
        log_message("=" * 80)
        for cache_key, cache_value in _research_cache.items():
            source_urls = [s.get('url', 'N/A') for s in cache_value.get('scraped_sources', [])]
            log_message(f"Session: {cache_key}")
            log_message(f"  User-Text: {cache_value.get('user_text', 'N/A')}")
            log_message(f"  Timestamp: {cache_value.get('timestamp', 0)}")
            log_message(f"  Mode: {cache_value.get('mode', 'N/A')}")
            log_message(f"  Quellen ({len(source_urls)}):")
            for i, url in enumerate(source_urls, 1):
                log_message(f"    {i}. {url[:80]}{'...' if len(url) > 80 else ''}")
            log_message("-" * 80)
        log_message("=" * 80)


def delete_cached_research(session_id: Optional[str]) -> None:
    """
    Deletes cached research for a session (thread-safe)

    Args:
        session_id: Session ID to delete
    """
    if not _research_cache or not _research_cache_lock or not session_id:
        return

    with _research_cache_lock:
        if session_id in _research_cache:
            del _research_cache[session_id]
            log_message(f"🗑️ Cache gelöscht für Session {session_id[:8]}...")


async def generate_cache_metadata(
    session_id: Optional[str],
    metadata_model: str,
    llm_client,
    haupt_llm_context_limit: int
):
    """
    Generiert KI-basierte Metadata für gecachte Research-Daten (synchron nach Haupt-LLM).

    Diese Funktion wird NACH der Haupt-LLM-Antwort aufgerufen und yieldet Messages
    für die Debug-Console.

    Args:
        session_id: Session ID für Cache-Lookup
        metadata_model: LLM-Modell für Metadata-Generierung (z.B. Automatik-LLM)
        llm_client: LLMClient instance for inference
        haupt_llm_context_limit: Context limit for main LLM (für num_ctx Berechnung)

    Yields:
        Debug messages für UI
    """
    if not session_id or not _research_cache or not _research_cache_lock:
        return

    try:
        # Hole Cache-Daten
        cache_entry = get_cached_research(session_id)
        if not cache_entry:
            log_message("⚠️ Metadata-Generierung: Kein Cache gefunden")
            return

        scraped_sources = cache_entry.get('scraped_sources', [])
        if not scraped_sources:
            log_message("⚠️ Metadata-Generierung: Keine Quellen im Cache")
            return

        # UI-Message: Start
        yield {"type": "debug", "message": "📝 Starte Cache-Metadata Generierung..."}
        log_message("📝 Generiere KI-basierte Cache-Metadata...")
        log_message("📝 Erstelle Cache-Zusammenfassung...")

        # Baue Preview der Quellen (erste 3 Quellen, 500 Zeichen pro Quelle)
        sources_preview = "\n\n".join([
            f"Quelle {i+1}: {s.get('title', 'N/A')}\nURL: {s.get('url', 'N/A')}\nInhalt: {s.get('content', '')[:500]}..."
            for i, s in enumerate(scraped_sources[:3])  # Erste 3 Quellen für Kontext
        ])

        # Lade Prompt aus externer Datei (prompts/cache_metadata.txt)
        from .prompt_loader import get_cache_metadata_prompt
        metadata_prompt = get_cache_metadata_prompt(sources_preview=sources_preview)

        # DEBUG: Zeige Metadata-Generierung Prompt vollständig
        log_message("=" * 60)
        log_message("📋 METADATA GENERATION PROMPT:")
        log_message("-" * 60)
        log_message(metadata_prompt)
        log_message("-" * 60)
        log_message(f"Prompt-Länge: {len(metadata_prompt)} Zeichen, ~{len(metadata_prompt.split())} Wörter")
        log_message("=" * 60)

        messages = [{'role': 'user', 'content': metadata_prompt}]

        # Dynamisches num_ctx basierend auf Haupt-LLM-Limit (50% für Metadata, kurzer Output)
        metadata_num_ctx = min(2048, haupt_llm_context_limit // 2)  # Max 2048 oder 50% des Limits

        log_message(f"Total Messages: {len(messages)}, Temperature: 0.1, num_ctx: {metadata_num_ctx} (Haupt-LLM-Limit: {haupt_llm_context_limit}), num_predict: 100")
        log_message("=" * 60)

        metadata_start = time.time()

        log_message(f"🔧 Using metadata model: {metadata_model}")

        # Async LLM call
        response = await llm_client.chat(
            model=metadata_model,
            messages=messages,
            options={
                'temperature': 0.1,
                'num_ctx': metadata_num_ctx,
                'num_predict': 100
            }
        )

        metadata_summary = response.text.strip()
        metadata_time = time.time() - metadata_start

        # LLM folgt den Anweisungen im Prompt (max 60 Wörter)
        # Kein Hardcoded-Limit im Code - das LLM steuert die Länge selbst

        # Update Cache mit Metadata
        with _research_cache_lock:
            if session_id in _research_cache:
                _research_cache[session_id]['metadata_summary'] = metadata_summary

        tokens_per_second = response.tokens_per_second

        # UI-Message: Completion mit tokens/sec
        yield {"type": "debug", "message": f"✅ Cache-Metadata fertig ({metadata_time:.1f}s, {tokens_per_second:.1f} t/s)"}

        # Log-File Messages (detailliert)
        log_message(f"✅ Cache-Metadata generiert ({metadata_time:.1f}s, {tokens_per_second:.1f} t/s): {metadata_summary}")
        log_message(f"✅ Zusammenfassung erstellt: {metadata_summary[:80]}{'...' if len(metadata_summary) > 80 else ''}")

    except Exception as e:
        log_message(f"⚠️ Fehler bei Metadata-Generierung: {e}")
        log_message("⚠️ Metadata-Generierung fehlgeschlagen")
        yield {"type": "debug", "message": "⚠️ Cache-Metadata Generierung fehlgeschlagen"}
