"""
Multi-Agent Debate System
AIfred (Main) + Sokrates (Critic)

Implements multi-agent debate patterns for improved answer quality:
- User-as-Judge: AIfred answers, Sokrates critiques, user decides
- Auto-Consensus: Iterative refinement until LGTM or max rounds
- Devil's Advocate: Pro and Contra arguments for balanced analysis

This module contains the core Multi-Agent logic extracted from state.py.
The functions work with async generators for streaming UI updates.
"""

from typing import TYPE_CHECKING, Any, AsyncGenerator, Optional

# Imports for the functions (same as original state.py methods)
from .llm_client import LLMClient, build_llm_options
from .formatting import format_number, format_thinking_process
from .message_builder import build_messages_from_llm_history
from .i18n import t
from .context_manager import (
    estimate_tokens,
    strip_thinking_blocks,
    summarize_history_if_needed,
    get_largest_compression_model,
    _last_vram_limit_cache
)
from .prompt_loader import (
    get_agent_system_prompt,
    get_agent_direct_prompt,
    get_sokrates_critic_prompt,
    get_sokrates_devils_advocate_prompt,
    get_aifred_refinement_prompt,
    get_sokrates_tribunal_prompt,
    get_aifred_defense_prompt,
    get_salomo_mediator_prompt,
    get_salomo_judge_prompt,
)
from .logging_utils import log_message, log_raw_messages, console_separator
from ..backends.base import LLMOptions

if TYPE_CHECKING:
    from ..state import AIState



def _estimate_prompt_tokens(prompt: str) -> int:
    """Estimate tokens in a prompt (3.5 chars/token for German/mixed text)."""
    return int(len(prompt) / 3.5) if prompt else 0


# ============================================================
# CONSENSUS VOTING HELPERS
# ============================================================

def count_lgtm_votes(alfred_text: str, sokrates_text: str, salomo_text: str) -> dict:
    """Count [LGTM] votes from all agents, ignoring if [WEITER] is present.

    [WEITER] overrides [LGTM] to handle negation cases like:
    "Das ist noch kein LGTM" -> Would otherwise false-positive on "LGTM"

    Returns:
        dict: {"alfred": bool, "sokrates": bool, "salomo": bool}
    """
    votes = {"alfred": False, "sokrates": False, "salomo": False}

    for name, text in [("alfred", alfred_text), ("sokrates", sokrates_text), ("salomo", salomo_text)]:
        content = strip_thinking_blocks(text).strip().upper()
        # [WEITER] overrides [LGTM] (for negation case)
        if "[WEITER]" in content:
            votes[name] = False
        elif "[LGTM]" in content:
            votes[name] = True

    return votes


def check_consensus(votes: dict, consensus_type: str) -> bool:
    """Check if consensus is reached based on type.

    Args:
        votes: dict with agent names as keys and bool votes as values
        consensus_type: "majority" (2/3) or "unanimous" (3/3)

    Returns:
        bool: True if consensus reached
    """
    lgtm_count = sum(votes.values())
    if consensus_type == "unanimous":
        return bool(lgtm_count == 3)  # All must agree
    else:  # majority
        return bool(lgtm_count >= 2)  # 2/3 is enough


def format_votes_debug(votes: dict, round_num: int) -> str:
    """Format votes for debug output.

    Args:
        votes: dict with agent names as keys and bool votes as values
        round_num: Current round number

    Returns:
        str: Formatted debug string
    """
    lgtm_count = sum(votes.values())
    alfred_vote = "✅" if votes.get("alfred", False) else "❌"
    sokrates_vote = "✅" if votes.get("sokrates", False) else "❌"
    salomo_vote = "✅" if votes.get("salomo", False) else "❌"

    return f"🗳️ Votes R{format_number(round_num)}: AIfred {alfred_vote}, Sokrates {sokrates_vote}, Salomo {salomo_vote} ({format_number(lgtm_count)}/3)"


def parse_pro_contra(analysis: str) -> tuple[str, str]:
    """Parse Pro and Contra sections from Sokrates' analysis.

    Standalone function that can be imported and used directly.
    """
    pro_args = ""
    contra_args = ""

    lower_analysis = analysis.lower()

    # Find Pro section
    pro_markers = ["## pro", "**pro", "pro:", "pro-argumente:", "pro arguments:"]
    contra_markers = ["## contra", "**contra", "contra:", "contra-argumente:", "contra arguments:"]

    pro_start = -1
    contra_start = -1

    for marker in pro_markers:
        idx = lower_analysis.find(marker)
        if idx != -1 and (pro_start == -1 or idx < pro_start):
            pro_start = idx

    for marker in contra_markers:
        idx = lower_analysis.find(marker)
        if idx != -1 and (contra_start == -1 or idx < contra_start):
            contra_start = idx

    if pro_start != -1 and contra_start != -1:
        if pro_start < contra_start:
            pro_args = analysis[pro_start:contra_start].strip()
            contra_args = analysis[contra_start:].strip()
        else:
            contra_args = analysis[contra_start:pro_start].strip()
            pro_args = analysis[pro_start:].strip()
    elif pro_start != -1:
        pro_args = analysis[pro_start:].strip()
    elif contra_start != -1:
        contra_args = analysis[contra_start:].strip()
    else:
        # No clear sections - return full analysis as pro
        pro_args = analysis.strip()

    return pro_args, contra_args


# ============================================================
# STREAMING HELPERS
# ============================================================

def _format_stream_result(
    result: dict[str, Any],
    agent_label: str,
    model: str,
) -> str:
    """Format a stream result (from _stream_agent_to_history) into UI-ready HTML.

    Applies thinking-block formatting and prepends web sources collapsible.
    Used by all callers to avoid duplicating formatting logic.
    """
    text = result["text"]
    sources_html = result.get("sources_html", "")
    inference_time = result.get("metadata_dict", {}).get("inference_time", 0)

    sandbox_html = result.get("sandbox_html", "")

    formatted = format_thinking_process(
        text,
        model_name=f"{agent_label} ({model})",
        inference_time=inference_time,
    )
    # Order: [thinking] [sources] [sandbox] [text]
    # format_thinking_process returns: [thinking collapsibles]\n\n[text]
    # We insert sources and sandbox between thinking and text
    inserts = ""
    if sources_html:
        inserts += f"\n\n{sources_html}"
    if sandbox_html:
        inserts += f"\n\n{sandbox_html}"
    if inserts:
        # Split at first double-newline after the last </details> (end of thinking block)
        import re
        # Find position after all leading <details>...</details> blocks
        match = re.search(r'((?:<details[\s\S]*?</details>\s*)+)([\s\S]*)', formatted)
        if match:
            collapsibles_part = match.group(1).rstrip()
            text_part = match.group(2).lstrip()
            formatted = f"{collapsibles_part}{inserts}\n\n{text_part}"
        else:
            # No thinking block — just prepend
            formatted = f"{inserts.lstrip()}\n\n{formatted}"
    return formatted


def _get_plugin_ui_status(tool_name: str, tool_args: dict, lang: str) -> str:
    """Ask plugins for UI status. First non-empty response wins."""
    from .plugin_registry import discover_tools
    for p in discover_tools():
        status = p.get_ui_status(tool_name, tool_args, lang)
        if status:
            return status
    return ""


async def _stream_agent_to_history(
    state: 'AIState',
    agent: str,
    agent_label: str,
    llm_client: LLMClient,
    model: str,
    messages: list,
    options: LLMOptions,
    toolkit: Any = None,
) -> AsyncGenerator[dict[str, Any] | None, None]:
    """Stream an agent's response into current_ai_response (unified streaming).

    Generic streaming function used by all agents (Sokrates, AIfred, Salomo).
    Delegates chunk processing to run_llm_stream() pipeline and handles
    UI-specific concerns (streaming, TTS, tool status).

    Performance-optimized: Does NOT update chat_history during streaming.
    Only updates state.current_ai_response which is shown in unified streaming_box.
    Caller is responsible for appending final result to chat_history.

    Args:
        agent: Agent key for state/TTS ("sokrates", "aifred", "salomo")
        agent_label: Display label for logs ("Sokrates", "AIfred Refinement", "Salomo")
    """
    from .llm_pipeline import run_llm_stream, PipelineResult

    # State setup: UI styling, vLLM model loading, TTS init
    state._set_current_agent(agent)
    state._streaming_sub().current_ai_response = ""  # type: ignore[attr-defined]

    if state.backend_type == "vllm":
        await state._ensure_vllm_model(model)

    if state.enable_tts and state.tts_autoplay and state.tts_streaming_enabled:
        state._init_streaming_tts(agent=agent)

    # Consume pipeline event stream
    pipeline_result: PipelineResult | None = None

    async for event in run_llm_stream(
        llm_client, model, messages, options, agent_label,
        toolkit=toolkit, on_debug=state.add_debug,
    ):
        event_type = event["type"]

        if event_type == "content":
            if state.stream_text_to_ui(event["text"]):
                yield  # type: ignore[misc]

        elif event_type == "ttft":
            state.add_debug(f"⚡ TTFT: {format_number(event['value'], 2)}s")

        elif event_type == "tool_call_start":
            # Only sets the UI tool-status; the readable debug-log line
            # is emitted from the "tool_call" event below where the args
            # are already parsed.
            tool_name = event.get("name", "")
            status = _get_plugin_ui_status(tool_name, {}, state.ui_language)
            if status:
                state.set_tool_status(status)
            yield  # type: ignore[misc]

        elif event_type == "tool_call":
            tool_name = event.get("name", "")
            full_args = event.get("arguments", "")
            from .debug_format import format_tool_call
            # Prefix with agent label only in multi-agent modes so single-agent
            # logs stay clean. agent_label is the display name passed in here.
            from_multi_agent = state.multi_agent_mode != "standard"
            agent_prefix = agent_label if from_multi_agent else ""
            state.add_debug(f"🔧 {format_tool_call(tool_name, full_args, agent=agent_prefix)}")

            import json as _json
            tool_args: dict[str, Any] = {}
            try:
                tool_args = _json.loads(full_args) if full_args else {}
            except (ValueError, _json.JSONDecodeError):
                pass

            if tool_name == "store_memory":
                state.set_tool_status(t("tool_memory", lang=state.ui_language))
            else:
                status = _get_plugin_ui_status(tool_name, tool_args, state.ui_language)
                if status:
                    state.set_tool_status(status)

            yield  # type: ignore[misc]
            yield  # type: ignore[misc]

        elif event_type == "tool_result":
            from .debug_format import format_tool_result
            state.add_debug(f"   ↳ {format_tool_result(event.get('result', ''))}")
            state.clear_tool_status()
            yield  # type: ignore[misc]

        elif event_type == "pipeline_result":
            pipeline_result = event["result"]

    if not pipeline_result:
        state._set_current_agent("")
        return

    # Flush remaining buffer to state
    if state.flush_stream_to_ui():
        yield  # type: ignore[misc]

    state.add_debug(pipeline_result.debug_msg)

    # Finalize streaming TTS
    audio_urls: list[str] = []
    if state.enable_tts and state.tts_autoplay and state.tts_streaming_enabled:
        audio_urls = await state._finalize_streaming_tts()
    if audio_urls:
        log_message(f"🔊 {agent_label}: {len(audio_urls)} audio URLs collected for message")

    # Build web sources collapsible from tracked URLs
    sources_html = ""
    if pipeline_result.fetched_urls:
        from .formatting import build_sources_collapsible
        successful = [{"url": u["url"], "word_count": 0, "rank_index": i, "success": True}
                      for i, u in enumerate(pipeline_result.fetched_urls) if u.get("success")]
        failed = [{"url": u["url"], "error": "fetch failed", "rank_index": i}
                  for i, u in enumerate(pipeline_result.fetched_urls) if not u.get("success")]
        sources_html = build_sources_collapsible(successful, failed)

    # Build sandbox output (iframes for HTML, img tags for plots)
    sandbox_html = ""
    sandbox_parts: list[str] = []
    if pipeline_result.sandbox_html_urls:
        from .formatting import build_sandbox_iframe
        sandbox_parts.extend(build_sandbox_iframe(url) for url in pipeline_result.sandbox_html_urls)
    if pipeline_result.sandbox_image_urls:
        from .formatting import build_sandbox_image
        sandbox_parts.extend(build_sandbox_image(url) for url in pipeline_result.sandbox_image_urls)
    sandbox_html = "\n".join(sandbox_parts)

    # Sync to llm_history with CLEAN text (no HTML collapsibles)
    state._sync_to_llm_history(agent, pipeline_result.text)

    # Clear streaming state (cleanup BEFORE yield)
    state._js_chunk_buffer = ""
    state._streaming_sub().current_ai_response = ""  # type: ignore[attr-defined]
    state._set_current_agent("")

    # Return result as final yield (dict = result, None = UI update)
    yield {
        "text": pipeline_result.text,
        "sources_html": sources_html,
        "sandbox_html": sandbox_html,
        "metadata_display": pipeline_result.metadata_display,
        "metadata_dict": pipeline_result.metadata_dict,
        "audio_urls": audio_urls,
    }


async def _check_compression_if_needed(
    state: 'AIState',
    llm_client: LLMClient,
    agent_context_limit: int,
    system_prompt_tokens: int = 0
) -> AsyncGenerator[None, None]:
    """
    Check if history compression is needed during multi-agent debate.

    NOTE: Main PRE-MESSAGE check runs in send_message() BEFORE multi-agent starts.
    This function handles compression DURING long debates where the debate itself
    might push context usage above threshold.

    Compression triggers at 70% of agent_context_limit (HISTORY_COMPRESSION_TRIGGER).

    IMPORTANT (v2.14.4+): Use the CURRENT AGENT's context limit, not min_ctx!
    Each agent (AIfred, Sokrates, Salomo) may have different context windows.
    Compression should trigger based on the NEXT agent's limit to prevent overflow.

    Args:
        state: AIState instance
        llm_client: LLM client for compression
        agent_context_limit: Context window limit OF THE NEXT AGENT (not min_ctx!)
        system_prompt_tokens: Estimated tokens for current agent's system prompt (v2.14.0+)
    """
    try:
        # Select largest model for compression (AIfred/Sokrates/Salomo)
        compression_model = get_largest_compression_model(
            aifred_model=state._effective_model_id("aifred"),
            sokrates_model=state._effective_model_id("sokrates"),
            salomo_model=state._effective_model_id("salomo")
        )

        # Run compression check (yields events if compression happens) - DUAL-HISTORY
        _ch = state._chat_sub()
        async for event in summarize_history_if_needed(
            history=_ch.chat_history,
            llm_client=llm_client,
            model_name=compression_model,  # Use largest available model for quality
            context_limit=agent_context_limit,  # Use agent-specific limit, not min_ctx!
            llm_history=_ch.llm_history,
            system_prompt_tokens=system_prompt_tokens
        ):
            if event["type"] == "history_update":
                # DUAL-HISTORY: Update both histories
                _ch.chat_history = event["chat_history"]
                if event.get("llm_history") is not None:
                    _ch.llm_history = event["llm_history"]
                state.add_debug(f"✅ History compressed: {len(_ch.chat_history)} UI / {len(_ch.llm_history)} LLM messages")
                yield
            elif event["type"] == "debug":
                state.add_debug(event["message"])
                yield
            elif event["type"] == "progress":
                state.is_compressing = True
                yield

        state.is_compressing = False

    except Exception as e:
        state.add_debug(f"⚠️ Compression check failed: {e}")
        state.is_compressing = False


# ============================================================
# FORCED RESEARCH (keyword override → full pipeline)
# ============================================================

async def _execute_forced_research(
    state: 'AIState',
    user_query: str,
    mode: str,
    model_id: str,
    lang: str,
) -> AsyncGenerator[None, None]:
    """Execute forced web research via the unified pipeline.

    Delegates to execute_research() which handles the full pipeline:
    Query generation → Multi-API search → URL ranking → Scraping → Cache.

    Results stored in state._research_context and state._research_sources_html.
    """
    from .research_tools import execute_research

    async for _ in execute_research(
        state=state,
        user_query=user_query,
        lang=lang,
        # No pre_generated_queries → Automatik-LLM generates them
    ):
        yield  # Forward yields for progress bar updates


# ============================================================
# SHARED DEBATE HELPERS (deduplicated from debate functions)
# ============================================================


async def _execute_agent_stream(
    state: 'AIState',
    agent: str,
    agent_label: str,
    llm_client: LLMClient,
    model: str,
    messages: list,
    options: LLMOptions,
    toolkit: Any = None,
) -> AsyncGenerator[Optional[dict[str, Any]], None]:
    """Execute streaming for one agent turn.

    Yields None for UI updates, then yields the result dict as final item.
    Caller uses: `async for item in _execute_agent_stream(...): ...`

    Returns None result on stream failure (caller decides how to handle).
    """
    result = None
    async for item in _stream_agent_to_history(
        state=state, agent=agent, agent_label=agent_label,
        llm_client=llm_client, model=model,
        messages=messages, options=options, toolkit=toolkit,
    ):
        if isinstance(item, dict):
            result = item
        else:
            yield None  # Forward UI update

    if result is None:
        state.add_debug(f"❌ {agent_label} stream returned no result")

    yield result  # Final item = result dict (or None on failure)


def _setup_debate_contexts(
    state: 'AIState',
    alfred_model: str,
    sokrates_model: str,
    salomo_model: str,
) -> dict[str, Any]:
    """Set up context limits, temperatures, and VRAM cache for a 3-agent debate.

    Returns dict with all computed values for use by debate functions.
    """
    from .research.context_utils import get_agent_num_ctx

    # Context limits
    main_llm_ctx, aifred_source = get_agent_num_ctx("aifred", state, alfred_model, fallback=32768)
    sokrates_num_ctx, sokrates_source = get_agent_num_ctx("sokrates", state, sokrates_model, fallback=32768)
    salomo_num_ctx, salomo_source = get_agent_num_ctx("salomo", state, salomo_model, fallback=32768)

    state.add_debug(f"🎯 AIfred: {format_number(main_llm_ctx)} tok ({aifred_source})")
    state.add_debug(f"🎯 Sokrates: {format_number(sokrates_num_ctx)} tok ({sokrates_source})")
    state.add_debug(f"🎯 Salomo: {format_number(salomo_num_ctx)} tok ({salomo_source})")

    # VRAM cache for history compression
    _last_vram_limit_cache["aifred_limit"] = main_llm_ctx
    _last_vram_limit_cache["sokrates_limit"] = sokrates_num_ctx
    _last_vram_limit_cache["salomo_limit"] = salomo_num_ctx
    min_ctx = min(sokrates_num_ctx, main_llm_ctx, salomo_num_ctx)
    _last_vram_limit_cache["limit"] = min_ctx

    state.add_debug(
        f"📊 Context limits: AIfred={format_number(main_llm_ctx)} tok, "
        f"Sokrates={format_number(sokrates_num_ctx)} tok, "
        f"Salomo={format_number(salomo_num_ctx)} tok, "
        f"Compression={format_number(min_ctx)} tok"
    )

    # Temperatures
    if state.temperature_mode == "manual":  # type: ignore[has-type]
        alfred_temp = state.temperature
        sokrates_temp = state.sokrates_temperature
        salomo_temp = state.salomo_temperature
    else:
        alfred_temp = state.temperature
        sokrates_temp = min(1.0, alfred_temp + state.sokrates_temperature_offset)
        salomo_temp = min(1.0, alfred_temp + state.salomo_temperature_offset)

    state.add_debug(
        f"🌡️ Temps: AIfred={format_number(alfred_temp, 1)}, "
        f"Sokrates={format_number(sokrates_temp, 1)}, "
        f"Salomo={format_number(salomo_temp, 1)}"
    )

    # LLM options
    sokrates_options = build_llm_options(state, "sokrates", sokrates_temp, sokrates_num_ctx)
    alfred_options = build_llm_options(state, "aifred", alfred_temp, main_llm_ctx)
    salomo_options = build_llm_options(state, "salomo", salomo_temp, salomo_num_ctx)

    return {
        "aifred_ctx": main_llm_ctx, "sokrates_ctx": sokrates_num_ctx, "salomo_ctx": salomo_num_ctx,
        "aifred_temp": alfred_temp, "sokrates_temp": sokrates_temp, "salomo_temp": salomo_temp,
        "aifred_options": alfred_options, "sokrates_options": sokrates_options, "salomo_options": salomo_options,
        "min_ctx": min_ctx,
    }


async def _recall_agent_memories(
    state: 'AIState',
    agent_ids: list[str],
    user_query: str,
    detected_lang: str,
) -> dict[str, tuple[str, Any]]:
    """Recall memory + toolkit for multiple agents.

    Returns dict: {agent_id: (memory_ctx, toolkit)}
    """
    from .agent_memory import prepare_agent_toolkit

    memory_enabled = state.agent_memory_enabled
    sid = state.session_id
    result = {}

    for agent_id in agent_ids:
        mem_ctx, toolkit = await prepare_agent_toolkit(
            agent_id, user_query, lang=detected_lang or "de",
            memory_enabled=memory_enabled, research_tools_enabled=True,
            state=state, session_id=sid,
        )
        if mem_ctx:
            state.add_debug(f"🧠 Memory context recalled for {agent_id.title()}")
        result[agent_id] = (mem_ctx, toolkit)

    return result


def _build_debate_messages(
    state: 'AIState',
    system_prompt: str,
    perspective: str,
    detected_lang: Optional[str],
    current_user_text: Optional[str] = None,
) -> list[dict[str, str]]:
    """Build message list for an agent in a debate.

    Combines system prompt + LLM history (filtered: no raw system messages except compressions).
    """
    history_messages = build_messages_from_llm_history(
        state._chat_sub().llm_history,
        current_user_text=current_user_text or "",
        perspective=perspective,
        detected_language=detected_lang,
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in history_messages:
        if msg["role"] != "system" or "Compressed:" in msg.get("content", ""):
            messages.append(msg)

    return messages


def _add_agent_result_panel(
    state: 'AIState',
    agent: str,
    result: dict[str, Any],
    agent_label: str,
    model: str,
    mode: str,
    round_num: Optional[int] = None,
) -> str:
    """Format result and add agent panel to chat history. Returns response text."""
    formatted = _format_stream_result(result, agent_label, model)
    metadata_dict = result["metadata_dict"]
    audio_urls = result.get("audio_urls", [])

    state.add_agent_panel(
        agent=agent,
        content=formatted,
        mode=mode,
        round_num=round_num,
        metadata={**metadata_dict, "audio_urls": audio_urls},
        sync_llm_history=False,
    )

    return str(result["text"])


def _finalize_debate(state: 'AIState') -> None:
    """Save session and add debug separator after a debate."""
    state._save_current_session()
    console_separator()
    state.add_debug("────────────────────")


# ============================================================
# UNIFIED AGENT RESPONSE (all agents, all research modes)
# ============================================================

async def _run_agent_direct_response(
    state: 'AIState',
    agent: str,
    agent_label: str,
    emoji: str,
    get_prompt_func: Any,
    user_query: str,
    detected_lang: Optional[str] = None,
    research_mode: str = "none",
    detected_intent: Optional[str] = None,
) -> AsyncGenerator[None, None]:
    """Unified response handler for all agents (AIfred, Sokrates, Salomo, custom).

    Handles all research modes:
    - "none": No research tools, agent answers from own knowledge
    - "automatik": Agent gets web_search/read_webpage tools, decides autonomously
    - "quick"/"deep": Forced web search executed before agent response, results injected as context

    Args:
        agent: Agent key ("aifred", "sokrates", "salomo", or custom agent id)
        agent_label: Display label for debug output
        emoji: Agent emoji for debug
        get_prompt_func: Prompt loader function returning system prompt
        user_query: The user's question
        detected_lang: Language from intent detection, defaults to UI language
        research_mode: "none", "automatik", "quick", or "deep"
        detected_intent: Intent from intent detection (FAKTISCH/KREATIV/GEMISCHT)
    """
    if detected_lang is None:
        from .prompt_loader import get_language
        detected_lang = get_language()

    try:
        llm_client = LLMClient(
            backend_type=state.backend_type,
            base_url=state.backend_url
        )

        # Determine agent model — custom agents use AIfred's model
        _default_agents = ("aifred", "sokrates", "salomo", "vision")
        if agent in _default_agents:
            agent_model_id = state._effective_model_id(agent) or state._effective_model_id("aifred")
        else:
            agent_model_id = state._effective_model_id("aifred")
        state.add_debug(f"{emoji} {agent_label}-LLM: {agent_model_id} ({state.backend_type})")

        # Context limit — custom agents use AIfred's context
        from .research.context_utils import get_agent_num_ctx
        ctx_agent = agent if agent in _default_agents else "aifred"
        agent_num_ctx, ctx_source = get_agent_num_ctx(ctx_agent, state, agent_model_id)
        state.add_debug(f"   🎯 Context: {format_number(agent_num_ctx)} ({ctx_source})")

        # Combined toolkit: memory + research tools (based on research_mode)
        from .agent_memory import prepare_agent_toolkit
        memory_enabled = state.agent_memory_enabled  # type: ignore[attr-defined]

        # System prompt (memory layer depends on incognito toggle)
        # tools=True only in automatik mode (all tools available, agent decides)
        # own-knowledge mode: no tools, fast direct response
        research_tools_enabled = research_mode == "automatik"
        system_prompt = get_prompt_func(lang=detected_lang, memory=memory_enabled, tools=research_tools_enabled)

        memory_ctx, toolkit = await prepare_agent_toolkit(
            agent, user_query,
            lang=detected_lang or "de",
            memory_enabled=memory_enabled,
            research_tools_enabled=research_tools_enabled,
            state=state if research_tools_enabled else None,
            session_id=state.session_id,
        )
        mem_tok = 0
        if memory_ctx:
            system_prompt = f"{system_prompt}\n\n{memory_ctx}"
            mem_tok = estimate_tokens([{"content": memory_ctx}])
            state.add_debug(f"🧠 Memory context injected ({mem_tok:,} tok)")
        if toolkit:
            state.add_debug(f"🔧 Toolkit: {[t.name for t in toolkit.tools]} for {agent_label}")
        if not memory_enabled:
            state.add_debug("🔒 Incognito mode (no memory)")
        yield  # type: ignore[misc]  # Flush debug messages (RAG, memory, toolkit)

        # Forced web search (quick/deep): execute research pipeline BEFORE agent response
        research_context = ""
        if research_mode in ("quick", "deep"):
            state.add_debug(f"🔎 Forced web research ({research_mode})...")
            yield  # type: ignore[misc]
            async for _ in _execute_forced_research(
                state, user_query, research_mode, agent_model_id,
                detected_lang or "de",
            ):
                yield  # type: ignore[misc]
            research_context = getattr(state, "_research_context", "")

        # Build messages with agent's perspective
        messages: list[dict[str, Any]] = build_messages_from_llm_history(
            state._chat_sub().llm_history[:-1],
            user_query,
            perspective=agent,
            detected_language=detected_lang
        )

        # Inject research context into system prompt (forced web search results)
        if research_context:
            system_prompt = f"{system_prompt}\n\n{research_context}"

        messages.insert(0, {"role": "system", "content": system_prompt})

        # Temperature — Sokrates/Salomo have own settings, others use AIfred's global
        if agent in ("sokrates", "salomo"):
            if state.temperature_mode == "manual":  # type: ignore[has-type]
                agent_temp = getattr(state, f"{agent}_temperature")
            else:
                agent_temp = min(1.0, state.temperature + getattr(state, f"{agent}_temperature_offset"))
        else:
            agent_temp = state.temperature  # type: ignore[has-type]

        # Token breakdown: System + Memory + RAG + Research + History = Total / Limit
        sys_tok = estimate_tokens([{"content": system_prompt}], model_name=agent_model_id)
        hist_tok = estimate_tokens([m for m in messages if m["role"] != "system"], model_name=agent_model_id)
        total_tok = sys_tok + hist_tok

        # Break down sys_tok into components (all appended to system_prompt)
        research_tok = estimate_tokens([{"content": research_context}]) if research_context else 0
        base_sys_tok = sys_tok - mem_tok - research_tok

        parts = [f"System {format_number(base_sys_tok)}"]
        if mem_tok:
            parts.append(f"Memory {format_number(mem_tok)}")
        if research_tok:
            parts.append(f"Research {format_number(research_tok)}")
        parts.append(f"History {format_number(hist_tok)}")
        state.add_debug(f"📊 Prompt: {' + '.join(parts)} = {format_number(total_tok)} / {format_number(agent_num_ctx)} tok ({int(total_tok / agent_num_ctx * 100)}%)")
        state.add_debug(f"🌡️ Temperature: {format_number(agent_temp, 1)}")

        # Set tool-output budget for this inference: caps how many tokens
        # a single tool result may inject into the conversation. Read by
        # backends/base.py just before appending the tool message.
        from .tool_output_cap import budget_var, compute_budget
        budget_var.set(compute_budget(agent_num_ctx, sys_tok, hist_tok, mem_tok))

        # Build LLM options — custom agents use AIfred's settings
        opts_agent = agent if agent in _default_agents else "aifred"
        saved_thinking = getattr(state, f"{opts_agent}_thinking", True)
        setattr(state, f"{opts_agent}_thinking", state.aifred_thinking)

        agent_options = build_llm_options(state, opts_agent, agent_temp, agent_num_ctx)

        setattr(state, f"{opts_agent}_thinking", saved_thinking)

        # Stream response via shared helper (SSOT for all streaming logic)
        result = None
        async for item in _stream_agent_to_history(
            state=state, agent=agent, agent_label=agent_label,
            llm_client=llm_client, model=agent_model_id,
            messages=messages, options=agent_options, toolkit=toolkit,
        ):
            if isinstance(item, dict):
                result = item
            else:
                yield  # type: ignore[misc]

        if not result:
            await llm_client.close()
            yield
            return

        metadata_dict = result.get("metadata_dict", {})
        audio_urls = result.get("audio_urls", [])

        # Format thinking + sources (SSOT helper)
        formatted_response = _format_stream_result(result, agent_label, agent_model_id)

        # Merge forced research sources (if any)
        research_sources = getattr(state, "_research_sources_html", "")
        if research_sources:
            formatted_response = f"{research_sources}\n\n{formatted_response}"
            state._research_sources_html = ""  # type: ignore[attr-defined]

        # Add to chat history
        panel_mode = "web_research" if research_mode in ("quick", "deep") else "direct"
        panel_meta = {**metadata_dict, "audio_urls": audio_urls}
        state.add_agent_panel(
            agent=agent,
            content=formatted_response,
            mode=panel_mode,
            metadata=panel_meta,
            sync_llm_history=False
        )

        # Cleanup
        state._save_current_session()
        console_separator()
        state.add_debug("────────────────────")

        await llm_client.close()
        yield

    except Exception as e:
        state.add_debug(f"❌ {agent_label} Direct Response Error: {e}")
        state.add_agent_panel(
            agent=agent,
            content=f"Error: {str(e)}",
            mode="error"
        )
        yield


async def run_sokrates_direct_response(
    state: 'AIState',
    user_query: str,
    detected_lang: Optional[str] = None
) -> AsyncGenerator[None, None]:
    """Sokrates responds directly to user."""
    async for _ in _run_agent_direct_response(
        state, "sokrates", "Sokrates", "🏛️",
        lambda lang=None, memory=True, tools=False: get_agent_direct_prompt("sokrates", lang=lang, memory=memory, tools=tools),
        user_query, detected_lang,
    ):
        yield


async def run_salomo_direct_response(
    state: 'AIState',
    user_query: str,
    detected_lang: Optional[str] = None
) -> AsyncGenerator[None, None]:
    """Salomo responds directly to user."""
    async for _ in _run_agent_direct_response(
        state, "salomo", "Salomo", "👑",
        lambda lang=None, memory=True, tools=False: get_agent_direct_prompt("salomo", lang=lang, memory=memory, tools=tools),
        user_query, detected_lang,
    ):
        yield


async def run_generic_agent_direct_response(
    state: 'AIState',
    agent_id: str,
    user_query: str,
    detected_lang: Optional[str] = None,
    research_mode: str = "none",
    detected_intent: Optional[str] = None,
) -> AsyncGenerator[None, None]:
    """Any agent responds directly to user (generic routing).

    This is the single entry point for all agent responses. Research mode
    determines tool availability:
    - "none": No research tools
    - "automatik": Agent gets web_search/read_webpage tools
    - "quick"/"deep": Forced research before response
    """
    from .agent_config import get_agent_config
    from .prompt_loader import get_agent_direct_prompt

    config = get_agent_config(agent_id)
    if config is None:
        state.add_debug(f"⚠️ Unknown agent: {agent_id}")
        yield
        return

    async for _ in _run_agent_direct_response(
        state, agent_id, config.display_name, config.emoji,
        lambda lang=None, memory=True, tools=False: get_agent_direct_prompt(agent_id, lang=lang, memory=memory, tools=tools),
        user_query, detected_lang,
        research_mode=research_mode,
        detected_intent=detected_intent,
    ):
        yield


# ============================================================
# SOKRATES ANALYSIS
# ============================================================

async def run_sokrates_analysis(
    state: 'AIState',
    user_query: str,
    alfred_answer: str,
    detected_lang: Optional[str] = None
) -> AsyncGenerator[None, None]:
    """
    Run Sokrates analysis based on current multi_agent_mode

    This is called after AIfred's response is complete.
    Uses streaming for real-time output and collects metadata.
    Yields to update UI during analysis.

    For auto_consensus mode: Iterates until Sokrates says LGTM or max_rounds reached.

    Args:
        state: The AIState object for accessing chat_history, add_debug, etc.
        user_query: The original user question
        alfred_answer: AIfred's answer to critique
        detected_lang: Language detected by LLM intent detection ("de" or "en")
                      Defaults to UI-Language if not provided.
    """
    # Fallback to UI language if not provided
    if detected_lang is None:
        from .prompt_loader import get_language
        detected_lang = get_language()

    state.debate_in_progress = True
    state.sokrates_critique = ""  # Clear previous
    state.debate_round = 0

    # DEBUG: Log entry to verify function is called
    state.add_debug(f"🔍 run_sokrates_analysis START: mode={state.multi_agent_mode}, alfred_answer_len={len(alfred_answer)}")
    yield  # Update UI

    # detected_lang comes from LLM-based intent detection (passed from state.py)

    try:
        llm_client = LLMClient(backend_type=state.backend_type, base_url=state.backend_url)

        # Determine models
        sokrates_model = state._effective_model_id("sokrates") or state._effective_model_id("aifred")
        sokrates_display = state.sokrates_model if state.sokrates_model else state.aifred_model  # type: ignore[has-type]
        alfred_model = state._effective_model_id("aifred")
        salomo_model = state._effective_model_id("salomo") or state._effective_model_id("aifred")
        state.add_debug(f"🏛️ Sokrates-LLM: {sokrates_display}")

        # Debate setup: context limits, temperatures, LLM options
        ctx = _setup_debate_contexts(state, alfred_model, sokrates_model, salomo_model)
        sokrates_options = ctx["sokrates_options"]
        alfred_options = ctx["aifred_options"]
        sokrates_num_ctx = ctx["sokrates_ctx"]
        main_llm_ctx = ctx["aifred_ctx"]
        salomo_num_ctx = ctx["salomo_ctx"]
        memory_enabled = state.agent_memory_enabled

        # Agent Memory + Research Tools: recall once before debate starts
        memories = await _recall_agent_memories(state, ["sokrates", "salomo", "aifred"], user_query, detected_lang or "de")
        sokrates_memory_ctx, sokrates_toolkit = memories["sokrates"]
        salomo_memory_ctx, salomo_toolkit = memories["salomo"]
        aifred_memory_ctx, aifred_toolkit = memories["aifred"]

        # Track current answer (may be refined in auto_consensus)
        current_answer = alfred_answer
        consensus_reached = False
        max_rounds = state.max_debate_rounds if state.multi_agent_mode == "auto_consensus" else 1

        for round_num in range(1, max_rounds + 1):
            state.debate_round = round_num

            # === SOKRATES CRITIQUE ===
            # Get system prompts: minimal (base personality) + mode-specific
            sokrates_minimal = get_agent_system_prompt("sokrates", "task",lang=detected_lang, multi_agent=True, memory=memory_enabled)
            if state.multi_agent_mode == "devils_advocate":
                mode_prompt = get_sokrates_devils_advocate_prompt(lang=detected_lang)
            else:
                # Critic prompt for all other modes (Sokrates never says LGTM)
                # round_num prevents hallucinating "progress" in round 1
                mode_prompt = get_sokrates_critic_prompt(round_num=round_num, lang=detected_lang)

            # Combine: minimal first, then mode-specific, then memory
            system_prompt = f"{sokrates_minimal}\n\n{mode_prompt}"
            if sokrates_memory_ctx:
                system_prompt = f"{system_prompt}\n\n{sokrates_memory_ctx}"

            # Build messages + compression check
            sokrates_messages = _build_debate_messages(state, system_prompt, "sokrates", detected_lang)

            sokrates_prompt_tokens = _estimate_prompt_tokens(system_prompt)
            async for _ in _check_compression_if_needed(state, llm_client, sokrates_num_ctx, sokrates_prompt_tokens):
                yield

            sokrates_msg_tokens = estimate_tokens(sokrates_messages, model_name=sokrates_model)
            sokrates_ctx = sokrates_options.num_ctx if sokrates_options and sokrates_options.num_ctx else 8192
            state.add_debug(f"📊 Sokrates R{round_num}: {format_number(sokrates_msg_tokens)} / {format_number(sokrates_ctx)} tokens")
            log_raw_messages(f"Sokrates R{round_num}", sokrates_messages)

            # Stream Sokrates response
            result = None
            async for item in _execute_agent_stream(
                state, "sokrates", "Sokrates", llm_client, sokrates_model,
                sokrates_messages, sokrates_options, sokrates_toolkit,
            ):
                if isinstance(item, dict):
                    result = item
                else:
                    yield

            if result is None:
                break

            sokrates_mode = "advocatus_diaboli" if state.multi_agent_mode == "devils_advocate" else "critical_review"
            sokrates_response_text = _add_agent_result_panel(
                state, "sokrates", result, "Sokrates", sokrates_model, sokrates_mode, round_num,
            )
            state.sokrates_critique = sokrates_response_text
            yield

            state._save_current_session()

            # Parse Pro/Contra for devils_advocate
            if state.multi_agent_mode == "devils_advocate":
                state.sokrates_pro_args, state.sokrates_contra_args = parse_pro_contra(sokrates_response_text)
                break  # Devils advocate is always one round

            # For critical_review: only one round (user decides)
            if state.multi_agent_mode == "critical_review":
                break

            # === AUTO-CONSENSUS (TRIALOG): Salomo synthesizes and decides ===
            if state.multi_agent_mode == "auto_consensus":
                salomo_display = state.salomo_model if state.salomo_model else state.aifred_model  # type: ignore[has-type]
                if round_num == 1:
                    state.add_debug(f"👑 Salomo-LLM: {salomo_display}")

                salomo_options = ctx["salomo_options"]

                # Build Salomo system prompt
                salomo_minimal = get_agent_system_prompt("salomo", "task", lang=detected_lang, multi_agent=True, memory=memory_enabled)
                mediator_prompt = get_salomo_mediator_prompt(round_num=round_num, lang=detected_lang)
                salomo_system = f"{salomo_minimal}\n\n{mediator_prompt}"
                if salomo_memory_ctx:
                    salomo_system = f"{salomo_system}\n\n{salomo_memory_ctx}"

                # Build messages + compression check
                salomo_messages = _build_debate_messages(state, salomo_system, "observer", detected_lang)

                salomo_prompt_tokens = _estimate_prompt_tokens(salomo_system)
                async for _ in _check_compression_if_needed(state, llm_client, salomo_num_ctx, salomo_prompt_tokens):
                    yield

                salomo_msg_tokens = estimate_tokens(salomo_messages, model_name=salomo_model)
                state.add_debug(f"📊 Salomo R{round_num}: {format_number(salomo_msg_tokens)} / {format_number(salomo_num_ctx)} tokens")
                log_raw_messages(f"Salomo R{round_num}", salomo_messages)

                # Stream Salomo response
                salomo_result = None
                async for item in _execute_agent_stream(
                    state, "salomo", "Salomo", llm_client, salomo_model,
                    salomo_messages, salomo_options, salomo_toolkit,
                ):
                    if isinstance(item, dict):
                        salomo_result = item
                    else:
                        yield

                if salomo_result is None:
                    break

                salomo_response_text = _add_agent_result_panel(
                    state, "salomo", salomo_result, "Salomo", salomo_model, "synthesis", round_num,
                )
                state.salomo_synthesis = salomo_response_text
                yield

                state._save_current_session()

                # 3-Agent Consensus Voting
                votes = count_lgtm_votes(current_answer, sokrates_response_text, salomo_response_text)
                state.add_debug(format_votes_debug(votes, round_num))

                if check_consensus(votes, state.consensus_type):
                    lgtm_count = sum(votes.values())
                    type_label = "unanimous" if state.consensus_type == "unanimous" else "majority"
                    state.add_debug(f"✅ Consensus reached in round {format_number(round_num)} ({format_number(lgtm_count)}/3 votes, {type_label})")
                    consensus_reached = True
                    break

                # AIfred refines based on Salomo's feedback
                if round_num < max_rounds:
                    cleaned_salomo_text = strip_thinking_blocks(salomo_response_text)
                    refinement_prompt = get_aifred_refinement_prompt(
                        critique=cleaned_salomo_text, user_interjection="",
                        lang=detected_lang, round_num=round_num + 1,
                    )

                    aifred_system_prompt = get_agent_system_prompt("aifred", "task", lang=detected_lang, multi_agent=True, memory=memory_enabled)
                    if aifred_memory_ctx:
                        aifred_system_prompt = f"{aifred_system_prompt}\n\n{aifred_memory_ctx}"
                    aifred_prompt_tokens = _estimate_prompt_tokens(aifred_system_prompt) + _estimate_prompt_tokens(refinement_prompt)
                    async for _ in _check_compression_if_needed(state, llm_client, main_llm_ctx, aifred_prompt_tokens):
                        yield

                    alfred_messages = _build_debate_messages(state, aifred_system_prompt, "aifred", detected_lang, current_user_text=refinement_prompt)

                    alfred_msg_tokens = estimate_tokens(alfred_messages, model_name=state._effective_model_id("aifred"))
                    alfred_ctx = alfred_options.num_ctx if alfred_options and alfred_options.num_ctx else 32768
                    state.add_debug(f"📊 AIfred R{round_num + 1}: {format_number(alfred_msg_tokens)} / {format_number(alfred_ctx)} tokens")
                    log_raw_messages(f"AIfred R{round_num + 1}", alfred_messages)

                    # Stream AIfred refinement
                    alfred_result = None
                    async for item in _execute_agent_stream(
                        state, "aifred", "AIfred Refinement", llm_client, alfred_model,
                        alfred_messages, alfred_options, aifred_toolkit,
                    ):
                        if isinstance(item, dict):
                            alfred_result = item
                        else:
                            yield

                    if alfred_result is None:
                        break

                    current_answer = _add_agent_result_panel(
                        state, "aifred", alfred_result, "AIfred", alfred_model, "refinement", round_num + 1,
                    )
                    yield

                    state._save_current_session()

        # End of debate
        if state.multi_agent_mode == "auto_consensus":
            if consensus_reached:
                state.add_debug(f"🎯 Debate finished: consensus after {format_number(state.debate_round)} rounds")
            else:
                state.add_debug(f"⚠️ No consensus after {format_number(max_rounds)} rounds")
                if 'votes' in locals():
                    state.add_debug(format_votes_debug(votes, state.debate_round))

        await llm_client.close()
        _finalize_debate(state)

    except Exception as e:
        state.add_debug(f"❌ Sokrates Error: {e}")

    finally:
        state.debate_in_progress = False

    yield  # Final UI update


# ============================================================
# TRIBUNAL MODE (AIfred vs Sokrates, Salomo judges at end)
# ============================================================

async def run_tribunal(
    state: 'AIState',
    user_query: str,
    alfred_answer: str,
    detected_lang: Optional[str] = None
) -> AsyncGenerator[None, None]:
    """
    Run Tribunal mode: AIfred and Sokrates debate, Salomo judges at end.

    This is a separate mode from auto_consensus:
    - AIfred and Sokrates alternate for max_debate_rounds
    - Salomo only speaks at the very end with a final verdict
    - No LGTM during debate - Salomo delivers a definitive judgment

    Args:
        state: The AIState object
        user_query: The original user question
        alfred_answer: AIfred's initial answer
        detected_lang: Language detected by LLM intent detection ("de" or "en")
                      Defaults to UI-Language if not provided.
    """
    # Fallback to UI language if not provided
    if detected_lang is None:
        from .prompt_loader import get_language
        detected_lang = get_language()

    state.debate_in_progress = True
    state.sokrates_critique = ""
    state.salomo_synthesis = ""
    state.debate_round = 0
    yield

    # detected_lang comes from LLM-based intent detection (passed from state.py)

    try:
        llm_client = LLMClient(backend_type=state.backend_type, base_url=state.backend_url)

        # Determine models
        sokrates_model = state._effective_model_id("sokrates") or state._effective_model_id("aifred")
        sokrates_display = state.sokrates_model if state.sokrates_model else state.aifred_model  # type: ignore[has-type]
        salomo_model = state._effective_model_id("salomo") or state._effective_model_id("aifred")
        salomo_display = state.salomo_model if state.salomo_model else state.aifred_model  # type: ignore[has-type]
        alfred_model = state._effective_model_id("aifred")

        state.add_debug("⚖️ Tribunal mode started")
        state.add_debug(f"🏛️ Sokrates-LLM: {sokrates_display}")
        state.add_debug(f"👑 Salomo-LLM: {salomo_display}")

        # Debate setup: context limits, temperatures, LLM options
        ctx = _setup_debate_contexts(state, alfred_model, sokrates_model, salomo_model)
        sokrates_options = ctx["sokrates_options"]
        alfred_options = ctx["aifred_options"]
        salomo_options = ctx["salomo_options"]
        sokrates_num_ctx = ctx["sokrates_ctx"]
        main_llm_ctx = ctx["aifred_ctx"]
        salomo_num_ctx = ctx["salomo_ctx"]
        memory_enabled = state.agent_memory_enabled

        # Agent Memory + Research Tools
        memories = await _recall_agent_memories(state, ["sokrates", "salomo", "aifred"], user_query, detected_lang or "de")
        t_sokrates_memory_ctx, t_sokrates_toolkit = memories["sokrates"]
        t_salomo_memory_ctx, t_salomo_toolkit = memories["salomo"]
        t_aifred_memory_ctx, t_aifred_toolkit = memories["aifred"]

        max_rounds = state.max_debate_rounds

        # === DEBATE PHASE: AIfred vs Sokrates ===
        for round_num in range(1, max_rounds + 1):
            state.debate_round = round_num

            # --- SOKRATES ATTACK ---
            sokrates_minimal = get_agent_system_prompt("sokrates", "task", lang=detected_lang, multi_agent=True, memory=memory_enabled)
            mode_prompt = get_sokrates_tribunal_prompt(round_num=round_num, lang=detected_lang)
            system_prompt = f"{sokrates_minimal}\n\n{mode_prompt}"
            if t_sokrates_memory_ctx:
                system_prompt = f"{system_prompt}\n\n{t_sokrates_memory_ctx}"

            sokrates_prompt_tokens = _estimate_prompt_tokens(system_prompt)
            async for _ in _check_compression_if_needed(state, llm_client, sokrates_num_ctx, sokrates_prompt_tokens):
                yield

            sokrates_messages = _build_debate_messages(state, system_prompt, "sokrates", detected_lang)

            result = None
            async for item in _execute_agent_stream(
                state, "sokrates", "Sokrates", llm_client, sokrates_model,
                sokrates_messages, sokrates_options, t_sokrates_toolkit,
            ):
                if isinstance(item, dict):
                    result = item
                else:
                    yield

            if result is None:
                break

            sokrates_response_text = _add_agent_result_panel(
                state, "sokrates", result, "Sokrates", sokrates_model, "tribunal", round_num,
            )
            state.sokrates_critique = sokrates_response_text
            yield

            state._save_current_session()

            # --- AIFRED DEFENSE ---
            if round_num < max_rounds:
                cleaned_sokrates_text = strip_thinking_blocks(sokrates_response_text)
                refinement_prompt = get_aifred_defense_prompt(
                    critique=cleaned_sokrates_text, user_interjection="",
                    lang=detected_lang, round_num=round_num + 1,
                )

                aifred_system_prompt = get_agent_system_prompt("aifred", "task", lang=detected_lang, multi_agent=True, memory=memory_enabled)
                aifred_prompt_tokens = _estimate_prompt_tokens(aifred_system_prompt) + _estimate_prompt_tokens(refinement_prompt)
                async for _ in _check_compression_if_needed(state, llm_client, main_llm_ctx, aifred_prompt_tokens):
                    yield

                alfred_messages = _build_debate_messages(state, aifred_system_prompt, "aifred", detected_lang, current_user_text=refinement_prompt)

                alfred_result = None
                async for item in _execute_agent_stream(
                    state, "aifred", "AIfred Refinement", llm_client, alfred_model,
                    alfred_messages, alfred_options, t_aifred_toolkit,
                ):
                    if isinstance(item, dict):
                        alfred_result = item
                    else:
                        yield

                if alfred_result is None:
                    break

                _add_agent_result_panel(
                    state, "aifred", alfred_result, "AIfred", alfred_model, "tribunal", round_num + 1,
                )
                yield

                state._save_current_session()

        # === JUDGMENT PHASE: Salomo delivers final verdict ===
        state.add_debug("👑 Salomo rendering verdict...")

        salomo_minimal = get_agent_system_prompt("salomo", "task", lang=detected_lang, multi_agent=True, memory=memory_enabled)
        judge_prompt = get_salomo_judge_prompt(lang=detected_lang)
        salomo_system = f"{salomo_minimal}\n\n{judge_prompt}"
        if t_salomo_memory_ctx:
            salomo_system = f"{salomo_system}\n\n{t_salomo_memory_ctx}"

        salomo_prompt_tokens = _estimate_prompt_tokens(salomo_system)
        async for _ in _check_compression_if_needed(state, llm_client, salomo_num_ctx, salomo_prompt_tokens):
            yield

        salomo_messages = _build_debate_messages(state, salomo_system, "observer", detected_lang)

        salomo_msg_tokens = estimate_tokens(salomo_messages, model_name=salomo_model)
        state.add_debug(f"📊 Salomo Verdict: {format_number(salomo_msg_tokens)} / {format_number(salomo_num_ctx)} tokens")

        salomo_result = None
        async for item in _execute_agent_stream(
            state, "salomo", "Salomo", llm_client, salomo_model,
            salomo_messages, salomo_options, t_salomo_toolkit,
        ):
            if isinstance(item, dict):
                salomo_result = item
            else:
                yield

        if salomo_result is None:
            raise RuntimeError("Salomo verdict stream returned no result")

        salomo_response_text = _add_agent_result_panel(
            state, "salomo", salomo_result, "Salomo", salomo_model, "verdict", max_rounds,
        )
        state.salomo_synthesis = salomo_response_text
        yield

        state.add_debug(f"⚖️ Tribunal completed after {max_rounds} rounds + verdict")

        await llm_client.close()
        _finalize_debate(state)

    except Exception as e:
        state.add_debug(f"❌ Tribunal Error: {e}")

    finally:
        state.debate_in_progress = False

    yield


# ============================================================
# SYMPOSION - Multi-Agent Round Table Discussion
# ============================================================

async def run_symposion(
    state: 'AIState',
    user_query: str,
    detected_lang: Optional[str] = None,
) -> AsyncGenerator[None, None]:
    """Run a Symposion: selected agents discuss a topic in rounds.

    Each agent responds in sequence, seeing all prior responses.
    No winner, no LGTM - multiperspective discussion.
    """
    from .agent_config import get_agent_config
    from .prompt_loader import get_agent_system_prompt, load_prompt
    from .agent_memory import prepare_agent_toolkit
    from .research.context_utils import get_agent_num_ctx

    agents = state.symposion_agents
    max_rounds = state.max_debate_rounds
    memory_enabled = state.agent_memory_enabled

    default_agents = ("aifred", "sokrates", "salomo", "vision")

    agent_configs = []
    for agent_id in agents:
        cfg = get_agent_config(agent_id)
        if cfg:
            agent_configs.append((agent_id, cfg))

    if len(agent_configs) < 2:
        state.add_debug("⚠️ Symposion requires at least 2 agents")
        yield
        return

    agent_names = ", ".join(cfg.display_name for _, cfg in agent_configs)
    state.add_debug(f"🏛️ Symposion: {agent_names} ({max_rounds} rounds)")
    state.debate_in_progress = True
    yield

    try:
        # Load symposion discussion rules + reflection augmentation
        # (Reflection is appended from round 2 onwards so the discussion
        # gains depth without sacrificing breadth — agents must address
        # gaps left by earlier contributions while keeping their own
        # multiperspective stance.)
        symposion_prompt = load_prompt("shared/symposion", lang=detected_lang)
        reflection_prompt = load_prompt("shared/symposion_reflection", lang=detected_lang)

        # Shared conversation (all agents see prior responses)
        conversation: list[dict[str, str]] = [
            {"role": "user", "content": user_query}
        ]

        llm_client = LLMClient(backend_type=state.backend_type)

        for round_num in range(1, max_rounds + 1):
            state.debate_round = round_num

            for agent_id, cfg in agent_configs:
                agent_label = cfg.display_name
                emoji = cfg.emoji

                # Model: custom agents use AIfred's model
                if agent_id in default_agents:
                    model_id = state._effective_model_id(agent_id) or state._effective_model_id("aifred")
                else:
                    model_id = state._effective_model_id("aifred")

                state.add_debug(f"{emoji} {agent_label} (R{round_num})")

                # Context limit
                ctx_agent = agent_id if agent_id in default_agents else "aifred"
                agent_num_ctx, _ = get_agent_num_ctx(ctx_agent, state, model_id)

                # System prompt: agent identity + symposion rules + memory
                # From round 2 onwards augment with the reflection prompt so
                # each follow-up round actively probes for unanswered aspects.
                agent_system = get_agent_system_prompt(
                    agent_id, prompt_key="direct", lang=detected_lang, memory=memory_enabled
                )
                system_prompt = f"{agent_system}\n\n{symposion_prompt}"
                if round_num >= 2:
                    system_prompt = f"{system_prompt}\n\n{reflection_prompt}"

                # Memory recall (round 1) + toolkit with research tools (every round)
                memory_ctx = ""
                mem_ctx, toolkit = await prepare_agent_toolkit(
                    agent_id, user_query, lang=detected_lang or "de",
                    memory_enabled=memory_enabled,
                    research_tools_enabled=True,
                    state=state,
                    session_id=state.session_id,
                )
                if round_num == 1 and mem_ctx:
                    memory_ctx = mem_ctx

                if memory_ctx:
                    system_prompt = f"{system_prompt}\n\n{memory_ctx}"

                # Build messages: system + conversation history
                messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
                for msg in conversation:
                    if msg["role"] == "user":
                        messages.append(msg)
                    elif msg.get("agent") == agent_id:
                        messages.append({"role": "assistant", "content": msg["content"]})
                    else:
                        messages.append({"role": "user", "content": msg["content"]})

                # Build options (use agent's temperature from state)
                opts_agent = agent_id if agent_id in default_agents else "aifred"
                agent_temp = getattr(state, f"{opts_agent}_temperature", 0.7)
                agent_options = build_llm_options(state, opts_agent, agent_temp, agent_num_ctx)

                # Stream response
                result = None
                async for item in _stream_agent_to_history(
                    state, agent_id, agent_label, llm_client,
                    model=model_id, messages=messages,
                    options=agent_options, toolkit=toolkit,
                ):
                    if isinstance(item, dict):
                        result = item
                    else:
                        yield

                if result is None:
                    state.add_debug(f"❌ {agent_label} returned no result")
                    continue

                metadata_dict = result["metadata_dict"]
                audio_urls = result.get("audio_urls", [])

                formatted = _format_stream_result(result, agent_label, model_id)

                state.add_agent_panel(
                    agent=agent_id,
                    content=formatted,
                    mode="symposion",
                    round_num=round_num,
                    metadata={**metadata_dict, "audio_urls": audio_urls},
                    sync_llm_history=False,
                )

                # Add to conversation for next agents
                conversation.append({
                    "role": "assistant",
                    "agent": agent_id,
                    "content": f"[{agent_label}]: {strip_thinking_blocks(result['text'])}",
                })

                state._streaming_sub().current_ai_response = ""
                state._set_current_agent("")
                yield

        await llm_client.close()

        state.add_debug(f"🏛️ Symposion done ({max_rounds} rounds, {len(agent_configs)} agents)")
        console_separator()
        state.add_debug("────────────────────")

    except Exception as e:
        state.add_debug(f"❌ Symposion Error: {e}")

    finally:
        state.debate_in_progress = False
        state._save_current_session()

    yield
