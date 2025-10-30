"""
Agent Core Module - AI Research and Decision Making

This module handles agent-based research workflows including:
- Query optimization
- URL rating with AI
- Multi-mode research (quick/deep/automatic)
- Interactive decision-making
"""

import time
import re
import threading
import ollama
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from .agent_tools import search_web, scrape_webpage, build_context
from .formatting import format_thinking_process, build_debug_accordion
from .logging_utils import debug_print, console_print, console_separator
from .message_builder import build_messages_from_history
from .prompt_loader import (
    get_url_rating_prompt,
    get_query_optimization_prompt,
    get_decision_making_prompt,
    get_intent_detection_prompt,
    get_followup_intent_prompt,
    get_system_rag_prompt
)

# Compiled Regex Patterns (Performance-Optimierung)
THINK_TAG_PATTERN = re.compile(r'<think>(.*?)</think>', re.DOTALL)

# ============================================================
# RESEARCH CACHE (Dependency Injection)
# ============================================================
_research_cache = None
_research_cache_lock = None

# ============================================================
# MODEL CONTEXT LIMITS
# ============================================================
# Speichert die Context-Limits der aktuell genutzten Modelle
# Diese werden beim Service-Start und bei Modellwechsel von Ollama abgefragt
_haupt_llm_context_limit = 4096      # Fallback: 4096 Tokens
_automatik_llm_context_limit = 4096  # Fallback: 4096 Tokens


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


def generate_cache_metadata(session_id: Optional[str], model_choice: str) -> None:
    """
    Generiert KI-basierte Metadata für gecachte Research-Daten (asynchron nach UI-Update).

    Diese Funktion wird NACH dem UI-Update aufgerufen, damit der User nicht auf die
    Metadata-Generierung warten muss. Sie holt den Cache, generiert eine semantische
    Zusammenfassung der Quellen, und updated den Cache.

    Args:
        session_id: Session ID für Cache-Lookup
        model_choice: LLM-Modell für Metadata-Generierung
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

        # DEBUG: Zeige Messages-Array vollständig
        debug_print("=" * 60)
        debug_print(f"📨 MESSAGES an {model_choice} (Metadata):")
        debug_print("-" * 60)
        for i, msg in enumerate(messages):
            debug_print(f"Message {i+1} - Role: {msg['role']}")
            debug_print(f"Content: {msg['content']}")
            debug_print("-" * 60)
        # Dynamisches num_ctx basierend auf Haupt-LLM-Limit (50% für Metadata, kurzer Output)
        metadata_num_ctx = min(2048, _haupt_llm_context_limit // 2)  # Max 2048 oder 50% des Limits

        debug_print(f"Total Messages: {len(messages)}, Temperature: 0.1, num_ctx: {metadata_num_ctx} (Haupt-LLM-Limit: {_haupt_llm_context_limit}), num_predict: 100")
        debug_print("=" * 60)

        metadata_start = time.time()
        metadata_response = ollama.chat(
            model=model_choice,
            messages=messages,
            options={'temperature': 0.1, 'num_ctx': metadata_num_ctx, 'num_predict': 100}
        )
        metadata_summary = metadata_response['message']['content'].strip()
        metadata_time = time.time() - metadata_start

        # Kürze auf max 150 Zeichen falls nötig
        if len(metadata_summary) > 150:
            metadata_summary = metadata_summary[:147] + "..."

        # Update Cache mit Metadata
        with _research_cache_lock:
            if session_id in _research_cache:
                _research_cache[session_id]['metadata_summary'] = metadata_summary

        debug_print(f"✅ Cache-Metadata generiert ({metadata_time:.1f}s): {metadata_summary}")
        console_print(f"✅ Zusammenfassung erstellt {metadata_summary[:80]}{'...' if len(metadata_summary) > 80 else ''}")
        console_separator()

    except Exception as e:
        debug_print(f"⚠️ Fehler bei Metadata-Generierung: {e}")
        console_print(f"⚠️ Metadata-Generierung fehlgeschlagen")
        console_separator()


def save_cached_research(session_id: Optional[str], user_text: str, scraped_sources: List[Dict], mode: str, metadata_summary: Optional[str] = None) -> None:
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
            'metadata_summary': metadata_summary  # 🆕 KI-generierte Zusammenfassung
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


def estimate_tokens(messages):
    """
    Schätzt Token-Anzahl aus Messages

    Args:
        messages: Liste von Message-Dicts mit 'content' Key

    Returns:
        int: Geschätzte Anzahl Tokens (Faustregel: 1 Token ≈ 4 Zeichen)
    """
    total_size = sum(len(m['content']) for m in messages)
    return total_size // 4


def query_model_context_limit(model_name: str) -> int:
    """
    Fragt das Context-Limit eines Modells von Ollama ab.

    Nutzt die original_context_length (Training Context) als sicheres Limit,
    nicht die erweiterte context_length (RoPE-Scaling).

    Diese Funktion wird NUR beim Service-Start und bei Modellwechsel aufgerufen,
    NICHT bei jedem Request!

    Args:
        model_name: Name des Ollama-Modells (z.B. "phi3:mini", "qwen3:8b")

    Returns:
        int: Original Context Limit in Tokens (z.B. 4096 für phi3:mini, 32768 für qwen3:8b)
             Fallback: 4096 wenn Abfrage fehlschlägt
    """
    try:
        # Ollama API abfragen
        response = ollama.show(model_name)
        # Konvertiere Pydantic-Objekt zu Dict (model_dump() statt deprecated dict())
        data = response.model_dump() if hasattr(response, 'model_dump') else response.dict()
        # WICHTIG: Key heißt 'modelinfo' nicht 'model_info'!
        model_details = data.get('modelinfo', {})

        # Suche nach original_context_length (sicherstes Limit)
        # Beispiel phi3: "phi3.rope.scaling.original_context_length": 4096
        # Beispiel qwen: "qwen2.context_length": 32768

        # PRIORITÄT 1: Suche nach original_context_length (für Modelle mit RoPE-Scaling)
        for key, value in model_details.items():
            if 'original_context' in key.lower():
                limit = int(value)
                debug_print(f"📏 Model {model_name}: Context Limit = {limit} Tokens (aus {key}, original)")
                return limit

        # PRIORITÄT 2: Suche nach .context_length (für Modelle ohne RoPE-Scaling)
        for key, value in model_details.items():
            if key.endswith('.context_length'):
                limit = int(value)
                debug_print(f"📏 Model {model_name}: Context Limit = {limit} Tokens (aus {key})")
                return limit

        # Fallback: Wenn nicht gefunden, nutze 4K (konservativ)
        debug_print(f"⚠️ Model {model_name}: Context Limit nicht gefunden, nutze 4096 Fallback")
        return 4096

    except Exception as e:
        debug_print(f"⚠️ Fehler beim Abfragen von Model-Info für {model_name}: {e}")
        return 4096  # Konservativer Fallback


def set_haupt_llm_context_limit(model_name: str) -> None:
    """
    Setzt das Context-Limit für das Haupt-LLM.
    Wird beim Service-Start und bei Modellwechsel aufgerufen.

    Args:
        model_name: Name des Haupt-LLM Modells
    """
    global _haupt_llm_context_limit
    _haupt_llm_context_limit = query_model_context_limit(model_name)
    debug_print(f"✅ Haupt-LLM Context-Limit gesetzt: {_haupt_llm_context_limit}")


def set_automatik_llm_context_limit(model_name: str) -> None:
    """
    Setzt das Context-Limit für das Automatik-LLM.
    Wird beim Service-Start und bei Modellwechsel aufgerufen.

    Args:
        model_name: Name des Automatik-LLM Modells
    """
    global _automatik_llm_context_limit
    _automatik_llm_context_limit = query_model_context_limit(model_name)
    debug_print(f"✅ Automatik-LLM Context-Limit gesetzt: {_automatik_llm_context_limit}")


def calculate_dynamic_num_ctx(messages, llm_options=None, is_automatik_llm=False):
    """
    Berechnet optimales num_ctx basierend auf Message-Größe und Model-Limit.

    Die Berechnung berücksichtigt:
    1. Message-Größe + 30% Puffer + 2048 für Antwort
    2. Model-Maximum (Haupt-LLM oder Automatik-LLM Limit)
    3. User-Override (falls in llm_options gesetzt)

    Args:
        messages: Liste von Message-Dicts mit 'content' Key
        llm_options: Dict mit optionalem 'num_ctx' Override
        is_automatik_llm: True wenn für Automatik-LLM berechnet wird (default: False = Haupt-LLM)

    Returns:
        int: Optimales num_ctx (gerundet auf Standard-Größen, geclippt auf Model-Limit)
    """
    # Check für manuellen Override
    user_num_ctx = llm_options.get('num_ctx') if llm_options else None
    if user_num_ctx:
        return user_num_ctx

    # Berechne Tokens aus Message-Größe
    estimated_tokens = estimate_tokens(messages)  # 1 Token ≈ 4 Zeichen

    # Puffer: +30% für Varianz + 2048 für Antwort
    needed_tokens = int(estimated_tokens * 1.3) + 2048

    # Runde auf Standard-Größe
    if needed_tokens <= 2048:
        calculated_ctx = 2048
    elif needed_tokens <= 4096:
        calculated_ctx = 4096
    elif needed_tokens <= 8192:
        calculated_ctx = 8192
    elif needed_tokens <= 10240:
        calculated_ctx = 10240
    elif needed_tokens <= 12288:
        calculated_ctx = 12288
    elif needed_tokens <= 16384:
        calculated_ctx = 16384
    elif needed_tokens <= 20480:
        calculated_ctx = 20480  # 20K
    elif needed_tokens <= 24576:
        calculated_ctx = 24576  # 24K
    elif needed_tokens <= 28672:
        calculated_ctx = 28672  # 28K
    elif needed_tokens <= 32768:
        calculated_ctx = 32768  # 32K
    elif needed_tokens <= 49152:
        calculated_ctx = 49152  # 48K
    elif needed_tokens <= 65536:
        calculated_ctx = 65536  # 64K
    elif needed_tokens <= 98304:
        calculated_ctx = 98304  # 96K
    else:
        calculated_ctx = 131072  # 128K

    # WICHTIG: Clippe auf Model-Limit
    model_limit = _automatik_llm_context_limit if is_automatik_llm else _haupt_llm_context_limit
    llm_type = "Automatik-LLM" if is_automatik_llm else "Haupt-LLM"

    if calculated_ctx > model_limit:
        debug_print(f"⚠️ Context {calculated_ctx} > {llm_type}-Limit {model_limit}, clippe auf {model_limit}")

        # Zusätzliche Warnung NUR wenn Messages TATSÄCHLICH größer als Model-Limit
        if estimated_tokens > model_limit:  # Kontext ÜBERSCHRITTEN
            console_print(f"⚠️ WARNUNG: Kontext überschritten! ({estimated_tokens} Tokens > {model_limit} Tokens Limit)")
            console_print(f"⚠️ Ältere Messages werden abgeschnitten!")

        return model_limit

    return calculated_ctx


def detect_query_intent(user_query, automatik_model="qwen3:1.7b"):
    """
    Erkennt die Intent einer User-Anfrage für adaptive Temperature-Wahl

    Args:
        user_query: User-Frage
        automatik_model: LLM für Intent-Detection (default: qwen3:1.7b)

    Returns:
        str: "FAKTISCH", "KREATIV" oder "GEMISCHT"
    """
    prompt = get_intent_detection_prompt(user_query=user_query)

    try:
        debug_print(f"🎯 Intent-Detection für Query: {user_query[:60]}...")

        response = ollama.chat(
            model=automatik_model,
            messages=[{'role': 'user', 'content': prompt}],
            options={
                'temperature': 0.2,  # Niedrig für konsistente Intent-Detection
                'num_ctx': 4096  # Standard Context für Intent-Detection
            }
        )

        intent_raw = response['message']['content'].strip().upper()

        # Extrahiere Intent (auch wenn LLM mehr Text schreibt)
        if "FAKTISCH" in intent_raw:
            intent = "FAKTISCH"
        elif "KREATIV" in intent_raw:
            intent = "KREATIV"
        elif "GEMISCHT" in intent_raw:
            intent = "GEMISCHT"
        else:
            debug_print(f"⚠️ Intent unbekannt: '{intent_raw}' → Default: FAKTISCH")
            intent = "FAKTISCH"  # Fallback

        debug_print(f"✅ Intent erkannt: {intent}")
        return intent

    except Exception as e:
        debug_print(f"❌ Intent-Detection Fehler: {e} → Fallback: FAKTISCH")
        return "FAKTISCH"  # Safe Fallback


def detect_cache_followup_intent(original_query, followup_query, automatik_model="qwen3:1.7b"):
    """
    Erkennt die Intent einer Nachfrage zu einer gecachten Recherche

    Args:
        original_query: Ursprüngliche Recherche-Frage
        followup_query: Nachfrage des Users
        automatik_model: LLM für Intent-Detection

    Returns:
        str: "FAKTISCH", "KREATIV" oder "GEMISCHT"
    """
    prompt = get_followup_intent_prompt(
        original_query=original_query,
        followup_query=followup_query
    )

    try:
        debug_print(f"🎯 Cache-Followup Intent-Detection mit {automatik_model}: {followup_query[:60]}...")

        response = ollama.chat(
            model=automatik_model,
            messages=[{'role': 'user', 'content': prompt}],
            options={
                'temperature': 0.2,
                'num_ctx': 4096  # Standard Context
            }
        )

        intent_raw = response['message']['content'].strip().upper()

        # Extrahiere Intent
        if "FAKTISCH" in intent_raw:
            intent = "FAKTISCH"
        elif "KREATIV" in intent_raw:
            intent = "KREATIV"
        elif "GEMISCHT" in intent_raw:
            intent = "GEMISCHT"
        else:
            debug_print(f"⚠️ Cache-Intent unbekannt: '{intent_raw}' → Default: FAKTISCH")
            intent = "FAKTISCH"  # Bei Recherche-Nachfragen meist faktisch

        debug_print(f"✅ Cache-Followup Intent ({automatik_model}): {intent}")
        return intent

    except Exception as e:
        debug_print(f"❌ Cache-Followup Intent-Detection Fehler: {e} → Fallback: FAKTISCH")
        return "FAKTISCH"


def get_temperature_for_intent(intent):
    """
    Gibt die passende Temperature für einen Intent zurück

    Args:
        intent: "FAKTISCH", "KREATIV" oder "GEMISCHT"

    Returns:
        float: Temperature (0.2, 0.5 oder 0.8)
    """
    temp_map = {
        "FAKTISCH": 0.2,
        "KREATIV": 0.8,
        "GEMISCHT": 0.5
    }
    return temp_map.get(intent, 0.2)  # Fallback: 0.2


def optimize_search_query(user_text, automatik_model, history=None):
    """
    Extrahiert optimierte Suchbegriffe aus User-Frage

    Args:
        user_text: Volle User-Frage (kann lang sein)
        automatik_model: Automatik-LLM für Query-Optimierung
        history: Chat History (optional, für Kontext bei Nachfragen)

    Returns:
        tuple: (optimized_query, reasoning_content)
    """
    prompt = get_query_optimization_prompt(user_text=user_text)

    # DEBUG: Zeige Query Optimization Prompt
    debug_print("=" * 60)
    debug_print("📋 QUERY OPTIMIZATION PROMPT:")
    debug_print("-" * 60)
    debug_print(prompt)
    debug_print("-" * 60)
    debug_print(f"Prompt-Länge: {len(prompt)} Zeichen, ~{len(prompt.split())} Wörter")
    debug_print("=" * 60)

    try:
        debug_print(f"🔍 Query-Optimierung mit {automatik_model}")
        console_print("🔧 Query-Optimierung startet")

        # Baue Messages mit History (letzte 2-3 Turns für Kontext bei Nachfragen)
        # Build messages from history (last 3 turns for context)
        messages = build_messages_from_history(history, prompt, max_turns=3)

        # DEBUG: Zeige Messages-Array vollständig
        debug_print("=" * 60)
        debug_print(f"📨 MESSAGES an {automatik_model} (Query-Opt):")
        debug_print("-" * 60)
        for i, msg in enumerate(messages):
            debug_print(f"Message {i+1} - Role: {msg['role']}")
            debug_print(f"Content: {msg['content']}")
            debug_print("-" * 60)
        # Dynamisches num_ctx basierend auf Automatik-LLM-Limit (100% für Query-Opt wegen History)
        query_num_ctx = min(8192, _automatik_llm_context_limit)  # Max 8192 oder volles Limit

        debug_print(f"Total Messages: {len(messages)}, Temperature: 0.3, num_ctx: {query_num_ctx} (Automatik-LLM-Limit: {_automatik_llm_context_limit})")
        debug_print("=" * 60)

        response = ollama.chat(
            model=automatik_model,
            messages=messages,
            options={
                'temperature': 0.3,  # Leicht kreativ für Keywords, aber stabil
                'num_ctx': query_num_ctx  # Dynamisch basierend auf Model
            }
        )

        raw_response = response['message']['content'].strip()

        # Extrahiere <think> Inhalt BEVOR wir ihn entfernen (für Debug-Output)
        think_match = THINK_TAG_PATTERN.search(raw_response)
        think_content = think_match.group(1).strip() if think_match else None

        # Säubern: Entferne <think> Tags und deren Inhalt
        optimized_query = THINK_TAG_PATTERN.sub('', raw_response)

        # Entferne Anführungszeichen und Sonderzeichen
        optimized_query = re.sub(r'["\'\n\r]', '', optimized_query)
        optimized_query = ' '.join(optimized_query.split())  # Normalize whitespace

        # ============================================================
        # POST-PROCESSING: Temporale Kontext-Erkennung
        # ============================================================
        # Garantiert aktuelles Jahr bei zeitlich relevanten Queries, auch wenn LLM es vergisst

        # Dynamisches Jahr (nicht hardcoded!)
        current_year = str(datetime.now().year)

        temporal_keywords = [
            'neu', 'neue', 'neuer', 'neues', 'neueste', 'neuester', 'neuestes',
            'aktuell', 'aktuelle', 'aktueller', 'aktuelles',
            'latest', 'recent', 'new', 'newest',
            'beste', 'bester', 'bestes', 'best',
            'top', 'current'
        ]

        comparison_keywords = [
            'vs', 'versus', 'vergleich', 'compare', 'comparison',
            'oder', 'or', 'vs.', 'gegen', 'statt', 'instead'
        ]

        query_lower = optimized_query.lower()

        # Regel 1: "beste/neueste X" → + aktuelles Jahr (falls nicht schon vorhanden)
        if any(kw in query_lower for kw in temporal_keywords) and current_year not in optimized_query:
            optimized_query += f" {current_year}"
            debug_print(f"   ⏰ Temporaler Kontext ergänzt: {current_year}")

        # Regel 2: "X vs Y" → + aktuelles Jahr (falls nicht schon vorhanden)
        elif any(kw in query_lower for kw in comparison_keywords) and current_year not in optimized_query:
            optimized_query += f" {current_year}"
            debug_print(f"   ⚖️ Vergleichs-Kontext ergänzt: {current_year}")

        debug_print(f"🔍 Query-Optimierung:")
        debug_print(f"   Original: {user_text[:80]}{'...' if len(user_text) > 80 else ''}")
        debug_print(f"   Optimiert: {optimized_query}")

        # Return: Tuple (optimized_query, reasoning)
        return (optimized_query, think_content)

    except Exception as e:
        debug_print(f"⚠️ Fehler bei Query-Optimierung: {e}")
        debug_print(f"   Fallback zu Original-Query")
        return (user_text, None)


def ai_rate_urls(urls, titles, query, automatik_model):
    """
    KI bewertet alle URLs auf einmal (effizient!)

    Args:
        urls: Liste von URLs
        titles: Liste von Titeln (parallel zu URLs)
        query: Suchanfrage
        automatik_model: Automatik-LLM für URL-Bewertung

    Returns:
        Liste von {'url', 'score', 'reasoning'}, sortiert nach Score
    """
    if not urls:
        return []

    # Erstelle nummerierte Liste für KI mit Titel + URL
    url_list = "\n".join([
        f"{i+1}. Titel: {titles[i] if i < len(titles) else 'N/A'}\n   URL: {url}"
        for i, url in enumerate(urls)
    ])

    prompt = get_url_rating_prompt(query=query, url_list=url_list)

    # DEBUG: Zeige URL Rating Prompt
    debug_print("=" * 60)
    debug_print("📋 URL RATING PROMPT:")
    debug_print("-" * 60)
    debug_print(prompt)
    debug_print("-" * 60)
    debug_print(f"Prompt-Länge: {len(prompt)} Zeichen, ~{len(prompt.split())} Wörter")
    debug_print("=" * 60)

    try:
        debug_print(f"🔍 URL-Rating mit {automatik_model}")

        messages = [{'role': 'user', 'content': prompt}]

        # DEBUG: Zeige Messages-Array vollständig
        debug_print("=" * 60)
        debug_print(f"📨 MESSAGES an {automatik_model} (URL-Rating):")
        debug_print("-" * 60)
        for i, msg in enumerate(messages):
            debug_print(f"Message {i+1} - Role: {msg['role']}")
            debug_print(f"Content: {msg['content']}")
            debug_print("-" * 60)
        # Dynamisches num_ctx basierend auf Automatik-LLM-Limit (100% für URL-Rating wegen vieler URLs)
        rating_num_ctx = min(8192, _automatik_llm_context_limit)  # Max 8192 oder volles Limit

        debug_print(f"Total Messages: {len(messages)}, Temperature: 0.0, num_ctx: {rating_num_ctx} (Automatik-LLM-Limit: {_automatik_llm_context_limit})")
        debug_print("=" * 60)

        response = ollama.chat(
            model=automatik_model,
            messages=messages,
            options={
                'temperature': 0.0,  # Komplett deterministisch für maximale Konsistenz!
                'num_ctx': rating_num_ctx  # Dynamisch basierend auf Model
            }
        )

        answer = response['message']['content']

        # Entferne <think> Blöcke (falls Qwen3 Thinking Mode)
        answer_cleaned = THINK_TAG_PATTERN.sub('', answer).strip()

        # Parse Antwort
        rated_urls = []
        lines = answer_cleaned.strip().split('\n')

        for i, line in enumerate(lines):
            if not line.strip() or i >= len(urls):
                continue

            try:
                # Parse: "1. Score: 9 - Reasoning: ..."
                score_part = line.split('Score:')[1].split('-')[0].strip()
                score = int(score_part)

                reasoning_part = line.split('Reasoning:')[1].strip() if 'Reasoning:' in line else "N/A"

                rated_urls.append({
                    'url': urls[i],
                    'score': score,
                    'reasoning': reasoning_part
                })
            except Exception as e:
                debug_print(f"⚠️ Parse-Fehler für URL {i+1}: {e}")
                debug_print(f"   Problematische Zeile: '{line.strip()}'")
                debug_print(f"   Erwartet: '[NUM]. Score: [0-10] - Reasoning: [TEXT]'")
                # Fallback
                rated_urls.append({
                    'url': urls[i],
                    'score': 5,
                    'reasoning': "Parse-Fehler"
                })

        # Sortiere nach Score (beste zuerst)
        rated_urls.sort(key=lambda x: x['score'], reverse=True)

        debug_print(f"✅ {len(rated_urls)} URLs bewertet")

        return rated_urls

    except Exception as e:
        debug_print(f"❌ Fehler bei URL-Rating: {e}")
        # Fallback: Gib URLs ohne Rating zurück
        return [{'url': url, 'score': 5, 'reasoning': 'Rating fehlgeschlagen'} for url in urls]


def perform_agent_research(user_text, stt_time, mode, model_choice, automatik_model, history, session_id=None, temperature_mode='auto', temperature=0.2, llm_options=None):
    """
    Agent-Recherche mit AI-basierter URL-Bewertung

    Args:
        user_text: User-Frage
        stt_time: STT-Zeit
        mode: "quick" oder "deep"
        model_choice: Haupt-LLM für finale Antwort
        automatik_model: Automatik-LLM für Query-Opt & URL-Rating
        llm_options: Dict mit Ollama-Optionen (num_ctx, etc.) - Optional
        history: Chat History
        session_id: Session-ID für Research-Cache (optional)
        temperature_mode: 'auto' (Intent-Detection) oder 'manual' (fixer Wert)
        temperature: Temperature-Wert (0.0-2.0) - nur bei mode='manual'

    Returns:
        tuple: (ai_text, history, inference_time)
    """

    agent_start = time.time()
    tool_results = []

    # Extrahiere num_ctx aus llm_options oder nutze Standardwerte
    if llm_options is None:
        llm_options = {}

    # Context Window Größen
    # Haupt-LLM: Vom User konfigurierbar (None = Auto, sonst fixer Wert)
    user_num_ctx = llm_options.get('num_ctx')  # Kann None sein!

    # Debug: Zeige Context Window Modus
    if user_num_ctx is None:
        debug_print(f"📊 Context Window: Haupt-LLM=Auto (dynamisch, Ollama begrenzt auf Model-Max)")
    else:
        debug_print(f"📊 Context Window: Haupt-LLM={user_num_ctx} Tokens (manuell gesetzt)")

    # DEBUG: Session-ID prüfen
    debug_print(f"🔍 DEBUG: session_id = {session_id} (type: {type(session_id)})")

    # 0. Cache-Check: Nachfrage zu vorheriger Recherche (von Automatik-LLM oder explizit)
    cache_entry = get_cached_research(session_id)
    cached_sources = cache_entry.get('scraped_sources', []) if cache_entry else []

    if cached_sources:
            debug_print(f"💾 Cache-Hit! Nutze gecachte Recherche (Session {session_id[:8]}...)")
            debug_print(f"   Ursprüngliche Frage: {cache_entry.get('user_text', 'N/A')[:80]}...")
            debug_print(f"   Cache enthält {len(cached_sources)} Quellen")

            # Console-Output für Cache-Hit
            console_print(f"💾 Cache-Hit! Nutze gecachte Daten ({len(cached_sources)} Quellen)")
            original_q = cache_entry.get('user_text', 'N/A')
            console_print(f"📋 Ursprüngliche Frage: {original_q[:60]}{'...' if len(original_q) > 60 else ''}")

            # Nutze ALLE Quellen aus dem Cache
            scraped_only = cached_sources
            context = build_context(user_text, scraped_only)

            # System-Prompt für Nachfrage (allgemein, LLM entscheidet Fokus)
            system_prompt = f"""Du bist ein AI Voice Assistant mit ECHTZEIT Internet-Zugang!

Der User stellt eine Nachfrage zu einer vorherigen Recherche.

**Ursprüngliche Frage:** "{cache_entry.get('user_text', 'N/A')}"
**Aktuelle Nachfrage:** "{user_text}"

# VERFÜGBARE QUELLEN (aus vorheriger Recherche):

{context}

# 🚫 ABSOLUTES VERBOT - NIEMALS ERFINDEN:
- ❌ KEINE Namen von Personen, Preisträgern, Wissenschaftlern (außer explizit in Quellen genannt!)
- ❌ KEINE Daten, Termine, Jahreszahlen (außer explizit in Quellen genannt!)
- ❌ KEINE Entdeckungen, Erfindungen, wissenschaftliche Details (außer explizit beschrieben!)
- ❌ KEINE Zahlen, Statistiken, Messungen (außer explizit in Quellen!)
- ❌ KEINE Zitate oder wörtliche Rede (außer explizit zitiert!)
- ⚠️ BEI UNSICHERHEIT: "Laut den Quellen ist [Detail] nicht spezifiziert"
- ❌ NIEMALS aus Kontext "raten" oder "folgern" was gemeint sein könnte!

# AUFGABE:
- Beantworte die Nachfrage AUSFÜHRLICH basierend auf den verfügbaren Quellen
- Wenn der User eine spezifische Quelle erwähnt (z.B. "Quelle 1"), fokussiere darauf
- Gehe auf ALLE relevanten Details ein - ABER NUR was EXPLIZIT in Quellen steht!
- Zitiere konkrete Fakten: Namen, Zahlen, Daten, Versionen - NUR wenn EXPLIZIT genannt!
- ⚠️ WICHTIG: Nutze NUR Informationen die EXPLIZIT in den Quellen stehen!
- ❌ KEINE Halluzinationen oder Erfindungen!
- Falls Quelle nicht das enthält was User fragt: "Diese Quelle enthält keine Informationen über [Detail]"

# ANTWORT-STIL:
- Sehr detailliert (3-5 Absätze)
- Konkrete Details und Fakten nennen - aber NUR aus Quellen!
- Bei mehreren Quellen: Zeige Zusammenhänge auf
- Logisch strukturiert
- Deutsch

# QUELLENANGABE:
- LISTE AM ENDE **NUR** DIE TATSÄCHLICH GENUTZTEN QUELLEN AUF:

  **Quellen:**
  - Quelle 1: https://... (Thema: [Was wurde dort behandelt])
  - Quelle 2: https://... (Thema: [Was wurde dort behandelt])
  (etc.)"""

            # Generiere Antwort mit Cache-Daten
            messages = []

            # History hinzufügen (falls vorhanden) - LLM sieht vorherige Konversation
            for h in history:
                user_msg = h[0].split(" (STT:")[0].split(" (Agent:")[0] if " (STT:" in h[0] or " (Agent:" in h[0] else h[0]
                ai_msg = h[1].split(" (Inferenz:")[0] if " (Inferenz:" in h[1] else h[1]
                messages.extend([
                    {'role': 'user', 'content': user_msg},
                    {'role': 'assistant', 'content': ai_msg}
                ])

            # System-Prompt + aktuelle User-Frage
            messages.insert(0, {'role': 'system', 'content': system_prompt})
            messages.append({'role': 'user', 'content': user_text})

            # Dynamische num_ctx Berechnung für Cache-Hit (Haupt-LLM)
            final_num_ctx = calculate_dynamic_num_ctx(messages, llm_options, is_automatik_llm=False)
            if llm_options and llm_options.get('num_ctx'):
                debug_print(f"🎯 Cache-Hit Context Window: {final_num_ctx} Tokens (manuell)")
                console_print(f"🪟 Context Window: {final_num_ctx} Tokens (manual)")
            else:
                estimated_tokens = estimate_tokens(messages)
                debug_print(f"🎯 Cache-Hit Context Window: {final_num_ctx} Tokens (dynamisch, ~{estimated_tokens} Tokens benötigt)")
                console_print(f"🪟 Context Window: {final_num_ctx} Tokens (auto)")

            # Temperature entscheiden: Manual Override oder Auto (Intent-Detection)
            if temperature_mode == 'manual':
                final_temperature = temperature
                debug_print(f"🌡️ Cache-Hit Temperature: {final_temperature} (MANUAL OVERRIDE)")
                console_print(f"🌡️ Temperature: {final_temperature} (manual)")
            else:
                # Auto: Intent-Detection für Cache-Followup
                followup_intent = detect_cache_followup_intent(
                    original_query=cache_entry.get('user_text', ''),
                    followup_query=user_text,
                    automatik_model=automatik_model
                )
                final_temperature = get_temperature_for_intent(followup_intent)
                debug_print(f"🌡️ Cache-Hit Temperature: {final_temperature} (Intent: {followup_intent})")
                console_print(f"🌡️ Temperature: {final_temperature} (auto, {followup_intent})")

            # Console: LLM starts
            console_print(f"🤖 Haupt-LLM startet: {model_choice} (Cache-Daten)")

            llm_start = time.time()
            response = ollama.chat(
                model=model_choice,
                messages=messages,
                options={
                    'temperature': final_temperature,  # Adaptive oder Manual Temperature!
                    'num_ctx': final_num_ctx  # Dynamisch berechnet oder User-Vorgabe
                }
            )
            llm_time = time.time() - llm_start

            final_answer = response['message']['content']

            total_time = time.time() - agent_start

            # Console: LLM finished
            console_print(f"✅ Haupt-LLM fertig ({llm_time:.1f}s, {len(final_answer)} Zeichen, Cache-Total: {total_time:.1f}s)")
            console_separator()

            # Formatiere <think> Tags als Collapsible (falls vorhanden)
            final_answer_formatted = format_thinking_process(final_answer, model_name=model_choice, inference_time=llm_time)

            # Zeitmessung-Text
            timing_text = f" (Cache-Hit: {total_time:.1f}s = LLM {llm_time:.1f}s)"
            ai_text_with_timing = final_answer_formatted + timing_text

            # Update History
            user_display = f"{user_text} (Agent: Cache-Hit, {len(cached_sources)} Quellen)"
            ai_display = ai_text_with_timing
            history.append([user_display, ai_display])

            debug_print(f"✅ Cache-basierte Antwort fertig in {total_time:.1f}s")
            return (ai_text_with_timing, history, total_time)
    else:
        if session_id:
            debug_print(f"⚠️ Kein Cache für Session {session_id[:8]}... gefunden → Normale Web-Recherche")

    # 1. Query Optimization: KI extrahiert Keywords (mit Zeitmessung und History-Kontext!)
    query_opt_start = time.time()
    optimized_query, query_reasoning = optimize_search_query(user_text, automatik_model, history)
    query_opt_time = time.time() - query_opt_start

    # 2. Web-Suche (Brave → Tavily → SearXNG Fallback) mit optimierter Query
    debug_print("=" * 60)
    debug_print(f"🔍 Web-Suche mit optimierter Query")
    debug_print("=" * 60)

    search_result = search_web(optimized_query)
    tool_results.append(search_result)

    # Console Log: Welche API wurde benutzt?
    api_source = search_result.get('source', 'Unbekannt')

    # Zeige API-Stats (wenn vorhanden)
    stats = search_result.get('stats', {})
    apis_used = search_result.get('apis_used', [])

    if stats and apis_used:
        # Multi-API Search mit Stats
        total_urls = stats.get('total_urls', 0)
        unique_urls = stats.get('unique_urls', 0)
        duplicates = stats.get('duplicates_removed', 0)

        console_print(f"🌐 Web-Suche: {', '.join(apis_used)} ({len(apis_used)} APIs)")
        if duplicates > 0:
            console_print(f"🔄 Deduplizierung: {total_urls} URLs → {unique_urls} unique ({duplicates} Duplikate)")
    else:
        # Single API oder alte Version
        console_print(f"🌐 Web-Suche mit: {api_source}")

    # 2. URLs + Titel extrahieren (Search-APIs liefern bereits max 10)
    related_urls = search_result.get('related_urls', [])
    titles = search_result.get('titles', [])

    # Initialisiere Variablen für Fälle ohne URLs
    rated_urls = []
    rating_time = 0.0  # Default: 0.0 statt None für sichere Übergabe an build_debug_accordion

    if not related_urls:
        debug_print("⚠️ Keine URLs gefunden, nur Abstract")
    else:
        debug_print(f"📋 {len(related_urls)} URLs gefunden")

        # 3. AI bewertet alle URLs (1 Call!) - mit Titeln für bessere Aktualitäts-Erkennung
        debug_print(f"🤖 KI bewertet URLs mit {automatik_model}...")
        console_print(f"⚖️ KI bewertet URLs mit: {automatik_model}")
        rating_start = time.time()
        rated_urls = ai_rate_urls(related_urls, titles, user_text, automatik_model)
        rating_time = time.time() - rating_start

        # Debug: Zeige ALLE Bewertungen (nicht nur Top 5)
        debug_print("=" * 60)
        debug_print("📊 URL-BEWERTUNGEN (alle):")
        debug_print("=" * 60)
        for idx, item in enumerate(rated_urls, 1):
            url_short = item['url'][:70] + '...' if len(item['url']) > 70 else item['url']
            reasoning_short = item['reasoning'][:80] + '...' if len(item['reasoning']) > 80 else item['reasoning']
            emoji = "✅" if item['score'] >= 7 else "⚠️" if item['score'] >= 5 else "❌"
            debug_print(f"{idx}. {emoji} Score {item['score']}/10: {url_short}")
            debug_print(f"   Grund: {reasoning_short}")
        debug_print("=" * 60)

        # 4. Scraping basierend auf Modus
        if mode == "quick":
            target_sources = 3
            initial_scrape_count = 3  # Quick-Modus: Kein Fallback nötig
            debug_print(f"⚡ Schnell-Modus: Scrape beste 3 URLs")
        elif mode == "deep":
            target_sources = 5  # Ziel: 5 erfolgreiche Quellen
            initial_scrape_count = 7  # Starte mit 7 URLs (Fallback für Fehler)
            debug_print(f"🔍 Ausführlich-Modus: Scrape beste {initial_scrape_count} URLs (Ziel: {target_sources} erfolgreiche)")
        else:
            target_sources = 3  # Fallback
            initial_scrape_count = 3

        # 4.5. Validierung: Fallback wenn rated_urls leer ist
        if not rated_urls:
            debug_print("⚠️ WARNUNG: Keine URLs konnten bewertet werden!")
            debug_print("   Fallback: Nutze Original-URLs ohne Rating")
            # Fallback: Nutze Original-URLs ohne Rating
            rated_urls = [{'url': u, 'score': 5, 'reasoning': 'No rating available'} for u in related_urls[:target_sources]]

        # 5. Scrape URLs PARALLEL (großer Performance-Win!)
        console_print("🌐 Web-Scraping startet (parallel)")

        # Filtere URLs nach Score und Limit
        # THRESHOLD GESENKT: 5 → 3 (weniger restriktiv, mehr Quellen)
        # Deep-Modus: Starte mit initial_scrape_count URLs (Fallback für Fehler)
        scrape_limit = initial_scrape_count if mode == "deep" else target_sources
        urls_to_scrape = [
            item for item in rated_urls
            if item['score'] >= 3  # ← War 5, jetzt 3!
        ][:scrape_limit]  # Deep: 7 URLs, Quick: 3 URLs

        # FALLBACK: Wenn ALLE URLs < 3, nimm trotzdem die besten!
        if not urls_to_scrape and rated_urls:
            debug_print(f"⚠️ Alle URLs haben Score < 3 → Nutze Top {target_sources} als Fallback")
            console_print(f"⚠️ Niedrige URL-Scores → Nutze beste {target_sources} URLs als Fallback")
            urls_to_scrape = rated_urls[:target_sources]

        if not urls_to_scrape:
            debug_print("⚠️ Keine URLs zum Scrapen (rated_urls ist leer)")
            console_print("⚠️ Keine URLs verfügbar → 0 Quellen gescraped")
        else:
            debug_print(f"🚀 Parallel Scraping: {len(urls_to_scrape)} URLs gleichzeitig")

            # Parallel Scraping mit ThreadPoolExecutor
            scraped_results = []
            with ThreadPoolExecutor(max_workers=min(5, len(urls_to_scrape))) as executor:
                # Starte alle Scrape-Tasks parallel
                future_to_item = {
                    executor.submit(scrape_webpage, item['url']): item
                    for item in urls_to_scrape
                }

                # Sammle Ergebnisse (in Completion-Order für Live-Feedback)
                for future in as_completed(future_to_item):
                    item = future_to_item[future]
                    url_short = item['url'][:60] + '...' if len(item['url']) > 60 else item['url']

                    try:
                        scrape_result = future.result(timeout=10)  # Max 10s pro URL (Download failed → kein Playwright → max 10s)

                        if scrape_result['success']:
                            tool_results.append(scrape_result)
                            scraped_results.append(scrape_result)
                            debug_print(f"  ✅ {url_short}: {scrape_result['word_count']} Wörter (Score: {item['score']})")
                        else:
                            debug_print(f"  ❌ {url_short}: {scrape_result.get('error', 'Unknown')} (Score: {item['score']})")

                    except Exception as e:
                        debug_print(f"  ❌ {url_short}: Exception: {e} (Score: {item['score']})")

            debug_print(f"✅ Parallel Scraping fertig: {len(scraped_results)}/{len(urls_to_scrape)} erfolgreich")

            # AUTOMATISCHES FALLBACK: Wenn zu wenige Quellen erfolgreich → Scrape weitere URLs
            if mode == "deep" and len(scraped_results) < target_sources and len(urls_to_scrape) < len(rated_urls):
                missing_count = target_sources - len(scraped_results)
                already_scraped_urls = {item['url'] for item in urls_to_scrape}

                # Finde nächste URLs die noch nicht gescraped wurden
                remaining_urls = [
                    item for item in rated_urls
                    if item['url'] not in already_scraped_urls and item['score'] >= 3
                ][:missing_count + 2]  # +2 Reserve für weitere Fehler

                if remaining_urls:
                    debug_print(f"🔄 Fallback: {len(scraped_results)}/{target_sources} erfolgreich → Scrape {len(remaining_urls)} weitere URLs")
                    console_print(f"🔄 Scrape {len(remaining_urls)} zusätzliche URLs (Fallback für Fehler)")

                    # Scrape zusätzliche URLs parallel
                    with ThreadPoolExecutor(max_workers=min(5, len(remaining_urls))) as executor:
                        future_to_item = {
                            executor.submit(scrape_webpage, item['url']): item
                            for item in remaining_urls
                        }

                        for future in as_completed(future_to_item):
                            item = future_to_item[future]
                            url_short = item['url'][:60] + '...' if len(item['url']) > 60 else item['url']

                            try:
                                scrape_result = future.result(timeout=10)

                                if scrape_result['success']:
                                    tool_results.append(scrape_result)
                                    scraped_results.append(scrape_result)
                                    debug_print(f"  ✅ {url_short}: {scrape_result['word_count']} Wörter (Score: {item['score']})")

                                    # Stoppe wenn Ziel erreicht
                                    if len(scraped_results) >= target_sources:
                                        debug_print(f"🎯 Ziel erreicht: {len(scraped_results)}/{target_sources} Quellen")
                                        break
                                else:
                                    debug_print(f"  ❌ {url_short}: {scrape_result.get('error', 'Unknown')} (Score: {item['score']})")

                            except Exception as e:
                                debug_print(f"  ❌ {url_short}: Exception: {e} (Score: {item['score']})")

                    debug_print(f"✅ Fallback-Scraping fertig: {len(scraped_results)} total (Ziel: {target_sources})")

            console_print(f"✅ Web-Scraping fertig: {len(scraped_results)} URLs erfolgreich")

    # 6. Context Building - NUR gescrapte Quellen (keine SearXNG Ergebnisse!)
    # Filtere: Nur tool_results die 'word_count' haben (= erfolgreich gescraped)

    # DEBUG: Zeige ALLE tool_results Details BEVOR Filterung
    debug_print("=" * 80)
    debug_print(f"🔍 SCRAPING RESULTS ANALYSE ({len(tool_results)} total results):")
    for i, result in enumerate(tool_results, 1):
        has_word_count = 'word_count' in result
        is_success = result.get('success', False)
        word_count = result.get('word_count', 0)
        url = result.get('url', 'N/A')[:80]
        debug_print(f"  {i}. {url}")
        debug_print(f"     success={is_success}, has_word_count={has_word_count}, words={word_count}")
    debug_print("=" * 80)

    scraped_only = [r for r in tool_results if 'word_count' in r and r.get('success')]

    debug_print(f"🧩 Baue Context aus {len(scraped_only)} gescrapten Quellen (von {len(tool_results)} total)...")
    console_print(f"🧩 {len(scraped_only)} Quellen mit Inhalt gefunden")

    # DEBUG: Zeige erste 200 Zeichen jeder gescrapten Quelle
    if scraped_only:
        debug_print("=" * 80)
        debug_print("📦 GESCRAPTE INHALTE (Preview erste 200 Zeichen):")
        for i, result in enumerate(scraped_only, 1):
            content = result.get('content', '')
            url = result.get('url', 'N/A')[:80]
            debug_print(f"Quelle {i} - {result.get('word_count', 0)} Wörter:")
            debug_print(f"  URL: {url}")
            debug_print(f"  Content: {content[:200].replace(chr(10), ' ')}...")
            debug_print("-" * 40)
        debug_print("=" * 80)
    else:
        debug_print("⚠️⚠️⚠️ WARNING: scraped_only ist LEER! Keine Daten für Context! ⚠️⚠️⚠️")
        console_print("⚠️ WARNUNG: Keine gescrapten Inhalte gefunden!")

    context = build_context(user_text, scraped_only)
    debug_print(f"📊 Context-Größe: {len(context)} Zeichen, ~{len(context)//4} Tokens")

    # DEBUG: Zeige ANFANG des Contexts (erste 800 Zeichen)
    debug_print("=" * 80)
    debug_print(f"📄 CONTEXT PREVIEW (erste 800 von {len(context)} Zeichen):")
    debug_print("-" * 80)
    debug_print(context[:800])
    if len(context) > 800:
        debug_print(f"\n... [{len(context) - 800} weitere Zeichen] ...")
    debug_print("=" * 80)

    # Console Log: Systemprompt wird erstellt
    console_print("📝 Systemprompt wird erstellt")

    # 7. Erweiterer System-Prompt für Agent-Awareness (MAXIMAL DIREKT!)
    system_prompt = get_system_rag_prompt(
        current_year=time.strftime("%Y"),
        current_date=time.strftime("%d.%m.%Y"),
        context=context
    )

    # 8. AI Inference mit History + System-Prompt
    messages = []

    # History hinzufügen (falls vorhanden)
    for h in history:
        user_msg = h[0].split(" (STT:")[0].split(" (Agent:")[0] if " (STT:" in h[0] or " (Agent:" in h[0] else h[0]
        ai_msg = h[1].split(" (Inferenz:")[0] if " (Inferenz:" in h[1] else h[1]
        messages.extend([
            {'role': 'user', 'content': user_msg},
            {'role': 'assistant', 'content': ai_msg}
        ])

    # System-Prompt + aktuelle User-Frage
    messages.insert(0, {'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_text})

    # DEBUG: Prüfe Größe des System-Prompts
    debug_print(f"📊 System-Prompt Größe: {len(system_prompt)} Zeichen")
    debug_print(f"📊 Anzahl Messages an Ollama: {len(messages)}")
    total_message_size = sum(len(m['content']) for m in messages)
    estimated_tokens = estimate_tokens(messages)
    debug_print(f"📊 Gesamte Message-Größe an Ollama: {total_message_size} Zeichen, ~{estimated_tokens} Tokens")

    # DEBUG: Zeige ALLE Messages die an den Haupt-LLM gehen
    debug_print("=" * 80)
    debug_print(f"📨 MESSAGES an {model_choice} (Haupt-LLM mit RAG):")
    debug_print("-" * 80)
    for i, msg in enumerate(messages):
        debug_print(f"Message {i+1} - Role: {msg['role']}")
        content_preview = msg['content'][:500] if len(msg['content']) > 500 else msg['content']
        if len(msg['content']) > 500:
            debug_print(f"Content (erste 500 Zeichen): {content_preview}")
            debug_print(f"... [noch {len(msg['content']) - 500} Zeichen]")
        else:
            debug_print(f"Content: {content_preview}")
        debug_print("-" * 80)
    debug_print("=" * 80)

    # Console Logs: Stats
    console_print(f"📊 Systemprompt: {len(system_prompt)} Zeichen")
    console_print(f"📊 Messages: {len(messages)}, Gesamt: {total_message_size} Zeichen (~{estimated_tokens} Tokens)")

    # Dynamische num_ctx Berechnung (Haupt-LLM für Web-Recherche mit Research-Daten)
    final_num_ctx = calculate_dynamic_num_ctx(messages, llm_options, is_automatik_llm=False)
    if llm_options and llm_options.get('num_ctx'):
        debug_print(f"🎯 Context Window: {final_num_ctx} Tokens (manuell vom User gesetzt)")
        console_print(f"🪟 Context Window: {final_num_ctx} Tokens (manuell)")
    else:
        debug_print(f"🎯 Context Window: {final_num_ctx} Tokens (dynamisch berechnet, ~{estimated_tokens} Tokens benötigt)")
        console_print(f"🪟 Context Window: {final_num_ctx} Tokens (auto)")

    # Temperature entscheiden: Manual Override oder Auto (immer 0.2 bei Web-Recherche)
    if temperature_mode == 'manual':
        final_temperature = temperature
        debug_print(f"🌡️ Web-Recherche Temperature: {final_temperature} (MANUAL OVERRIDE)")
        console_print(f"🌡️ Temperature: {final_temperature} (manuell)")
    else:
        # Auto: Web-Recherche → Immer Temperature 0.2 (faktisch)
        final_temperature = 0.2
        debug_print(f"🌡️ Web-Recherche Temperature: {final_temperature} (fest, faktisch)")
        console_print(f"🌡️ Temperature: {final_temperature} (auto, faktisch)")

    # Console Log: Haupt-LLM startet (im Agent-Modus)
    console_print(f"🤖 Haupt-LLM startet: {model_choice} (mit {len(scraped_only)} Quellen)")

    inference_start = time.time()
    response = ollama.chat(
        model=model_choice,
        messages=messages,
        options={
            'temperature': final_temperature,  # Adaptive oder Manual Temperature!
            'num_ctx': final_num_ctx  # Dynamisch berechnet oder User-Vorgabe
        }
    )
    inference_time = time.time() - inference_start

    agent_time = time.time() - agent_start

    ai_text = response['message']['content']

    # Console Log: Haupt-LLM fertig
    console_print(f"✅ Haupt-LLM fertig ({inference_time:.1f}s, {len(ai_text)} Zeichen, Agent-Total: {agent_time:.1f}s)")
    console_separator()

    # 9. History mit Agent-Timing + Debug Accordion
    mode_label = "Schnell" if mode == "quick" else "Ausführlich"
    user_with_time = f"{user_text} (STT: {stt_time:.1f}s, Agent: {agent_time:.1f}s, {mode_label}, {len(scraped_only)} Quellen)"

    # Formatiere mit Debug Accordion (Query Reasoning + URL Rating + Final Answer <think>) inkl. Inferenz-Zeiten
    ai_text_formatted = build_debug_accordion(query_reasoning, rated_urls, ai_text, automatik_model, model_choice, query_opt_time, rating_time, inference_time)

    history.append([user_with_time, ai_text_formatted])

    # Speichere Scraping-Daten im Cache (für Nachfragen) OHNE Metadata
    # Metadata wird später asynchron generiert (nach UI-Update, damit User nicht warten muss)
    debug_print(f"🔍 DEBUG Cache-Speicherung: session_id = {session_id}, scraped_only = {len(scraped_only)} Quellen")
    save_cached_research(session_id, user_text, scraped_only, mode, metadata_summary=None)

    debug_print(f"✅ Agent fertig: {agent_time:.1f}s gesamt, {len(ai_text)} Zeichen")
    debug_print("=" * 60)
    debug_print("═" * 80)  # Separator nach jeder Anfrage

    return ai_text, history, inference_time


def chat_interactive_mode(user_text, stt_time, model_choice, automatik_model, voice_choice, speed_choice, enable_tts, tts_engine, history, session_id=None, temperature_mode='auto', temperature=0.2, llm_options=None):
    """
    Automatik-Modus: KI entscheidet selbst, ob Web-Recherche nötig ist

    Args:
        user_text: User-Frage
        stt_time: STT-Zeit (0.0 bei Text-Eingabe)
        model_choice: Haupt-LLM für finale Antwort
        automatik_model: Automatik-LLM für Entscheidung
        voice_choice, speed_choice, enable_tts, tts_engine: Für Fallback zu Eigenes Wissen
        history: Chat History
        session_id: Session-ID für Research-Cache (optional)
        temperature_mode: 'auto' (Intent-Detection) oder 'manual' (fixer Wert)
        temperature: Temperature-Wert (0.0-2.0) - nur bei mode='manual'
        llm_options: Dict mit Ollama-Optionen (num_ctx, etc.) - Optional

    Returns:
        tuple: (ai_text, history, inference_time)
    """

    debug_print("🤖 Automatik-Modus: KI prüft, ob Recherche nötig...")
    console_print("📨 User Request empfangen")

    # ============================================================
    # CODE-OVERRIDE: Explizite Recherche-Aufforderung (Trigger-Wörter)
    # ============================================================
    # Diese Keywords triggern SOFORT neue Recherche ohne KI-Entscheidung!
    explicit_keywords = [
        'recherchiere', 'recherchier',  # "recherchiere!", "recherchier mal"
        'suche im internet', 'such im internet',
        'schau nach', 'schau mal nach',
        'google', 'googel', 'google mal',  # Auch Tippfehler
        'finde heraus', 'find heraus',
        'check das', 'prüfe das'
    ]

    user_lower = user_text.lower()
    if any(keyword in user_lower for keyword in explicit_keywords):
        debug_print(f"⚡ CODE-OVERRIDE: Explizite Recherche-Aufforderung erkannt → Skip KI-Entscheidung!")
        console_print(f"⚡ Explizite Recherche erkannt → Web-Suche startet")
        # Direkt zur Recherche, KEIN Cache-Check!
        return perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options)

    # ============================================================
    # Cache-Check: Baue Metadata für LLM-Entscheidung
    # ============================================================
    cache_metadata = ""
    cache_entry = get_cached_research(session_id)
    cached_sources = cache_entry.get('scraped_sources', []) if cache_entry else []

    if cached_sources:
            cache_age = time.time() - cache_entry.get('timestamp', 0)

            # 🆕 Prüfe ob KI-generierte Metadata verfügbar ist
            metadata_summary = cache_entry.get('metadata_summary')

            if metadata_summary:
                # NEUE VERSION: Nutze KI-generierte semantische Zusammenfassung
                debug_print(f"📝 Nutze KI-generierte Metadata für Entscheidung: {metadata_summary}")
                sources_text = f"🤖 KI-Zusammenfassung der gecachten Quellen:\n\"{metadata_summary}\""
            else:
                # FALLBACK: Nutze URLs + Titel (alte Version)
                debug_print("📝 Nutze Fallback (URLs + Titel) für Entscheidung")
                source_list = []
                for i, source in enumerate(cached_sources[:5], 1):  # Max 5 Quellen zeigen
                    url = source.get('url', 'N/A')
                    title = source.get('title', 'N/A')
                    source_list.append(f"{i}. {url}\n   Titel: \"{title}\"")
                sources_text = "\n".join(source_list)

            cache_metadata = f"""

═══════════════════════════════════════════════════════════

⚠️ GECACHTE RECHERCHE VERFÜGBAR!

Ursprüngliche Frage: "{cache_entry.get('user_text', 'N/A')}"
Cache-Alter: {cache_age:.0f} Sekunden
Anzahl Quellen: {len(cached_sources)}

{sources_text}

═══════════════════════════════════════════════════════════

ENTSCHEIDUNG:
Kann "{user_text}" mit diesen gecachten Quellen beantwortet werden?

✅ JA → <search>context</search> (Cache nutzen!)
   Beispiele: "genauer?", "Quelle 1?", "mehr Details?"

❌ NEIN → `<search>yes</search>` (neue Recherche nötig!)
   Beispiele:
   - Andere Zeitangabe (morgen → Wochenende)
   - Anderes Thema (Wetter → Nobelpreis)
   - Quellen-URLs passen nicht zum neuen Thema
"""
            debug_print(f"💾 Cache vorhanden: {len(cached_sources)} Quellen, {cache_age:.0f}s alt")
            debug_print(f"   Cache-Metadata wird an LLM übergeben ({len(cache_metadata)} Zeichen)")
            debug_print("=" * 60)
            debug_print("📋 CACHE_METADATA CONTENT:")
            debug_print(cache_metadata)
            debug_print("=" * 60)

    # Schritt 1: KI fragen, ob Recherche nötig ist (mit Zeitmessung!)
    decision_prompt = get_decision_making_prompt(
        user_text=user_text,
        cache_metadata=cache_metadata
    )

    # DEBUG: Zeige kompletten Prompt für Diagnose
    debug_print("=" * 60)
    debug_print("📋 DECISION PROMPT an phi3:mini:")
    debug_print("-" * 60)
    debug_print(decision_prompt)
    debug_print("-" * 60)
    debug_print(f"Prompt-Länge: {len(decision_prompt)} Zeichen, ~{len(decision_prompt.split())} Wörter")
    debug_print("=" * 60)

    try:
        # Zeit messen für Entscheidung
        debug_print(f"🤖 Automatik-Entscheidung mit {automatik_model}")

        # ⚠️ WICHTIG: KEINE History für Decision-Making!
        # Die History würde phi3:mini verwirren - es würde jede neue Frage
        # als "Nachfrage" interpretieren wenn vorherige ähnliche Fragen existieren.
        # Beispiel: "Wie wird das Wetter morgen?" nach "Wie ist das Wetter?"
        # → phi3:mini würde <search>context</search> antworten statt <search>yes</search>
        messages = [{'role': 'user', 'content': decision_prompt}]

        # Dynamisches num_ctx basierend auf Automatik-LLM-Limit (50% des Original-Context)
        decision_num_ctx = min(2048, _automatik_llm_context_limit // 2)  # Max 2048 oder 50% des Limits

        # DEBUG: Zeige Messages-Array vollständig
        debug_print("=" * 60)
        debug_print(f"📨 MESSAGES an {automatik_model} (Decision):")
        debug_print("-" * 60)
        for i, msg in enumerate(messages):
            debug_print(f"Message {i+1} - Role: {msg['role']}")
            debug_print(f"Content: {msg['content']}")
            debug_print("-" * 60)
        debug_print(f"Total Messages: {len(messages)}, Temperature: 0.2, num_ctx: {decision_num_ctx} (Automatik-LLM-Limit: {_automatik_llm_context_limit})")
        debug_print("=" * 60)

        decision_start = time.time()
        response = ollama.chat(
            model=automatik_model,
            messages=messages,
            options={
                'temperature': 0.2,  # Niedrig für konsistente yes/no Entscheidungen
                'num_ctx': decision_num_ctx  # Dynamisch basierend auf Model
            }
        )
        decision_time = time.time() - decision_start

        decision = response['message']['content'].strip().lower()

        debug_print(f"🤖 KI-Entscheidung: {decision} (Entscheidung mit {automatik_model}: {decision_time:.1f}s)")

        # ============================================================
        # Parse Entscheidung UND respektiere sie!
        # ============================================================
        if '<search>yes</search>' in decision or ('yes' in decision and '<search>context</search>' not in decision):
            debug_print("✅ KI entscheidet: NEUE Web-Recherche nötig → Cache wird IGNORIERT!")
            console_print(f"🔍 KI-Entscheidung: Web-Recherche JA ({decision_time:.1f}s)")

            # WICHTIG: Cache LÖSCHEN vor neuer Recherche!
            # Die KI hat entschieden dass neue Daten nötig sind (z.B. neue Zeitangabe)
            delete_cached_research(session_id)

            # Jetzt neue Recherche MIT session_id → neue Daten werden gecacht
            return perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options)

        elif '<search>context</search>' in decision or 'context' in decision:
            debug_print("🔄 KI entscheidet: Nachfrage zu vorheriger Recherche → Versuche Cache")
            console_print(f"💾 KI-Entscheidung: Cache nutzen ({decision_time:.1f}s)")
            # Rufe perform_agent_research MIT session_id auf → Cache-Check wird durchgeführt
            # Wenn kein Cache gefunden wird, fällt es automatisch auf normale Recherche zurück
            return perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options)

        else:
            debug_print("❌ KI entscheidet: Eigenes Wissen ausreichend → Kein Agent")
            console_print(f"🧠 KI-Entscheidung: Web-Recherche NEIN ({decision_time:.1f}s)")

            # Jetzt normale Inferenz MIT Zeitmessung
            # Build messages from history (all turns)
            messages = build_messages_from_history(history, user_text)

            # Console: Message Stats
            total_chars = sum(len(m['content']) for m in messages)
            console_print(f"📊 Messages: {len(messages)}, Gesamt: {total_chars} Zeichen (~{total_chars//4} Tokens)")

            # Dynamische num_ctx Berechnung für Eigenes Wissen (Haupt-LLM)
            final_num_ctx = calculate_dynamic_num_ctx(messages, llm_options, is_automatik_llm=False)
            if llm_options and llm_options.get('num_ctx'):
                debug_print(f"🎯 Eigenes Wissen Context Window: {final_num_ctx} Tokens (manuell)")
                console_print(f"🪟 Context Window: {final_num_ctx} Tokens (manual)")
            else:
                estimated_tokens = estimate_tokens(messages)
                debug_print(f"🎯 Eigenes Wissen Context Window: {final_num_ctx} Tokens (dynamisch, ~{estimated_tokens} Tokens benötigt)")
                console_print(f"🪟 Context Window: {final_num_ctx} Tokens (auto)")

            # Temperature entscheiden: Manual Override oder Auto (Intent-Detection)
            if temperature_mode == 'manual':
                final_temperature = temperature
                debug_print(f"🌡️ Eigenes Wissen Temperature: {final_temperature} (MANUAL OVERRIDE)")
                console_print(f"🌡️ Temperature: {final_temperature} (manual)")
            else:
                # Auto: Intent-Detection für Eigenes Wissen
                own_knowledge_intent = detect_query_intent(user_text, automatik_model)
                final_temperature = get_temperature_for_intent(own_knowledge_intent)
                debug_print(f"🌡️ Eigenes Wissen Temperature: {final_temperature} (Intent: {own_knowledge_intent})")
                console_print(f"🌡️ Temperature: {final_temperature} (auto, {own_knowledge_intent})")

            # Console: LLM starts
            console_print(f"🤖 Haupt-LLM startet: {model_choice}")

            # Zeit messen für finale Inferenz
            inference_start = time.time()
            response = ollama.chat(
                model=model_choice,
                messages=messages,
                options={
                    'temperature': final_temperature,  # Adaptive oder Manual Temperature!
                    'num_ctx': final_num_ctx  # Dynamisch berechnet oder User-Vorgabe
                }
            )
            inference_time = time.time() - inference_start

            ai_text = response['message']['content']

            # Console: LLM finished
            console_print(f"✅ Haupt-LLM fertig ({inference_time:.1f}s, {len(ai_text)} Zeichen)")
            console_separator()

            # User-Text mit Timing (Entscheidungszeit + Inferenzzeit)
            if stt_time > 0:
                user_with_time = f"{user_text} (STT: {stt_time:.1f}s, Entscheidung: {decision_time:.1f}s, Inferenz: {inference_time:.1f}s)"
            else:
                user_with_time = f"{user_text} (Entscheidung: {decision_time:.1f}s, Inferenz: {inference_time:.1f}s)"

            # Formatiere <think> Tags als Collapsible (falls vorhanden) mit Modell-Name und Inferenz-Zeit
            ai_text_formatted = format_thinking_process(ai_text, model_name=model_choice, inference_time=inference_time)

            history.append([user_with_time, ai_text_formatted])
            debug_print(f"✅ AI-Antwort generiert ({len(ai_text)} Zeichen, Inferenz: {inference_time:.1f}s)")
            debug_print("═" * 80)  # Separator nach jeder Anfrage
            console_separator()  # Separator auch in Console

            return ai_text, history, inference_time

    except Exception as e:
        debug_print(f"⚠️ Fehler bei Automatik-Modus Entscheidung: {e}")
        debug_print("   Fallback zu Eigenes Wissen")
        # Fallback: Verwende standard chat function (muss importiert werden in main)
        raise  # Re-raise to be handled by caller
