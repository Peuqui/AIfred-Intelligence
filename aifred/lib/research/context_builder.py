"""
Context Builder - Build context and generate LLM response

Handles:
- Context building from scraped sources
- Integration of old research metadata
- Message construction with history
- LLM inference and response generation
- Cache saving
"""

import re
from typing import Dict, List, Optional, AsyncIterator

from ..tools import build_context
from ..timer import Timer
# Cache system removed - will be replaced with Vector DB
from ..prompt_loader import get_system_rag_prompt
from ..context_manager import calculate_dynamic_num_ctx, estimate_tokens, strip_thinking_blocks
from ..message_builder import build_messages_from_llm_history
from ..formatting import format_thinking_process, build_debug_accordion, build_sources_collapsible, build_inference_metadata, format_number
from ..logging_utils import log_message
from ..config import (
    CHARS_PER_TOKEN,
    TTL_HOURS
)
from ..vector_cache import format_ttl_hours
from ..intent_detector import get_temperature_for_intent, get_temperature_label
from .context_utils import get_rag_context_budget
from ..streaming_utils import stream_llm_response


async def build_and_generate_response(
    user_text: str,
    tool_results: List[Dict],
    failed_sources: List[Dict],
    history: List,
    llm_history: List[Dict[str, str]],
    session_id: Optional[str],
    mode: str,
    model_choice: str,
    automatik_model: str,
    query_reasoning: Optional[str],
    query_opt_time: float,
    llm_client,
    automatik_llm_client,
    llm_options: Optional[Dict],
    temperature_mode: str,
    temperature: float,
    agent_timer: Timer,
    stt_time: float,
    state=None,  # AIState object (REQUIRED for per-agent num_ctx lookup)
    user_name: Optional[str] = None,
    detected_intent: Optional[str] = None,
    detected_language: Optional[str] = None,
    volatility: Optional[str] = None,  # From Automatik-LLM (NOCACHE/DAILY/etc.)
    backend_type: str = "",  # Backend type for metadata display
    agent: str = "aifred",
) -> AsyncIterator[Dict]:
    """
    Build context and generate LLM response

    Args:
        user_text: User's question
        tool_results: All tool results (including failed)
        failed_sources: Failed scraping attempts (for cache storage)
        history: Chat history
        session_id: Session ID for caching
        mode: Research mode ('quick' or 'deep')
        model_choice: Main LLM model name
        automatik_model: Automatik LLM model name (for debug accordion)
        query_reasoning: Query optimization reasoning (for debug accordion)
        query_opt_time: Query optimization time (for debug accordion)
        llm_client: Main LLM client
        automatik_llm_client: Automatik LLM client (for cache metadata generation)
        llm_options: LLM options (num_ctx override, etc.)
        temperature_mode: 'manual' or 'auto'
        temperature: Temperature value (if manual)
        agent_start: Start time for timing
        stt_time: STT processing time (if applicable)
        detected_intent: Pre-detected intent (FAKTISCH/KREATIV/GEMISCHT) - avoids duplicate detection
        detected_language: Pre-detected language ("de" or "en") - avoids regex detection

    Yields:
        Dict: Debug messages, content chunks, final result
    """
    # Filter: Only successfully scraped sources
    scraped_only = [r for r in tool_results if 'word_count' in r and r['word_count'] > 0]

    # Sort sources SAME way as build_context() for consistent logging
    # (News > Wikipedia, short > long)
    def prioritize_source(result):
        url = result.get('url', '').lower()
        word_count = result.get('word_count', 0)
        if 'wikipedia.org' in url:
            return 1000
        elif word_count < 5000:
            return word_count
        else:
            return 500 + word_count

    scraped_sorted = sorted(scraped_only, key=prioritize_source)

    # DEBUG logging - sorted sources (matches Context order sent to LLM)
    for i, result in enumerate(scraped_sorted, 1):
        log_message(f"  Source {i}: {result.get('word_count', 0)} words from {result.get('url', 'N/A')[:50]}")

    if scraped_sorted:
        for i, src in enumerate(scraped_sorted[:3], 1):
            content_preview = src.get('content', '')[:200].replace('\n', ' ')
            log_message(f"  📄 Source {i} Preview: {content_preview}...")

    # ============================================================
    # VRAM-AWARE Context Building (via context_utils.py)
    # Calculate max available context BEFORE build_context() runs
    # ============================================================
    max_rag_tokens, actual_reserve, max_ctx = await get_rag_context_budget(
        llm_client=llm_client,
        model_choice=model_choice,
        history=history,
        user_text=user_text,
        state=state
    )

    # 6. Build context with dynamic limit
    context = build_context(user_text, scraped_only, max_context_tokens=max_rag_tokens)

    # Estimate tokens
    est_tokens = len(context) // CHARS_PER_TOKEN
    log_message(f"📊 Context built: ~{format_number(est_tokens)} tok")

    # Show context preview
    if len(context) > 800:
        preview = context[:800] + "..."
        log_message(f"📝 Context Preview (first 800 chars):\n{preview}")

    # Use pre-detected language from LLM intent detection, or UI language setting
    if detected_language:
        detected_user_language = detected_language
    elif state and hasattr(state, 'ui_language'):
        detected_user_language = state.ui_language
    else:
        raise ValueError("No language available: detected_language is None and state.ui_language not accessible")

    # System prompt with personality (if enabled)
    system_prompt = get_system_rag_prompt(
        context=context,
        user_text=user_text,
        lang=detected_user_language
    )

    yield {"type": "debug", "message": "✅ System prompt created"}

    # Build messages using central function (handles [AIFRED]: stripping + personality reminder)
    # This ensures consistency with all other LLM calls in the codebase
    from ..prompt_loader import set_language
    set_language(detected_user_language)  # Set language for personality reminder

    messages = build_messages_from_llm_history(
        llm_history=llm_history,
        current_user_text=user_text,
        perspective="aifred",  # AIfred speaking - uses correct role assignment
        detected_language=detected_user_language  # Pass language for personality reminder
    )

    # Agent Memory: recall + toolkit
    memory_toolkit = None
    if state and getattr(state, 'agent_memory_enabled', False):
        from ..agent_memory import prepare_agent_toolkit
        memory_ctx, memory_toolkit = await prepare_agent_toolkit(
            "aifred", user_text, lang=detected_user_language,
            research_tools_enabled=False,
        )
        if memory_ctx:
            # Eingehaengt wird unten, NACH dem System-Prompt — siehe
            # message_builder.inject_before_question.
            mem_tok = estimate_tokens([{"content": memory_ctx}])
            yield {"type": "debug",
                   "message": f"🧠 Memory context injected ({mem_tok:,} tok, before question)"}
        if memory_toolkit:
            yield {"type": "debug", "message": f"🔧 Toolkit: {[t.name for t in memory_toolkit.tools]}"}

    # Insert RAG system prompt as first message
    messages.insert(0, {"role": "system", "content": system_prompt})

    # Erinnerungen ans Ende, direkt vor die Nutzerfrage — sonst entwerten
    # sie als fragenabhaengiger Block den Praefix-Cache des ganzen Verlaufs.
    if memory_ctx:
        from ..message_builder import inject_before_question
        inject_before_question(messages, memory_ctx)

    # DEBUG: Show message sizes
    for i, msg in enumerate(messages):
        role = msg['role']
        content_len = len(msg['content'])
        log_message(f"  Message {i+1} ({role}): {content_len} chars")

    # Count actual input tokens (using real tokenizer)
    input_tokens = estimate_tokens(messages, model_name=model_choice)

    # Dynamic num_ctx calculation using centralized function
    from .context_utils import get_agent_num_ctx
    if state:
        final_num_ctx, ctx_source = get_agent_num_ctx("aifred", state, model_choice)
        if ctx_source == "manual":
            if llm_options is None:
                llm_options = {}
            llm_options['num_ctx'] = final_num_ctx
            log_message(f"🔧 Manual Context: {format_number(final_num_ctx)} (per-agent setting)")
            yield {"type": "debug", "message": f"🔧 Context: {format_number(final_num_ctx)} (manual)"}
        else:
            log_message(f"🎯 Context: {format_number(final_num_ctx)} ({ctx_source})")
            yield {"type": "debug", "message": f"🎯 Context: {format_number(final_num_ctx)} ({ctx_source})"}
    else:
        # Fallback if no state available
        enable_vram_limit = True
        final_num_ctx, vram_debug_msgs = await calculate_dynamic_num_ctx(
            llm_client, model_choice, messages, llm_options,
            enable_vram_limit=enable_vram_limit
        )
        for debug_msg in vram_debug_msgs:
            yield {"type": "debug", "message": debug_msg}

    # Get model max context for compact display
    model_limit, _ = await llm_client.get_model_context_limit(model_choice)

    # Show compact context info (like Automatik-LLM)
    log_message(f"📊 AIfred-LLM: {format_number(input_tokens)} / {format_number(final_num_ctx)} tok [Context={final_num_ctx}]")
    yield {"type": "debug", "message": f"📊 AIfred-LLM: {format_number(input_tokens)} / {format_number(final_num_ctx)} tok"}

    # VRAM Warning: Check if VRAM change detected AND content doesn't fit
    vram_warning = llm_options.get('_vram_warning') if llm_options else None
    if vram_warning and input_tokens > final_num_ctx:
        # Content doesn't fit AND more VRAM available → Show orange blocking warning
        vram_diff = vram_warning["vram_diff"]
        potential_tokens = vram_warning.get("potential_tokens")

        warning_msg = (
            f"⚠️ VRAM change detected: {vram_diff:+.0f}MB additional memory available!\n\n"
            f"💡 **Recommendation:** Go to **Control Panel → vLLM Restart** to use the "
            f"extended context window"
        )

        if potential_tokens:
            warning_msg += (
                f" ({format_number(vram_warning['current_tokens'])} → "
                f"~{format_number(potential_tokens)} Tokens)."
            )
        else:
            warning_msg += "."

        log_message(warning_msg)
        yield {"type": "debug", "message": warning_msg}

    # SAFEGUARD: Check if input already exceeds context limit
    if input_tokens > final_num_ctx:
        error_msg = (
            f"❌ Input too large: {format_number(input_tokens)} Tokens > "
            f"Context limit {format_number(final_num_ctx)} Tokens\n"
            f"   Please shorten your request or enable 'Manual Context' with a higher value."
        )
        # Only yield - UI will handle logging via add_debug() which calls log_message()
        yield {"type": "debug", "message": error_msg}

        # Reset UI progress state before returning error
        yield {"type": "progress", "phase": "idle"}
        yield {"type": "progress", "step": "ready"}

        yield {"type": "error", "message": error_msg}
        return

    # Temperature decision: Manual Override or Auto (reuse pre-detected intent)
    if temperature_mode == 'manual':
        final_temperature = temperature
        yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (manual)"}
    else:
        # Auto: Reuse detected_intent from state.py (no duplicate LLM call)
        intent = detected_intent or "chat"
        final_temperature = get_temperature_for_intent(intent)
        temp_label = get_temperature_label(intent)
        log_message(f"🌡️ RAG Temperature: {final_temperature} (Intent: {detected_intent})")
        yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (auto, {temp_label})"}

    # LLM Inference (with MoE/Dense architecture + calibration info)
    from ..gpu_utils import is_moe_model
    is_moe = is_moe_model(model_choice)
    arch_label = "MoE" if is_moe else "Dense"
    from ..model_vram_cache import get_llamacpp_calibration_info
    _cal = get_llamacpp_calibration_info(model_choice)
    if _cal and _cal["mode"] == "hybrid":
        arch_label += f", hybrid ngl={_cal['ngl']}"
    from ..agent_config import get_agent_label
    yield {"type": "debug", "message": f"{get_agent_label('aifred')}-LLM starting: {model_choice} ({arch_label})"}
    yield {"type": "progress", "phase": "llm"}

    # Build LLM options (include enable_thinking from user settings)
    # Note: num_predict intentionally omitted - Ollama generates until EOS or num_ctx full
    research_llm_options = {
        'temperature': final_temperature,
        'num_ctx': final_num_ctx
    }

    # Add enable_thinking if provided in llm_options (user toggle)
    if llm_options and 'enable_thinking' in llm_options:
        research_llm_options['enable_thinking'] = llm_options['enable_thinking']

    # Stream response using centralized utility
    ai_text = ""
    metrics = {}
    inference_time = 0.0
    tokens_per_sec = 0.0
    tokens_generated = 0
    tokens_prompt = 0
    ttft = None

    async for chunk in stream_llm_response(
        llm_client, model_choice, messages, research_llm_options,
        ttft_label="TTFT",
        toolkit=memory_toolkit,
    ):
        if chunk["type"] == "content":
            yield chunk
        elif chunk["type"] == "debug":
            yield chunk
        elif chunk["type"] == "thinking_warning":
            yield chunk
        elif chunk["type"] == "stream_result":
            # Final chunk with accumulated data
            ai_text = chunk["text"]
            metrics = chunk["metrics"]
            inference_time = chunk["inference_time"]
            tokens_per_sec = metrics.get("tokens_per_second", 0)
            tokens_generated = metrics.get("tokens_generated", 0)
            tokens_prompt = metrics.get("tokens_prompt", 0)
            ttft = chunk.get("ttft")

    # Strip thinking + update llm_history BEFORE history_tokens calculation
    ai_text_clean = strip_thinking_blocks(ai_text) if ai_text else ""
    if ai_text_clean:
        from ..message_builder import build_llm_history_entry
        llm_history.append(build_llm_history_entry(agent, ai_text_clean))

    # History tokens now reflect the current conversation state (incl. AI response)
    from ..context_manager import estimate_tokens_from_llm_history
    history_tokens = estimate_tokens_from_llm_history(llm_history)

    # Determine volatility: Automatik-LLM has priority, fallback to LLM tag or DAILY
    final_volatility = "DAILY"  # Default fallback

    if volatility and volatility in TTL_HOURS:
        # Automatik-LLM provided volatility - use it (most reliable)
        final_volatility = volatility
        log_message(f"✅ Volatility (Automatik): {final_volatility}")
        # Debug message already shown in Decision line (conversation_handler.py)
    else:
        # Fallback: Try to extract from LLM response tag
        volatility_match = re.search(r'<volatility>(.*?)</volatility>', ai_text, re.IGNORECASE | re.DOTALL)
        if volatility_match:
            extracted = volatility_match.group(1).strip().upper()
            if extracted in TTL_HOURS:
                final_volatility = extracted
                log_message(f"✅ Volatility (LLM-Tag): {final_volatility}")
            else:
                log_message(f"⚠️ Unknown volatility '{extracted}', fallback to DAILY")
        else:
            log_message("⚠️ No volatility from Automatik or LLM, fallback to DAILY")

    # Use final_volatility from here on
    volatility = final_volatility

    # Remove volatility tag from answer before displaying to user
    ai_text = re.sub(r'<volatility>.*?</volatility>', '', ai_text, flags=re.IGNORECASE | re.DOTALL).strip()

    # Create clean version without thinking blocks for cache storage
    # Handles both <think> (DeepSeek) and Harmony (GPT-OSS) formats
    ai_text_for_cache = strip_thinking_blocks(ai_text)

    # Separator after LLM response block (end of unit)
    from ..logging_utils import console_separator, CONSOLE_SEPARATOR
    console_separator()
    yield {"type": "debug", "message": CONSOLE_SEPARATOR}

    # Format thinking process (includes RAW logging - central point)
    thinking_html = format_thinking_process(
        ai_text,
        model_name=model_choice,
        inference_time=inference_time,
        tokens_per_sec=tokens_per_sec
    )

    # Build debug accordion with query reasoning (no RAW logging - done above)
    ai_response_complete = build_debug_accordion(
        query_reasoning=query_reasoning,
        ai_text=ai_text,
        automatik_model=automatik_model,
        main_model=model_choice,
        query_time=query_opt_time,
        final_time=inference_time
    )

    # Build sources collapsible (embedded in response like Denkprozess)
    # Extract used_sources for the collapsible
    used_sources_for_collapsible = [
        {
            "url": src.get("url", ""),
            "word_count": src.get("word_count", 0),
            "rank_index": src.get("rank_index", idx),
            "success": True
        }
        for idx, src in enumerate(scraped_only)
        if src.get("url")
    ]
    sources_collapsible = build_sources_collapsible(
        used_sources=used_sources_for_collapsible,
        failed_sources=failed_sources
    )

    # Prepend sources collapsible to response (before Denkprozess)
    if sources_collapsible:
        ai_response_complete = f"{sources_collapsible}\n\n{ai_response_complete}"

    # Centralized metadata (PP speed, debug log, chat bubble) - same as Own Knowledge
    total_time = agent_timer.elapsed()
    source_label = f"Web Research ({model_choice})"
    metadata_dict, metadata_display, debug_msg = build_inference_metadata(
        ttft=ttft,
        inference_time=inference_time,
        tokens_generated=tokens_generated,
        tokens_per_sec=tokens_per_sec,
        source=source_label,
        backend_metrics=metrics,
        tokens_prompt=tokens_prompt,
        history_tokens=history_tokens,
        backend_type=backend_type,
    )
    yield {"type": "debug", "message": debug_msg}
    metadata_dict["sources_count"] = len(scraped_only)

    # Build history content with sources collapsible prepended (like Denkprozess)
    history_content = thinking_html
    if sources_collapsible:
        history_content = f"{sources_collapsible}\n\n{history_content}"

    from ..message_builder import build_history_entry
    history.append(build_history_entry(
        agent, f"{history_content}\n\n{metadata_display}",
        "web_research", metadata_dict,
    ))
    # llm_history already updated above (before metadata calculation)

    log_message(f"✅ AI response generated ({len(ai_text)} chars, inference: {format_number(inference_time, 1)}s)")

    # ============================================================
    # Vector DB Auto-Learning: Save successful research to cache with TTL
    # ============================================================
    try:
        from ..vector_cache import get_cache

        # Main LLM already determined volatility via <volatility> tag
        # Save to cache with TTL-based expiry
        cache = get_cache()
        result = await cache.add(
            query=user_text,
            answer=ai_text_for_cache,  # Clean version without <think> block
            sources=scraped_only,
            failed_sources=failed_sources,  # URLs that couldn't be scraped
            metadata={
                'mode': mode,
                'volatility': volatility  # Main-LLM decision
            }
        )

        if result.get('success'):
            if result.get('skipped'):
                # NOCACHE volatility - intentionally not saved (already shown in Decision line)
                log_message(f"🚫 Vector Cache: Skipped (volatility={volatility})")
                yield {"type": "debug", "message": "🚫 Not cached (volatile)"}
            elif result.get('duplicate'):
                log_message("⚠️ Vector Cache: Duplicate detected, skipped")
                yield {"type": "debug", "message": "⚠️ Cache duplicate - not saved"}
            else:
                ttl_hours = TTL_HOURS.get(volatility)
                if ttl_hours is not None and ttl_hours > 0:
                    ttl_formatted = format_ttl_hours(ttl_hours)
                    log_message(f"💾 Vector Cache: Saved with {volatility} TTL ({ttl_formatted}, {result.get('total_entries')} entries)")
                    yield {"type": "debug", "message": f"💾 Saved to Cache (TTL: {ttl_formatted})"}
                else:
                    log_message(f"💾 Vector Cache: Saved as PERMANENT ({result.get('total_entries')} entries)")
                    yield {"type": "debug", "message": "💾 Saved to Cache (PERMANENT)"}
        else:
            log_message(f"⚠️ Vector Cache add failed: {result.get('error')}")

    except Exception as e:
        log_message(f"⚠️ Vector Cache auto-learning failed: {e}")

    # Separator after cache save (end of research unit)
    console_separator()
    yield {"type": "debug", "message": CONSOLE_SEPARATOR}

    # Subtle VRAM info (only when content fits)
    if vram_warning and input_tokens <= final_num_ctx:
        vram_diff = vram_warning["vram_diff"]
        potential_tokens = vram_warning.get("potential_tokens")

        info_msg = f"ℹ️ VRAM info: {vram_diff:+.0f}MB additional memory detected"

        if potential_tokens:
            info_msg += (
                f" (Context potential: {format_number(vram_warning['current_tokens'])} → "
                f"~{format_number(potential_tokens)} tokens). "
            )
        else:
            info_msg += ". "

        info_msg += "For extended capacity: Control Panel → vLLM Restart"

        log_message(info_msg)
        yield {"type": "debug", "message": info_msg}

    log_message(f"✅ Agent done: {format_number(total_time, 1)}s total, {len(ai_text)} chars")
    log_message("=" * 60)

    # Clear progress
    yield {"type": "progress", "clear": True}

    # Extract used_sources (successful URLs with word counts and rank) for UI display
    used_sources = [
        {
            "url": src.get("url", ""),
            "word_count": src.get("word_count", 0),
            "rank_index": src.get("rank_index", idx),  # Preserve ranking position
            "success": True  # Mark as successful for UI sorting
        }
        for idx, src in enumerate(scraped_only)
        if src.get("url")
    ]

    # Final result - unified Dict format
    yield {
        "type": "result",
        "data": {
            "response_clean": ai_text_clean,
            "response_html": ai_response_complete,
            "history": history,
            "inference_time": inference_time,
            "tokens_per_sec": tokens_per_sec,
            "ttft": ttft,
            "model_choice": model_choice,
            "failed_sources": failed_sources,
            "used_sources": used_sources,  # Successfully scraped URLs
        }
    }
