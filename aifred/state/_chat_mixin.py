"""Chat mixin for AIfred state.

Handles message sending, AI response streaming, agent panel display,
and chat clearing.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any, Dict, List

import reflex as rx

from ..lib import log_message
from ..lib.config import DEBUG_MESSAGES_MAX
from ..lib.context_manager import strip_thinking_blocks


class ChatMixin(rx.State, mixin=True):
    """Mixin for chat message sending and AI response streaming."""

    # ── State Variables ──────────────────────────────────────────────
    current_user_input: str = ""
    current_user_message: str = ""  # The message currently being processed
    # current_ai_response lives on StreamingState (separate React context)
    current_agent: str = ""  # Current streaming agent ID
    current_agent_display_name: str = ""  # Display name for streaming UI
    current_agent_emoji: str = ""  # Emoji for streaming UI
    is_generating: bool = False
    # True when the current is_generating was triggered by an external
    # channel (Message Hub: Puck, email, Telegram, …) rather than the
    # local browser.  The tick handler keeps running its mtime-watch
    # while a hub-side pipeline is in progress so the user sees the
    # chat bubble the moment STT / intent / etc. writes to the session
    # file — instead of only after the whole pipeline finishes.
    is_generating_hub: bool = False
    is_compressing: bool = False  # Shows if history compression is running

    # Debug Console
    debug_messages: List[str] = []
    auto_refresh_enabled: bool = True  # For Debug Console + Chat History + AI Response Area

    # Processing Progress (Automatik, Scraping, LLM)
    progress_active: bool = False
    progress_phase: str = ""  # "automatik", "scraping", "llm"
    progress_current: int = 0
    progress_total: int = 0
    progress_failed: int = 0  # Number of failed URLs

    # Tool Status (shown in UI while agent uses tools)
    tool_status: str = ""  # e.g. "🌐 bibleserver.com/HFA/Psalm139"

    # Research context (set by forced research pipeline, read by agent response)
    _research_context: str = ""
    _research_sources_html: str = ""

    # ── Debug / Progress ─────────────────────────────────────────────

    def add_debug(self, message: str) -> None:
        """Add message to debug console.

        Appends to Reflex State list (live browser UI) and forwards
        to the Debug Bus (logfile + optional session file persistence).
        """
        import datetime as _dt
        from ..lib.debug_bus import debug as _debug

        timestamp = _dt.datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"{timestamp} | {message}"

        # Reflex State list (live browser UI via WebSocket)
        self.debug_messages.append(formatted_msg)
        if len(self.debug_messages) > DEBUG_MESSAGES_MAX:
            self.debug_messages = self.debug_messages[-DEBUG_MESSAGES_MAX:]

        # Debug Bus (logfile + optional session persistence)
        _debug(message)

    def set_progress(self, phase: str, current: int = 0, total: int = 0, failed: int = 0) -> None:
        """Update processing progress."""
        self.progress_active = True
        self.progress_phase = phase
        self.progress_current = current
        self.progress_total = total
        self.progress_failed = failed

    def clear_progress(self) -> None:
        """Clear processing progress."""
        self.progress_active = False
        self.progress_phase = ""

    def set_tool_status(self, status: str) -> None:
        """Show tool activity in UI (e.g. '🌐 fetching bibleserver.com...')."""
        self.tool_status = status

    def clear_tool_status(self) -> None:
        """Clear tool status."""
        self.tool_status = ""
        self.progress_current = 0
        self.progress_total = 0
        self.progress_failed = 0

    # ── llama-swap Helpers ────────────────────────────────────────────

    def _llamaswap_base_url(self) -> str:
        """Get llama-swap base URL (without /v1 suffix)."""
        return self.backend_url.rstrip("/").removesuffix("/v1")  # type: ignore[attr-defined]

    async def _llamaswap_running_models(self) -> list[str]:
        """Query llama-swap /running endpoint. Returns list of loaded model IDs, empty on error."""
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(f"{self._llamaswap_base_url()}/running")
            return [m.get("model") for m in resp.json().get("running", [])]

    # ── Agent Panel Helpers ──────────────────────────────────────────

    def _get_mode_label(self, mode: str, round_num: int | None) -> str:
        """Generate mode label based on mode and UI language.

        Args:
            mode: Mode identifier (e.g., "auto_consensus", "tribunal", "direct")
            round_num: Optional round number for multi-round debates

        Returns:
            Localized label string (e.g., "Auto-Konsens", "Tribunal", "Direkte Antwort")
        """
        from ..lib.i18n import t

        # Mode label mapping (without round number)
        mode_labels = {
            "auto_consensus": t("auto_consensus_label", lang=self.ui_language).rstrip(":"),  # type: ignore[attr-defined]
            "tribunal": "Tribunal",  # Same in both languages
            "direct": "",  # No label — direct is the default mode now
            "refinement": t("refinement_label", lang=self.ui_language).rstrip(":"),  # type: ignore[attr-defined]
            "synthesis": t("salomo_synthesis_label", lang=self.ui_language).rstrip(":"),  # type: ignore[attr-defined]
            "verdict": t("salomo_verdict_label", lang=self.ui_language).rstrip(":"),  # type: ignore[attr-defined]
            "critical_review": t("critical_review_label", lang=self.ui_language).rstrip(":"),  # type: ignore[attr-defined]
            "advocatus_diaboli": t("advocatus_diaboli_label", lang=self.ui_language).rstrip(":"),  # type: ignore[attr-defined]
            "symposion": "Symposion",  # Same in both languages
            "error": "Error",  # Same in both languages
            "standard": "",  # No label for standard mode
        }

        return mode_labels.get(mode, "")

    def _set_current_agent(self, agent_id: str) -> None:
        """Set current streaming agent with display info for UI."""
        from ..lib.agent_config import get_agent_config
        self.current_agent = agent_id
        if agent_id:
            cfg = get_agent_config(agent_id)
            self.current_agent_display_name = cfg.display_name if cfg else agent_id.capitalize()
            self.current_agent_emoji = cfg.emoji if cfg else "\U0001f916"
        else:
            self.current_agent_display_name = ""
            self.current_agent_emoji = ""

    def _build_marker(self, agent: str, mode: str, round_num: int | None) -> str:
        """Build marker string for agent panels.

        Args:
            agent: Agent identifier ("aifred", "sokrates", "salomo")
            mode: Mode identifier (e.g., "refinement", "critical_review", "verdict")
            round_num: Optional round number

        Returns:
            Formatted marker like "<span style='...'>Auto-Konsens: Überarbeitung R2</span>\\n\\n"
            (includes multi_agent_mode prefix if active, no emoji - already shown left of bubble)
        """
        label = self._get_mode_label(mode, round_num)

        if not label:
            return ""  # No marker for standard mode

        # Prepend multi-agent mode prefix (e.g., "Auto-Konsens:", "Tribunal:")
        # Skip for "standard" mode, when mode already includes the prefix,
        # or when mode equals multi_agent_mode (prevents "[Critical Review: Critical Review R1]")
        mode_prefix = ""
        if self.multi_agent_mode != "standard" and mode not in ["auto_consensus", "tribunal", "devils_advocate", "symposion"] and mode != self.multi_agent_mode:  # type: ignore[attr-defined]
            # Get localized multi-agent mode label
            multi_mode_label = self._get_mode_label(self.multi_agent_mode, None)  # type: ignore[attr-defined]
            if multi_mode_label:
                mode_prefix = f"{multi_mode_label}: "

        # Add round suffix if present
        round_suffix = f" R{round_num}" if round_num else ""

        # Format with HTML span for styling (no emoji - already in UI)
        # Color: rgba(255, 255, 255, 1.0) = 100% opacity white (fully opaque)
        # Style: italic, smaller font
        # Spacing: 2 newlines after (converted to <br><br> in HTML export)
        return f"<span style='color: rgba(255, 255, 255, 0.6); font-style: italic; font-size: 12px;'>[{mode_prefix}{label}{round_suffix}]</span>\n\n"

    def _format_panel_metadata(self, metadata: dict | None) -> str:
        """Format metadata footer for agent panels."""
        if not metadata:
            return ""
        from ..lib.formatting import format_performance_footer
        return format_performance_footer(metadata)

    # ── LLM History Sync ─────────────────────────────────────────────

    def _sync_to_llm_history(self, agent: str, content: str) -> None:
        """Sync agent response to llm_history with speaker label.

        Strips only thinking blocks (<think>, Harmony analysis).
        Code tags (<python>, <code>, etc.) are preserved because they
        provide important context for the LLM.

        IMPORTANT: Callers should pass RAW content (before format_thinking_process),
        not formatted content with <details> collapsibles. If the caller already
        formats before calling add_agent_panel(), use sync_llm_history=False and
        sync manually with raw content.

        Args:
            agent: Agent identifier ("aifred", "sokrates", "salomo")
            content: Agent response content (should be RAW, not formatted)
        """
        label = agent.upper()
        clean_content = strip_thinking_blocks(content)

        if clean_content:
            self._chat_sub().llm_history.append({
                "role": "assistant",
                "content": f"[{label}]: {clean_content}"
            })

    # ── Central Agent Panel ──────────────────────────────────────────

    def add_agent_panel(
        self,
        agent: str,  # "aifred", "sokrates", "salomo"
        content: str,
        mode: str = "standard",
        round_num: int | None = None,
        metadata: dict | None = None,
        sync_llm_history: bool = True,
        generate_tts: bool | None = None,
    ) -> None:
        """Add an agent response as a new message to chat_history.

        This is the ONLY function that should be used to add agent panels to chat_history.
        It handles:
        - Emoji marker generation
        - Mode labeling (Auto-Consensus, Tribunal, etc.)
        - Round numbering (R1, R2, ...)
        - Metadata formatting (TTFT, Inference time, tok/s, Source)
        - LLM history synchronization
        - Session persistence
        - TTS generation (queued for sequential playback)

        With the new dict-based chat_history, each message is standalone.
        No more replace_last logic - just append new messages.

        Args:
            agent: Agent identifier ("aifred", "sokrates", "salomo")
            content: Agent response content (WITHOUT marker, WITHOUT metadata)
            mode: Mode identifier (e.g., "auto_consensus", "tribunal", "direct", "standard")
            round_num: Round number for multi-round debates (None/0 = no round, 1+ = round number)
            metadata: Optional dict with TTFT, inference_time, tokens_per_sec, source
            sync_llm_history: If True, syncs to llm_history (set False if caller already did)
            generate_tts: If True, generate TTS and add to queue. If None, uses self.enable_tts.
                         If False, skip TTS. For multi-agent modes, this enables per-response TTS.
        """
        import asyncio

        from ..lib.i18n import t
        from ..lib.prompt_loader import get_language

        # 1. Build marker (emoji + mode label + round number)
        marker = self._build_marker(agent, mode, round_num if round_num and round_num > 0 else None)

        # 2. Format metadata footer
        meta_footer = self._format_panel_metadata(metadata)

        # 3. Translate consensus tags to natural language for UI display
        # These are trigger words for the Multi-Agent system, already parsed by count_lgtm_votes()
        # Now we make them human-readable in the UI (and TTS will speak what's displayed)
        # Uses detected language (from Intent Detection) for correct localization
        lang = self._last_detected_language or get_language()  # type: ignore[attr-defined, has-type]
        content = re.sub(r'\[LGTM\]', t("consensus_agreed", lang=lang), content, flags=re.IGNORECASE)
        content = re.sub(r'\[WEITER\]', t("consensus_continue", lang=lang), content, flags=re.IGNORECASE)

        # 4. Remove thinking blocks from content before storing (for History/Token estimation)
        clean_content = strip_thinking_blocks(content)

        # 5. Assemble final content for display
        if marker:
            final_content = f"{marker}{clean_content}\n\n{meta_footer}"
        else:
            # Standard mode: no marker, just content + metadata
            final_content = f"{clean_content}\n\n{meta_footer}" if meta_footer else clean_content

        # 5. Create new message entry (dict-based format)
        # Include audio URLs if streaming TTS generated them
        msg_metadata = metadata.copy() if metadata else {}
        if self._pending_audio_urls:  # type: ignore[attr-defined, has-type]
            msg_metadata["audio_urls"] = self._pending_audio_urls.copy()  # type: ignore[attr-defined, has-type]
            log_message(f"🔊 add_agent_panel: Stored {len(self._pending_audio_urls)} audio URLs in message metadata")  # type: ignore[attr-defined, has-type]
            self._pending_audio_urls: list[str] = []  # type: ignore[attr-defined, var-annotated]

        # Store agent's playback rate for HTML export (browser speed setting, per-agent)
        # Always set when audio_urls are present, regardless of source
        audio_urls = msg_metadata.get("audio_urls", [])
        if audio_urls:
            msg_metadata["playback_rate"] = "1.0x"  # Speed is baked into audio via engine or ffmpeg
        # SSOT: base dict from shared builder
        from ..lib.formatting import build_assistant_chat_entry
        new_message: Dict[str, Any] = build_assistant_chat_entry(final_content, agent, msg_metadata)

        # Browser-specific fields (mode, round, audio, sources)
        new_message["mode"] = mode
        new_message["round_num"] = round_num
        new_message["used_sources"] = []
        new_message["failed_sources"] = []
        new_message["has_audio"] = bool(audio_urls)
        new_message["audio_urls_json"] = json.dumps(audio_urls) if audio_urls else "[]"

        # 5. Append to chat_history (no more replace_last!)
        self._chat_sub().chat_history.append(new_message)

        # 6. Sync to llm_history (with speaker label)
        # Note: Some callers (streaming functions) already sync to llm_history,
        # so they should pass sync_llm_history=False to avoid duplicates
        if sync_llm_history:
            self._sync_to_llm_history(agent, content)

        # 7. Save session (async, non-blocking)
        self._save_current_session()  # type: ignore[attr-defined]

        # 8. Generate TTS and add to queue (if enabled)
        # Determine if TTS should be generated
        # SKIP if streaming TTS is ACTIVE (autoplay + streaming both on) —
        # text was already sent sentence-by-sentence during inference.
        should_generate_tts = generate_tts if generate_tts is not None else self.enable_tts  # type: ignore[attr-defined]
        streaming_active = self.tts_autoplay and self.tts_streaming_enabled  # type: ignore[attr-defined]
        if should_generate_tts and not streaming_active:
            # Check per-agent TTS enabled setting
            agent_tts_enabled = self.tts_agent_voices.get(agent, {}).get("enabled", True)  # type: ignore[attr-defined]
            if agent_tts_enabled:
                # Schedule TTS generation as background task
                # This runs async without blocking add_agent_panel()
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._queue_tts_for_agent(content, agent))  # type: ignore[attr-defined]
                except RuntimeError:
                    # No running loop - this shouldn't happen in normal operation
                    # but we handle it gracefully
                    self.add_debug(f"⚠️ TTS: No event loop for {agent}")

    # ── Multi-Agent Dispatch ─────────────────────────────────────────

    async def _dispatch_multi_agent(
        self,
        user_msg: str,
        ai_text: str,
        detected_language: str,
        skip_analysis: bool,
    ) -> AsyncGenerator[None, None]:
        """Run Multi-Agent analysis if activated and not skipped.

        Args:
            user_msg: The user message
            ai_text: The AI response (AIfred R1)
            detected_language: Language ("de" or "en")
            skip_analysis: True to skip Multi-Agent (e.g. user addressed AIfred directly)

        Yields:
            Nothing directly, but updates state via run_tribunal/run_sokrates_analysis
        """
        from ..lib.multi_agent import run_sokrates_analysis, run_tribunal

        # Skip if standard mode, no AI text, or explicitly skipped
        if self.multi_agent_mode == "standard" or not ai_text or skip_analysis:  # type: ignore[attr-defined]
            return

        # Generate TTS for AIfred's initial response BEFORE Multi-Agent starts
        # This ensures AIfred's voice is heard first, then Sokrates/Salomo follow
        # (Sokrates/Salomo TTS is generated via add_agent_panel() in multi_agent.py)
        # SKIP if streaming TTS is enabled - text was already sent sentence-by-sentence
        if self.enable_tts and not self.tts_streaming_enabled:  # type: ignore[attr-defined]
            agent_tts_enabled = self.tts_agent_voices.get("aifred", {}).get("enabled", True)  # type: ignore[attr-defined]
            if agent_tts_enabled:
                # Wait for TTS to complete so we can update message metadata with audio URL
                await self._queue_tts_for_agent(ai_text, agent="aifred")  # type: ignore[attr-defined]
                yield  # Update UI with audio button (chat_history was reassigned in _queue_tts_for_agent)

        if self.multi_agent_mode == "tribunal":  # type: ignore[attr-defined]
            self.add_debug("⚖️ Multi-Agent: Tribunal startet...")
            yield
            async for _ in run_tribunal(self, user_msg, ai_text, detected_language):  # type: ignore[arg-type]
                yield
        else:
            self.add_debug("🏛️ Multi-Agent: Sokrates-Analyse startet...")
            yield
            async for _ in run_sokrates_analysis(self, user_msg, ai_text, detected_language):  # type: ignore[arg-type]
                yield

    # ── VL Inference Helper ──────────────────────────────────────────

    async def _process_vision_request(
        self,
        user_msg: str,
        content_parts: list[dict],
        detected_intent: str,
        detected_language: str,
        vision_prompt_key: str = "task_qa",
    ) -> AsyncGenerator[None, None]:
        """Run VL inference via call_llm and process results.

        Shared by VL Direct, VL Shortcut, and VL Follow-up paths.
        Handles streaming, history update, cleanup, title generation and session save.

        The VL model acts as the currently active agent (with that agent's
        memory, tools, and personality). For multi-agent modes with multiple
        selected agents, defaults to "aifred".

        For llamacpp: prefers the -speed variant (single GPU, faster) unless
        the base variant is already running (avoid unnecessary model swap).
        """
        from ..lib.llm_engine import call_llm

        # Determine effective vision model (speed/TTS variants resolved centrally)
        effective_vision_id = self._effective_model_id("vision")  # type: ignore[attr-defined]
        if effective_vision_id != self.vision_model_id:  # type: ignore[attr-defined]
            self.add_debug(f"⚡ VL variant: {effective_vision_id}")  # type: ignore[attr-defined]
            yield

        # VL acts as the active agent (memory, tools, personality)
        acting_agent = self.active_agent or "aifred"  # type: ignore[attr-defined]
        self._set_current_agent(acting_agent)
        self.add_debug(f"📷 VL acting as: {acting_agent}")  # type: ignore[attr-defined]
        yield

        result_data = None
        async for item in call_llm(
            user_text=user_msg,
            model_choice=effective_vision_id,
            history=self._chat_sub().chat_history,
            llm_history=self._chat_sub().llm_history[:-1],
            detected_intent=detected_intent,
            detected_language=detected_language,
            temperature_mode="manual",
            temperature=self.vision_temperature,  # type: ignore[attr-defined]
            backend_type=self.backend_type,  # type: ignore[attr-defined]
            backend_url=self.backend_url,  # type: ignore[attr-defined]
            enable_thinking=self.vision_thinking,  # type: ignore[attr-defined]
            state=self,
            multimodal_content=content_parts,
            vision_prompt_key=vision_prompt_key,
            provider=self.cloud_api_provider if self.backend_type == "cloud_api" else None,  # type: ignore[attr-defined]
            agent=acting_agent,
        ):
            if item["type"] == "debug":
                self.add_debug(item["message"])
                yield
            elif item["type"] == "content":
                if self.stream_text_to_ui(item["text"]):  # type: ignore[attr-defined]
                    yield
            elif item["type"] == "progress":
                if item.get("clear", False):
                    self.clear_progress()  # type: ignore[attr-defined]
                else:
                    self.set_progress(  # type: ignore[attr-defined]
                        phase=item.get("phase", ""),
                        current=item.get("current", 0),
                        total=item.get("total", 0),
                    )
                yield
            elif item["type"] == "result":
                # Flush remaining buffer to state
                if self.flush_stream_to_ui():  # type: ignore[attr-defined]
                    yield
                result_data = item["data"]

        # Separator after VL inference (consistent with all other inference paths)
        from ..lib.logging_utils import console_separator, CONSOLE_SEPARATOR
        console_separator()
        self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
        yield

        if result_data:
            # call_llm() got llm_history[:-1] (N-1 entries) and appended
            # the AI response → returned slice has N entries when successful.
            # llm_history still has N entries (prior + user_msg from line 511).
            # Length equality means exactly one AI entry was added → append it.
            ch = self._chat_sub()
            returned_llm = result_data["llm_history"]
            if (len(returned_llm) == len(ch.llm_history)
                    and returned_llm[-1].get("role") == "assistant"):
                ch.llm_history = list(ch.llm_history) + [returned_llm[-1]]
            self._chat_sub().chat_history = result_data["history"]

        self._streaming_sub().current_ai_response = ""  # type: ignore[attr-defined]
        self.current_user_message = ""
        self.is_generating = False
        yield

        async for _ in self._generate_session_title(title_model_override=effective_vision_id):  # type: ignore[attr-defined]
            yield
        self._save_current_session()  # type: ignore[attr-defined]
        self.refresh_session_list()  # type: ignore[attr-defined]
        yield

    # ── Main Send Message ────────────────────────────────────────────

    async def _phase_tts_container_checks(self) -> AsyncGenerator[None, None]:
        """Ensure VRAM state matches TTS requirements before LLM loads.

        Uses ensure_tts_state (SSOT) — same function as FreeEcho.2 Puck.
        Browser passes what it wants, function handles the rest.
        """
        from ..lib.tts_engine_manager import ensure_tts_state, GPU_ENGINES

        wanted = ""
        if self.enable_tts and self.tts_engine in GPU_ENGINES:  # type: ignore[attr-defined]
            if not (self.tts_engine == "xtts" and self.xtts_force_cpu):  # type: ignore[attr-defined]
                wanted = self.tts_engine  # type: ignore[attr-defined]

        gen = ensure_tts_state(
            wanted_tts=wanted,
            backend_type=self.backend_type,  # type: ignore[attr-defined]
            xtts_force_cpu=self.xtts_force_cpu,  # type: ignore[attr-defined]
        )
        try:
            while True:
                msg = next(gen)
                self.add_debug(f"🔊 {msg}")  # type: ignore[attr-defined]
                yield
        except StopIteration:
            pass

    async def _phase_pre_message_compression(
        self, llm_client: Any, detected_language: str,
    ) -> AsyncGenerator[None, None]:
        """Check if history compression is needed before adding new message."""
        if not self._chat_sub().chat_history:
            return

        from ..lib.context_manager import summarize_history_if_needed, get_largest_compression_model
        from ..lib.research.context_utils import get_agent_num_ctx
        from ..lib.prompt_loader import get_max_system_prompt_tokens

        # Determine effective context limit (minimum of all agents)
        context_limits: list[int] = []
        aifred_ctx, _ = get_agent_num_ctx("aifred", self, self._effective_model_id("aifred"))  # type: ignore[attr-defined, arg-type]
        context_limits.append(aifred_ctx)

        if self.multi_agent_mode != "standard":  # type: ignore[attr-defined]
            if self.sokrates_model_id:  # type: ignore[attr-defined]
                sokrates_ctx, _ = get_agent_num_ctx("sokrates", self, self._effective_model_id("sokrates"))  # type: ignore[attr-defined, arg-type]
                context_limits.append(sokrates_ctx)
            if self.salomo_model_id:  # type: ignore[attr-defined]
                salomo_ctx, _ = get_agent_num_ctx("salomo", self, self._effective_model_id("salomo"))  # type: ignore[attr-defined, arg-type]
                context_limits.append(salomo_ctx)

        context_limit = min(context_limits) if context_limits else 4096
        system_prompt_tokens = get_max_system_prompt_tokens(self.multi_agent_mode, detected_language)  # type: ignore[attr-defined]

        compression_model = get_largest_compression_model(
            aifred_model=self._effective_model_id("aifred"),  # type: ignore[attr-defined]
            sokrates_model=self._effective_model_id("sokrates"),  # type: ignore[attr-defined]
            salomo_model=self._effective_model_id("salomo"),  # type: ignore[attr-defined]
        )

        async for event in summarize_history_if_needed(
            history=self._chat_sub().chat_history,
            llm_client=llm_client,
            model_name=compression_model,
            context_limit=context_limit,
            llm_history=self._chat_sub().llm_history,
            system_prompt_tokens=system_prompt_tokens,
            detected_language=detected_language,
        ):
            if event["type"] == "history_update":
                self._chat_sub().chat_history = event["chat_history"]
                if event.get("llm_history") is not None:
                    self._chat_sub().llm_history = event["llm_history"]
                _ch = self._chat_sub()
                self.add_debug(f"✅ Pre-Message Compression: {len(_ch.chat_history)} UI / {len(_ch.llm_history)} LLM messages")
                yield
            elif event["type"] == "debug":
                self.add_debug(event["message"])
                yield

    async def _phase_finally_tts(self, ai_text: str) -> AsyncGenerator[None, None]:
        """Generate TTS audio in finally block (standard mode only, non-streaming)."""
        import re
        if not (self.enable_tts and self.multi_agent_mode == "standard" and not self.tts_streaming_enabled):  # type: ignore[attr-defined]
            return

        try:
            self.add_debug("🔊 TTS: Starting TTS generation...")
            _ch = self._chat_sub()
            if len(_ch.llm_history) > 0:
                last_msg = _ch.llm_history[-1]
                if last_msg.get("role") == "assistant":
                    ai_response = last_msg.get("content", "")
                    tts_agent = "aifred"
                    agent_match = re.match(r'^\[(AIFRED|SOKRATES|SALOMO)\]:\s*', ai_response)
                    if agent_match:
                        tts_agent = agent_match.group(1).lower()
                        ai_response = ai_response[agent_match.end():]
                    agent_tts_enabled = self.tts_agent_voices.get(tts_agent, {}).get("enabled", True)  # type: ignore[attr-defined]
                    if ai_response and ai_response.strip() and agent_tts_enabled:
                        await self._generate_tts_for_response(ai_response, agent=tts_agent)  # type: ignore[attr-defined]
                        yield
                    else:
                        self.add_debug("⚠️ TTS: Enabled but no AI response to convert")
                else:
                    self.add_debug("⚠️ TTS: Last message is not from assistant")
            else:
                self.add_debug("⚠️ TTS: Enabled but LLM history is empty")
        except (RuntimeError, FileNotFoundError, ValueError) as tts_error:
            self.add_debug(f"⚠️ TTS generation failed: {tts_error}")
            from ..lib.logging_utils import log_message
            log_message(f"❌ TTS error in finally block: {tts_error}")

    async def send_message(self, text: str = "") -> AsyncGenerator[None, None]:  # type: ignore[misc]
        """Send message to LLM with optional web research.

        Args:
            text: User text from UI (via call_script callback).
                  Empty when called programmatically — reads from current_user_input.
        """
        # Must be logged in to send messages
        if not self.logged_in_user:  # type: ignore[attr-defined]
            self.add_debug("⚠️ Please log in first")
            return

        # If no text but images present, use default prompt
        has_pending_images = len(self.pending_images) > 0  # type: ignore[attr-defined]
        # Text from call_script callback (UI click) or current_user_input (injection/transcription)
        user_text = (text or self.current_user_input).strip()

        if not user_text and not has_pending_images:
            return  # Nothing to send

        if self.is_generating:
            self.add_debug("⚠️ Already generating, please wait...")
            return

        await self._ensure_backend_initialized()  # type: ignore[attr-defined]

        # ============================================================
        # PHASE 1: Spinner + textarea clear — yield IMMEDIATELY
        # ============================================================
        # Minimal state for instant UI feedback (spinner + correct agent indicator)
        # Textarea is already cleared client-side by the call_script in on_click
        self.is_generating = True
        self._set_current_agent("")
        self._streaming_sub().current_ai_response = ""  # type: ignore[attr-defined]
        yield  # Spinner visible immediately

        # ============================================================
        # PHASE 2: Build and add user message to chat
        # ============================================================
        user_msg = user_text
        self.current_user_input = ""  # Clear state (for injection/transcription path)
        self.current_user_message = user_msg
        self.used_sources: list[dict[str, Any]] = []  # type: ignore[attr-defined, var-annotated]
        self.failed_sources: list[dict[str, str]] = []  # type: ignore[attr-defined, var-annotated]
        self.all_sources: list[dict[str, Any]] = []  # type: ignore[attr-defined, var-annotated]
        self.clear_tts_queue()  # type: ignore[attr-defined]

        display_user_msg = user_msg
        if has_pending_images:
            # Generate clickable image thumbnails as HTML
            image_html_parts: list[str] = []
            for img in self.pending_images:  # type: ignore[attr-defined]
                url = img.get('url', '')
                if url:
                    image_html_parts.append(
                        f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
                        f'<img src="{url}" style="width:50px;height:50px;object-fit:cover;'
                        f'border-radius:4px;cursor:pointer;margin-right:4px;"></a>'
                    )
            image_html = "".join(image_html_parts)

            if not user_msg or user_msg.strip() == "":
                # Image-only upload
                if len(self.pending_images) == 1:  # type: ignore[attr-defined]
                    display_user_msg = f"{image_html}\n\n📷 {self.pending_images[0].get('name', 'Image')}"  # type: ignore[attr-defined]
                else:
                    img_names = ", ".join([img.get("name", "unknown") for img in self.pending_images])  # type: ignore[attr-defined]
                    display_user_msg = f"{image_html}\n\n📷 {len(self.pending_images)} images: {img_names}"  # type: ignore[attr-defined]
            else:
                # Text + images
                display_user_msg = f"{image_html}\n\n{user_msg}" if image_html else user_msg

        self._chat_sub().chat_history.append({
            "role": "user",
            "content": display_user_msg,
            "agent": "",
            "mode": "",
            "round_num": 0,
            "metadata": {
                "images": [{"name": img.get("name", ""), "url": img.get("url", "")} for img in self.pending_images] if has_pending_images else []  # type: ignore[attr-defined]
            },
            "timestamp": datetime.now().isoformat(),
            "time_display": datetime.now().strftime("%d.%m. \u2014 %H:%M"),
            "used_sources": [],
            "failed_sources": [],
            "has_audio": False,
            "audio_urls_json": "[]",
        })
        self._chat_sub().llm_history.append({"role": "user", "content": user_msg})
        self.add_debug("📨 User request received")

        # ============================================================
        # PHASE 3: TTS streaming init
        # ============================================================
        agent_tts_on = self.tts_agent_voices.get("aifred", {}).get("enabled", True)  # type: ignore[attr-defined]
        if self.enable_tts and self.tts_autoplay and self.tts_streaming_enabled and agent_tts_on:  # type: ignore[attr-defined]
            self._init_streaming_tts(agent="aifred")  # type: ignore[attr-defined]
            from ..lib.api import tts_queue_clear
            tts_queue_clear(self.session_id)  # type: ignore[attr-defined]
            yield rx.call_script(f"if(window.startTtsStream) startTtsStream('{self.session_id}');")  # type: ignore[attr-defined]
        else:
            yield  # Push user message bubble to browser

        # ============================================================
        # PHASE 4: vLLM model loading (AFTER user message is visible)
        # ============================================================
        if self.backend_type == "vllm":  # type: ignore[attr-defined]
            from . import _global_backend_state
            mgr = _global_backend_state.get("vllm_manager")
            if not (mgr and mgr.is_running()) and self.aifred_model_id:  # type: ignore[attr-defined]
                self.add_debug(f"🚀 Starting vLLM with {self.aifred_model_id}...")  # type: ignore[attr-defined]
                yield  # Show debug message while loading
            await self._ensure_vllm_model()  # type: ignore[attr-defined]

        # TTS: Ensure Docker container is running BEFORE Ollama loads models (reserves VRAM)
        async for _ in self._phase_tts_container_checks():
            yield

        try:
            # ============================================================
            # MAIN TRY BLOCK: Covers ALL stages from LLM client creation
            # through inference. Ensures is_generating is ALWAYS reset in finally,
            # even if intent detection hangs, compression fails, or the generator
            # is cancelled by WebSocket disconnect (GeneratorExit).
            # ============================================================
            ai_text = ""  # Must be initialized here — used in finally block

            # ============================================================
            # VISION FAST PATH: Images present → VL model handles everything
            # Skip Intent Detection, Automatik and AIfred entirely.
            # VL model receives: AIfred system prompt + user text + image(s).
            # ============================================================
            if has_pending_images:
                import copy
                local_images = copy.deepcopy(self.pending_images)  # type: ignore[attr-defined]
                self.clear_pending_images()  # type: ignore[attr-defined]

                # Use UI language (no Intent Detection)
                from ..lib.prompt_loader import get_language
                detected_language = get_language()
                self._last_detected_language = detected_language  # type: ignore[attr-defined]

                # Cold start warning for llama.cpp
                if self.backend_type == "llamacpp":  # type: ignore[attr-defined]
                    try:
                        running = await self._llamaswap_running_models()
                        _eff_vl = self._effective_model_id("vision")  # type: ignore[attr-defined]
                        if _eff_vl not in running:
                            self.add_debug(f"🔄 VL Model Cold Start ({_eff_vl}) — loading...")  # type: ignore[attr-defined]
                            yield
                    except Exception:
                        pass

                img_count = len(local_images)
                self.add_debug(f"📷 VL Direct ({img_count} image(s)) → {self.vision_model}")  # type: ignore[attr-defined]
                yield

                # Build multimodal content (images + text) for call_llm()
                from ..lib.vision_utils import load_image_as_base64
                from pathlib import Path

                content_parts: list[dict] = []

                # Qwen3-VL respects /no_think prefix (Ollama ignores API think param for VL)
                if not self.vision_thinking:  # type: ignore[attr-defined]
                    content_parts.append({"type": "text", "text": "/no_think"})

                # Images first (VL models handle it best this way)
                for img in local_images:
                    img_path = Path(img["path"])
                    base64_data = load_image_as_base64(img_path)
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_data}"},
                    })

                # User text after images (fallback description if empty)
                # Task-adaptive prompt: user text → Q&A, no text → OCR
                has_user_text = bool(user_msg.strip())
                prompt_text = user_msg.strip() if has_user_text else (
                    "Beschreibe und analysiere dieses Bild." if detected_language == "de"
                    else "Describe and analyze this image."
                )
                content_parts.append({"type": "text", "text": prompt_text})
                vision_prompt_key = "task_qa" if has_user_text else "task"

                async for _ in self._process_vision_request(
                    user_msg, content_parts, "GEMISCHT", detected_language, vision_prompt_key,
                ):
                    yield
                return  # Vision fast path complete

            # Create LLM client once - used for ALL LLM operations
            from ..lib.llm_client import LLMClient
            llm_client = LLMClient(
                backend_type=self.backend_type,  # type: ignore[attr-defined]
                base_url=self.backend_url,  # type: ignore[attr-defined]
            )

            # ============================================================
            # VL SHORTCUT: If VL model is still loaded AND images in history,
            # do relevance check with VL model directly → avoid double swap.
            # Only for llamacpp (model swapping), only when user has text input.
            # ============================================================
            if (not has_pending_images
                    and user_msg.strip()
                    and self.backend_type == "llamacpp"  # type: ignore[attr-defined]
                    and self.vision_model_id):  # type: ignore[attr-defined]
                from ..lib.vision_utils import (
                    collect_image_context_from_history,
                    build_image_context_string,
                    build_recent_context_string,
                    resolve_image_path_by_index,
                    load_image_as_base64,
                )
                _vl_image_list = collect_image_context_from_history(self._chat_sub().chat_history)

                if _vl_image_list:
                    # Check if VL model is currently loaded
                    _vl_model_loaded = False
                    try:
                        _running = await self._llamaswap_running_models()
                        _eff_vl = self._effective_model_id("vision")  # type: ignore[attr-defined]
                        _vl_model_loaded = _eff_vl in _running
                    except Exception:
                        pass

                    if _vl_model_loaded:
                        log_message("📷 VL Shortcut: VL model still loaded, checking relevance directly")
                        _vl_ctx_str = build_image_context_string(_vl_image_list)
                        _vl_recent_ctx = build_recent_context_string(self._chat_sub().chat_history)

                        from ..lib.intent_detector import detect_vl_relevance
                        _vl_idx = await detect_vl_relevance(
                            user_query=user_msg,
                            image_context=_vl_ctx_str,
                            automatik_model=_eff_vl,
                            llm_client=llm_client,
                            recent_context=_vl_recent_ctx,
                        )

                        if _vl_idx is not None:
                            _vl_path = resolve_image_path_by_index(_vl_image_list, _vl_idx)
                            if _vl_path:
                                self.add_debug(f"📷 VL Follow-up (shortcut): Image {_vl_idx} → {_vl_path.name}")
                                yield

                                # Use UI language (no Intent Detection needed)
                                from ..lib.prompt_loader import get_language
                                detected_language = get_language()
                                self._last_detected_language = detected_language  # type: ignore[attr-defined]

                                # Build multimodal content
                                content_parts_sc: list[dict] = []
                                if not self.vision_thinking:  # type: ignore[attr-defined]
                                    content_parts_sc.append({"type": "text", "text": "/no_think"})

                                base64_data = load_image_as_base64(_vl_path)
                                content_parts_sc.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{base64_data}"},
                                })
                                content_parts_sc.append({"type": "text", "text": user_msg})

                                async for _ in self._process_vision_request(
                                    user_msg, content_parts_sc, "FAKTISCH", detected_language,
                                ):
                                    yield
                                return  # VL shortcut complete — no Automatik swap needed

                        # VL model loaded but question not image-related → normal flow
                        log_message("📷 VL Shortcut: NONE — proceeding to normal flow")

            # ============================================================
            # AUTOMATIK NUM_CTX CALCULATION (once, used for all Automatik calls)
            # ============================================================
            # When Automatik = AIfred (same model): don't set num_ctx → no model reload
            # When different models: use AUTOMATIK_LLM_NUM_CTX from config.py
            from ..lib.config import AUTOMATIK_LLM_NUM_CTX
            from ..lib.formatting import format_number
            effective_auto = self._effective_automatik_id  # type: ignore[attr-defined]
            if effective_auto == self._effective_model_id("aifred"):  # type: ignore[attr-defined]
                # Same model: MUST send same num_ctx as preload to prevent Ollama reload!
                # Ollama uses MODEL DEFAULT (not currently loaded context) when num_ctx is omitted.
                # Omitting num_ctx causes Ollama to reload with default → then main inference
                # sends calibrated num_ctx → Ollama reloads AGAIN. Two unnecessary reloads!
                auto_num_ctx: int | None = self.aifred_max_context if self.aifred_max_context else None  # type: ignore[attr-defined]
                log_message(f"🔧 Automatik = AIfred ({effective_auto}) → num_ctx={auto_num_ctx} (match preload)")

                # Warning if AIfred context is below recommended Automatik threshold
                effective_ctx = self.aifred_max_context or 0  # type: ignore[attr-defined]
                if effective_ctx > 0 and effective_ctx < AUTOMATIK_LLM_NUM_CTX:
                    self.add_debug(
                        f"⚠️ Automatik Context ({format_number(effective_ctx)}) < recommended ({format_number(AUTOMATIK_LLM_NUM_CTX)}) - Automatik tasks may be less reliable"
                    )
                    log_message(f"⚠️ Automatik Context warning: {effective_ctx} < {AUTOMATIK_LLM_NUM_CTX}")
            else:
                # Different model: use config constant
                auto_num_ctx = AUTOMATIK_LLM_NUM_CTX
                log_message(f"🔧 Automatik ≠ AIfred → Context: {auto_num_ctx}")

            # ============================================================
            # VL AUTOMATIK OVERRIDE: VL model loaded → use for Automatik
            # Avoids unnecessary model switch: VL→Automatik→AIfred
            # Instead: VL handles Automatik → then only 1 switch to AIfred
            # (or 0 switches if VL = AIfred)
            # Only for llamacpp (model swapping) and Automatik research mode.
            # ============================================================
            _eff_vl_id = self._effective_model_id("vision")  # type: ignore[attr-defined]
            if (self.backend_type == "llamacpp"  # type: ignore[attr-defined]
                    and _eff_vl_id
                    and self.research_mode == "automatik"  # type: ignore[attr-defined]
                    and effective_auto != _eff_vl_id):
                try:
                    _running = await self._llamaswap_running_models()
                    if _eff_vl_id in _running:
                        effective_auto = _eff_vl_id
                        auto_num_ctx = None  # Let llama-swap use model's configured context
                        self.add_debug(f"📷 VL Automatik: {effective_auto} already loaded → using for decision")
                        log_message(f"📷 VL Automatik Override: {effective_auto} (saves model switch)")
                        yield
                except Exception:
                    pass

            # ============================================================
            # COLD START DETECTION (llama.cpp only)
            # llama-swap loads models on-demand — first request triggers cold start.
            # Check /running BEFORE the first LLM call so the user knows why it's slow.
            # ============================================================
            if self.backend_type == "llamacpp":  # type: ignore[attr-defined]
                try:
                    running_models = await self._llamaswap_running_models()
                    if effective_auto not in running_models:
                        # Extract model details from llama-swap config
                        details = ""
                        try:
                            from ..lib.calibration import parse_llamaswap_config
                            from ..lib.config import LLAMASWAP_CONFIG_PATH
                            model_info = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH).get(effective_auto, {})
                            parts: list[str] = []
                            if model_info.get("current_context"):
                                parts.append(f"Context: {format_number(model_info['current_context'])}")
                            if model_info.get("kv_cache_quant"):
                                parts.append(f"KV-Cache: {model_info['kv_cache_quant']}")
                            if parts:
                                details = f" ({', '.join(parts)})"
                        except Exception:
                            pass
                        self.add_debug(f"🔄 Model Cold Start ({effective_auto}){details} — loading into VRAM, this may take a while")
                        log_message(f"🔄 Cold Start: {effective_auto}{details}")
                        yield
                except Exception:
                    pass  # Can't check — proceed normally, don't show false warnings

            # ============================================================
            # INTENT + ADDRESSEE + LANGUAGE DETECTION (first LLM call)
            # ============================================================
            # Must run BEFORE compression check to get detected_language
            from ..lib.intent_detector import detect_query_intent_and_addressee

            # If user_msg is empty (image-only) or URL-only, skip Intent Detection and use UI language
            _msg_stripped = user_msg.strip()
            _is_url_only = bool(_msg_stripped) and bool(re.match(r'^https?://\S+$', _msg_stripped))
            if not _msg_stripped or _is_url_only:
                from ..lib.prompt_loader import get_language
                detected_intent = "FAKTISCH"
                addressed_to = None
                detected_language = get_language()
                intent_raw = ""
                mode_switch_updates: dict = {}
                remaining_query = ""
                _reason = "URL-only" if _is_url_only else "image-only"
                self.add_debug(f"🎯 Intent: {detected_intent} ({_reason}), Lang: {detected_language.upper()} (UI)")
                self._last_detected_language = detected_language  # type: ignore[attr-defined]
            else:
                (
                    detected_intent,
                    addressed_to,
                    detected_language,
                    mode_switch_updates,
                    remaining_query,
                    intent_raw,
                ) = await detect_query_intent_and_addressee(
                    user_msg,
                    effective_auto,
                    llm_client,
                    automatik_num_ctx=auto_num_ctx,
                )
                # Log Intent Detection result to UI debug console (always visible)
                from ..lib.intent_detector import format_intent_result
                from ..lib.intent_detector import format_mode_switch_summary
                switch_str = format_mode_switch_summary(mode_switch_updates) if mode_switch_updates else "–"
                remaining_str = remaining_query[:50] if remaining_query else "–"
                self.add_debug(
                    f"🎯 {format_intent_result(detected_intent, addressed_to, detected_language)}"
                    f", Switch: {switch_str}, Remaining: {remaining_str}"
                )
                self._last_detected_language = detected_language  # type: ignore[attr-defined]

            # ============================================================
            # MODE SWITCH HANDLER (Option C: pure + combined)
            # ============================================================
            # If the user requested a mode/config change (voice or text),
            # apply it here BEFORE the rest of the pipeline. The detection
            # happens inside Intent Detection (same LLM call, no extra latency).
            #
            # Cases:
            # - Pure switch ("Start tribunal"): apply + confirmation message
            # - Combined ("Start tribunal and discuss X"): apply + continue
            #   with remaining query in the new mode
            if mode_switch_updates:
                from ..lib.intent_detector import format_mode_switch_summary
                from ..lib.session_storage import update_session_config

                # Apply to state (so the rest of the pipeline uses the new mode)
                if "active_agent" in mode_switch_updates:
                    self.active_agent = mode_switch_updates["active_agent"]  # type: ignore[attr-defined]
                if "multi_agent_mode" in mode_switch_updates:
                    self.multi_agent_mode = mode_switch_updates["multi_agent_mode"]  # type: ignore[attr-defined]
                if "research_mode" in mode_switch_updates:
                    from ..lib import TranslationManager
                    self.research_mode = mode_switch_updates["research_mode"]  # type: ignore[attr-defined]
                    self.research_mode_display = TranslationManager.get_research_mode_display(  # type: ignore[attr-defined]
                        self.research_mode, self.ui_language  # type: ignore[attr-defined]
                    )

                # Persist to session file (SSOT) — no _last_session_mtime update
                # yet because _save_current_session below will do it
                if self.session_id:
                    update_session_config(
                        self.session_id,
                        **mode_switch_updates,
                    )

                summary = format_mode_switch_summary(mode_switch_updates, lang=detected_language)
                self.add_debug(f"🔧 Mode switch: {summary}")

                if not remaining_query:
                    # Pure command → confirmation message, skip normal agent pipeline
                    from datetime import datetime as _dt
                    # Always show who the active agent is after the switch
                    confirm_parts = [summary]
                    if "active_agent" not in mode_switch_updates:
                        from ..lib.agent_config import get_agent_config
                        _active = self.active_agent  # type: ignore[attr-defined]
                        _cfg = get_agent_config(_active)
                        _name = _cfg.display_name if _cfg else _active.capitalize()
                        confirm_parts.append(f"Agent: {_name}")
                    confirm_text = f"✅ **{' · '.join(confirm_parts)}**"
                    self._chat_sub().chat_history.append({
                        "role": "assistant",
                        "content": confirm_text,
                        "agent": "system",
                        "mode": "mode_switch",
                        "round_num": 0,
                        "metadata": {},
                        "timestamp": _dt.now().isoformat(),
                        "time_display": _dt.now().strftime("%d.%m. \u2014 %H:%M"),
                        "used_sources": [],
                        "failed_sources": [],
                        "has_audio": False,
                        "audio_urls_json": "[]",
                    })
                    self._chat_sub().llm_history.append({
                        "role": "assistant",
                        "content": confirm_text,
                    })
                    self._save_current_session()
                    yield
                    return

                # Combined → replace user_msg with remaining and continue normally.
                # Important: also update llm_history so the LLM sees the cleaned question.
                user_msg = remaining_query
                if self._chat_sub().llm_history and self._chat_sub().llm_history[-1]["role"] == "user":
                    self._chat_sub().llm_history[-1]["content"] = remaining_query
                self.add_debug(f"➡️ Continuing with: {remaining_query[:60]}")
                yield

            # PRE-MESSAGE: History Compression Check
            async for _ in self._phase_pre_message_compression(llm_client, detected_language):
                yield

            # ============================================================
            # VL FOLLOW-UP PATH: No new images, but images in history?
            # Check if the follow-up question relates to a previous image.
            # ============================================================
            if not has_pending_images:
                from ..lib.vision_utils import (
                    collect_image_context_from_history,
                    build_image_context_string,
                    build_recent_context_string,
                    resolve_image_path_by_index,
                    load_image_as_base64,
                )

                image_list = collect_image_context_from_history(self._chat_sub().chat_history)

                if image_list:
                    image_context_str = build_image_context_string(image_list)
                    recent_ctx = build_recent_context_string(self._chat_sub().chat_history)

                    from ..lib.intent_detector import detect_vl_relevance
                    vl_image_idx = await detect_vl_relevance(
                        user_query=user_msg,
                        image_context=image_context_str,
                        automatik_model=effective_auto,
                        llm_client=llm_client,
                        automatik_num_ctx=auto_num_ctx,
                        recent_context=recent_ctx,
                    )

                    if vl_image_idx is not None:
                        image_path = resolve_image_path_by_index(image_list, vl_image_idx)

                        if image_path:
                            self.add_debug(f"📷 VL Follow-up: Image {vl_image_idx} → {image_path.name}")
                            yield

                            # Cold start check for VL model
                            if self.backend_type == "llamacpp":  # type: ignore[attr-defined]
                                try:
                                    running = await self._llamaswap_running_models()
                                    _eff_vl = self._effective_model_id("vision")  # type: ignore[attr-defined]
                                    if _eff_vl not in running:
                                        self.add_debug(f"🔄 VL Model Cold Start ({_eff_vl}) — loading...")  # type: ignore[attr-defined]
                                        yield
                                except Exception:
                                    pass

                            # Build multimodal content
                            content_parts = []

                            if not self.vision_thinking:  # type: ignore[attr-defined]
                                content_parts.append({"type": "text", "text": "/no_think"})

                            base64_data = load_image_as_base64(image_path)
                            content_parts.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{base64_data}"},
                            })
                            content_parts.append({"type": "text", "text": user_msg})

                            async for _ in self._process_vision_request(
                                user_msg, content_parts, detected_intent, detected_language,
                            ):
                                yield
                            return  # VL follow-up complete

            # ============================================================
            # DIALOG ROUTING (uses intent/addressee from above)
            # ============================================================

            # Track if Sokrates should be skipped (AIfred direct addressing)
            skip_sokrates_analysis = False

            # Determine which agent responds:
            # Priority: addressee from prompt > button selection > default (aifred)
            #
            # Sticky-routing: an explicit inline address (LLM-detected, before
            # the active_agent fallback) persists as the new active_agent so
            # subsequent unaddressed turns route to the same agent. The user
            # switches simply by addressing someone else.
            if addressed_to and addressed_to != self.active_agent:  # type: ignore[attr-defined]
                self.active_agent = addressed_to  # type: ignore[attr-defined]
                self._persist_session_config()  # type: ignore[attr-defined]

            if not addressed_to and self.active_agent != "aifred":  # type: ignore[attr-defined]
                addressed_to = self.active_agent  # type: ignore[attr-defined]

            responding_agent = addressed_to or "aifred"

            if responding_agent == "aifred":
                if addressed_to == "aifred":
                    # User explicitly addressed AIfred → skip Sokrates
                    from ..lib.agent_config import get_agent_label
                    self.add_debug(f"{get_agent_label('aifred')} Direct addressing")
                    skip_sokrates_analysis = True
            else:
                from ..lib.agent_config import get_agent_config
                agent_config = get_agent_config(responding_agent)
                agent_label = agent_config.display_name if agent_config else responding_agent.capitalize()
                agent_emoji = agent_config.emoji if agent_config else "🤖"
                self.add_debug(f"{agent_emoji} Direct addressing: {agent_label}")
            yield

            # ============================================================
            # KEYWORD/URL OVERRIDE: Force web research for explicit requests
            # When user says "recherchiere" etc., always force web search
            # regardless of model size or research_mode setting.
            # ============================================================
            effective_research_mode: str = self.research_mode  # type: ignore[attr-defined]

            if effective_research_mode == "automatik":
                from ..lib.research.query_processor import detect_urls_in_text
                detected_urls = detect_urls_in_text(user_msg, max_urls=7)

                explicit_keywords = [
                    # German
                    'recherchiere', 'recherchier',
                    'suche im internet', 'such im internet',
                    'schau nach', 'schau mal nach',
                    'google', 'googel', 'google mal',
                    'finde heraus', 'find heraus',
                    'check das', 'prüfe das',
                    # English
                    'search for', 'search the web',
                    'look up', 'look it up',
                    'find out', 'research',
                ]
                user_lower = user_msg.lower()

                # Forced research disabled — model uses web_fetch/web_search tools autonomously
                # if detected_urls or any(kw in user_lower for kw in explicit_keywords):
                #     if detected_urls:
                #         self.add_debug(f"⚡ {len(detected_urls)} URL(s) detected → Forced Research")
                #     else:
                #         self.add_debug("⚡ Explicit research request → Forced Research")
                #     effective_research_mode = "deep"
                #     yield
                if detected_urls:
                    self.add_debug(f"🔗 {len(detected_urls)} URL(s) detected (model decides via tools)")
                if any(kw in user_lower for kw in explicit_keywords):
                    self.add_debug("🔍 Research keywords detected (model decides via tools)")

            # ============================================================
            # UNIFIED AGENT RESPONSE (Single Source of Truth)
            # All agents (AIfred, Sokrates, custom) use the same path.
            # research_mode determines tool availability:
            #   "none"      → no research tools
            #   "automatik" → agent gets web_search/read_webpage tools
            #   "quick"/"deep" → forced research before response
            # ============================================================

            from ..lib.multi_agent import run_generic_agent_direct_response

            # Symposion mode: all selected agents respond in sequence
            if self.multi_agent_mode == "symposion" and len(self.symposion_agents) >= 2:  # type: ignore[attr-defined]
                from ..lib.multi_agent import run_symposion
                async for _ in run_symposion(self, user_msg, detected_language):  # type: ignore[arg-type]
                    yield
            else:
                async for _ in run_generic_agent_direct_response(
                    self,
                    responding_agent,
                    user_msg,
                    detected_language,
                    research_mode=effective_research_mode,
                    detected_intent=detected_intent,
                ):
                    yield

                # Multi-Agent analysis (Sokrates critique etc.)
                # Get the AI response from the last chat history entry
                _last = self._chat_sub().chat_history[-1] if self._chat_sub().chat_history else {}
                ai_text = _last.get("content", "") if _last.get("role") == "assistant" else ""

                async for _ in self._dispatch_multi_agent(
                    user_msg, ai_text, detected_language, skip_sokrates_analysis,
                ):
                    yield

            self._streaming_sub().current_ai_response = ""  # type: ignore[attr-defined]
            self.current_user_message = ""
            self.is_generating = False
            self._save_current_session()  # type: ignore[attr-defined]
            yield

        except Exception as e:
            error_msg = f"Error: {e!s}"
            self._js_chunk_buffer = ""  # type: ignore[attr-defined]
            self._streaming_sub().current_ai_response = error_msg  # type: ignore[attr-defined]

            # APPEND error as separate panel
            # Note: User panel was already created above with user_msg/display_user_msg
            self.add_agent_panel(
                agent="aifred",
                content=error_msg,
                mode="error",
                round_num=None,
                metadata=None,  # No metrics for errors
                sync_llm_history=True,  # Sync error to llm_history
            )

            self.add_debug(f"❌ Generation failed: {e}")
            from ..backends.base import BackendConnectionError
            if not isinstance(e, BackendConnectionError):
                import traceback
                self.add_debug(f"Traceback: {traceback.format_exc()}")

        finally:
            self.is_generating = False
            yield  # Let React update is_generating=False (button re-enables via Reflex binding)
            # NOTE: TTS polling stops automatically via data-polling attribute (MutationObserver)
            # Clear pending images after sending
            if len(self.pending_images) > 0:  # type: ignore[attr-defined]
                self.clear_pending_images()  # type: ignore[attr-defined]

            # TTS: handled by _queue_tts_for_agent (line ~390) — no duplicate here

            # Generate session title at end of flow (uses small Automatik model)
            # Only runs on first Q&A pair, skipped if title already exists
            # Skip if no AI response was generated (e.g. RPC connection error)
            if ai_text:
                # Use effective model ID to avoid llama-swap model swap
                effective_id = self._effective_model_id("aifred")  # type: ignore[attr-defined]
                async for _ in self._generate_session_title(title_model_override=effective_id):  # type: ignore[attr-defined]
                    yield  # Forward UI updates from title generation

            # Auto-Save: Session nach jeder Chat-Nachricht speichern
            # IMPORTANT: Save BEFORE refresh so message_count is up-to-date
            self._save_current_session()  # type: ignore[attr-defined]

            # Refresh session list to update sorting (last_seen changed) and message count
            self.refresh_session_list()  # type: ignore[attr-defined]
            yield

            # Final cleanup: Clear streaming state
            self._set_current_agent("")
            self._streaming_sub().current_ai_response = ""  # type: ignore[attr-defined]

    # ── Clear Chat ───────────────────────────────────────────────────

    def clear_chat(self) -> None:
        """UI Event Handler: Clear chat history (shows debug message)."""
        if not self.logged_in_user:  # type: ignore[attr-defined]
            self.add_debug("⚠️ Bitte zuerst anmelden")
            return
        self._clear_chat_internal(silent=False)  # type: ignore[attr-defined]

    # ── Save Session Memory ──────────────────────────────────────────

    async def save_session_memory(self) -> None:
        """Generate a session summary and store it for all participating agents."""
        import re
        import reflex as rx
        from ..lib.agent_memory import get_agent_memory

        if not self.logged_in_user:  # type: ignore[attr-defined]
            return

        history = self._chat_sub().chat_history  # type: ignore[attr-defined]
        if len(history) < 2:
            yield rx.toast.info("Not enough messages to summarize", duration=3000, position="top-center")
            return

        memory = get_agent_memory()
        if not memory:
            yield rx.toast.error("Agent memory unavailable", duration=3000, position="top-center")
            return

        # Collect all agents that participated in this conversation
        participating_agents: set[str] = set()
        for msg in history:
            if msg.get("role") == "assistant":
                agent = msg.get("agent", "")
                if agent and agent != "vision":
                    participating_agents.add(agent)
        # Always include aifred as default
        if not participating_agents:
            participating_agents.add("aifred")

        # Build conversation text for summarization
        conv_lines = []
        for msg in history:
            role = msg.get("role", "user")
            agent = msg.get("agent_display_name", msg.get("agent", ""))
            content = msg.get("content", "")
            clean = re.sub(r'<[^>]+>', '', content).strip()
            if role == "user":
                conv_lines.append(f"User: {clean}")
            else:
                speaker = agent or "Assistant"
                conv_lines.append(f"{speaker}: {clean}")

        conversation_text = "\n".join(conv_lines)

        # Limit to ~4000 chars to keep LLM call fast
        if len(conversation_text) > 4000:
            conversation_text = conversation_text[-4000:]

        # Generate summary via LLM
        from ..lib.llm_client import LLMClient
        llm_client = LLMClient(
            backend_type=self.backend_type,  # type: ignore[attr-defined]
            base_url=self.backend_url,  # type: ignore[attr-defined]
        )
        model = self._effective_model_id("aifred")  # type: ignore[attr-defined]

        summary_prompt = (
            "Summarize this conversation in 4-5 sentences. "
            "Focus on the key topics, decisions, insights, and user preferences. "
            "Write in the same language as the conversation.\n\n"
            f"{conversation_text}"
        )

        from ..lib.agent_config import get_agent_config
        agent_names = []
        for aid in participating_agents:
            cfg = get_agent_config(aid)
            agent_names.append(cfg.display_name if cfg else aid.capitalize())

        self.add_debug(f"📌 Generating session summary ({len(history)} messages) for: {', '.join(agent_names)}")  # type: ignore[attr-defined]
        yield

        try:
            summary = ""
            async for chunk in llm_client.chat_stream(
                model=model,
                messages=[{"role": "user", "content": summary_prompt}],
                options={
                    "temperature": 0.3,
                    "max_tokens": 300,
                    "top_k": 40, "top_p": 0.95, "min_p": 0.05,
                    "repeat_penalty": 1.0,
                    "enable_thinking": False,
                },
            ):
                if chunk.get("type") == "content":
                    summary += chunk.get("text", "")

            summary = summary.strip()
            if not summary:
                yield rx.toast.error("Failed to generate summary", duration=3000, position="top-center")
                return

            # Store/update per agent — check duplicates individually
            sid = self.session_id  # type: ignore[attr-defined]
            stored_count = 0
            updated_count = 0
            for aid in participating_agents:
                if sid and memory.find_by_session(aid, sid):
                    memory.update_by_session(aid, sid, summary)
                    updated_count += 1
                else:
                    await memory.store(
                        agent_id=aid,
                        content=summary,
                        memory_type="session_summary",
                        summary=summary[:120],
                        session_id=sid,
                    )
                    stored_count += 1

            parts = []
            if stored_count:
                parts.append(f"{stored_count} pinned")
            if updated_count:
                parts.append(f"{updated_count} updated")
            status = ", ".join(parts)
            self.add_debug(f"📌 Session memory: {status} — {summary[:100]}...")  # type: ignore[attr-defined]
            yield rx.toast.success(f"Session memory: {status}", duration=3000, position="top-center")

        except Exception as e:
            self.add_debug(f"❌ Session pin failed: {e}")  # type: ignore[attr-defined]
            yield rx.toast.error(f"Error: {e}", duration=3000, position="top-center")
