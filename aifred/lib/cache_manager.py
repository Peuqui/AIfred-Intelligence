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
from .logging_utils import debug_print, console_print, console_separator


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
        debug_print("🔍 DEBUG Cache-Lookup: _research_cache oder _research_cache_lock ist None, oder keine session_id")
        return None

    with _research_cache_lock:
        # DEBUG: Zeige Cache-Inhalt (Keys) für Diagnose
        cache_keys = list(_research_cache.keys())
        debug_print(f"🔍 DEBUG Cache-Lookup: Suche session_id = {session_id[:8]}...")
        debug_print(f"   Cache enthält {len(cache_keys)} Einträge: {[k[:8] + '...' for k in cache_keys]}")

        if session_id in _research_cache:
            cache_entry = _research_cache[session_id]
            debug_print(f"   ✅ Cache-Hit! Eintrag gefunden mit {len(cache_entry.get('scraped_sources', []))} Quellen")
            return cache_entry.copy()
        else:
            debug_print(f"   ❌ Cache-Miss! session_id '{session_id[:8]}...' nicht in Cache")
    return None


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
        debug_print("⚠️ DEBUG Cache-Speicherung fehlgeschlagen: Cache nicht initialisiert oder keine session_id")
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
        debug_print(f"💾 Research-Cache gespeichert für Session {session_id[:8]}...")
        debug_print(f"   Cache enthält jetzt {cache_size} Einträge: {[k[:8] + '...' for k in _research_cache.keys()]}")
        debug_print(f"   Gespeichert: {len(scraped_sources)} Quellen, user_text: '{user_text[:50]}...'")

        # DEBUG: Zeige KOMPLETTEN Cache-Inhalt
        debug_print("=" * 80)
        debug_print("📦 KOMPLETTER CACHE-INHALT:")
        debug_print("=" * 80)
        for cache_key, cache_value in _research_cache.items():
            source_urls = [s.get('url', 'N/A') for s in cache_value.get('scraped_sources', [])]
            debug_print(f"Session: {cache_key}")
            debug_print(f"  User-Text: {cache_value.get('user_text', 'N/A')}")
            debug_print(f"  Timestamp: {cache_value.get('timestamp', 0)}")
            debug_print(f"  Mode: {cache_value.get('mode', 'N/A')}")
            debug_print(f"  Quellen ({len(source_urls)}):")
            for i, url in enumerate(source_urls, 1):
                debug_print(f"    {i}. {url[:80]}{'...' if len(url) > 80 else ''}")
            debug_print("-" * 80)
        debug_print("=" * 80)


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
            debug_print(f"🗑️ Cache gelöscht für Session {session_id[:8]}...")


def generate_cache_metadata(
    session_id: Optional[str],
    model_choice: str,
    llm_client,
    haupt_llm_context_limit: int = 4096
) -> None:
    """
    Generiert KI-basierte Metadata für gecachte Research-Daten (asynchron nach UI-Update).

    Diese Funktion wird NACH dem UI-Update aufgerufen, damit der User nicht auf die
    Metadata-Generierung warten muss. Sie holt den Cache, generiert eine semantische
    Zusammenfassung der Quellen, und updated den Cache.

    Args:
        session_id: Session ID für Cache-Lookup
        model_choice: LLM-Modell für Metadata-Generierung
        llm_client: LLMClient instance for inference
        haupt_llm_context_limit: Context limit for main LLM
    """
    if not session_id or not _research_cache or not _research_cache_lock:
        return

    try:
        # Hole Cache-Daten
        cache_entry = get_cached_research(session_id)
        if not cache_entry:
            debug_print("⚠️ Metadata-Generierung: Kein Cache gefunden")
            return

        scraped_sources = cache_entry.get('scraped_sources', [])
        if not scraped_sources:
            debug_print("⚠️ Metadata-Generierung: Keine Quellen im Cache")
            return

        debug_print("📝 Generiere KI-basierte Cache-Metadata...")
        console_print("📝 Erstelle Cache-Zusammenfassung...")

        # Baue Prompt für Metadata-Generierung
        sources_preview = "\n\n".join([
            f"Quelle {i+1}: {s.get('title', 'N/A')}\nURL: {s.get('url', 'N/A')}\nInhalt: {s.get('content', '')[:500]}..."
            for i, s in enumerate(scraped_sources[:3])  # Erste 3 Quellen für Kontext
        ])

        metadata_prompt = f"""Analysiere die Recherche-Quellen und erstelle eine KURZE Zusammenfassung (max 150 Zeichen):

QUELLEN:
{sources_preview}

AUFGABE:
Beschreibe in 1-2 Sätzen:
- Welches Thema/Zeitraum wird abgedeckt?
- Welche Hauptinformationen sind enthalten?

BEISPIELE:
Wetter: "7-Tage-Vorhersage Niestetal (24.10-30.10): Temp 12-18°C, Niederschlag, Wind"
News: "Nobelpreis Physik 2025: Verleihung 7.10, Gewinner-Namen nicht genannt"
Produkt: "iPhone 16 Pro: Display, Prozessor A18, Kamera 48MP, Akku 4685mAh"

Antworte NUR mit der Zusammenfassung (max 150 Zeichen), nichts anderes!"""

        # DEBUG: Zeige Metadata-Generierung Prompt vollständig
        debug_print("=" * 60)
        debug_print("📋 METADATA GENERATION PROMPT:")
        debug_print("-" * 60)
        debug_print(metadata_prompt)
        debug_print("-" * 60)
        debug_print(f"Prompt-Länge: {len(metadata_prompt)} Zeichen, ~{len(metadata_prompt.split())} Wörter")
        debug_print("=" * 60)

        messages = [{'role': 'user', 'content': metadata_prompt}]

        # Dynamisches num_ctx basierend auf Haupt-LLM-Limit (50% für Metadata, kurzer Output)
        metadata_num_ctx = min(2048, haupt_llm_context_limit // 2)  # Max 2048 oder 50% des Limits

        debug_print(f"Total Messages: {len(messages)}, Temperature: 0.1, num_ctx: {metadata_num_ctx} (Haupt-LLM-Limit: {haupt_llm_context_limit}), num_predict: 100")
        debug_print("=" * 60)

        metadata_start = time.time()

        # Use LLMClient (sync for now, will be async later)
        response = llm_client.chat_sync(
            model=model_choice,
            messages=messages,
            options={
                'temperature': 0.1,
                'num_ctx': metadata_num_ctx,
                'num_predict': 100
            }
        )

        metadata_summary = response.text.strip()
        metadata_time = time.time() - metadata_start

        # Kürze auf max 150 Zeichen falls nötig
        if len(metadata_summary) > 150:
            metadata_summary = metadata_summary[:147] + "..."

        # Update Cache mit Metadata
        with _research_cache_lock:
            if session_id in _research_cache:
                _research_cache[session_id]['metadata_summary'] = metadata_summary

        debug_print(f"✅ Cache-Metadata generiert ({metadata_time:.1f}s): {metadata_summary}")
        console_print(f"✅ Zusammenfassung erstellt: {metadata_summary[:80]}{'...' if len(metadata_summary) > 80 else ''}")
        console_separator()

    except Exception as e:
        debug_print(f"⚠️ Fehler bei Metadata-Generierung: {e}")
        console_print("⚠️ Metadata-Generierung fehlgeschlagen")
        console_separator()
