"""
Cache Handler - Handles cache hits from previous research sessions

Extracted from perform_agent_research() to improve modularity.
"""

import time
from typing import Dict, List, Optional, AsyncIterator

from ..cache_manager import get_cached_research
from ..agent_tools import build_context
from ..prompt_loader import load_prompt
from ..context_manager import estimate_tokens, calculate_dynamic_num_ctx
from ..intent_detector import detect_cache_followup_intent, get_temperature_for_intent
from ..formatting import format_thinking_process
from ..logging_utils import log_message, console_separator, CONSOLE_SEPARATOR


async def handle_cache_hit(
    session_id: Optional[str],
    user_text: str,
    history: List[tuple],
    model_choice: str,
    automatik_model: str,
    llm_client,
    automatik_llm_client,
    llm_options: Optional[Dict],
    temperature_mode: str,
    temperature: float,
    agent_start: float
) -> AsyncIterator[Dict]:
    """
    Handles cache hit - uses cached research data to answer follow-up question

    Args:
        session_id: Session ID for cache lookup
        user_text: Current user question
        history: Chat history
        model_choice: Main LLM model name
        automatik_model: Automatik LLM model name
        llm_client: Main LLM client
        automatik_llm_client: Automatik LLM client
        llm_options: LLM options (num_ctx override, etc.)
        temperature_mode: 'manual' or 'auto'
        temperature: Temperature value (if manual)
        agent_start: Start time for timing

    Yields:
        Dict: Progress, debug, content, and result events
    """
    # 0. Cache-Check
    cache_entry = get_cached_research(session_id)
    cached_sources = cache_entry.get('scraped_sources', []) if cache_entry else []

    if not cached_sources or not cache_entry:
        # No cache hit
        if session_id:
            log_message(f"⚠️ Kein Cache für Session {session_id[:8]}... gefunden → Normale Web-Recherche")
        return

    # Cache-Hit! (cache_entry is guaranteed not None here)
    log_message(f"💾 Cache-Hit! Nutze gecachte Recherche (Session {session_id[:8]}...)")
    log_message(f"   Ursprüngliche Frage: {cache_entry.get('user_text', 'N/A')[:80]}...")
    log_message(f"   Cache enthält {len(cached_sources)} Quellen")

    # Console-Output für Cache-Hit
    yield {"type": "debug", "message": f"💾 Cache-Hit! Nutze gecachte Daten ({len(cached_sources)} Quellen)"}
    original_q = cache_entry.get('user_text', 'N/A')
    yield {"type": "debug", "message": f"📋 Ursprüngliche Frage: {original_q[:60]}{'...' if len(original_q) > 60 else ''}"}

    # Nutze ALLE Quellen aus dem Cache
    scraped_only = cached_sources
    # Intelligenter Context (Limit aus config.py: MAX_RAG_CONTEXT_TOKENS)
    context = build_context(user_text, scraped_only)

    # System-Prompt für Cache-Hit: Nutze separate Prompt-Datei
    system_prompt = load_prompt(
        'system_rag_cache_hit',
        original_question=cache_entry.get('user_text', 'N/A'),
        current_question=user_text,
        current_year=time.strftime("%Y"),
        current_date=time.strftime("%d.%m.%Y"),
        context=context
    )

    # Generiere Antwort mit Cache-Daten
    messages = []

    # History hinzufügen (falls vorhanden) - LLM sieht vorherige Konversation
    for h in history:
        user_msg = h[0].split(" (STT:")[0].split(" (Agent:")[0] if " (STT:" in h[0] or " (Agent:" in h[0] else h[0]
        ai_msg = h[1].split(" (Inferenz:")[0] if " (Inferenz:" in h[1] else h[1]
        messages.extend([
            {'role': 'user', 'content': user_msg},
            {'role': 'assistant', 'content': ai_msg}
        ])

    # System-Prompt + aktuelle User-Frage
    messages.insert(0, {'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_text})

    # Query Haupt-Model Context Limit (falls nicht manuell gesetzt)
    if not (llm_options and llm_options.get('num_ctx')):
        model_limit = await llm_client.get_model_context_limit(model_choice)
        log_message(f"📊 Haupt-LLM ({model_choice}): Max. Context = {model_limit} Tokens (Modell-Parameter von Ollama)")
        yield {"type": "debug", "message": f"📊 Haupt-LLM ({model_choice}): Max. Context = {model_limit} Tokens"}

    # Dynamische num_ctx Berechnung für Cache-Hit (Haupt-LLM)
    final_num_ctx = await calculate_dynamic_num_ctx(llm_client, model_choice, messages, llm_options)
    if llm_options and llm_options.get('num_ctx'):
        log_message(f"🎯 Cache-Hit Context Window: {final_num_ctx} Tokens (manuell)")
        yield {"type": "debug", "message": f"🪟 Context Window: {final_num_ctx} Tokens (manual)"}
    else:
        estimated_tokens = estimate_tokens(messages)
        log_message(f"🎯 Cache-Hit Context Window: {final_num_ctx} Tokens (dynamisch, ~{estimated_tokens} Tokens benötigt)")
        yield {"type": "debug", "message": f"🪟 Context Window: {final_num_ctx} Tokens (auto)"}

    # Temperature entscheiden: Manual Override oder Auto (Intent-Detection)
    if temperature_mode == 'manual':
        final_temperature = temperature
        log_message(f"🌡️ Cache-Hit Temperature: {final_temperature} (MANUAL OVERRIDE)")
        yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (manual)"}
    else:
        # Auto: Intent-Detection für Cache-Followup
        followup_intent = await detect_cache_followup_intent(
            original_query=cache_entry.get('user_text', ''),
            followup_query=user_text,
            automatik_model=automatik_model,
            llm_client=automatik_llm_client
        )
        final_temperature = get_temperature_for_intent(followup_intent)
        log_message(f"🌡️ Cache-Hit Temperature: {final_temperature} (Intent: {followup_intent})")
        yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (auto, {followup_intent})"}

    # Console: LLM starts
    yield {"type": "debug", "message": f"🤖 Haupt-LLM startet: {model_choice} (Cache-Daten)"}

    # Show LLM generation phase
    yield {"type": "progress", "phase": "llm"}

    llm_start = time.time()
    final_answer = ""
    metrics = {}
    ttft = None
    first_token_received = False

    # Stream response from LLM
    async for chunk in llm_client.chat_stream(
        model=model_choice,
        messages=messages,
        options={
            'temperature': final_temperature,  # Adaptive oder Manual Temperature!
            'num_ctx': final_num_ctx  # Dynamisch berechnet oder User-Vorgabe
        }
    ):
        if chunk["type"] == "content":
            # Measure TTFT
            if not first_token_received:
                ttft = time.time() - llm_start
                first_token_received = True
                log_message(f"⚡ TTFT (Time-to-First-Token): {ttft:.2f}s")
                yield {"type": "debug", "message": f"⚡ TTFT: {ttft:.2f}s"}

            final_answer += chunk["text"]
            yield {"type": "content", "text": chunk["text"]}
        elif chunk["type"] == "done":
            metrics = chunk["metrics"]

    llm_time = time.time() - llm_start
    total_time = time.time() - agent_start

    # Console: LLM finished
    tokens_generated = metrics.get("tokens_generated", 0)
    tokens_per_sec = metrics.get("tokens_per_second", 0)
    yield {"type": "debug", "message": f"✅ Haupt-LLM fertig ({llm_time:.1f}s, {tokens_generated} tokens, {tokens_per_sec:.1f} tok/s, Cache-Total: {total_time:.1f}s)"}

    # Formatiere <think> Tags als Collapsible (falls vorhanden)
    final_answer_formatted = format_thinking_process(final_answer, model_name=model_choice, inference_time=llm_time)

    # Zeitmessung-Text
    timing_text = f" (Cache-Hit: {total_time:.1f}s = LLM {llm_time:.1f}s, {tokens_per_sec:.1f} tok/s)"
    ai_text_with_timing = final_answer_formatted + timing_text

    # Update History
    user_display = f"{user_text} (Agent: Cache-Hit, {len(cached_sources)} Quellen)"
    ai_display = ai_text_with_timing
    history.append((user_display, ai_display))

    log_message(f"✅ Cache-basierte Antwort fertig in {total_time:.1f}s")

    # Clear progress before final result
    yield {"type": "progress", "clear": True}

    # Separator nach Cache-Hit (Log-File + Debug-Konsole)
    console_separator()
    yield {"type": "debug", "message": CONSOLE_SEPARATOR}

    # Yield final result
    yield {"type": "result", "data": (ai_text_with_timing, history, total_time)}
