"""
Query Optimizer - Search Query Optimization with LLM

Optimizes user queries for better web search results:
- Extracts key search terms
- Adds temporal context (current year)
- Handles follow-up questions with history
"""

import re
from datetime import datetime
from typing import Optional, Tuple, List, Dict
from .logging_utils import debug_print, console_print
from .prompt_loader import get_query_optimization_prompt
from .message_builder import build_messages_from_history


# Compiled Regex Pattern
THINK_TAG_PATTERN = re.compile(r'<think>(.*?)</think>', re.DOTALL)


async def optimize_search_query(
    user_text: str,
    automatik_model: str,
    history: Optional[List],
    llm_client,
    automatik_llm_context_limit: int
) -> Tuple[str, Optional[str]]:
    """
    Extrahiert optimierte Suchbegriffe aus User-Frage

    Args:
        user_text: Volle User-Frage (kann lang sein)
        automatik_model: Automatik-LLM für Query-Optimierung
        history: Chat History (für Kontext bei Nachfragen)
        llm_client: LLMClient instance
        automatik_llm_context_limit: Context limit for automatik LLM

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
        query_num_ctx = min(8192, automatik_llm_context_limit)  # Max 8192 oder volles Limit

        debug_print(f"Total Messages: {len(messages)}, Temperature: 0.3, num_ctx: {query_num_ctx} (Automatik-LLM-Limit: {automatik_llm_context_limit})")
        debug_print("=" * 60)

        # Async LLM call
        response = await llm_client.chat(
            model=automatik_model,
            messages=messages,
            options={
                'temperature': 0.3,  # Leicht kreativ für Keywords, aber stabil
                'num_ctx': query_num_ctx
            }
        )
        raw_response = response.text.strip()

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
        debug_print("   Fallback zu Original-Query")
        return (user_text, None)
