"""
Context Builder - Build structured context for LLM

Extracted from agent_tools.py for better modularity.
"""

import logging
from typing import Dict, List

from ..config import MAX_RAG_CONTEXT_TOKENS, MAX_WORDS_PER_SOURCE, CHARS_PER_TOKEN
from ..logging_utils import log_message

# Logging Setup
logger = logging.getLogger(__name__)

def build_context(user_text: str, tool_results: List[Dict], max_context_tokens: int = None) -> str:
    """
    Baut strukturierten Kontext für AI aus Tool-Ergebnissen

    INTELLIGENT CONTEXT MANAGEMENT:
    - Priorisiert KURZE, AKTUELLE Quellen
    - Limitiert LANGE Quellen (Wikipedia) auf MAX_WORDS_PER_SOURCE
    - Sortiert: News-Artikel > Wikipedia/Lange Artikel

    Args:
        user_text: User-Frage
        tool_results: Liste von Recherche-Ergebnissen
        max_context_tokens: Optional, falls None wird MAX_RAG_CONTEXT_TOKENS aus config.py verwendet
    """
    if max_context_tokens is None:
        max_context_tokens = MAX_RAG_CONTEXT_TOKENS
    context_header = f"# User-Frage\n{user_text}\n\n# Recherche-Ergebnisse\n\n"
    context_footer = ""  # Kein Footer mehr - System-Prompt enthält bereits alle Anweisungen

    successful_results = [r for r in tool_results if r.get('success', False)]

    if not successful_results:
        context = context_header + "*Keine erfolgreichen Recherche-Ergebnisse gefunden.*\n\n" + context_footer
        return context

    # INTELLIGENTE SORTIERUNG: News > Wikipedia
    def prioritize_source(result):
        url = result.get('url', '').lower()
        word_count = result.get('word_count', 0)

        # Wikipedia = niedrige Priorität (große Zahl = später)
        if 'wikipedia.org' in url:
            return 1000
        # Kurze Artikel (News) = hohe Priorität (kleine Zahl = früher)
        elif word_count < 5000:
            return word_count
        # Lange Artikel = niedrige Priorität
        else:
            return 500 + word_count

    successful_results.sort(key=prioritize_source)

    # Baue Context mit intelligentem Limiting
    sources_text = []
    total_tokens = 0
    max_source_tokens = max_context_tokens - 1000  # Reserve für Header/Footer

    for i, result in enumerate(successful_results, 1):
        source = result.get('source', 'Unbekannt')
        content = result.get('content', result.get('abstract', ''))
        url = result.get('url', '')
        title = result.get('title', '')
        word_count = result.get('word_count', 0)

        # Limitiere LANGE Quellen (Wikipedia) - Wert aus config.py
        if word_count > MAX_WORDS_PER_SOURCE:
            # Schneide Content ab (Grob: 1 Token = 0.75 Wörter)
            words = content.split()
            content = ' '.join(words[:MAX_WORDS_PER_SOURCE])
            log_message(f"⚠️ Quelle {i} ({url}) gekürzt: {word_count} → {MAX_WORDS_PER_SOURCE} Wörter")

        # Format source
        source_text = ""
        if title:
            source_text += f"## Quelle {i}: {title}\n"
        else:
            source_text += f"## Quelle {i}: {source}\n"

        if url:
            source_text += f"**🔗 URL:** {url}\n\n"

        if content:
            source_text += f"{content}\n\n"

        source_text += "---\n\n"

        # Token-Check - Ratio aus config.py
        source_tokens = len(source_text) // CHARS_PER_TOKEN
        if total_tokens + source_tokens > max_source_tokens:
            log_message(f"⚠️ Context-Limit erreicht bei Quelle {i}, stoppe hier")
            break

        sources_text.append(source_text)
        total_tokens += source_tokens

    context = context_header + ''.join(sources_text) + context_footer
    estimated_tokens = len(context) // CHARS_PER_TOKEN
    logger.info(f"Context gebaut: {len(context)} Zeichen (~{estimated_tokens} Tokens), {len(sources_text)} Quellen")
    log_message(f"📦 Context gebaut: {len(context)} Zeichen (~{estimated_tokens} Tokens), {len(sources_text)} Quellen")

    return context


# ============================================================
# TOOL REGISTRY
# ============================================================

