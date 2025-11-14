"""
Context Builder - Build context and generate LLM response

Handles:
- Context building from scraped sources
- Integration of old research metadata
- Message construction with history
- LLM inference and response generation
- Cache saving
"""

import time
from typing import Dict, List, Optional, AsyncIterator

from ..agent_tools import build_context
# Cache system removed - will be replaced with Vector DB
from ..prompt_loader import load_prompt
from ..context_manager import calculate_dynamic_num_ctx, estimate_tokens
from ..message_builder import build_messages_from_history
from ..formatting import format_thinking_process, build_debug_accordion, format_metadata
from ..logging_utils import log_message
from ..config import CHARS_PER_TOKEN
from ..intent_detector import detect_query_intent, get_temperature_for_intent, get_temperature_label


async def build_and_generate_response(
    user_text: str,
    scraped_results: List[Dict],
    tool_results: List[Dict],
    history: List[tuple],
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
    agent_start: float,
    stt_time: float
) -> AsyncIterator[Dict]:
    """
    Build context and generate LLM response

    Args:
        user_text: User's question
        scraped_results: Successfully scraped sources
        tool_results: All tool results (including failed)
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

    Yields:
        Dict: Debug messages, content chunks, final result
    """
    # Filter: Only successfully scraped sources
    scraped_only = [r for r in tool_results if 'word_count' in r and r['word_count'] > 0]

    # DEBUG logging (can be removed in production)
    for i, result in enumerate(tool_results, 1):
        if result.get('success'):
            log_message(f"  Quelle {i}: {result.get('word_count', 0)} Wörter von {result.get('url', 'N/A')[:50]}")

    if scraped_only:
        for i, src in enumerate(scraped_only[:3], 1):
            content_preview = src.get('content', '')[:200].replace('\n', ' ')
            log_message(f"  📄 Quelle {i} Preview: {content_preview}...")

    # ============================================================
    # Build context from current sources (old metadata system removed)
    # TODO: Replace with Vector DB semantic search in Phase 1
    # ============================================================
    context = build_context(user_text, scraped_only)

    # Estimate tokens
    est_tokens = len(context) // CHARS_PER_TOKEN
    log_message(f"📊 Context: ~{est_tokens} Tokens")

    # Show context preview
    if len(context) > 800:
        preview = context[:800] + "..."
        log_message(f"📝 Context Preview (erste 800 Zeichen):\n{preview}")

    # Spracherkennung für User-Text
    from ..prompt_loader import detect_language
    detected_user_language = detect_language(user_text)

    # System prompt (timestamp injected automatically by load_prompt)
    system_prompt = load_prompt(
        'system_rag',
        lang=detected_user_language,
        user_text=user_text,
        context=context
    )

    yield {"type": "debug", "message": "✅ System-Prompt erstellt"}

    # Build messages with history
    messages = build_messages_from_history(history, user_text)

    # Insert system prompt as first message
    messages.insert(0, {"role": "system", "content": system_prompt})

    # DEBUG: Show message sizes
    for i, msg in enumerate(messages):
        role = msg['role']
        content_len = len(msg['content'])
        log_message(f"  Message {i+1} ({role}): {content_len} Zeichen")

    # Count actual input tokens (using real tokenizer)
    input_tokens = estimate_tokens(messages, model_name=model_choice)

    # Dynamic num_ctx calculation
    final_num_ctx = await calculate_dynamic_num_ctx(llm_client, model_choice, messages, llm_options)

    # Get model max context for compact display
    model_limit = await llm_client.get_model_context_limit(model_choice)

    # Show compact context info (like Automatik-LLM)
    yield {"type": "debug", "message": f"📊 Haupt-LLM: {input_tokens} / {final_num_ctx} Tokens (max: {model_limit})"}

    # Temperature
    if temperature_mode == 'manual':
        final_temperature = temperature
        yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (manual)"}
    else:
        # Auto: Intent-Detection für RAG-Recherche (faktisch/gemischt/kreativ)
        rag_intent = await detect_query_intent(
            user_query=user_text,
            automatik_model=automatik_model,
            llm_client=automatik_llm_client
        )
        final_temperature = get_temperature_for_intent(rag_intent)
        temp_label = get_temperature_label(rag_intent)
        log_message(f"🌡️ RAG Temperature: {final_temperature} (Intent: {rag_intent})")
        yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (auto, {temp_label})"}

    # LLM Inference
    yield {"type": "debug", "message": f"🤖 Haupt-LLM startet: {model_choice}"}
    yield {"type": "progress", "phase": "llm"}

    # Build LLM options (include enable_thinking from user settings)
    research_llm_options = {
        'temperature': final_temperature,
        'num_ctx': final_num_ctx
    }

    # Add enable_thinking if provided in llm_options (user toggle)
    if llm_options and 'enable_thinking' in llm_options:
        research_llm_options['enable_thinking'] = llm_options['enable_thinking']

    inference_start = time.time()
    ai_text = ""
    metrics = {}
    first_token_received = False

    # Stream response
    async for chunk in llm_client.chat_stream(
        model=model_choice,
        messages=messages,
        options=research_llm_options
    ):
        if chunk["type"] == "content":
            if not first_token_received:
                ttft = time.time() - inference_start
                first_token_received = True
                log_message(f"⚡ TTFT: {ttft:.2f}s")
                yield {"type": "debug", "message": f"⚡ TTFT: {ttft:.2f}s"}

            ai_text += chunk["text"]
            yield {"type": "content", "text": chunk["text"]}
        elif chunk["type"] == "debug":
            # Forward debug messages from backend (e.g., thinking mode retry warning)
            yield chunk
        elif chunk["type"] == "thinking_warning":
            # Forward thinking mode warning (model doesn't support reasoning)
            yield chunk
        elif chunk["type"] == "done":
            metrics = chunk["metrics"]

    inference_time = time.time() - inference_start

    # Log completion
    tokens_generated = metrics.get("tokens_generated", 0)
    tokens_per_sec = metrics.get("tokens_per_second", 0)
    yield {"type": "debug", "message": f"✅ Haupt-LLM fertig ({inference_time:.1f}s, {tokens_generated} tokens, {tokens_per_sec:.1f} tok/s)"}

    # Separator nach LLM-Antwort-Block (Ende der Einheit)
    from ..logging_utils import console_separator, CONSOLE_SEPARATOR
    console_separator()
    yield {"type": "debug", "message": CONSOLE_SEPARATOR}

    # Format thinking process
    thinking_html = format_thinking_process(ai_text, model_name=model_choice, inference_time=inference_time)

    # Build debug accordion with query reasoning
    ai_response_complete = build_debug_accordion(
        query_reasoning=query_reasoning,
        ai_text=ai_text,
        automatik_model=automatik_model,
        main_model=model_choice,
        query_time=query_opt_time,
        final_time=inference_time
    )

    # Update history
    total_time = time.time() - agent_start
    metadata = format_metadata(f"(Inferenz: {inference_time:.1f}s, {tokens_per_sec:.1f} tok/s, Quelle: Web-Recherche)")

    if stt_time > 0:
        user_metadata = format_metadata(f"(STT: {stt_time:.1f}s, Agent: {mode}, {len(scraped_only)} Quellen)")
        user_with_time = f"{user_text} {user_metadata}"
    else:
        user_metadata = format_metadata(f"(Agent: {mode}, {len(scraped_only)} Quellen)")
        user_with_time = f"{user_text} {user_metadata}"

    history.append((user_with_time, thinking_html + " " + metadata))

    log_message(f"✅ AI-Antwort generiert ({len(ai_text)} Zeichen, Inferenz: {inference_time:.1f}s)")

    # ============================================================
    # Vector DB Auto-Learning: Save successful research to cache
    # ============================================================
    try:
        from ..vector_cache import get_cache
        from ..config import CACHE_EXCLUDE_VOLATILE

        # Step 1: Check for volatile keywords
        user_text_lower = user_text.lower()
        has_volatile_keyword = any(keyword in user_text_lower for keyword in CACHE_EXCLUDE_VOLATILE)

        should_cache = True  # Default: cache everything

        if has_volatile_keyword:
            # Volatile keyword found → Ask LLM for override decision
            log_message("⚠️ Volatile keyword detected in query, asking LLM for cache decision...")
            yield {"type": "debug", "message": "🤔 Volatile keyword → Checking if cacheable..."}

            # Spracherkennung für User-Text
            from ..prompt_loader import detect_language
            detected_user_language = detect_language(user_text)

            # Load cache decision prompt with current date
            answer_preview = ai_text[:300] + "..." if len(ai_text) > 300 else ai_text
            current_date = time.strftime("%d.%m.%Y")
            cache_prompt = load_prompt("cache_decision", lang=detected_user_language, query=user_text, answer_preview=answer_preview, current_date=current_date)

            # Ask Automatik-LLM for decision (use existing client to avoid deadlock)
            try:
                response = await automatik_llm_client.chat(
                    model=automatik_model,
                    messages=[{'role': 'user', 'content': cache_prompt}],
                    options={'temperature': 0.1, 'num_ctx': 2048, 'enable_thinking': False}  # Very deterministic, no reasoning
                )
                decision = response.text.strip().lower()

                if 'cacheable' in decision and 'not_cacheable' not in decision:
                    should_cache = True
                    log_message("✅ LLM Override: Cacheable (concept question with volatile keyword)")
                    yield {"type": "debug", "message": "✅ LLM: Cacheable (override)"}
                else:
                    should_cache = False
                    log_message("❌ LLM Decision: Not cacheable (volatile data)")
                    yield {"type": "debug", "message": "❌ LLM: Not cacheable (volatile)"}
            except Exception as e:
                log_message(f"⚠️ LLM cache decision failed: {e}, defaulting to NOT cache")
                should_cache = False
        else:
            # No volatile keyword → Ask LLM for normal decision
            log_message("🤔 No volatile keyword, asking LLM for cache decision...")
            yield {"type": "debug", "message": "🤔 Checking if cacheable..."}

            answer_preview = ai_text[:300] + "..." if len(ai_text) > 300 else ai_text
            current_date = time.strftime("%d.%m.%Y")
            cache_prompt = load_prompt("cache_decision", query=user_text, answer_preview=answer_preview, current_date=current_date)

            # Use existing client to avoid deadlock
            try:
                response = await automatik_llm_client.chat(
                    model=automatik_model,
                    messages=[{'role': 'user', 'content': cache_prompt}],
                    options={'temperature': 0.1, 'num_ctx': 2048, 'enable_thinking': False}  # Fast decision, no reasoning
                )
                decision = response.text.strip().lower()

                if 'cacheable' in decision and 'not_cacheable' not in decision:
                    should_cache = True
                    log_message("✅ LLM Decision: Cacheable")
                    yield {"type": "debug", "message": "✅ LLM: Cacheable"}
                else:
                    should_cache = False
                    log_message("❌ LLM Decision: Not cacheable")
                    yield {"type": "debug", "message": "❌ LLM: Not cacheable"}
            except Exception as e:
                log_message(f"⚠️ LLM cache decision failed: {e}, defaulting to cache")
                should_cache = True  # Bei Fehler: cachen (safe default)

        # Step 2: Save to cache if decision is positive
        if should_cache:
            cache = get_cache()
            result = await cache.add(
                query=user_text,
                answer=ai_text,
                sources=scraped_only,
                metadata={'mode': mode}
            )

            if result.get('success'):
                if result.get('duplicate'):
                    log_message("⚠️ Vector Cache: Duplicate detected, skipped")
                    yield {"type": "debug", "message": "⚠️ Cache duplicate - not saved"}
                else:
                    log_message(f"💾 Vector Cache: Auto-learned from web research ({result.get('total_entries')} entries)")
                    yield {"type": "debug", "message": "💾 Saved to Vector Cache"}
            else:
                log_message(f"⚠️ Vector Cache add failed: {result.get('error')}")
        else:
            log_message("🚫 Vector Cache: Skipped (LLM decision: not cacheable)")
            yield {"type": "debug", "message": "🚫 Not cached (volatile data)"}

    except Exception as e:
        log_message(f"⚠️ Vector Cache auto-learning failed: {e}")

    # Separator nach Cache-Decision-Block (Ende der Einheit)
    from ..logging_utils import console_separator, CONSOLE_SEPARATOR
    console_separator()
    yield {"type": "debug", "message": CONSOLE_SEPARATOR}

    log_message(f"✅ Agent fertig: {total_time:.1f}s gesamt, {len(ai_text)} Zeichen")
    log_message("=" * 60)

    # Clear progress
    yield {"type": "progress", "clear": True}

    # Final result
    yield {"type": "result", "data": (ai_response_complete, history, inference_time)}
