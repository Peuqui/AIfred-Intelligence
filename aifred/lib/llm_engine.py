"""
LLM Engine - der Bildaufruf des Reflex-Chats und der Sitzungstitel.

``call_llm`` ruft nur noch state/_chat_mixin.py auf: VL Direct (Bild-Upload, das Hauptmodell sieht
selbst). Kanäle, Scheduler und Alarme laufen über den Zug-Dienst (lib/turn_service.py); mit Reflex
geht auch ``call_llm``.

Yields Dict-Messages die vom Aufrufer geroutet werden:
- {"type": "debug", "message": str}
- {"type": "content", "text": str}
- {"type": "progress", ...}
- {"type": "result", "data": {...}}
"""

import re
from collections.abc import AsyncIterator
from typing import Any, cast

from .context_manager import estimate_tokens
from .formatting import build_inference_metadata, format_number
from .llm_client import LLMClient, build_llm_options
from .logging_utils import log_message
from .prompt_loader import get_agent_system_prompt
from .research.context_utils import get_agent_num_ctx


async def call_llm(
    user_text: str,
    model_choice: str,
    history: list,  # chat_history - will be updated and returned in result
    llm_history: list[dict[str, str]],
    detected_language: str,
    temperature: float,
    backend_type: str,
    backend_url: str | None,
    state: Any,
    multimodal_content: list[dict],
    memory_ctx: str | None = None,
    provider: str | None = None,
    agent: str = "aifred",
    vision_task_addon: str = "",
    external_toolkit: Any | None = None,
) -> AsyncIterator[dict]:
    """
    Generiert die Antwort auf ein Bild im Reflex-Chat; Werkzeuge kommen über ``external_toolkit``
    vom Aufrufer.

    Args:
        user_text: Die User-Nachricht (Erinnerungsabruf)
        model_choice: Model ID (z.B. "qwen3:4b")
        history: Chat History (wird aktualisiert und im Result zurückgegeben)
        llm_history: LLM History (Liste von {"role": ..., "content": ...})
        detected_language: Sprache für Prompts ("de" oder "en")
        temperature: Temperatur des antwortenden Agenten (agent_tuning)
        backend_type: Backend-Typ ("ollama", "vllm", "llamacpp", "cloud_api")
        backend_url: Backend-URL
        state: AIState Objekt für den num_ctx-Lookup und die Sampling-Werte
        multimodal_content: Bilder und Text der Nutzerfrage
        memory_ctx: Agent-Memory-Kontext aus prepare_agent_toolkit (vom Caller bereits gesammelte Erinnerungen; kommt als eigene Nachricht vor die Nutzerfrage)

    Yields:
        Dict mit type und payload:
        - {"type": "debug", "message": str}
        - {"type": "content", "text": str}
        - {"type": "progress", "phase": str} oder {"type": "progress", "clear": True}
        - {"type": "result", "data": {"history": ..., "llm_history": ...}} - Finale Ergebnisse
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
    # Build without user text, then append the images with the question
    messages: list[dict[str, Any]] = list(build_messages_from_llm_history(
        llm_history,
        perspective=agent,
        detected_language=detected_language
    ))
    messages.append({"role": "user", "content": multimodal_content})
    log_message("📷 Multimodal content injected into user message")

    # The real agent's prompt stack (identity, personality, memory, tools) + the vision task as
    # addon — the caller renders the SSOT vision/task_adaptive.txt (identical perception body for
    # every image, only the follow-up instruction differs by whether the user asked something specific).
    system_prompt = get_agent_system_prompt(agent, "task", lang=detected_language, source="browser")
    if vision_task_addon:
        system_prompt = f"{system_prompt}\n\n{vision_task_addon}"
    # Agent Memory: recall + toolkit. The caller's toolkit and memories, else (no toolkit came)
    # a memory-only lookup when the tab has memory on.
    memory_toolkit = external_toolkit
    if memory_toolkit:
        pass   # already logged by the caller
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

    messages.insert(0, {"role": "system", "content": system_prompt})

    # Erinnerungen ans ENDE, direkt vor die Nutzerfrage — nicht an den
    # System-Prompt. Begruendung in message_builder.inject_before_question.
    if memory_ctx:
        from .message_builder import inject_before_question
        inject_before_question(messages, memory_ctx)
        mem_tok = estimate_tokens([{"content": memory_ctx}])
        yield {"type": "debug", "message": f"🧠 Memory context injected ({mem_tok:,} tok, before question)"}

    # Create LLM client
    llm_client = LLMClient(backend_type=backend_type, base_url=backend_url, provider=provider)

    try:
        yield {"type": "debug", "message": f"🌡️ Temperature: {format_number(temperature, 1)}"}

        # Centralized SSOT: get_agent_num_ctx handles vision_num_ctx_enabled,
        # VRAM calibration cache, XTTS reservation and backend-specific logic.
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
        llm_options = build_llm_options(state, agent, model_choice, temperature, final_num_ctx)

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
        from .llm_pipeline import PipelineResult, run_llm_stream
        log_message("🔬 DEBUG: Starting inference")

        pipeline_result: PipelineResult | None = None

        async for event in run_llm_stream(
            llm_client, model_choice, cast(list, messages), llm_options, agent_label,
            source="browser", toolkit=memory_toolkit,
        ):
            event_type = event["type"]
            if event_type == "content":
                yield {"type": "content", "text": event["text"]}
            elif event_type == "ttft":
                yield {"type": "debug", "message": f"⚡ TTFT: {format_number(event['value'], 2)}s"}
            elif event_type == "tool_call":
                from .debug_format import format_tool_call
                yield {"type": "debug", "message": f"🔧 {format_tool_call(event.get('name', ''), event.get('arguments', ''))}"}
            elif event_type == "tool_result":
                from .debug_format import format_tool_result
                result_str = event.get("result", "") or ""
                result_tokens = estimate_tokens([{"content": result_str}]) if result_str else 0
                yield {"type": "debug", "message": f"   ↳ {format_tool_result(result_str, token_count=result_tokens)}"}
            elif event_type == "debug":
                # Backend warnings (truncation, context guard, discarded
                # tool calls) — forward to the caller's debug sink.
                if event.get("message"):
                    yield {"type": "debug", "message": event["message"]}
            elif event_type == "pipeline_result":
                pipeline_result = event["result"]

        if not pipeline_result:
            yield {"type": "progress", "clear": True}
            return

        response_clean = pipeline_result.text_clean
        from .bubble import render_bubble
        bubble_html = render_bubble(
            pipeline_result.text, pipeline_result.artifacts, model_name=model_choice,
        )

        # Update llm_history BEFORE calculating history_tokens
        # so "History: X tok" reflects the current conversation state (incl. AI response)
        from .formatting import assistant_history_entry
        history_entry = assistant_history_entry(agent, response_clean, pipeline_result.history_notes)
        if history_entry is not None:
            llm_history.append(history_entry)

        # Metadata with history_tokens, backend_type, source_label
        from .context_manager import estimate_tokens_from_llm_history
        history_tokens = estimate_tokens_from_llm_history(llm_history)
        source_label = f"{agent_label} ({model_choice})"

        metadata_dict, metadata_display, debug_msg = build_inference_metadata(
            ttft=pipeline_result.ttft,
            inference_time=pipeline_result.inference_time,
            work=pipeline_result.work,
            source=source_label,
            tokens_prompt=pipeline_result.metrics.get("tokens_prompt", 0),
            history_tokens=history_tokens,
            backend_type=backend_type,
            agent_label=agent_label,
            truncated=pipeline_result.truncated,
            load_time=pipeline_result.load_time,
        )
        yield {"type": "debug", "message": debug_msg}

        # Update chat_history (UI display with thinking, the turn's bubble
        # artifacts in turn order, and metadata) — same renderer as the
        # browser multi-agent path (lib/bubble.py).
        from .message_builder import build_history_entry
        ai_with_source = f"{bubble_html}\n\n{metadata_display}"
        history.append(build_history_entry(agent, ai_with_source, "own_knowledge", metadata_dict))

        # Clear progress
        yield {"type": "progress", "clear": True}

        # Final result - unified Dict format
        yield {
            "type": "result",
            "data": {"history": history, "llm_history": llm_history}
        }

    finally:
        await llm_client.close()


def first_exchange(llm_history: list[dict[str, str]]) -> tuple[str, str] | None:
    """The first user message and the first reply of a session, for its title.

    The reply loses its speaker label ("[AIFRED]: ", a custom agent's uppercased id): the title
    model would echo it. An image-only first turn has no user text and stands as "[Bildanalyse]".
    None until both exist.
    """
    first_user = next((entry.get("content", "") for entry in llm_history if entry.get("role") == "user"), "")
    first_reply = next((entry.get("content", "") for entry in llm_history if entry.get("role") == "assistant"), "")
    first_reply = re.sub(r"^\[[A-Z0-9_]+\]:\s*", "", first_reply, count=1)
    if not first_reply:
        return None
    return first_user or "[Bildanalyse]", first_reply


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

    from .config import (
        AUTOMATIK_LLM_NUM_CTX,
        BACKEND_URLS,
        DEFAULT_SETTINGS,
        SESSION_TITLE_NUM_PREDICT,
        SESSION_TITLE_TIMEOUT_SECONDS,
    )
    from .context_manager import strip_thinking_blocks
    from .llm_client import LLMClient
    from .logging_utils import log_message
    from .prompt_loader import get_language, load_prompt
    from .session_storage import update_session_title
    from .settings import load_settings

    settings = load_settings() or {}
    backend_type = settings.get("backend_type", DEFAULT_SETTINGS["backend_type"])
    backend_url = BACKEND_URLS.get(backend_type, "")

    # Model: override > Automatik-Modell aus Settings. Title-Generation ist
    # eine Hilfsaufgabe und gehoert zum Automatik-LLM. Wenn der User in den
    # Einstellungen "Automatik-LLM = (wie Alfred-LLM)" gewaehlt hat, ist
    # automatik_model leer und get_effective_model_from_settings() faellt
    # intern auf aifred_model zurueck (siehe config.py).
    if model_override:
        model = model_override
    else:
        from .config import get_effective_model_from_settings
        model = get_effective_model_from_settings("automatik")

    if not model:
        return ""

    # Truncate and clean inputs
    user_short = re.sub(r'<[^>]+>', '', user_text).strip()[:500]
    ai_short = strip_thinking_blocks(ai_response)[:500] if ai_response else ""
    if not user_short or not ai_short:
        # Happens legitimately after a crashed generation (answer was
        # reasoning-only → empty after thinking-strip) — but say WHY,
        # the bare "empty response" downstream is undebuggable.
        from .debug_bus import debug
        which = "user text" if not user_short else "AI answer (empty after thinking-strip)"
        debug(f"📝 Title skipped: no usable {which}")
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
                    "num_predict": SESSION_TITLE_NUM_PREDICT,
                    "enable_thinking": False,
                    "num_ctx": num_ctx,
                },
            ),
            timeout=SESSION_TITLE_TIMEOUT_SECONDS,
        )

        title = strip_thinking_blocks(response.text.strip()).strip().strip('"\'').rstrip('.!?:')

        if not title:
            from .debug_bus import debug
            debug(
                f"📝 Title LLM returned empty text "
                f"(raw len {len(response.text or '')}, model {model})"
            )

        if title and len(title) > 80:
            title = title[:77] + "..."

        if title:
            update_session_title(session_id, title)
            from .debug_bus import debug
            debug(f"📝 Title generated: {title}")

        return title

    except TimeoutError:
        from .debug_bus import debug
        debug(f"⚠️ Title generation timed out (>{SESSION_TITLE_TIMEOUT_SECONDS:.0f}s)")
        return ""
    except Exception as exc:  # noqa: BLE001 — a failed title must not break the reply that asked for it; logged and shown
        log_message(f"Title generation failed: {exc}", "warning")
        # Also surface in the session debug console — the logfile is
        # truncated on every restart, which already ate one diagnosis.
        from .debug_bus import debug
        debug(f"📝 Title generation failed: {exc}")
        return ""
