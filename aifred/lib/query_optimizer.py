"""
Query Optimizer - Search Query Optimization with LLM

Optimizes user queries for better web search results:
- Extracts key search terms
- Adds temporal context (current year)
- Handles follow-up questions with history
"""

import re
from datetime import datetime
from typing import Optional, Tuple, List
from .logging_utils import log_message
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
    log_message("=" * 60)
    log_message("📋 QUERY OPTIMIZATION PROMPT:")
    log_message("-" * 60)
    log_message(prompt)
    log_message("-" * 60)
    log_message(f"Prompt-Länge: {len(prompt)} Zeichen, ~{len(prompt.split())} Wörter")
    log_message("=" * 60)

    try:
        log_message(f"🔍 Query-Optimierung mit {automatik_model}")
        log_message("🔧 Query-Optimierung startet")

        # Baue Messages mit History (letzte 2-3 Turns für Kontext bei Nachfragen)
        messages = build_messages_from_history(history, prompt, max_turns=3)

        # DEBUG: Zeige Messages-Array vollständig
        log_message("=" * 60)
        log_message(f"📨 MESSAGES an {automatik_model} (Query-Opt):")
        log_message("-" * 60)
        for i, msg in enumerate(messages):
            log_message(f"Message {i+1} - Role: {msg['role']}")
            log_message(f"Content: {msg['content']}")
            log_message("-" * 60)

        # Dynamisches num_ctx basierend auf Automatik-LLM-Limit (100% für Query-Opt wegen History)
        query_num_ctx = min(8192, automatik_llm_context_limit)  # Max 8192 oder volles Limit

        log_message(f"Total Messages: {len(messages)}, Temperature: 0.3, num_ctx: {query_num_ctx} (Automatik-LLM-Limit: {automatik_llm_context_limit})")
        log_message("=" * 60)

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

        # Tokens/s Output (wie in Gradio-Version)
        if hasattr(response, 'tokens_per_second') and response.tokens_per_second:
            tokens_per_sec = int(response.tokens_per_second)
            log_message(f"⚡ {tokens_per_sec} t/s")
            log_message(f"⚡ Query-Opt Performance: {tokens_per_sec} t/s")

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
            log_message(f"   ⏰ Temporaler Kontext ergänzt: {current_year}")

        # Regel 2: "X vs Y" → + aktuelles Jahr (falls nicht schon vorhanden)
        elif any(kw in query_lower for kw in comparison_keywords) and current_year not in optimized_query:
            optimized_query += f" {current_year}"
            log_message(f"   ⚖️ Vergleichs-Kontext ergänzt: {current_year}")

        log_message("🔍 Query-Optimierung:")
        log_message(f"   Original: {user_text[:80]}{'...' if len(user_text) > 80 else ''}")
        log_message(f"   Optimiert: {optimized_query}")

        # Return: Tuple (optimized_query, reasoning)
        return (optimized_query, think_content)

    except Exception as e:
        log_message(f"⚠️ Fehler bei Query-Optimierung: {e}")
        log_message("   Fallback zu Original-Query")
        return (user_text, None)
