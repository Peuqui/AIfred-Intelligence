"""
RAG Context Builder - Build context from cache for Retrieval-Augmented Generation

Handles:
- Querying cache for potentially relevant entries
- LLM-based relevance filtering
- Context assembly for RAG-enhanced answers
"""

from typing import List, Dict, Optional
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

    log_message(f"🔍 Checking cache for RAG context...")

    # Query cache for potential RAG candidates (distance 0.5-1.2)
    rag_candidates = await cache.query_for_rag(user_query, n_results=max_candidates)

    if not rag_candidates:
        log_message("❌ No RAG candidates found in cache")
        return None

    log_message(f"📊 Found {len(rag_candidates)} RAG candidates, checking relevance...")

    # Filter candidates by relevance using Automatik-LLM
    relevant_entries = []

    # Extract keywords from current query for quick heuristic check
    current_keywords = set(user_query.lower().split())
    # Remove common stop words
    stop_words = {'was', 'ist', 'der', 'die', 'das', 'ein', 'eine', 'im', 'in', 'nach', 'suche', 'internet', 'zu', 'von', 'für'}
    current_keywords = current_keywords - stop_words

    for candidate in rag_candidates:
        cached_query = candidate['query']
        cached_answer = candidate['answer']
        distance = candidate['distance']

        # Quick heuristic check: Do queries share significant keywords?
        cached_keywords = set(cached_query.lower().split()) - stop_words
        shared_keywords = current_keywords & cached_keywords

        # If they share at least one significant keyword, likely relevant
        has_shared_keyword = len(shared_keywords) > 0
        if has_shared_keyword:
            log_message(f"  🎯 Quick match via keyword: {shared_keywords} (d={distance:.3f})")

        # Create preview of cached content (first 300 chars)
        content_preview = cached_answer[:300] + "..." if len(cached_answer) > 300 else cached_answer

        # Load relevance check prompt
        relevance_prompt = load_prompt(
            'rag_relevance_check',
            cached_query=cached_query,
            cached_content_preview=content_preview,
            current_query=user_query
        )

        # Log prompt for first candidate (debugging)
        if candidate == rag_candidates[0]:
            log_message(f"  📄 Relevance Prompt Preview: cached='{cached_query[:40]}...' current='{user_query[:40]}...'")
            log_message(f"  📝 Full Prompt (first 500 chars):\n{relevance_prompt[:500]}...")

        # Ask Automatik-LLM: Is this relevant?
        try:
            response = await automatik_llm_client.chat(
                model=automatik_model,
                messages=[{'role': 'user', 'content': relevance_prompt}],
                options={'temperature': 0.1, 'num_ctx': 2048}  # Deterministic
            )

            # LLMResponse object has .text attribute, not dict
            decision = response.text.strip().lower()

            # Log full LLM decision for debugging
            log_message(f"  🤖 LLM Decision: '{decision}' for query '{cached_query[:50]}...'")

            # Decision: LLM says relevant OR shared keyword heuristic triggered
            is_relevant_llm = 'relevant' in decision and 'not_relevant' not in decision
            is_relevant = is_relevant_llm or has_shared_keyword

            if is_relevant:
                # Relevant! Include in context
                relevant_entries.append(candidate)
                reason = "LLM" if is_relevant_llm else "keyword match"
                log_message(f"  ✅ Relevant via {reason} (d={distance:.3f}): {cached_query[:60]}...")
            else:
                log_message(f"  ❌ Not relevant (d={distance:.3f}): {cached_query[:60]}...")

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
