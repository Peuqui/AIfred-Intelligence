"""
LLM Engine - Zentrale Funktion für LLM-Aufrufe aller Agenten.

Diese Funktion wird aufgerufen von:
- state.py: research_mode == "none"
- state.py: research_mode in ["quick", "deep"] wenn LLM web=false entscheidet
- conversation_handler.py: Automatik-Modus wenn LLM web=false entscheidet

Yields Dict-Messages die vom Aufrufer geroutet werden:
- {"type": "debug", "message": str}
- {"type": "content", "text": str}
- {"type": "progress", ...}
- {"type": "result", "data": {...}}
"""

from typing import AsyncIterator, Dict, List, Optional, Any, cast

from .llm_client import LLMClient, build_llm_options
from .formatting import format_number, build_inference_metadata
from .prompt_loader import get_agent_direct_prompt, get_agent_system_prompt, load_prompt
from .context_manager import estimate_tokens
from .intent_detector import get_temperature_for_intent, get_temperature_label
from .logging_utils import log_message
from .message_builder import inject_rag_context
from .research.context_utils import get_agent_num_ctx



async def call_llm(
    user_text: str,
    model_choice: str,
    history: List,  # chat_history - will be updated and returned in result
    llm_history: List[Dict[str, str]],
    detected_intent: str,
    detected_language: str,
    temperature_mode: str,
    temperature: float,
    backend_type: str,
    backend_url: Optional[str],
    enable_thinking: bool,
    state: Optional[Any] = None,
    use_direct_prompt: bool = False,
    multimodal_content: Optional[List[Dict]] = None,
    rag_context: Optional[str] = None,
    memory_ctx: Optional[str] = None,
    vision_json_context: Optional[Dict] = None,
    stt_time: float = 0.0,
    cloud_provider_label: Optional[str] = None,
    num_ctx_manual_enabled: bool = False,
    num_ctx_manual_value: Optional[int] = None,
    provider: Optional[str] = None,
    agent: str = "aifred",
    vision_prompt_key: str = "task",
    external_toolkit: Optional[Any] = None,
    num_ctx_source_label: str = "",
    source: str = "browser",
) -> AsyncIterator[Dict]:
    """
    Generiert eine LLM-Antwort basierend auf eigenem Wissen (ohne Web-Recherche).

    Args:
        user_text: Die User-Nachricht
        model_choice: Model ID (z.B. "qwen3:4b")
        history: Chat History (wird aktualisiert und im Result zurückgegeben)
        llm_history: LLM History (Liste von {"role": ..., "content": ...})
        detected_intent: Intent für Temperature-Bestimmung (z.B. "FAKTISCH")
        detected_language: Sprache für Prompts ("de" oder "en")
        temperature_mode: "auto" oder "manual"
        temperature: Temperature-Wert (nur bei manual relevant)
        backend_type: Backend-Typ ("ollama", "vllm", "tabbyapi", "llamacpp", "cloud_api")
        backend_url: Backend-URL
        enable_thinking: Thinking Mode aktiviert?
        state: AIState Objekt für num_ctx Lookup (optional)
        use_direct_prompt: True wenn User AIfred direkt angesprochen hat
        multimodal_content: Multimodal Content für Bilder (optional)
        rag_context: Web-/Dokument-Recherche-Kontext (wird im "RECHERCHE-ERGEBNISSE"-Wrapper eingebettet — NUR für echte Recherchedaten nutzen)
        memory_ctx: Agent-Memory-Kontext aus prepare_agent_toolkit (wird roh an system_prompt angehängt — für vom Caller bereits gesammelte Erinnerungen)
        vision_json_context: Vision JSON Kontext (optional)
        stt_time: Speech-to-Text Zeit für Metadata (optional)
        cloud_provider_label: Cloud Provider Label für Debug-Ausgabe (optional)
        num_ctx_manual_enabled: Manuelles num_ctx aktiviert?
        num_ctx_manual_value: Manueller num_ctx Wert

    Yields:
        Dict mit type und payload:
        - {"type": "debug", "message": str}
        - {"type": "content", "text": str}
        - {"type": "progress", "phase": str} oder {"type": "progress", "clear": True}
        - {"type": "result", "data": {...}} - Finale Ergebnisse
    """
    yield {"type": "progress", "phase": "llm"}

    # Compute proper display name early (used in all debug output)
    from .agent_config import get_agent_config
    _agent_cfg = get_agent_config(agent)
    agent_label = _agent_cfg.display_name if _agent_cfg else agent.capitalize()

    # Build messages using centralized function (v2.16.0+)
    # Uses perspective="aifred" to correctly assign roles for all agents
    from .message_builder import build_messages_from_llm_history

    # Perspective must match the actual agent — NOT hardcoded to "aifred".
    # build_messages_from_llm_history uses the perspective to load the correct
    # personality reminder (prompts/<lang>/<agent>/reminder.txt) as user-message
    # prefix. With "aifred" hardcoded, every agent (Pater Tuck, Sokrates, etc.)
    # would get AIfred's "[STIL: Britischer Butler]" reminder → agent-identity leak.
    messages: list[dict[str, Any]]
    if multimodal_content is not None:
        # Multimodal: Build without user text, then append multimodal content
        messages = list(build_messages_from_llm_history(
            llm_history,
            perspective=agent,
            detected_language=detected_language
        ))
        messages.append({"role": "user", "content": multimodal_content})
        log_message("📷 Multimodal content injected into user message")
    else:
        # Standard: Include user text directly
        messages = list(build_messages_from_llm_history(
            llm_history,
            current_user_text=user_text,
            perspective=agent,
            detected_language=detected_language
        ))

    # Inject system prompt (agent-aware)
    # When multimodal content is present, use the real agent's prompt stack
    # (identity, personality, memory, tools) + vision task as addon.
    if multimodal_content is not None and agent != "vision":
        # Real agent prompt (with full layer stack: identity, tools, memory, personality)
        system_prompt = get_agent_system_prompt(agent, "task", lang=detected_language, source=source)
        # Append vision-specific task instructions as addon
        # vision_prompt_key: "task" → vision_ocr, "task_qa" → vision_qa
        vision_file = "vision/vision_qa" if vision_prompt_key == "task_qa" else "vision/vision_ocr"
        vision_addon = load_prompt(vision_file, lang=detected_language)
        if vision_addon:
            system_prompt = f"{system_prompt}\n\n{vision_addon}"
    elif agent == "vision":
        # Legacy fallback: explicit "vision" agent (backward compat for direct VL calls)
        system_prompt = get_agent_system_prompt("vision", vision_prompt_key, lang=detected_language)
    elif use_direct_prompt:
        system_prompt = get_agent_direct_prompt(agent, lang=detected_language)
    else:
        system_prompt = get_agent_system_prompt(agent, "task", lang=detected_language, source=source)
    # Agent Memory: recall + toolkit
    # Three possible sources (SSOT: memory_ctx is always appended RAW to system_prompt,
    # never wrapped in the "web research" rag_context template):
    #   1. external_toolkit + memory_ctx from caller (Message Hub / Scheduler path)
    #   2. state-based self-lookup (legacy VL / direct call_llm path with agent_memory_enabled)
    #   3. No memory (multi_agent.py path assembles memory_ctx itself before calling)
    memory_toolkit = external_toolkit
    if memory_toolkit:
        # External toolkit provided (e.g. from Message Hub / Scheduler) — already logged by caller
        pass
    elif state and getattr(state, 'agent_memory_enabled', False):
        from .agent_memory import prepare_agent_toolkit
        self_memory_ctx, memory_toolkit = await prepare_agent_toolkit(
            agent, user_text, lang=detected_language or "de",
            research_tools_enabled=False,
        )
        # state-based lookup overrides any memory_ctx parameter (state-path is authoritative here)
        if self_memory_ctx:
            memory_ctx = self_memory_ctx
        if memory_toolkit:
            yield {"type": "debug", "message": f"🔧 Toolkit: {[t.name for t in memory_toolkit.tools]}"}

    # Append memory_ctx RAW to system_prompt (matches the SSOT used by multi_agent.py:723
    # and the state-based path above). DO NOT route through rag_context — that wraps the
    # content in the "# AKTUELLE RECHERCHE-ERGEBNISSE" web-search template which confuses
    # the agent when the payload is really agent memory, not web research.
    if memory_ctx:
        system_prompt = f"{system_prompt}\n\n{memory_ctx}"
        mem_tok = estimate_tokens([{"content": memory_ctx}])
        yield {"type": "debug", "message": f"🧠 Memory context injected ({mem_tok:,} tok)"}

    messages.insert(0, {"role": "system", "content": system_prompt})

    # Inject RAG context — ONLY for real web/document research results.
    # The rag_context wrapper (shared/rag_context.txt) tells the LLM:
    # "You have internet access, these are research results, do not use training data".
    # That is correct for web_search/document_store hits, but WRONG for agent memory
    # (which is handled above via memory_ctx).
    if rag_context:
        inject_rag_context(messages, rag_context)
        rag_tok = estimate_tokens([{"content": rag_context}])
        yield {"type": "debug", "message": f"💡 RAG context injected ({rag_tok:,} tok)"}

    # Inject Vision JSON context if available
    if vision_json_context:
        from .message_builder import inject_vision_json_context
        inject_vision_json_context(messages, vision_json_context)
        log_message(f"📷 Vision JSON injected ({len(str(vision_json_context))} chars)")

    # Create LLM client
    llm_client = LLMClient(backend_type=backend_type, base_url=backend_url, provider=provider)

    try:
        # Temperature decision
        if temperature_mode == 'manual':
            final_temperature = temperature
            yield {"type": "debug", "message": f"🌡️ Temperature: {format_number(final_temperature, 1)} (manual)"}
        else:
            final_temperature = get_temperature_for_intent(detected_intent)
            temp_label = get_temperature_label(detected_intent)
            yield {"type": "debug", "message": f"🌡️ Temperature: {format_number(final_temperature, 1)} (auto, {temp_label})"}

        # Calculate num_ctx
        if num_ctx_manual_enabled and num_ctx_manual_value:
            final_num_ctx = num_ctx_manual_value
            if num_ctx_source_label:
                yield {"type": "debug", "message": f"🎯 Context: {format_number(final_num_ctx)} ({num_ctx_source_label})"}
        else:
            # Use centralized SSOT: get_agent_num_ctx handles vision_num_ctx_enabled,
            # VRAM calibration cache, XTTS reservation and backend-specific logic.
            assert state is not None, "state required for num_ctx lookup"
            final_num_ctx, ctx_source = get_agent_num_ctx(agent, state, model_choice)
            yield {"type": "debug", "message": f"🎯 Context: {format_number(final_num_ctx)} ({ctx_source})"}

        # Count input tokens
        input_tokens = estimate_tokens(messages, model_name=model_choice)

        # Get native context limit for display (local read, no API call!)
        from .research.context_utils import get_model_native_context
        model_limit = get_model_native_context(model_choice, backend_type)

        # Show context info
        yield {"type": "debug", "message": f"📊 {agent_label}: {format_number(input_tokens)} / {format_number(final_num_ctx)} tokens (max: {format_number(model_limit)})"}

        # Build LLM options via central builder (all sampling params from state).
        llm_options = build_llm_options(state, agent, final_temperature, final_num_ctx)  # type: ignore[arg-type]

        # Console: LLM starts (with MoE/Dense architecture + calibration info)
        from .gpu_utils import is_moe_model
        is_moe = is_moe_model(model_choice) if backend_type in ("ollama", "llamacpp") else False
        arch_label = "MoE" if is_moe else "Dense"
        if backend_type == "llamacpp":
            from .model_vram_cache import get_llamacpp_calibration_info
            _cal = get_llamacpp_calibration_info(model_choice)
            if _cal and _cal["mode"] == "hybrid":
                arch_label += f", hybrid ngl={_cal['ngl']}"
        from .agent_config import get_agent_emoji
        yield {"type": "debug", "message": f"{get_agent_emoji(agent)} {agent_label}-LLM starting: {model_choice} ({arch_label})"}

        # Stream response via unified pipeline (handles TTFT, tool tracking, metadata)
        from .llm_pipeline import run_llm_stream, PipelineResult
        log_message("🔬 DEBUG: Starting inference")

        pipeline_result: PipelineResult | None = None

        async for event in run_llm_stream(
            llm_client, model_choice, cast(list, messages), llm_options, agent_label,
            toolkit=memory_toolkit, retry=False,
        ):
            event_type = event["type"]
            if event_type == "content":
                yield {"type": "content", "text": event["text"]}
            elif event_type == "ttft":
                yield {"type": "debug", "message": f"⚡ TTFT: {format_number(event['value'], 2)}s"}
            elif event_type == "tool_call":
                yield {"type": "debug", "message": f"🔧 Tool call: {event.get('name', '')}(...)"}
            elif event_type == "tool_result":
                yield {"type": "debug", "message": f"🔧 Tool result: {event.get('result', '')[:100]}"}
            elif event_type == "pipeline_result":
                pipeline_result = event["result"]

        if not pipeline_result:
            yield {"type": "progress", "clear": True}
            return

        response_clean = pipeline_result.text_clean
        thinking_html = pipeline_result.thinking_html

        # Update llm_history BEFORE calculating history_tokens
        # so "History: X tok" reflects the current conversation state (incl. AI response)
        if response_clean:
            from .message_builder import build_llm_history_entry
            llm_history.append(build_llm_history_entry(agent, response_clean))

        # Rebuild metadata with hub-specific params (history_tokens, backend_type, source_label)
        from .context_manager import estimate_tokens_from_llm_history
        history_tokens = estimate_tokens_from_llm_history(llm_history)
        source_label = f"VL ({model_choice})" if agent == "vision" else f"{agent_label} ({model_choice})"

        metadata_dict, metadata_display, debug_msg = build_inference_metadata(
            ttft=pipeline_result.ttft,
            inference_time=pipeline_result.inference_time,
            tokens_generated=pipeline_result.metrics.get("tokens_generated", 0),
            tokens_per_sec=pipeline_result.tokens_per_sec,
            source=source_label,
            backend_metrics=pipeline_result.metrics,
            tokens_prompt=pipeline_result.metrics.get("tokens_prompt", 0),
            history_tokens=history_tokens,
            backend_type=backend_type,
            agent_label=agent_label,
        )
        yield {"type": "debug", "message": debug_msg}

        # Update chat_history (UI display with thinking + metadata)
        from .message_builder import build_history_entry
        ai_with_source = f"{thinking_html}\n\n{metadata_display}"
        history.append(build_history_entry(agent, ai_with_source, "own_knowledge", metadata_dict))

        # Clear progress
        yield {"type": "progress", "clear": True}

        # Final result - unified Dict format
        yield {
            "type": "result",
            "data": {
                "response_clean": response_clean,
                "response_html": thinking_html,
                "history": history,
                "llm_history": llm_history,
                "inference_time": pipeline_result.inference_time,
                "tokens_per_sec": pipeline_result.tokens_per_sec,
                "ttft": pipeline_result.ttft,
                "model_choice": model_choice,
                "failed_sources": [],
                "metadata_dict": metadata_dict,
            }
        }

    finally:
        await llm_client.close()


async def generate_session_title(
    user_text: str,
    ai_response: str,
    session_id: str,
    lang: str = "",
    model_override: str = "",
    num_ctx_override: int = 0,
) -> str:
    """Generate a session title via LLM. Central function for all callers.

    Uses the AIfred model from settings (no separate Automatik model needed).
    Can be called with or without Reflex state.

    Args:
        user_text: First user message
        ai_response: First AI response
        session_id: Session to update
        lang: Language for prompt (default: current language from settings)
        model_override: Use this model instead of AIfred model (e.g. after Vision)
        num_ctx_override: Use this num_ctx (e.g. to avoid Ollama reload)

    Returns:
        Generated title string (empty on failure)
    """
    import asyncio
    import re
    from .llm_client import LLMClient
    from .prompt_loader import load_prompt, get_language
    from .session_storage import update_session_title
    from .settings import load_settings
    from .config import DEFAULT_SETTINGS, BACKEND_URLS, AUTOMATIK_LLM_NUM_CTX
    from .context_manager import strip_thinking_blocks
    from .logging_utils import log_message

    settings = load_settings() or {}
    backend_type = settings.get("backend_type", DEFAULT_SETTINGS["backend_type"])
    backend_url = BACKEND_URLS.get(backend_type, "")

    # Model: override > effective model from settings (respects TTS/speed variants)
    if model_override:
        model = model_override
    else:
        from .config import get_effective_model_from_settings
        model = get_effective_model_from_settings("aifred")

    if not model:
        return ""

    # Truncate and clean inputs
    user_short = re.sub(r'<[^>]+>', '', user_text).strip()[:500]
    ai_short = strip_thinking_blocks(ai_response)[:500] if ai_response else ""
    if not user_short or not ai_short:
        return ""

    prompt = load_prompt(
        "utility/chat_title",
        lang=lang or get_language(),
        user_message=user_short,
        ai_response=ai_short,
    )

    num_ctx = num_ctx_override or AUTOMATIK_LLM_NUM_CTX

    try:
        provider = settings.get("cloud_api_provider") if backend_type == "cloud_api" else None
        client = LLMClient(backend_type=backend_type, base_url=backend_url, provider=provider)
        response = await asyncio.wait_for(
            client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={
                    "temperature": 0.3,
                    "num_predict": 300,
                    "enable_thinking": False,
                    "num_ctx": num_ctx,
                },
            ),
            timeout=30.0,
        )

        title = strip_thinking_blocks(response.text.strip()).strip().strip('"\'').rstrip('.!?:')

        if title and len(title) > 80:
            title = title[:77] + "..."

        if title:
            update_session_title(session_id, title)
            from .debug_bus import debug
            debug(f"📝 Title generated: {title}")

        return title

    except asyncio.TimeoutError:
        from .debug_bus import debug
        debug("⚠️ Title generation timed out (>30s)")
        return ""
    except Exception as exc:
        log_message(f"Title generation failed: {exc}", "warning")
        return ""
