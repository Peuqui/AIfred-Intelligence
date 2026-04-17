"""
Context Manager - Token and Context Window Management

Handles context limits and token estimation for LLMs:
- Query model context limits from backends (on-demand, no caching)
- Calculate optimal num_ctx for requests
- Token estimation for messages (using HuggingFace tokenizers)
- History compression (summarize_history_if_needed)
"""

import re
import asyncio
from .timer import Timer
from typing import Any, Dict, List, Optional, AsyncIterator
from .logging_utils import log_message, log_raw_messages, console_separator
from .prompt_loader import load_prompt
from .formatting import format_number
from .config import (
    HISTORY_COMPRESSION_TRIGGER,
    HISTORY_COMPRESSION_TARGET,
    HISTORY_SUMMARY_RATIO,
    HISTORY_SUMMARY_MIN_TOKENS,
    HISTORY_SUMMARY_TOLERANCE,
    HISTORY_MAX_SUMMARIES,
    HISTORY_SUMMARY_MAX_RATIO,
    HISTORY_SUMMARY_TEMPERATURE,
    XTTS_VRAM_MB,
    MOSS_TTS_VRAM_MB,
    VRAM_CONTEXT_RATIO_DENSE,
    VRAM_CONTEXT_RATIO_MOE
)
from .gpu_utils import is_moe_model

# Global tokenizer cache (model_name -> tokenizer)
_tokenizer_cache = {}


def parse_model_size_from_name(model_name: str) -> float:
    """
    Extract model size (in billions of parameters) from model name.

    Supports common naming patterns:
    - "qwen3:8b" → 8.0
    - "gemma2:2b" → 2.0
    - "llama3.1:70b-instruct-q4_K_M" → 70.0
    - "phi3:3.8b" → 3.8
    - "mistral:7b" → 7.0
    - "qwen2.5-coder:32b" → 32.0

    Args:
        model_name: Model identifier (e.g., "qwen3:8b", "gemma2:2b-instruct")

    Returns:
        float: Size in billions (e.g., 8.0 for 8B), or 0.0 if not parseable
    """
    if not model_name:
        return 0.0

    # Convert to lowercase for matching
    name_lower = model_name.lower()

    # Pattern: look for number followed by 'b' (billions)
    # Matches: 8b, 70b, 3.8b, 0.5b, etc.
    pattern = r'[:\-_]?(\d+(?:\.\d+)?)\s*b(?:[^a-z]|$)'
    match = re.search(pattern, name_lower)

    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    # Fallback: check for size in the tag after colon
    if ':' in model_name:
        tag = model_name.split(':')[1].lower()
        # Try to find number followed by 'b' in tag
        simple_match = re.search(r'^(\d+(?:\.\d+)?)\s*b', tag)
        if simple_match:
            try:
                return float(simple_match.group(1))
            except ValueError:
                pass

    return 0.0


def get_largest_compression_model(
    aifred_model: str,
    sokrates_model: str,
    salomo_model: str
) -> str:
    """
    Select the largest model from AIfred, Sokrates, and Salomo for compression.

    Compression quality is critical - use the most capable model available.
    Falls back to aifred_model if sizes cannot be determined.

    Args:
        aifred_model: AIfred's model ID
        sokrates_model: Sokrates' model ID
        salomo_model: Salomo's model ID

    Returns:
        str: Model ID of the largest model (by parameter count)
    """
    candidates = [
        (aifred_model, parse_model_size_from_name(aifred_model)),
        (sokrates_model, parse_model_size_from_name(sokrates_model)),
        (salomo_model, parse_model_size_from_name(salomo_model)),
    ]

    # Filter out empty models and sort by size (descending)
    valid_candidates = [(m, s) for m, s in candidates if m and s > 0]

    if not valid_candidates:
        # Fallback: return first non-empty model
        for model, _ in candidates:
            if model:
                return model
        return aifred_model  # Ultimate fallback

    # Sort by size descending, return largest
    valid_candidates.sort(key=lambda x: x[1], reverse=True)
    largest_model, largest_size = valid_candidates[0]

    log_message(f"🗜️ Compression model: {largest_model} ({largest_size}B) - largest of AIfred/Sokrates/Salomo")

    return largest_model


def count_tokens_with_tokenizer(text: str) -> int:
    """
    Count tokens using local Qwen3 tokenizer (lightweight, fully offline)

    Uses the tokenizers library with a locally cached tokenizer.json file.
    No network calls - reads only from ~/.cache/huggingface/

    Args:
        text: Text to tokenize

    Returns:
        int: Exact token count

    Raises:
        FileNotFoundError: If tokenizer.json not found in cache
        Exception: If tokenization fails
    """
    global _tokenizer_cache

    cache_key = "qwen3"  # Single tokenizer for all Qwen models

    if cache_key not in _tokenizer_cache:
        from tokenizers import Tokenizer
        import os
        import glob

        # Find cached tokenizer.json from local HuggingFace cache
        cache_pattern = os.path.expanduser(
            "~/.cache/huggingface/hub/models--Qwen--Qwen3-4B/snapshots/*/tokenizer.json"
        )
        matches = glob.glob(cache_pattern)

        if not matches:
            # Auto-download tokenizer (one-time, ~2 MB)
            log_message("Qwen3 tokenizer not cached — downloading...")
            from transformers import AutoTokenizer
            AutoTokenizer.from_pretrained("Qwen/Qwen3-4B")
            matches = glob.glob(cache_pattern)

        if not matches:
            raise FileNotFoundError("Qwen3 tokenizer download failed")

        tokenizer_path = matches[0]
        _tokenizer_cache[cache_key] = Tokenizer.from_file(tokenizer_path)
        log_message("✅ Loaded Qwen3 tokenizer from local cache")

    tokenizer = _tokenizer_cache[cache_key]
    encoded = tokenizer.encode(text)
    return len(encoded.ids)


def strip_thinking_blocks(text: str) -> str:
    """
    Remove thinking blocks from text (for History Compression).

    Supports two formats:
    1. <think>...</think> (DeepSeek/Qwen3 style)
    2. <|channel|>analysis<|message|>...<|end|> (GPT-OSS Harmony style)

    These blocks contain:
    - DeepSeek Reasoning (thinking process)
    - GPT-OSS Harmony analysis channel
    - Vision-LLM JSON (structured extraction)

    Args:
        text: Text with potential thinking blocks

    Returns:
        Text without thinking blocks
    """
    import re
    from .formatting import fix_orphan_closing_think_tag
    # Repair orphaned </think> (without <think>) before stripping
    text = fix_orphan_closing_think_tag(text)
    # Remove <think>...</think> blocks (non-greedy, multi-line)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Remove GPT-OSS Harmony analysis channel: <|channel|>analysis<|message|>...<|end|>
    text = re.sub(r'<\|channel\|>analysis<\|message\|>.*?<\|end\|>', '', text, flags=re.DOTALL)
    return text.strip()


def estimate_tokens(messages: List[Dict], model_name: Optional[str] = None) -> int:
    """
    Count tokens in messages using real tokenizer (with fallback)

    Args:
        messages: List of message dicts with 'content' key (str or list)
        model_name: Optional model name for accurate tokenization

    Returns:
        int: Token count (exact with tokenizer, estimated with fallback)
    """
    # Combine all message content (handle both str and multimodal list format)
    text_parts = []
    for m in messages:
        content = m['content']
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            # Multimodal content: extract text parts only (images don't count as text tokens)
            for part in content:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))

    total_text = "\n".join(text_parts)

    # Use local Qwen3 tokenizer - no fallback, errors must surface
    return count_tokens_with_tokenizer(total_text)


def strip_non_llm_content(text: str) -> str:
    """
    Remove content that doesn't go to the LLM (thinking blocks, metadata).

    These are stored in history for UI/export but should not count towards
    token estimation since they are stripped before LLM calls.
    """
    if not text:
        return ""
    # <details>...</details> (Thinking blocks in UI - collapsible)
    text = re.sub(r'<details[^>]*>.*?</details>', '', text, flags=re.DOTALL)
    # <span>...</span> (Metadata spans)
    text = re.sub(r'<span[^>]*>.*?</span>', '', text, flags=re.DOTALL)
    # <think>...</think> (Raw thinking blocks)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return text.strip()


def estimate_tokens_from_history(history: List[Dict[str, Any]]) -> int:
    """
    Estimate token count from chat history (dict-based format).

    Strips non-LLM content (thinking blocks, metadata) before estimation
    since these don't actually go to the LLM.

    Args:
        history: List of ChatMessage dicts with "role" and "content" fields

    Returns:
        int: Estimated token count (rule of thumb: 1 token ≈ 3.5 chars for German/mixed text)
    """
    total_size = 0
    for msg in history:
        content = msg.get("content", "")
        total_size += len(strip_non_llm_content(content))
    # 3.5 chars per token (better for German texts than 4)
    return int(total_size / 3.5)


def estimate_tokens_from_llm_history(llm_history: List[Dict[str, str]]) -> int:
    """
    Estimate token count from llm_history (dict format).

    llm_history is already cleaned - no stripping needed.

    Args:
        llm_history: List of {"role": ..., "content": ...} dicts

    Returns:
        int: Estimated token count
    """
    if not llm_history:
        return 0
    total_size = sum(len(msg.get("content", "")) for msg in llm_history)
    # 3.5 chars per token (better for German texts than 4)
    return int(total_size / 3.5)


def get_summary_target_tokens(tokens_to_compress: int) -> int:
    """
    Calculate dynamic summary size based on content being compressed.

    Summary = 25% of content being compressed (4:1 compression ratio).
    Minimum 500 tokens for meaningful summaries.

    Args:
        tokens_to_compress: Estimated tokens in content to be compressed

    Returns:
        int: Target token count for summary
    """
    target = int(tokens_to_compress * HISTORY_SUMMARY_RATIO)
    return max(HISTORY_SUMMARY_MIN_TOKENS, target)


def truncate_to_tokens(text: str, target_tokens: int) -> str:
    """
    Truncate text to approximately target token count.

    Uses char-based estimation (3.5 chars/token for German).

    Args:
        text: Text to truncate
        target_tokens: Target token count

    Returns:
        str: Truncated text (with ... indicator if truncated)
    """
    target_chars = int(target_tokens * 3.5)
    if len(text) <= target_chars:
        return text
    # Find last sentence boundary before limit
    truncated = text[:target_chars]
    last_period = truncated.rfind('. ')
    if last_period > target_chars * 0.7:  # At least 70% of content
        return truncated[:last_period + 1] + " [...]"
    return truncated + " [...]"


def calculate_max_summaries(context_limit: int, avg_summary_tokens: int = 500) -> int:
    """
    Calculate how many summaries fit based on context limit.

    Rule: Summaries should use max HISTORY_SUMMARY_MAX_RATIO (20%) of context.
    This ensures small models (4K) don't get overwhelmed by summaries.

    Examples:
        4K Context:  4096 * 0.2 / 500 = 1-2 Summaries
        8K Context:  8192 * 0.2 / 500 = 3 Summaries
        32K Context: 32768 * 0.2 / 500 = 13 → capped at HISTORY_MAX_SUMMARIES (10)

    Args:
        context_limit: The model's context window size in tokens
        avg_summary_tokens: Average expected tokens per summary (default: 500)

    Returns:
        int: Maximum number of summaries allowed (at least 1, at most HISTORY_MAX_SUMMARIES)
    """
    max_summary_budget = int(context_limit * HISTORY_SUMMARY_MAX_RATIO)
    max_summaries = max(1, max_summary_budget // avg_summary_tokens)
    return min(max_summaries, HISTORY_MAX_SUMMARIES)


def is_summary_message(msg: Dict[str, Any]) -> bool:
    """
    Check if a chat history message is a summary.

    Supports:
    - New role-based: msg["role"] == "system" with summary marker
    - Content-based: [📊 Summary #N|X Messages|Timestamp]
    - Legacy format: [📊 Compressed: N Messages] / [📊 Komprimiert: N Messages]

    Args:
        msg: ChatMessage dict with "role" and "content" fields

    Returns:
        bool: True if this is a summary entry
    """
    role = msg.get("role", "")
    content = msg.get("content", "")

    # System role is always a summary
    if role == "system":
        return True

    # Assistant with no user context and summary marker
    if role == "assistant":
        return bool(
            content.startswith("[📊 Compressed") or
            content.startswith("[📊 Komprimiert") or
            content.startswith("[📊 Summary #")
        )

    return False


def count_summaries(history: List[Dict[str, Any]]) -> int:
    """Count the number of summary entries in chat history."""
    return sum(1 for msg in history if is_summary_message(msg))


# Global cache for VRAM limits (prevents recalculation during history compression)
# Separate limits for AIfred and Sokrates since they use different models
_last_vram_limit_cache = {
    "limit": 0,           # Legacy/default (for compression, uses min of both)
    "aifred_limit": 0,    # AIfred's model-specific limit
    "sokrates_limit": 0   # Sokrates' model-specific limit
}

# Reserve tiers for LLM output (stepwise reduction before content truncation)
OUTPUT_RESERVE_PREFERRED = 32768  # 32K - Ideal for detailed answers (4x increased for 108K+ context)
OUTPUT_RESERVE_REDUCED = 6144     # 6K - Acceptable when VRAM is tight
OUTPUT_RESERVE_MINIMUM = 4096     # 4K - Minimum, below this content is truncated

# For backward compatibility (used in calculate_dynamic_num_ctx)
OUTPUT_RESERVE_TOKENS = OUTPUT_RESERVE_PREFERRED


def calculate_adaptive_reserve(
    available_context: int,
    base_input: int,
    max_rag_target: int
) -> tuple[int, int]:
    """
    Calculate adaptive reserve and RAG budget.

    Priority: Maximize reserve (32K), then reduce stepwise if RAG too tight.

    Steps:
    1. Try with 32K reserve → if RAG >= max_rag_target: perfect
    2. If 32K RAG tight but > min threshold: use 32K with reduced RAG
    3. If RAG < min threshold: Reduce reserve to 6K
    4. If 6K RAG tight: use 6K with reduced RAG
    5. If RAG < min threshold: Reduce to 4K (minimum)
    6. Only THEN: Truncate RAG content to available budget

    Args:
        available_context: Max available context (VRAM/model limited)
        base_input: Estimated input without RAG (system prompt + history + user)
        max_rag_target: Target size for RAG context (e.g., MAX_RAG_CONTEXT_TOKENS = 20K)

    Returns:
        tuple[int, int]: (actual_reserve, max_rag_tokens)
    """
    from .logging_utils import log_message
    from .formatting import format_number

    # Minimum RAG before reserve is reduced (80% of target)
    min_rag_threshold = int(max_rag_target * 0.8)  # e.g., 16K with 20K target

    # Step 1: Try with full reserve (32K)
    rag_budget_32k = available_context - base_input - OUTPUT_RESERVE_PREFERRED
    if rag_budget_32k >= max_rag_target:
        return OUTPUT_RESERVE_PREFERRED, max_rag_target
    if rag_budget_32k >= min_rag_threshold:
        # RAG slightly tight but acceptable - keep 32K reserve
        log_message(f"ℹ️ RAG slightly reduced: {format_number(max_rag_target)} → {format_number(rag_budget_32k)} tok (Reserve: 32K)")
        return OUTPUT_RESERVE_PREFERRED, rag_budget_32k

    # Step 2: Reduce to 6K reserve (VRAM tight)
    rag_budget_6k = available_context - base_input - OUTPUT_RESERVE_REDUCED
    if rag_budget_6k >= max_rag_target:
        log_message(f"⚠️ Reserve reduced: 32K → 6K (RAG budget: {format_number(rag_budget_6k)} tok)")
        return OUTPUT_RESERVE_REDUCED, max_rag_target
    if rag_budget_6k >= min_rag_threshold:
        # RAG slightly tight but acceptable - keep 6K reserve
        log_message(f"⚠️ Reserve reduced: 32K → 6K, RAG: {format_number(rag_budget_6k)} tok")
        return OUTPUT_RESERVE_REDUCED, rag_budget_6k

    # Step 3: Reduce to 4K reserve (minimum)
    rag_budget_4k = available_context - base_input - OUTPUT_RESERVE_MINIMUM
    if rag_budget_4k >= max_rag_target:
        log_message(f"⚠️ Reserve reduced: 32K → 4K (RAG budget: {format_number(rag_budget_4k)} tok)")
        return OUTPUT_RESERVE_MINIMUM, max_rag_target

    # Step 4: Reserve at minimum, RAG truncated
    if rag_budget_4k > 0:
        log_message(f"⚠️ RAG content truncated: {format_number(max_rag_target)} → {format_number(rag_budget_4k)} tok (reserve: 4K)")
        return OUTPUT_RESERVE_MINIMUM, max(4096, rag_budget_4k)  # At least 4K RAG

    # Extreme case: No space for RAG (should never happen)
    log_message(f"❌ Critical: No space for RAG content! (available: {format_number(available_context)}, base: {format_number(base_input)})")
    return OUTPUT_RESERVE_MINIMUM, 4096  # Fallback: Minimum RAG


async def get_max_available_context(
    llm_client,
    model_name: str,
    enable_vram_limit: bool = True
) -> tuple[int, int]:
    """
    Calculate max available context BEFORE context building.

    This function determines the practical limit BEFORE the RAG context is built,
    so build_context() knows how much space is available.

    Args:
        llm_client: LLMClient instance
        model_name: Model name
        enable_vram_limit: Whether VRAM-based limiting is applied

    Returns:
        tuple[int, int]: (max_practical_ctx, model_limit)
    """
    # Query model limit from backend
    model_limit, _ = await llm_client.get_model_context_limit(model_name)

    if enable_vram_limit:
        backend = llm_client._get_backend()
        max_practical_ctx, _ = await backend.calculate_practical_context(model_name)
    else:
        max_practical_ctx = model_limit

    return min(max_practical_ctx, model_limit), model_limit


async def calculate_dynamic_num_ctx(
    llm_client,
    model_name: str,
    messages: List[Dict],
    llm_options: Optional[Dict] = None,
    enable_vram_limit: bool = True,
    state = None  # Optional: AIState instance for storing VRAM limit
) -> tuple[int, list[str]]:
    """
    Calculate optimal num_ctx based on message size, model limit AND VRAM.

    This function is CENTRAL for all context calculations!
    It queries the model limit directly (~30ms) and calculates optimal num_ctx.

    The calculation considers:
    1. Message size × 2 (50/50 rule: 50% input, 50% output)
    2. Model maximum (via backend query)
    3. VRAM-based practical limit (NEW! prevents CPU offload)
    4. User override (if set in llm_options)

    Args:
        llm_client: LLMClient instance (any backend type)
        model_name: Model name (e.g., "qwen3:8b", "phi3:mini")
        messages: List of message dicts with 'content' key
        llm_options: Dict with optional 'num_ctx' override
        enable_vram_limit: Whether VRAM-based limiting is applied (default: True)

    Returns:
        tuple[int, list[str]]: (num_ctx, debug_messages)
            - num_ctx: Optimal num_ctx (rounded to standard sizes, clipped to practical limit)
            - debug_messages: VRAM debug messages for UI console (to be yielded by caller)

    Raises:
        RuntimeError: If model info cannot be queried
    """
    # Check for manual override
    user_num_ctx = llm_options.get('num_ctx') if llm_options else None
    if user_num_ctx:
        log_message(f"🎯 Context Window: {user_num_ctx} Tokens (manually set)")
        return user_num_ctx, []  # No VRAM messages for manual override

    # Calculate tokens from message size
    estimated_tokens = estimate_tokens(messages)  # 1 token ≈ 3.5 chars

    # Query model limit from backend (~40ms, does NOT load model!)
    model_limit, _ = await llm_client.get_model_context_limit(model_name)

    # NEW: Backend-specific practical limit calculation
    # - Ollama: Dynamic VRAM calculation (based on current free VRAM)
    # - vLLM: Cached startup value (FIXED, cannot be changed at runtime)
    # - TabbyAPI: Cached startup value or API query
    vram_debug_msgs = []
    backend = llm_client._get_backend()

    if enable_vram_limit:
        # Use backend-specific context calculation
        max_practical_ctx, vram_debug_msgs = await backend.calculate_practical_context(model_name)
    else:
        # VRAM limit disabled - use full model limit
        max_practical_ctx = model_limit
        log_message(f"⚠️ VRAM limit disabled - using full model limit {model_limit:,} (risk: CPU offload)")

    # TTS VRAM reservation — only for backends where context is dynamic (Ollama).
    # For llamacpp: llama-swap YAML has separate TTS-calibrated profiles with
    # adjusted tensor-split. The -c value IS the ground truth — no reduction needed.
    current_backend = getattr(state, 'backend_type', 'ollama') if state else 'ollama'
    tts_active = state and getattr(state, 'enable_tts', False)
    tts_engine = getattr(state, 'tts_engine', '').lower() if state else ''

    if tts_active and current_backend == "ollama":
        if 'xtts' in tts_engine and not getattr(state, 'xtts_force_cpu', False):
            vram_ratio = VRAM_CONTEXT_RATIO_MOE if is_moe_model(model_name) else VRAM_CONTEXT_RATIO_DENSE
            xtts_token_reserve = int(XTTS_VRAM_MB / vram_ratio)
            max_practical_ctx = max(2048, max_practical_ctx - xtts_token_reserve)
            vram_debug_msgs.append(f"🔊 XTTS reserved: ~{format_number(XTTS_VRAM_MB)} MB ({format_number(xtts_token_reserve)} tok)")

        elif 'moss' in tts_engine and getattr(state, 'moss_tts_device', '') == 'cuda':
            vram_ratio = VRAM_CONTEXT_RATIO_MOE if is_moe_model(model_name) else VRAM_CONTEXT_RATIO_DENSE
            moss_token_reserve = int(MOSS_TTS_VRAM_MB / vram_ratio)
            max_practical_ctx = max(2048, max_practical_ctx - moss_token_reserve)
            vram_debug_msgs.append(f"🔊 MOSS-TTS reserved: ~{format_number(MOSS_TTS_VRAM_MB)} MB ({format_number(moss_token_reserve)} tok)")

    # Ollama/vLLM/TabbyAPI/llama.cpp: Dynamic num_ctx calculation possible
    # gpu_utils.calculate_vram_based_context() returns:
    # - Calibrated: the measured max_context_gpu_only value
    # - Uncalibrated: dynamically calculated VRAM-based value
    # In both cases: Clip to model limit
    final_num_ctx = min(max_practical_ctx, model_limit)

    # Store VRAM limit in global cache for history compression
    # (prevents history from recalculating the limit)
    # This is called for AIfred (main LLM), so store in aifred_limit too
    _last_vram_limit_cache["limit"] = min(max_practical_ctx, model_limit)
    _last_vram_limit_cache["aifred_limit"] = min(max_practical_ctx, model_limit)

    # Optional: Also store in state if provided
    if state is not None:
        state.last_vram_limit = min(max_practical_ctx, model_limit)

    # Log Context Window Info
    available_output = final_num_ctx - estimated_tokens
    log_message(
        f"🎯 Context Window: {format_number(final_num_ctx)} tok "
        f"(Input: ~{format_number(estimated_tokens)}, Output space: ~{format_number(available_output)}, "
        f"VRAM limit: {format_number(max_practical_ctx)}, Model max: {format_number(model_limit)})"
    )

    return final_num_ctx, vram_debug_msgs


async def summarize_history_if_needed(
    history: List[Dict[str, Any]],
    llm_client,
    model_name: str,
    context_limit: int,
    max_summaries: int | None = None,
    llm_history: List[Dict[str, str]] | None = None,
    system_prompt_tokens: int = 0,
    detected_language: str = "de"
) -> AsyncIterator[Dict]:
    """
    Compress chat history when context utilization reaches trigger threshold.

    DUAL-UPDATE (v2.13.0+):
    - chat_history (UI): Original-Messages bleiben, Summary wird NACH komprimierten Messages eingefügt
    - llm_history (LLM): Alte Messages werden durch Summary ersetzt (ready-to-use für LLM)

    NEW ALGORITHM (dynamic, percentage-based):
    1. Trigger: >= 70% context utilization (HISTORY_COMPRESSION_TRIGGER)
    2. Target: Compress down to 30% (HISTORY_COMPRESSION_TARGET) - leaves room for ~2 roundtrips
    3. Summary size: 25% of compressed content (4:1 ratio, min 500 tokens)
    4. Tolerance: Allow up to 50% over target, then truncate

    No more fixed message count - compresses as much as needed to reach target!

    IMPORTANT (v2.14.0+): system_prompt_tokens is INCLUDED in utilization calculation!
    This prevents context overflow when system prompts are large (e.g., 2000+ tokens).

    Args:
        history: Chat history as list of ChatMessage dicts (UI - vollständig)
        llm_client: LLM Client for summarization
        model_name: Main LLM model
        context_limit: Context window limit of the model
        max_summaries: Maximum number of summaries before FIFO (default: from config)
        llm_history: LLM history as list of {"role": ..., "content": ...} dicts (LLM - komprimiert)
        system_prompt_tokens: Estimated tokens for system prompt(s) - included in utilization!

    Yields:
        Dict: Progress, debug messages, and history updates
        - {"type": "history_update", "chat_history": [...], "llm_history": [...]}

    Returns:
        None - Function does not modify history in-place, state update via yield
    """
    # Calculate dynamic max_summaries based on context limit
    # Small models (4K) get fewer summaries, large models (32K+) get more
    if max_summaries is None:
        max_summaries = calculate_max_summaries(context_limit)

    # Calculate thresholds from config
    trigger_threshold = int(context_limit * HISTORY_COMPRESSION_TRIGGER)  # 70%
    target_threshold = int(context_limit * HISTORY_COMPRESSION_TARGET)    # 30%

    # Token estimation - use llm_history if available (already cleaned, accurate)
    if llm_history:
        history_tokens = estimate_tokens_from_llm_history(llm_history)
    else:
        history_tokens = estimate_tokens_from_history(history)

    # CRITICAL (v2.14.0+): Include system prompt tokens in total!
    # This prevents overflow when system prompts are large (2000+ tokens).
    # Total = System Prompt + History (what actually goes to the LLM)
    total_tokens = system_prompt_tokens + history_tokens
    utilization = (total_tokens / context_limit) * 100

    # Debug: Show breakdown (System vs History)
    if system_prompt_tokens > 0:
        yield {"type": "debug", "message": f"📊 Context: System {format_number(system_prompt_tokens)} + History {format_number(history_tokens)} = {format_number(total_tokens)} / {format_number(context_limit)} tok ({int(utilization)}%)"}
    else:
        yield {"type": "debug", "message": f"📊 History: {format_number(history_tokens)} / {format_number(context_limit)} tok ({int(utilization)}%)"}

    # Check trigger: Only compress when >= 70% utilized
    if total_tokens < trigger_threshold:
        return

    # Safety: Need at least 2 messages (1 to compress, 1 to keep)
    if len(history) < 2:
        yield {"type": "debug", "message": f"📊 History: {format_number(total_tokens)} / {format_number(context_limit)} tok ({int(utilization)}%) - too few messages"}
        log_message(f"⚠️ Compression aborted: Only {len(history)} message(s) in history")
        return

    log_message(f"⚠️ History compression triggered: {int(utilization)}% utilization ({format_number(total_tokens)} tok) >= {int(HISTORY_COMPRESSION_TRIGGER*100)}% threshold")
    if system_prompt_tokens > 0:
        log_message(f"   └─ Breakdown: System {format_number(system_prompt_tokens)} + History {format_number(history_tokens)}")
    log_message(f"   └─ Target: {int(HISTORY_COMPRESSION_TARGET*100)}% = {format_number(target_threshold)} tok")

    # Progress indicator
    yield {"type": "progress", "phase": "compress"}
    yield {"type": "debug", "message": f"🗜️ Compressing: {int(utilization)}% → {int(HISTORY_COMPRESSION_TARGET*100)}% target ({format_number(total_tokens)} → {format_number(target_threshold)} tok)"}

    # Count existing summaries using helper function (supports old and new formats)
    summary_count = count_summaries(history)

    # ============================================================
    # FIFO: Only apply to llm_history, NOT chat_history! (v2.14.2+)
    # ============================================================
    # - chat_history (UI): Keep ALL summaries for user to see history
    # - llm_history (LLM): Apply FIFO, only newest summary stays (context limit)
    #
    # Count summaries in llm_history for FIFO decision
    llm_summary_count = 0
    if llm_history is not None:
        llm_summary_count = sum(
            1 for msg in llm_history
            if msg.get("role") == "system" and msg.get("content", "").startswith("[Summary]")
        )

    # FIFO only on llm_history - remove oldest summaries until below max
    while llm_summary_count >= max_summaries and llm_history is not None:
        log_message(f"⚠️ Max {max_summaries} summaries in LLM-History (have {llm_summary_count}) → removing oldest (FIFO)")
        for j, msg in enumerate(llm_history):
            if msg.get("role") == "system" and msg.get("content", "").startswith("[Summary]"):
                llm_history.pop(j)
                llm_summary_count -= 1
                yield {"type": "debug", "message": f"🗑️ Oldest LLM-Summary removed (FIFO, max={max_summaries})"}
                # NOTE: chat_history keeps ALL summaries - no removal there!
                break
        else:
            # No summary found to remove - shouldn't happen but safety
            break

    # Log how many summaries UI keeps (informational)
    if summary_count > 0:
        log_message(f"📌 Chat-History keeps {summary_count} summaries (UI display)")

    # COMPRESSION LOGIC (v2.16.0+):
    # All compression decisions are based on llm_history only.
    # chat_history is append-only: summary bubble gets appended at the end.
    if llm_history is None:
        raise ValueError("llm_history is None - this should never happen in Dual-History mode!")

    # Split llm_history into preserved summaries and compressible messages
    llm_preserved_summaries = []
    llm_remaining = []
    for msg in llm_history:
        if msg.get("role") == "system" and msg.get("content", "").startswith("[Summary]"):
            llm_preserved_summaries.append(msg)
        else:
            llm_remaining.append(msg)

    llm_to_compress = []
    current_tokens = estimate_tokens_from_llm_history(llm_preserved_summaries + llm_remaining)

    # Compress oldest llm messages until below target
    while current_tokens > target_threshold and len(llm_remaining) > 1:
        llm_to_compress.append(llm_remaining.pop(0))
        current_tokens = estimate_tokens_from_llm_history(llm_preserved_summaries + llm_remaining)

    # Calculate tokens being compressed
    tokens_to_compress = estimate_tokens_from_llm_history(llm_to_compress)
    summary_target_tokens = get_summary_target_tokens(tokens_to_compress)
    summary_target_words = int(summary_target_tokens * 0.75)  # ~0.75 words per token for German

    log_message("📝 Compression plan:")
    log_message(f"   └─ LLM messages to compress: {len(llm_to_compress)}")
    log_message(f"   └─ Tokens to compress: {format_number(tokens_to_compress)}")
    log_message(f"   └─ Summary target: {format_number(summary_target_tokens)} tok (~{format_number(summary_target_words)} words)")
    log_message(f"   └─ LLM remaining after: {len(llm_remaining)} messages, ~{format_number(current_tokens)} tok")

    # Format conversation for summary LLM
    conversation_text = ""
    for i, msg in enumerate(llm_to_compress, 1):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        # Strip thinking blocks from content
        clean_content = strip_thinking_blocks(content) if content else ""
        log_message(f"   └─ Msg {i}: {role}={len(content)}→{len(clean_content)} chars")
        conversation_text += f"{role.title()}: {clean_content}\n\n"

    # Use detected_language from Intent Detection (passed from state.py)

    # Load summarization prompt with dynamic target
    summary_prompt = load_prompt(
        'utility/history_summarization',
        lang=detected_language,
        conversation=conversation_text.strip(),
        max_tokens=summary_target_tokens,
        max_words=summary_target_words
    )

    # LLM Summarization
    import datetime
    start_timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

    # Get num_ctx from VRAM cache (calibrated value for compression model)
    # IMPORTANT: Always use VRAM cache value, NOT manual settings!
    # Manual num_ctx is for testing agents, not for compression.
    from .model_vram_cache import get_ollama_calibration, get_rope_factor_for_model, get_llamacpp_calibration
    # Try llama.cpp calibration first, then Ollama
    compression_num_ctx = get_llamacpp_calibration(model_name)
    if not compression_num_ctx:
        rope_factor = get_rope_factor_for_model(model_name)
        compression_num_ctx = get_ollama_calibration(model_name, rope_factor)
    if not compression_num_ctx:
        # Fallback: use context_limit (min of all agents) if not calibrated
        compression_num_ctx = context_limit
        log_message(f"⚠️ Compression model {model_name} not calibrated, using context_limit={context_limit}")

    log_message(f"🗜️ [START {start_timestamp}] Compressing {len(llm_to_compress)} messages with {model_name}...")
    log_message(f"   └─ Compression LLM: {model_name} (Context={format_number(compression_num_ctx)}, from VRAM cache)")
    yield {"type": "debug", "message": f"🗜️ Compression LLM: {model_name} (Context: {format_number(compression_num_ctx)})"}
    summary_timer = Timer()

    summary_text = ""
    tokens_generated = 0

    try:
        log_message("   Calling LLM (non-streaming)...")
        from ..backends.base import LLMMessage, LLMOptions

        messages = [LLMMessage(role="system", content=summary_prompt)]
        options = LLMOptions(
            temperature=HISTORY_SUMMARY_TEMPERATURE,
            num_ctx=compression_num_ctx,
            enable_thinking=False
        )

        # DEBUG: Log RAW messages sent to Compression LLM (controlled by DEBUG_LOG_RAW_MESSAGES)
        log_raw_messages("Compression LLM", messages)

        response = await llm_client.chat(
            model=model_name,
            messages=messages,
            options=options
        )

        summary_time = summary_timer.elapsed()
        end_timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

        summary_text = response.text if response else ""
        # Repair orphaned </think> tags before any processing
        from .formatting import fix_orphan_closing_think_tag
        if summary_text:
            summary_text = fix_orphan_closing_think_tag(summary_text)

        # Extract thinking content BEFORE stripping (for nested collapsible)
        think_match = re.search(r'<think>(.*?)</think>', summary_text, flags=re.DOTALL) if summary_text else None
        thinking_content = think_match.group(1).strip() if think_match else ""

        # Strip thinking blocks — models like GPT-OSS always reason regardless of enable_thinking
        summary_text = strip_thinking_blocks(summary_text).strip() if summary_text else ""

        if response:
            tokens_generated = response.tokens_generated
            tokens_per_second = response.tokens_per_second
        else:
            tokens_generated = len(summary_text) // 4
            tokens_per_second = tokens_generated / summary_time if summary_time > 0 else 0

        log_message(f"✅ [END {end_timestamp}] Summary generated:")
        log_message(f"   └─ Chars: {len(summary_text)}, Tokens: ~{format_number(tokens_generated)}")
        log_message(f"   └─ Time: {format_number(summary_time, 2)}s, Speed: {format_number(tokens_per_second, 1)} tok/s")

        # TOLERANCE CHECK: Is summary too large?
        summary_tokens = int(len(summary_text) / 3.5)
        max_allowed = int(summary_target_tokens * (1 + HISTORY_SUMMARY_TOLERANCE))

        if summary_tokens > max_allowed:
            log_message(f"⚠️ Summary too large: {format_number(summary_tokens)} > {format_number(max_allowed)} (target + 50%) → truncating")
            summary_text = truncate_to_tokens(summary_text, max_allowed)
            yield {"type": "debug", "message": f"✂️ Summary truncated: {format_number(summary_tokens)} → ~{format_number(max_allowed)} tok"}

        if tokens_to_compress > 0 and tokens_generated > 0:
            log_message(f"   └─ Compression: {format_number(tokens_to_compress)} → {format_number(tokens_generated)} tok ({format_number(tokens_to_compress/tokens_generated, 1)}:1)")
        console_separator()

    except asyncio.TimeoutError:
        log_message("⚠️ Async timeout during summary generation")
        yield {"type": "debug", "message": "⚠️ Summary generation timeout"}
        summary_text = ""
    except Exception as e:
        log_message(f"❌ Error during summary generation: {e}")
        yield {"type": "debug", "message": f"❌ Summary error: {e}"}
        summary_text = ""

    # Build new histories only if summary successful
    if summary_text and len(summary_text.strip()) > 10:
        # New format: [📊 Summary #N|X Messages|Timestamp]
        # - N = sequential number (count of ALL existing summaries in entire history + 1)
        # - X Messages = how many NON-SUMMARY messages were compressed
        # - Timestamp = when compression happened
        import datetime

        # Summary number = existing summaries in chat_history + 1
        summary_number = count_summaries(history) + 1

        # Count of LLM messages compressed
        non_summary_compressed = len(llm_to_compress)

        timestamp = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")

        # Build collapsible HTML for chat_history (UI display)
        # i18n labels
        is_de = detected_language == "de"
        thinking_label = f"\U0001f4ad Denkprozess ({model_name})" if is_de else f"\U0001f4ad Thinking Process ({model_name})"
        messages_label = "Nachrichten" if is_de else "Messages"
        summary_label = "Zusammenfassung" if is_de else "Summary"

        inner_html = ""
        if thinking_content:
            # Collapse multiple consecutive blank lines
            clean_thinking = re.sub(r'\n{3,}', '\n\n', thinking_content.strip())
            inner_html += (
                '<details style="font-size: 0.9em; margin-bottom: 0.5em;">\n'
                '<summary style="cursor: pointer; font-weight: bold; color: #aaa;">'
                f'{thinking_label}</summary>\n'
                '<div class="thinking-compact" style="max-height: 60vh; overflow-y: auto;">\n'
                f'{clean_thinking}\n'
                '</div>\n</details>\n\n'
            )
        inner_html += summary_text.strip()

        summary_header = f"\U0001f4ca {summary_label} #{summary_number} | {non_summary_compressed} {messages_label} | {timestamp}"
        summary_collapsible = (
            '<details style="font-size: 0.9em; margin-bottom: 1em;">\n'
            '<summary style="cursor: pointer; font-weight: bold; color: #aaa; '
            'position: sticky; top: 0; z-index: 1; background: inherit; padding: 2px 0;">'
            f'{summary_header}</summary>\n'
            '<div style="max-height: 60vh; overflow-y: auto; padding: 0.5em;">\n'
            f'{inner_html}\n'
            '</div>\n</details>'
        )

        summary_entry: Dict[str, Any] = {
            "role": "system",
            "content": summary_collapsible,
            "agent": "",
            "mode": "summary",
            "round_num": 0,
            "metadata": {
                "summary_number": summary_number,
                "message_count": non_summary_compressed,
            },
            "timestamp": datetime.datetime.now().isoformat(),
            "has_audio": False,
            "audio_urls_json": "[]"
        }

        # ============================================================
        # DUAL-HISTORY UPDATE (v2.16.0+)
        # ============================================================
        # chat_history (UI): Insert summary before the last user message.
        # The last user message triggered the compression, so the summary
        # (which covers messages BEFORE that request) belongs before it.
        # If the last message isn't a user message, append at end.
        if history and history[-1].get("role") == "user":
            new_chat_history = history[:-1] + [summary_entry] + [history[-1]]
        else:
            new_chat_history = history + [summary_entry]

        # llm_history (LLM): Replace compressed messages with summary
        # Structure: [existing summaries after FIFO] + [new summary] + [remaining messages]
        # NOTE: llm_to_compress messages are DELETED (replaced by summary), not kept!
        new_llm_history = None
        if llm_history is not None:
            # New summary as system message (clean text only, no UI markers)
            summary_system_msg = {
                "role": "system",
                "content": f"[Summary]\n{summary_text.strip()}"
            }

            # Use llm_remaining (already filtered and popped during compression loop)
            # llm_preserved_summaries = existing summaries after FIFO cleanup
            # llm_to_compress = messages that were compressed (will be DELETED)
            # llm_remaining = messages that stay (after compression loop)
            new_llm_history = llm_preserved_summaries + [summary_system_msg] + llm_remaining
    else:
        log_message("⚠️ Summary too short or empty - history remains unchanged")
        yield {"type": "debug", "message": "⚠️ Compression failed - history unchanged"}
        return

    # Calculate results (based on llm_history for accurate LLM token count)
    if new_llm_history is not None:
        # Use llm_history estimation (already dict format)
        new_history_tokens = estimate_tokens_from_llm_history(new_llm_history)
    else:
        new_history_tokens = estimate_tokens_from_history(new_chat_history)

    # Include system prompt in new utilization (same as trigger calculation)
    new_total_tokens = system_prompt_tokens + new_history_tokens
    new_utilization = (new_total_tokens / context_limit) * 100
    compression_ratio = history_tokens / new_history_tokens if new_history_tokens > 0 else 0
    summaries_count = count_summaries(new_chat_history)

    log_message("✅ History successfully compressed:")
    log_message(f"   └─ Chat-History: {len(history)} → {len(new_chat_history)} entries (UI complete)")
    if new_llm_history is not None:
        log_message(f"   └─ LLM-History: {len(new_llm_history)} messages (compressed)")
    log_message(f"   └─ History Tokens: {format_number(history_tokens)} → {format_number(new_history_tokens)} ({format_number(compression_ratio, 1)}:1)")
    log_message(f"   └─ Total (incl. System): {format_number(total_tokens)} → {format_number(new_total_tokens)} tok")
    log_message(f"   └─ Utilization: {int(utilization)}% → {int(new_utilization)}%")
    log_message(f"   └─ Space freed: {format_number(history_tokens - new_history_tokens)} tok")

    # Yield update to state (DUAL-HISTORY format)
    yield {
        "type": "history_update",
        "chat_history": new_chat_history,
        "llm_history": new_llm_history  # None if not provided
    }
    # Calculate message count change: Compressed NON-summary messages → 1 new summary
    yield {"type": "debug", "message": f"📦 Compressed: {int(utilization)}% → {int(new_utilization)}% ({format_number(total_tokens)} → {format_number(new_total_tokens)} tok, {non_summary_compressed}→1 msg, {summaries_count} summaries)"}


async def prepare_automatik_llm(
    backend,
    model_name: str,
    backend_type: str = "ollama",
    num_ctx: int | None = None
):
    """
    Preload Automatik-LLM to hide cold-start latency.

    For Ollama: Also sets num_ctx to avoid huge default KV-Cache allocation.
    IMPORTANT: If Automatik = Haupt-LLM, caller should pass the calibrated
    num_ctx to avoid a costly model reload when the main response starts.

    Args:
        backend: LLM Backend instance
        model_name: Model name (pure ID)
        backend_type: "ollama", "vllm", etc.
        num_ctx: Context size for Ollama preload. None = use AUTOMATIK_LLM_NUM_CTX (4K).
                 Caller should pass calibrated context if Automatik = Haupt-LLM.

    Yields:
        dict: {"type": "debug", "message": "..."} for UI console
        dict: {"type": "result", "data": (success, load_time)} as last
    """
    from .formatting import format_number
    from .config import AUTOMATIK_LLM_NUM_CTX

    preload_ctx = num_ctx or AUTOMATIK_LLM_NUM_CTX

    try:
        # Only Ollama and llama.cpp benefit from preloading:
        # - Ollama: Set num_ctx to avoid 262K default allocation
        # - llama.cpp: Trigger llama-swap cold-start before user's first question
        # - vLLM/TabbyAPI: Models stay loaded, no preload needed
        if backend_type not in ("ollama", "llamacpp"):
            yield {"type": "result", "data": (True, 0.0)}
            return

        if backend_type == "ollama":
            formatted_ctx = format_number(preload_ctx)
            yield {"type": "debug", "message": f"🤖 Automatik-LLM ({model_name}) is being preloaded (Context: {formatted_ctx})..."}
            log_message(f"🔄 prepare_automatik_llm: Preloading {model_name} with Context={preload_ctx}")
        else:
            # Extract model details from llama-swap config
            details = ""
            try:
                from .calibration import parse_llamaswap_config
                from .config import LLAMASWAP_CONFIG_PATH
                model_info = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH).get(model_name, {})
                parts = []
                if model_info.get("current_context"):
                    parts.append(f"Context: {format_number(model_info['current_context'])}")
                if model_info.get("kv_cache_quant"):
                    parts.append(f"KV-Cache: {model_info['kv_cache_quant']}")
                if parts:
                    details = f" ({', '.join(parts)})"
            except Exception:
                pass
            yield {"type": "debug", "message": f"🤖 Automatik-LLM ({model_name}) is being preloaded{details}..."}
            log_message(f"🔄 prepare_automatik_llm: Preloading {model_name}{details} (llama-swap cold-start)")

        import asyncio
        await asyncio.sleep(0)  # Flush UI update

        # Ollama: pass num_ctx to control KV-cache allocation
        # llama.cpp: num_ctx ignored (fixed in llama-swap YAML)
        success, load_time = await backend.preload_model(model_name, num_ctx=preload_ctx)

        if success:
            yield {"type": "debug", "message": f"✅ Automatik-LLM preloaded ({load_time:.1f}s)"}
        else:
            yield {"type": "debug", "message": f"⚠️ Automatik-LLM preload failed ({load_time:.1f}s)"}

        log_message(f"✅ prepare_automatik_llm: Done (success={success}, time={load_time:.1f}s)")
        yield {"type": "result", "data": (success, load_time)}

    except Exception as e:
        import traceback
        log_message(f"❌ prepare_automatik_llm EXCEPTION: {e}")
        log_message(f"   Traceback: {traceback.format_exc()}")
        raise

