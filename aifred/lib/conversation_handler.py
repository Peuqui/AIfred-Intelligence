"""
Conversation Handler - Vision Pipeline and Search Query Generation

This module contains:
- Vision pipeline (OCR, structured data extraction, image Q&A)
- Search query generation via Automatik-LLM
- Helper functions (JSON repair, history selection)

The main chat flow is handled by multi_agent.py (unified agent path).
"""

from typing import Any, Dict, List, Optional

from .timer import Timer
from .logging_utils import log_message, log_raw_messages
# Cache system removed - will be replaced with Vector DB
from .context_manager import estimate_tokens, strip_thinking_blocks
import json
import re


def _select_history_for_context(
    llm_history: List[Dict[str, str]],
    effective_ctx: int
) -> List[Dict[str, str]]:
    """
    Build a history subset that fits within a token budget (2/3 of effective_ctx).

    Iterates from newest to oldest entries, adding until the token limit is reached.

    Args:
        llm_history: Full LLM history (list of role/content dicts)
        effective_ctx: Effective context window size in tokens

    Returns:
        Selected history entries (chronological order), fitting within budget.
    """
    max_history_tokens = (effective_ctx * 2) // 3
    history_tokens = 0
    selected_history: List[Dict[str, str]] = []

    for entry in reversed(llm_history):
        entry_tokens = estimate_tokens([entry])
        if history_tokens + entry_tokens > max_history_tokens:
            break
        selected_history.insert(0, entry)
        history_tokens += entry_tokens

    return selected_history


def _repair_json(s: str) -> str:
    """Fix common LLM JSON errors like ]] instead of ]}."""
    # Fix double brackets: ]] → ]}
    s = re.sub(r'\]\](?=\s*$)', ']}', s)
    s = re.sub(r'\]\](?=\s*})', ']}', s)
    # Fix missing closing brace
    if s.count('{') > s.count('}'):
        s = s + '}'
    return s


def _parse_json_with_recovery(response: str, context: str) -> Any:
    """
    Parse a JSON string with recovery strategies for common LLM errors.

    Tries: direct parse -> repair -> extract from text -> raise ValueError.

    Args:
        response: Raw response string (potentially containing JSON)
        context: Description for error messages (e.g., "query_generation")

    Returns:
        Parsed JSON as dict.

    Raises:
        ValueError: If all parse attempts fail.
    """
    try:
        return json.loads(response)
    except json.JSONDecodeError as e1:
        log_message(f"⚠️ JSON parse error ({context}): {e1}")
        # Try repair
        repaired = _repair_json(response)
        log_message(f"🔧 Attempting JSON repair: {repaired}")
        try:
            result = json.loads(repaired)
            log_message("✅ JSON repair successful")
            return result
        except json.JSONDecodeError:
            # Try extract JSON from text
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                try:
                    extracted = _repair_json(json_match.group())
                    result = json.loads(extracted)
                    log_message("✅ JSON extraction + repair successful")
                    return result
                except json.JSONDecodeError as e3:
                    error_msg = f"JSON parse failed after all repair attempts ({context}): {e3}\nRaw: {response}"
                    log_message(f"❌ {error_msg}")
                    raise ValueError(error_msg)
            else:
                error_msg = f"No JSON found in response ({context}): {response}"
                log_message(f"❌ {error_msg}")
                raise ValueError(error_msg)


async def generate_web_search_queries(
    user_text: str,
    automatik_llm_client,
    automatik_model: str,
    has_images: bool = False,
    vision_json_context: Optional[Dict] = None,
    detected_language: str = "de",
    llm_history: Optional[List[Dict[str, str]]] = None,
    automatik_num_ctx: Optional[int] = None
) -> Dict:
    """
    Generate search queries WITHOUT deciding if web search is needed.

    Used in explicit web search modes (quick/deep) where the user has
    already decided that web search is needed. This function ONLY generates
    3 optimized search queries.

    Args:
        user_text: User query text
        automatik_llm_client: LLM client for Automatik-Model
        automatik_model: Automatik-LLM model name (e.g., "qwen3:4b")
        has_images: Whether the message includes image(s)
        vision_json_context: Structured data extracted from images
        detected_language: Language from Intent Detection ("de" or "en")
        llm_history: Optional chat history for context

    Returns:
        Dict with keys:
        - "queries": List[str] (3 optimized queries)
        - "generation_time": float (LLM call duration in seconds)
        - "raw_response": str (for debugging)
    """
    from .config import AUTOMATIK_LLM_NUM_CTX
    from ..backends.base import LLMMessage
    from .prompt_loader import get_query_generation_prompt

    # Get the query-only prompt
    prompt = get_query_generation_prompt(
        user_text=user_text,
        has_images=has_images,
        vision_json=vision_json_context,
        lang=detected_language
    )

    # DEBUG: Show complete prompt
    log_message("=" * 60)
    log_message("📋 QUERY GENERATION PROMPT:")
    log_message("-" * 60)
    log_message(prompt)
    log_message("-" * 60)
    log_message(f"Prompt length: {len(prompt)} chars, ~{len(prompt.split())} words")
    log_message("=" * 60)

    # Build messages with optional history context
    messages: List[LLMMessage] = []

    effective_ctx = automatik_num_ctx or AUTOMATIK_LLM_NUM_CTX
    if llm_history and len(llm_history) > 0:
        selected_history = _select_history_for_context(llm_history, effective_ctx)

        for entry in selected_history:
            role = entry.get("role", "user")
            content = entry.get("content", "")
            if content.startswith("[AIFRED]:"):
                content = content[9:].strip()
            messages.append(LLMMessage(role=role, content=content))

        if selected_history:
            history_tokens = estimate_tokens(selected_history)
            log_message(f"📜 History context: {len(selected_history)} entries, ~{int(history_tokens)} tokens")

    messages.append(LLMMessage(role="user", content=prompt))

    options: Dict = {
        "temperature": 0.3,  # Slightly higher for query diversity
        "enable_thinking": False,
        "format": "json"
    }
    if automatik_num_ctx is not None:
        options["num_ctx"] = automatik_num_ctx

    generation_timer = Timer()

    log_raw_messages("AUTOMATIK-LLM (generate_web_search_queries)", messages, estimate_tokens)

    try:
        response = await automatik_llm_client.chat(
            model=automatik_model,
            messages=messages,
            options=options
        )
        raw_response = strip_thinking_blocks(response.text).strip()
        generation_time = generation_timer.elapsed()

        log_message("=" * 60)
        log_message("📝 RAW QUERY GENERATION RESPONSE:")
        log_message("-" * 60)
        log_message(raw_response)
        log_message("-" * 60)
        log_message(f"Response length: {len(raw_response)} chars, time: {generation_time:.2f}s")
        log_message("=" * 60)

        # Parse JSON response
        result = _parse_json_with_recovery(raw_response, "query_generation")

        queries = result.get("queries", [])

        # Validate: must have at least 1 query - NO FALLBACK, raise error
        if not queries:
            error_msg = "LLM returned no queries (parsing error?)"
            log_message(f"❌ {error_msg}")
            raise ValueError(error_msg)

        log_message(f"✅ Query Generation: {len(queries)} queries")

        return {
            "queries": queries,
            "generation_time": generation_time,
            "raw_response": raw_response
        }

    except Exception as e:
        generation_time = generation_timer.elapsed()
        log_message(f"❌ Query generation failed ({generation_time:.2f}s): {e}")
        # NO FALLBACK - re-raise to make error visible
        raise
