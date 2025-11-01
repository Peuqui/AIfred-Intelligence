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
from ..cache_manager import get_all_metadata_summaries, save_cached_research, generate_cache_metadata
from ..prompt_loader import load_prompt
from ..context_manager import calculate_dynamic_num_ctx
from ..message_builder import build_messages_from_history
from ..formatting import format_thinking_process, build_debug_accordion
from ..logging_utils import log_message, CONSOLE_SEPARATOR
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
    # METADATA: Integrate old research summaries
    # ============================================================
    old_research_metadata = get_all_metadata_summaries(exclude_session_id=session_id)
    metadata_context = ""

    if old_research_metadata:
        metadata_list = [
            f"- Recherche '{m['user_text'][:40]}...': {m['summary']}"
            for m in old_research_metadata[:5]  # Max 5 alte Recherchen
        ]
        metadata_context = "Frühere Recherchen (Zusammenfassungen):\n" + "\n".join(metadata_list)
        log_message(f"📚 {len(old_research_metadata)} alte Recherchen als Kontext eingebunden")

    # Build context from current sources
    context = build_context(user_text, scraped_only)

    # Combine: Old metadata + current sources
    if old_research_metadata:
        combined_context = f"{metadata_context}\n\n{'-'*60}\n\nAKTUELLE RECHERCHE:\n{context}"
        context = combined_context

        # Estimate tokens
        est_tokens = len(context) // CHARS_PER_TOKEN
        log_message(f"📊 Kombinierter Context: ~{est_tokens} Tokens (Alte Metadata + Aktuelle Quellen)")

    # Show context preview
    if len(context) > 800:
        preview = context[:800] + "..."
        log_message(f"📝 Context Preview (erste 800 Zeichen):\n{preview}")

    # System prompt
    yield {"type": "debug", "message": "📋 System-Prompt wird erstellt..."}

    system_prompt = load_prompt(
        'system_rag',
        current_year=time.strftime("%Y"),
        current_date=time.strftime("%d.%m.%Y"),
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

    # Dynamic num_ctx calculation
    final_num_ctx = await calculate_dynamic_num_ctx(llm_client, model_choice, messages, llm_options)
    yield {"type": "debug", "message": f"🪟 Context Window: {final_num_ctx} Tokens"}

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

    inference_start = time.time()
    ai_text = ""
    metrics = {}
    first_token_received = False

    # Stream response
    async for chunk in llm_client.chat_stream(
        model=model_choice,
        messages=messages,
        options={
            'temperature': final_temperature,
            'num_ctx': final_num_ctx
        }
    ):
        if chunk["type"] == "content":
            if not first_token_received:
                ttft = time.time() - inference_start
                first_token_received = True
                log_message(f"⚡ TTFT: {ttft:.2f}s")
                yield {"type": "debug", "message": f"⚡ TTFT: {ttft:.2f}s"}

            ai_text += chunk["text"]
            yield {"type": "content", "text": chunk["text"]}
        elif chunk["type"] == "done":
            metrics = chunk["metrics"]

    inference_time = time.time() - inference_start

    # Log completion
    tokens_generated = metrics.get("tokens_generated", 0)
    tokens_per_sec = metrics.get("tokens_per_second", 0)
    yield {"type": "debug", "message": f"✅ Haupt-LLM fertig ({inference_time:.1f}s, {tokens_generated} tokens, {tokens_per_sec:.1f} tok/s)"}

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
    timing_suffix = f" (Inferenz: {inference_time:.1f}s, {tokens_per_sec:.1f} tok/s)"

    if stt_time > 0:
        user_with_time = f"{user_text} (STT: {stt_time:.1f}s, Agent: {mode}, {len(scraped_only)} Quellen)"
    else:
        user_with_time = f"{user_text} (Agent: {mode}, {len(scraped_only)} Quellen)"

    history.append((user_with_time, thinking_html + timing_suffix))

    log_message(f"✅ AI-Antwort generiert ({len(ai_text)} Zeichen, Inferenz: {inference_time:.1f}s)")

    # Save to cache
    log_message(f"🔍 Cache-Speicherung: session_id = {session_id}, scraped = {len(scraped_only)} Quellen")
    save_cached_research(session_id, user_text, scraped_only, mode, metadata_summary=None)

    # Generate cache metadata (async, runs AFTER main response)
    log_message("🔧 Starte Cache-Metadata-Generierung (async generator)...")
    yield {"type": "debug", "message": "🔧 Cache-Metadata wird generiert..."}

    async for metadata_msg in generate_cache_metadata(
        session_id=session_id,
        metadata_model=automatik_model,
        llm_client=automatik_llm_client,
        haupt_llm_context_limit=final_num_ctx
    ):
        log_message(f"🔧 Metadata-Message weitergeleitet: {metadata_msg}")
        yield metadata_msg  # Forward debug messages to UI

    log_message("🔧 Cache-Metadata-Generierung abgeschlossen")

    log_message(f"✅ Agent fertig: {total_time:.1f}s gesamt, {len(ai_text)} Zeichen")
    log_message("=" * 60)

    # Clear progress
    yield {"type": "progress", "clear": True}

    # Separator
    yield {"type": "debug", "message": CONSOLE_SEPARATOR}

    # Final result
    yield {"type": "result", "data": (ai_response_complete, history, inference_time)}
