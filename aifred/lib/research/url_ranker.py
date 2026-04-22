"""
URL Ranker - LLM-based relevance ranking for search results

Uses Automatik-LLM to rank URLs by relevance before scraping.
This improves scraping efficiency by prioritizing the most relevant sources.
"""

import re
import logging
from typing import Dict, List, Tuple, Optional

from ..prompt_loader import load_prompt
from ..logging_utils import log_message
from ..formatting import format_number
from ..context_manager import strip_thinking_blocks
from ..config import DEBUG_LOG_RAW_MESSAGES

logger = logging.getLogger(__name__)


def format_url_list_for_ranking(
    urls: List[str],
    titles: List[str],
    snippets: List[str]
) -> str:
    """
    Format URL list for LLM ranking prompt.

    Args:
        urls: List of URLs
        titles: List of titles (same order as URLs)
        snippets: List of snippets (same order as URLs)

    Returns:
        Formatted string with index, title, and snippet for each URL
    """
    lines = []
    for i, (url, title, snippet) in enumerate(zip(urls, titles, snippets)):
        # Truncate long snippets
        snippet_short = snippet[:200] + "..." if len(snippet) > 200 else snippet
        # Clean up whitespace
        snippet_short = " ".join(snippet_short.split())
        lines.append(f"{i}: {title} - {snippet_short}")

    return "\n".join(lines)


def parse_ranking_response(response: str, total_urls: int, top_n: int) -> List[int]:
    """
    Parse LLM ranking response into list of indices.

    Args:
        response: LLM response (expected: "3,7,1,12,5,8,2")
        total_urls: Total number of URLs (for validation)
        top_n: Expected number of indices

    Returns:
        List of valid indices, ordered by relevance
    """
    # Extract numbers from response
    numbers = re.findall(r'\d+', response)

    indices = []
    seen = set()

    for num_str in numbers:
        try:
            idx = int(num_str)
            # Validate: within range and not duplicate
            if 0 <= idx < total_urls and idx not in seen:
                indices.append(idx)
                seen.add(idx)

                # Stop when we have enough
                if len(indices) >= top_n:
                    break
        except ValueError:
            continue

    return indices


async def rank_urls_by_relevance(
    user_question: str,
    urls: List[str],
    titles: List[str],
    snippets: List[str],
    automatik_llm_client,
    automatik_model: str,
    llm_history: List[Dict],
    top_n: int = 7,
    llm_options: Optional[Dict] = None,
    automatik_num_ctx: Optional[int] = None
) -> Tuple[List[str], List[int], str]:
    """
    Rank URLs by relevance using Automatik-LLM.

    Args:
        user_question: User's question for relevance assessment
        urls: List of URLs from search results
        titles: List of titles (same order as URLs)
        snippets: List of snippets (same order as URLs)
        automatik_llm_client: Automatik LLM client
        automatik_model: Automatik model name
        top_n: Number of top URLs to return
        llm_options: Optional LLM options

    Returns:
        Tuple of (ranked_urls, ranking_indices, debug_summary)
        - ranked_urls: Top N URLs ordered by relevance
        - ranking_indices: Original indices in ranked order
        - debug_summary: Human-readable summary for debug console
    """
    # Skip ranking if not enough URLs
    if len(urls) <= top_n:
        log_message(f"⏭️ URL ranking skipped: Only {len(urls)} URLs (target: {top_n})")
        return urls, list(range(len(urls))), ""

    # Ensure we have matching data
    if len(urls) != len(titles) or len(urls) != len(snippets):
        log_message(f"⚠️ URL ranking: Mismatched data lengths (urls={len(urls)}, titles={len(titles)}, snippets={len(snippets)})")
        # Pad with empty strings if needed
        titles = titles + [""] * (len(urls) - len(titles))
        snippets = snippets + [""] * (len(urls) - len(snippets))

    log_message(f"🎯 URL Ranking: {len(urls)} URLs → Top {top_n}")

    # Format URL list first
    url_list_formatted = format_url_list_for_ranking(urls, titles, snippets)

    # Format last 6 history messages as context (3 user/assistant pairs)
    conversation_context = ""
    if llm_history:
        recent = [m for m in llm_history if m.get("role") in ("user", "assistant")][-6:]
        if recent:
            lines = ["CONVERSATION CONTEXT (recent messages):"]
            for m in recent:
                role = "User" if m["role"] == "user" else "Assistant"
                content = str(m.get("content", ""))[:300]
                lines.append(f"{role}: {content}")
            conversation_context = "\n".join(lines) + "\n"

    # Load and format prompt (always EN - output is numeric indices)
    prompt = load_prompt(
        "automatik/url_ranking",
        lang="en",
        user_question=user_question,
        url_list=url_list_formatted,
        top_n=top_n,
        conversation_context=conversation_context,
    )

    # Call Automatik-LLM
    try:
        messages = [{"role": "user", "content": prompt}]

        ranking_options: Dict = {
            "temperature": 0.0,  # Deterministic ranking
            "enable_thinking": False,  # Automatik-Task: Always disable thinking
        }
        if automatik_num_ctx is not None:
            ranking_options["num_ctx"] = automatik_num_ctx

        response = await automatik_llm_client.chat(
            model=automatik_model,
            messages=messages,
            options=ranking_options
        )

        # Extract response text from LLMResponse dataclass
        response_text = response.text if hasattr(response, 'text') else str(response)
        # Strip thinking blocks — models like GPT-OSS always reason regardless of enable_thinking
        response_text = strip_thinking_blocks(response_text).strip()

        # RAW Debug Output (controlled by DEBUG_LOG_RAW_MESSAGES flag)
        if DEBUG_LOG_RAW_MESSAGES:
            log_message("=" * 60)
            log_message("🎯 URL RANKING RAW RESPONSE:")
            log_message(f"Model: {automatik_model}")
            log_message(f"Input: {format_number(len(urls))} URLs → Top {format_number(top_n)}")
            log_message(f"Response: {response_text}")
            log_message("=" * 60)

        # Parse response
        ranking_indices = parse_ranking_response(response_text, len(urls), top_n)

        if not ranking_indices:
            log_message("⚠️ Could not parse ranking, using original order")
            ranking_indices = list(range(min(top_n, len(urls))))

        # Build ranked URL list
        ranked_urls = [urls[i] for i in ranking_indices]

        # Log ranked results
        log_message(f"✅ Ranked: {format_number(len(ranked_urls))} URLs selected")
        for i, (idx, url) in enumerate(zip(ranking_indices, ranked_urls), 1):
            url_short = url[:60] + "..." if len(url) > 60 else url
            log_message(f"   {i}. [#{idx}] {url_short}")

        # Build debug summary for UI console
        debug_summary = f"Ranked indices: {','.join(map(str, ranking_indices))}"

        return ranked_urls, ranking_indices, debug_summary

    except Exception as e:
        from ...backends.base import BackendConnectionError
        if isinstance(e, BackendConnectionError):
            raise
        log_message(f"❌ URL ranking error: {e}")
        # Return first N URLs on error
        return urls[:top_n], list(range(min(top_n, len(urls)))), f"Error: {e}"
