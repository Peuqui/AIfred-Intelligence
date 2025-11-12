"""
RAG Context Builder - Build context from cache for Retrieval-Augmented Generation

Handles:
- Querying cache for potentially relevant entries
- LLM-based relevance filtering
- Context assembly for RAG-enhanced answers
"""

import string
from typing import Dict, Optional
from .logging_utils import log_message
from .prompt_loader import load_prompt


async def build_rag_context(
    user_query: str,
    cache,
    automatik_llm_client,
    automatik_model: str,
    max_candidates: int = 5
) -> Optional[Dict]:
    """
    Build RAG context from cache entries using LLM-based relevance filtering.

    Args:
        user_query: Current user question
        cache: VectorCache instance
        automatik_llm_client: LLM client for relevance checking
        automatik_model: Model name for Automatik-LLM
        max_candidates: Max cache entries to check

    Returns:
        Dict with:
        {
            'context': str - formatted context for LLM,
            'sources': List[Dict] - relevant cache entries,
            'num_checked': int - total candidates checked,
            'num_relevant': int - relevant entries found
        }
        OR None if no relevant context found
    """

    log_message("🔍 Checking cache for RAG context...")

    # Query cache for potential RAG candidates (distance 0.5-1.2)
    rag_candidates = await cache.query_for_rag(user_query, n_results=max_candidates)

    if not rag_candidates:
        log_message("❌ No RAG candidates found in cache")
        return None

    log_message(f"📊 Found {len(rag_candidates)} RAG candidates, checking relevance...")

    # Filter candidates by relevance using Automatik-LLM
    relevant_entries = []

    # Extract keywords from current query for quick heuristic check
    # Remove punctuation first, then split
    query_cleaned = user_query.lower().translate(str.maketrans('', '', string.punctuation))
    current_keywords = set(query_cleaned.split())
    # Remove common stop words
    stop_words = {'was', 'ist', 'der', 'die', 'das', 'ein', 'eine', 'im', 'in', 'nach', 'suche', 'internet', 'zu', 'von', 'für'}
    current_keywords = current_keywords - stop_words

    for idx, candidate in enumerate(rag_candidates, 1):
        cached_query = candidate['query']
        cached_answer = candidate['answer']
        distance = candidate['distance']

        log_message(f"🔍 RAG Candidate {idx}/{len(rag_candidates)}: '{cached_query[:50]}...' (d={distance:.3f})")

        # Quick heuristic check: Do queries share significant keywords?
        cached_cleaned = cached_query.lower().translate(str.maketrans('', '', string.punctuation))
        cached_keywords = set(cached_cleaned.split()) - stop_words
        shared_keywords = current_keywords & cached_keywords

        # If they share at least one significant keyword, likely relevant
        has_shared_keyword = len(shared_keywords) > 0
        if has_shared_keyword:
            log_message(f"  🎯 Keyword match: {shared_keywords}")

        # Create preview of cached content (first 300 chars)
        content_preview = cached_answer[:300] + "..." if len(cached_answer) > 300 else cached_answer

        # Load relevance check prompt with current date
        import time
        current_date = time.strftime("%d.%m.%Y")
        relevance_prompt = load_prompt(
            'rag_relevance_check',
            cached_query=cached_query,
            cached_content_preview=content_preview,
            current_query=user_query,
            current_date=current_date
        )

        # Ask Automatik-LLM: Is this relevant?
        try:
            response = await automatik_llm_client.chat(
                model=automatik_model,
                messages=[{'role': 'user', 'content': relevance_prompt}],
                options={
                    'temperature': 0.1,  # Deterministic
                    'num_ctx': 2048,
                    'enable_thinking': False  # Fast decisions, no reasoning needed
                }
            )

            # LLMResponse object has .text attribute, not dict
            decision = response.text.strip().lower()

            # Decision: LLM says relevant OR shared keyword heuristic triggered
            is_relevant_llm = 'relevant' in decision and 'not_relevant' not in decision
            is_relevant = is_relevant_llm or has_shared_keyword

            if is_relevant:
                # Relevant! Include in context
                relevant_entries.append(candidate)
                reason = "LLM" if is_relevant_llm else "Keyword"
                log_message(f"  ✅ RELEVANT via {reason} | LLM said: '{decision}'")
            else:
                log_message(f"  ❌ NOT RELEVANT | LLM said: '{decision}' | No keyword match")

        except Exception as e:
            log_message(f"  ⚠️ Relevance check failed: {e}, skipping candidate")
            continue

    # If no relevant entries found, return None
    if not relevant_entries:
        log_message(f"❌ No relevant context found (checked {len(rag_candidates)} candidates)")
        return None

    # Build formatted context from relevant entries
    context_parts = []
    for i, entry in enumerate(relevant_entries, 1):
        context_parts.append(f"""
## Gecachte Information {i}
**Ursprüngliche Frage:** {entry['query']}
**Antwort:** {entry['answer']}
""")

    formatted_context = "\n".join(context_parts)

    log_message(f"✅ RAG context built: {len(relevant_entries)} relevant entries (from {len(rag_candidates)} candidates)")

    return {
        'context': formatted_context,
        'sources': relevant_entries,
        'num_checked': len(rag_candidates),
        'num_relevant': len(relevant_entries)
    }
