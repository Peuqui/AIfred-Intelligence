"""Chat mixin for AIfred state.

Handles message sending, AI response streaming, agent panel display,
and chat clearing.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

import reflex as rx
from reflex.event import EventSpec

from ..lib.config import DEBUG_MESSAGES_MAX, LLAMASWAP_BACKENDS
from ..lib.logging_utils import log_exception, log_message

if TYPE_CHECKING:
    from ..lib.agent_tuning import AgentTuning
    from ..lib.vision_routing import Describer


class ChatMixin(rx.State, mixin=True):
    """Mixin for chat message sending and AI response streaming."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        _js_chunk_buffer: str
        _last_detected_language: str
        _pending_audio_urls: list[str]
        active_agent: rx.Field[str]
        agent_memory_enabled: rx.Field[bool]
        agent_tuning: rx.Field[dict[str, AgentTuning]]
        all_sources: rx.Field[list[dict[str, Any]]]
        automatik_model_id: rx.Field[str]
        backend_type: rx.Field[str]
        backend_url: rx.Field[str]
        cloud_api_provider: rx.Field[str]
        enable_tts: rx.Field[bool]
        failed_sources: rx.Field[list[dict[str, str]]]
        logged_in_user: rx.Field[str]
        multi_agent_mode: rx.Field[str]
        pending_images: rx.Field[list[dict[str, str]]]
        session_id: rx.Field[str]
        symposion_agents: rx.Field[list[str]]
        ui_language: rx.Field[str]
        used_sources: rx.Field[list[dict[str, Any]]]

        def _abort_streaming_tts(self) -> None: ...
        def _chat_sub(self): ...
        def _clear_chat_internal(self, silent: bool = False): ...
        @property
        def _effective_automatik_id(self) -> str: ...
        def _effective_model_id(self, agent: str) -> str: ...
        async def _ensure_backend_initialized(self) -> None: ...
        async def _generate_session_title(self, title_model_override: str = "") -> None: ...
        def _image_describer(self) -> Describer: ...
        def _init_streaming_tts(self, agent: str = "aifred") -> None: ...
        def _persist_session_config(self) -> None: ...
        def _save_current_session(self): ...
        def _spawn_tts_finalize(self) -> None: ...
        def _streaming_sub(self): ...
        def _tts_streaming_wanted(self, agent: str = "aifred") -> bool: ...
        async def _wait_for_tts_finalize(self) -> None: ...
        @rx.event
        def clear_pending_images(self) -> None: ...
        @rx.event
        def flush_stream_to_ui(self) -> bool: ...
        @rx.event
        def refresh_session_list(self): ...
        @rx.event
        def stream_text_to_ui(self, chunk: str) -> bool: ...
    # Ladezeit des letzten Cold Starts (Sekunden), gemessen am ersten
    # LLM-Aufruf nach der Erkennung; _add_agent_panel haengt sie an die
    # naechste Antwort und setzt sie zurueck — Warmstarts tragen nichts.
    _pending_load_time: float = 0.0
    # Tool-Schema-Token des letzten Turns (multi_agent setzt sie) — fuer die
    # Kompressionspruefung VOR dem Toolkit-Aufbau des naechsten Turns.
    _last_toolkit_tokens: int = 0


    # ── State Variables ──────────────────────────────────────────────
    current_user_input: rx.Field[str] = rx.field("")
    current_user_message: rx.Field[str] = rx.field("")  # The message currently being processed
    # current_ai_response lives on StreamingState (separate React context)
    current_agent: rx.Field[str] = rx.field("")  # Current streaming agent ID
    current_agent_display_name: rx.Field[str] = rx.field("")  # Display name for streaming UI
    current_agent_emoji: rx.Field[str] = rx.field("")  # Emoji for streaming UI
    is_generating: rx.Field[bool] = rx.field(False)
    # True when the current is_generating was triggered by an external
    # channel (Message Hub: FreeEcho.2, email, Telegram, …) rather than the
    # local browser.  The tick handler keeps running its mtime-watch
    # while a hub-side pipeline is in progress so the user sees the
    # chat bubble the moment STT / intent / etc. writes to the session
    # file — instead of only after the whole pipeline finishes.
    is_generating_hub: rx.Field[bool] = rx.field(False)
    is_compressing: rx.Field[bool] = rx.field(False)  # Shows if history compression is running

    # Debug Console
    debug_messages: rx.Field[list[str]] = rx.field(default_factory=list)
    auto_refresh_enabled: rx.Field[bool] = rx.field(True)  # For Debug Console + Chat History + AI Response Area

    # Processing Progress (Automatik, Scraping, LLM)
    progress_active: rx.Field[bool] = rx.field(False)
    progress_phase: rx.Field[str] = rx.field("")  # "automatik", "scraping", "llm"
    progress_current: rx.Field[int] = rx.field(0)
    progress_total: rx.Field[int] = rx.field(0)
    progress_failed: rx.Field[int] = rx.field(0)  # Number of failed URLs

    # Tool Status (shown in UI while agent uses tools)
    tool_status: rx.Field[str] = rx.field("")  # e.g. "🌐 bibleserver.com/HFA/Psalm139"

    # Outputs of the last execute_research run (web_search tool): the page
    # content for the tool result, the sources collapsible for the bubble,
    # and the llm_history line (research_note).
    _research_context: str = ""
    _research_sources_html: str = ""
    _research_note: str = ""

    # ── Debug / Progress ─────────────────────────────────────────────

    @rx.event
    def add_debug(self, message: str) -> None:
        """Add message to debug console.

        Appends to the Reflex State list (rx.foreach render + session
        persistence) and forwards to the Debug Bus (logfile + optional
        session file persistence).

        Lines appended from a background create_task reach the browser via
        the 500ms refresh_debug_console timer, which re-flags debug_messages
        dirty so Reflex pushes the delta.
        """
        import datetime as _dt

        from ..lib.debug_bus import debug as _debug

        timestamp = _dt.datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"{timestamp} | {message}"

        # Reflex State list. Reassign instead of .append() — Reflex only
        # fires the reactive update on identity change, not in-place mutation.
        self.debug_messages = [*self.debug_messages, formatted_msg][-DEBUG_MESSAGES_MAX:]

        # Debug Bus (logfile + optional session persistence)
        _debug(message)

    def _set_progress(self, phase: str, current: int = 0, total: int = 0, failed: int = 0) -> None:
        """Update processing progress."""
        self.progress_active = True
        self.progress_phase = phase
        self.progress_current = current
        self.progress_total = total
        self.progress_failed = failed

    @rx.event
    def clear_progress(self) -> None:
        """Clear processing progress."""
        self.progress_active = False
        self.progress_phase = ""

    @rx.event
    def set_tool_status(self, status: str) -> None:
        """Show tool activity in UI (e.g. '🌐 fetching bibleserver.com...')."""
        self.tool_status = status

    @rx.event
    def clear_tool_status(self) -> None:
        """Clear tool status."""
        self.tool_status = ""
        self.progress_current = 0
        self.progress_total = 0
        self.progress_failed = 0

    # ── llama-swap Helpers ────────────────────────────────────────────

    def _llamaswap_base_url(self) -> str:
        """Get llama-swap base URL (without /v1 suffix)."""
        return str(self.backend_url).rstrip("/").removesuffix("/v1")

    async def _llamaswap_running_models(self) -> list[str]:
        """Loaded model IDs according to llama-swap. Raises LlamaSwapError when it does not answer."""
        from ..lib.service_status import running_models
        return [m.name for m in await asyncio.to_thread(running_models, self._llamaswap_base_url())]

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
            "auto_consensus": t("auto_consensus_label", lang=self.ui_language).rstrip(":"),
            "tribunal": "Tribunal",  # Same in both languages
            "direct": "",  # No label — direct is the default mode now
            "refinement": t("refinement_label", lang=self.ui_language).rstrip(":"),
            "synthesis": t("salomo_synthesis_label", lang=self.ui_language).rstrip(":"),
            "verdict": t("salomo_verdict_label", lang=self.ui_language).rstrip(":"),
            "critical_review": t("critical_review_label", lang=self.ui_language).rstrip(":"),
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
        if self.multi_agent_mode != "standard" and mode not in ["auto_consensus", "tribunal", "symposion"] and mode != self.multi_agent_mode:
            # Get localized multi-agent mode label
            multi_mode_label = self._get_mode_label(self.multi_agent_mode, None)
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

    def _sync_to_llm_history(
        self, agent: str, content: str, history_notes: Sequence[str] = (),
    ) -> None:
        """Sync agent response to llm_history with speaker label.

        Strips only thinking blocks (<think>, Harmony analysis).
        Code tags (<python>, <code>, etc.) are preserved because they
        provide important context for the LLM.

        IMPORTANT: Callers should pass RAW content (before format_thinking_process),
        not formatted content with <details> collapsibles. If the caller already
        formats before calling _add_agent_panel(), use sync_llm_history=False and
        sync manually with raw content.

        Args:
            agent: Agent identifier ("aifred", "sokrates", "salomo")
            content: Agent response content (should be RAW, not formatted)
            history_notes: What the turn's tools left for the next turn
                (PipelineResult.history_notes), appended after the answer
        """
        from ..lib.formatting import assistant_history_entry
        entry = assistant_history_entry(agent, content, history_notes)
        if entry:
            ch = self._chat_sub()
            ch.llm_history = [*ch.llm_history, entry]

    # ── Central Agent Panel ──────────────────────────────────────────

    def _add_agent_panel(
        self,
        agent: str,  # "aifred", "sokrates", "salomo"
        content: str,
        mode: str = "standard",
        round_num: int | None = None,
        metadata: dict | None = None,
        sync_llm_history: bool = True,
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

        With the new dict-based chat_history, each message is standalone.
        No more replace_last logic - just append new messages.

        Args:
            agent: Agent identifier ("aifred", "sokrates", "salomo")
            content: Agent response content (WITHOUT marker, WITHOUT metadata)
            mode: Mode identifier (e.g., "auto_consensus", "tribunal", "direct", "standard")
            round_num: Round number for multi-round debates (None/0 = no round, 1+ = round number)
            metadata: Optional dict with TTFT, inference_time, tokens_per_sec, source
            sync_llm_history: If True, syncs to llm_history (set False if caller already did)
        """
        from ..lib.formatting import build_agent_panel_entry
        from ..lib.prompt_loader import get_language

        marker = self._build_marker(agent, mode, round_num if round_num and round_num > 0 else None)
        # The intent call's model load of this turn, if there was one: the first answer after it
        # carries it, added to a load the answer's own call measured.
        msg_metadata = metadata.copy() if metadata else {}
        if self._pending_load_time and msg_metadata:
            msg_metadata["load_time"] = msg_metadata.get("load_time", 0.0) + self._pending_load_time
            self._pending_load_time = 0.0
        if self._pending_audio_urls:
            msg_metadata["audio_urls"] = self._pending_audio_urls.copy()
            log_message(f"🔊 _add_agent_panel: Stored {len(self._pending_audio_urls)} audio URLs in message metadata")
            self._pending_audio_urls: list[str] = []
        new_message = build_agent_panel_entry(
            agent, content, mode=mode, round_num=round_num, metadata=msg_metadata, marker=marker,
            lang=self._last_detected_language or get_language(),
        )

        # 5. Append to chat_history (no more replace_last!).
        # Reassign instead of .append() so Reflex picks up the change.
        ch = self._chat_sub()
        ch.chat_history = [*ch.chat_history, new_message]

        # 6. Sync to llm_history (with speaker label)
        # Note: Some callers (streaming functions) already sync to llm_history,
        # so they should pass sync_llm_history=False to avoid duplicates
        if sync_llm_history:
            self._sync_to_llm_history(agent, content)

        # 7. Save session (async, non-blocking)
        self._save_current_session()

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

        # Skip if standard/symposion mode, no AI text, or explicitly skipped.
        # Symposion has NO follow-up critique step by design — before this
        # guard, a symposion with fewer than 2 selected agents fell through
        # to the single-agent path and then landed in the else-branch below,
        # spawning an unrequested Sokrates analysis (which also swapped the
        # model, because Sokrates resolves to the base variant).
        if self.multi_agent_mode in ("standard", "symposion") or not ai_text or skip_analysis:
            return

        if self.multi_agent_mode == "tribunal":
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
        detected_language: str,
        vision_task_addon: str = "",
    ) -> AsyncGenerator[None, None]:
        """Run VL inference via call_llm and process results.

        Used by the VL Direct path (fresh image upload). Follow-up questions
        about an already-uploaded image no longer re-present the image here —
        the model re-examines it on demand via the vision_analyze tool, guided
        by the /_upload/ URL anchored in the user turn of llm_history.
        Handles streaming, history update, cleanup, title generation and session save.

        The VL model acts as the currently active agent (with that agent's
        memory, tools, and personality). For multi-agent modes with multiple
        selected agents, defaults to "aifred".

        Only runs when the main model can see (see ``_image_describer``);
        otherwise the vision LLM describes the image and the normal text
        pipeline answers (``turn_images.describe_images``).
        """
        from ..lib.llm_engine import call_llm

        effective_vision_id = self._effective_model_id("aifred")

        # VL acts as the active agent (memory, tools, personality)
        acting_agent = self.active_agent or "aifred"
        self._set_current_agent(acting_agent)
        from ..lib.agent_config import get_agent_config
        _acting_cfg = get_agent_config(acting_agent)
        _acting_name = _acting_cfg.display_name if _acting_cfg else acting_agent.capitalize()
        self.add_debug(f"📷 VL acting as: {_acting_name}")
        yield

        # Full toolkit like the text path — without this, call_llm falls back
        # to its memory-only toolkit (store_memory) and the model CANNOT act
        # on image content ("save contact to Google" ended as a hallucinated
        # completion note in agent memory + a store_memory retry loop).
        from ..lib.agent_memory import prepare_agent_toolkit
        memory_ctx, vl_toolkit = await prepare_agent_toolkit(
            acting_agent, user_msg,
            lang=detected_language,
            memory_enabled=self.agent_memory_enabled,
            research_tools_enabled=True,
            state=self,
            session_id=self.session_id,
        )
        if vl_toolkit:
            self.add_debug(f"🔧 Toolkit: {[t.name for t in vl_toolkit.tools]} for {_acting_name}")
            yield

        if self._tts_streaming_wanted("aifred"):
            self._init_streaming_tts(agent="aifred")

        result_data = None
        async for item in call_llm(
            user_text=user_msg,
            model_choice=effective_vision_id,
            history=self._chat_sub().chat_history,
            llm_history=self._chat_sub().llm_history[:-1],
            detected_language=detected_language,
            temperature=self.agent_tuning["aifred"].temperature,
            backend_type=self.backend_type,
            backend_url=self.backend_url,
            state=self,
            multimodal_content=content_parts,
            vision_task_addon=vision_task_addon,
            provider=self.cloud_api_provider if self.backend_type == "cloud_api" else None,
            agent=acting_agent,
            external_toolkit=vl_toolkit,
            memory_ctx=memory_ctx if memory_ctx else None,
        ):
            if item["type"] == "debug":
                self.add_debug(item["message"])
                yield
            elif item["type"] == "content":
                if self.stream_text_to_ui(item["text"]):
                    yield
            elif item["type"] == "progress":
                if item.get("clear", False):
                    self.clear_progress()
                else:
                    self._set_progress(
                        phase=item.get("phase", ""),
                        current=item.get("current", 0),
                        total=item.get("total", 0),
                    )
                yield
            elif item["type"] == "result":
                # Flush remaining buffer to state
                if self.flush_stream_to_ui():
                    yield
                result_data = item["data"]

        # Separator after VL inference (consistent with all other inference paths)
        from ..lib.logging_utils import CONSOLE_SEPARATOR, console_separator
        console_separator()
        self.add_debug(CONSOLE_SEPARATOR)
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

        self._streaming_sub().current_ai_response = ""
        self.current_user_message = ""
        self.is_generating = False
        yield

        # Title generation runs fire-and-forget (it can take >100s with a
        # reasoning model) — see _generate_session_title. Save + refresh run
        # right away; the finished title reaches the browser over the Browser
        # Push Bus (kind="session_title").
        from ._base import track_orphan_task
        track_orphan_task(asyncio.create_task(
            self._generate_session_title(title_model_override=effective_vision_id)
        ))
        self._save_current_session()
        self.refresh_session_list()
        yield

    # ── Main Send Message ────────────────────────────────────────────

    async def _phase_pre_message_compression(
        self, llm_client: Any, detected_language: str,
    ) -> AsyncGenerator[None, None]:
        """Check if history compression is needed before adding new message."""
        if not self._chat_sub().chat_history:
            return

        from ..lib.context_manager import (
            largest_compression_model,
            summarize_history_if_needed,
        )
        from ..lib.prompt_loader import get_max_direct_prompt_tokens
        from ..lib.research.context_utils import get_agent_num_ctx

        # Determine effective context limit (minimum of all agents).
        # get_agent_num_ctx() runs resolve_variant_suffix itself — pass the
        # BASE model_id, not the already-resolved _effective_model_id, or
        # we get a double-suffix lookup that misses the YAML entry.
        context_limits: list[int] = []
        aifred_ctx, _ = get_agent_num_ctx("aifred", self, self.agent_tuning["aifred"].model_id)
        context_limits.append(aifred_ctx)

        if self.multi_agent_mode != "standard":
            if self.agent_tuning["sokrates"].model_id:
                sokrates_ctx, _ = get_agent_num_ctx("sokrates", self, self.agent_tuning["sokrates"].model_id)
                context_limits.append(sokrates_ctx)
            if self.agent_tuning["salomo"].model_id:
                salomo_ctx, _ = get_agent_num_ctx("salomo", self, self.agent_tuning["salomo"].model_id)
                context_limits.append(salomo_ctx)

        context_limit = min(context_limits) if context_limits else 4096
        # Mit DEMSELBEN Schalter messen, mit dem _run_agent_direct_response
        # den Prompt gleich baut.
        system_prompt_tokens = get_max_direct_prompt_tokens(
            self.multi_agent_mode,
            detected_language,
            memory=self.agent_memory_enabled,
        )

        compression_model = largest_compression_model(
            self._effective_model_id(agent) for agent in ("aifred", "sokrates", "salomo")
        )

        async for event in summarize_history_if_needed(
            history=self._chat_sub().chat_history,
            llm_client=llm_client,
            model_name=compression_model,
            context_limit=context_limit,
            llm_history=self._chat_sub().llm_history,
            system_prompt_tokens=system_prompt_tokens,
            detected_language=detected_language,
            toolkit_tokens=self._last_toolkit_tokens,
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


    @rx.event
    async def send_message(self, text: str = "") -> AsyncGenerator[None, None]:
        """Send message to LLM with optional web research.

        Args:
            text: User text from UI (via call_script callback).
                  Empty when called programmatically — reads from current_user_input.
        """
        # Must be logged in to send messages
        if not self.logged_in_user:
            self.add_debug("⚠️ Please log in first")
            return

        # If no text but images present, use default prompt
        has_pending_images = len(self.pending_images) > 0
        # Text from call_script callback (UI click) or current_user_input (injection/transcription)
        user_text = (text or self.current_user_input).strip()

        if not user_text and not has_pending_images:
            return  # Nothing to send

        if self.is_generating:
            self.add_debug("⚠️ Already generating, please wait...")
            return

        # Hartes Kalibrier-Gate: Die GPUs gehören während einer Kalibrierung
        # exklusiv der Messung. Früher blockierte der Foreground-State-Lock
        # Requests zufällig mit; seit die Kalibrierung ein Background-Event
        # ist, würde ein Send hier llama-swap mitten im Verify neu starten
        # und die Messwerte verderben (beobachtet 2026-07-05 17:17).
        from ..lib.calibration_gate import is_calibration_active
        if is_calibration_active():
            self.add_debug(
                "🛑 Inference blocked: calibration in progress — "
                "wait for it to finish or press the stop button"
            )
            return

        await self._ensure_backend_initialized()

        # No main model selected — e.g. the configured one was deleted and
        # got cleared on backend switch (we clear rather than silently
        # substitute). Stop here with a clear hint instead of crashing
        # downstream on an empty model id.
        if not self.agent_tuning["aifred"].model_id:
            self.add_debug("⚠️ No model selected — pick one in the settings panel")
            return

        # Coverage check: warn (debug console + log) when the active
        # TTS/VLM toggles point at a llama-swap profile that doesn't
        # exist in the YAML. The runtime resolver will silently fall
        # back to the next-best profile, which can OOM on first VLM
        # inference — surfacing the mismatch here gives the user a
        # chance to open the calibration matrix.
        if self.backend_type == "llamacpp":
            try:
                from ..lib.calibration import diagnose_uncalibrated_combo
                from ..lib.config import LLAMASWAP_CONFIG_PATH
                from ..lib.tts_llm_coordination import planned_tts_engine
                from ..lib.tts_service import gpu_engine_keys
                from ..lib.vision_prewarm import (
                    get_active_vlm_key,
                    is_vision_active,
                )
                _vlm_active_chat = is_vision_active()
                _vlm_key_chat = get_active_vlm_key() if _vlm_active_chat else ""
                _warn = diagnose_uncalibrated_combo(
                    LLAMASWAP_CONFIG_PATH,
                    self.agent_tuning["aifred"].model_id,
                    tts_active=bool(self.enable_tts),
                    tts_engine=planned_tts_engine(self.agent_tuning["aifred"].model_id) if self.enable_tts else "",
                    gpu_tts_engines=gpu_engine_keys() if self.enable_tts else set(),
                    vlm_active=_vlm_active_chat,
                    vlm_key=_vlm_key_chat,
                )
                if _warn:
                    self.add_debug(_warn)
                    log_message(_warn)
            except Exception as _e:  # noqa: BLE001 — a diagnostic never blocks sending; logged
                # Diagnostic only — never block a chat submit on a check failure
                log_exception("diagnose_uncalibrated_combo failed", _e)

        # ============================================================
        # PHASE 1: Spinner + textarea clear — yield IMMEDIATELY
        # ============================================================
        # Minimal state for instant UI feedback (spinner + correct agent indicator)
        # Textarea is already cleared client-side by the call_script in on_click
        self.is_generating = True
        self._set_current_agent("")
        self._streaming_sub().current_ai_response = ""

        # Cleanup bookkeeping referenced by the finally block — must exist
        # BEFORE the try so every abort point (including the early yields
        # below) resets is_generating and releases pipeline/TTS again.
        from ..lib.llm_client import LLMClient
        from ..lib.pipeline_registry import register_pipeline, unregister_pipeline
        ai_text = ""  # Used in finally block
        _pipeline_task: asyncio.Task | None = None
        llm_client: LLMClient | None = None
        _client_gone = False  # set on GeneratorExit — finally must not yield then
        _cancelled = False  # set on CancelledError (stop, supersession) — the spoken reply ends with it
        try:
            yield  # Spinner visible immediately

            # ============================================================
            # PHASE 2: Build and add user message to chat
            # ============================================================
            user_msg = user_text
            self.current_user_input = ""  # Clear state (for injection/transcription path)
            self.current_user_message = user_msg
            self.used_sources: list[dict[str, Any]] = []
            self.failed_sources: list[dict[str, str]] = []
            self.all_sources: list[dict[str, Any]] = []

            from pathlib import Path

            from ..lib import turn_images
            from ..lib.prompt_loader import get_language
            images = [turn_images.TurnImage(img["name"], img["url"], Path(img["path"])) for img in self.pending_images]
            display_user_msg = turn_images.bubble_text(user_msg, images, get_language())

            ch = self._chat_sub()
            ch.chat_history = [
                *ch.chat_history,
                {
                    "role": "user",
                    "content": display_user_msg,
                    "agent": "",
                    "mode": "",
                    "round_num": 0,
                    "metadata": {"images": turn_images.bubble_images(images)},
                    "timestamp": datetime.now().isoformat(),
                    "time_display": datetime.now().strftime("%d.%m. \u2014 %H:%M"),
                    "used_sources": [],
                    "failed_sources": [],
                    "has_audio": False,
                    "audio_urls_json": "[]",
                    "browser_speech_json": "[]",
                    "tts_note": "",
                },
            ]
            # Anchor the image location in the user turn of llm_history: the image
            # itself only goes into the one-off multimodal call, never into the
            # history. Without the URL the model "forgets" on a follow-up that an
            # image ever existed and falsely accuses itself of hallucinating. With
            # the /_upload/ URL it can re-examine the image via vision_analyze (on
            # the big model when that is vision-capable).
            llm_user_content = turn_images.history_text(user_msg, images)
            # Stempel EINMAL je Turn: derselbe Text geht live ans Modell und
            # in llm_history — das Modell kennt Datum und Uhrzeit damit schon
            # im ersten Turn, und der History-Eintrag ist byte-gleich zum
            # gesendeten Text (Praefix-Cache). Intent-/URL-Erkennung und
            # Gedaechtnis-Recall arbeiten weiter auf dem rohen user_msg.
            from ..lib.message_builder import stamp_user_turn, user_turn_stamp
            turn_stamp = user_turn_stamp()
            llm_user_content = stamp_user_turn(llm_user_content, turn_stamp)
            ch.llm_history = [*ch.llm_history,
                              {"role": "user", "content": llm_user_content}]
            self.add_debug("📨 User request received")

            # ============================================================
            # PHASE 3: Audio-Bus init (TTS streaming + SSE stream)
            # ============================================================
            # NOTE: Audio-Unlock laeuft NICHT hier — rx.call_script kommt
            # ueber WebSocket asynchron im Browser an, ist also keine
            # User-Geste mehr. Der Unlock passiert ueber einen
            # ``document.addEventListener('click', ...)``-Hook in custom.js,
            # der beim ALLERERSTEN User-Click im Tab feuert (im echten
            # Click-Stack) und dann sich selbst entfernt.
            # The reading of the reply is opened where its text starts to flow (one place per path:
            # ``_stream_agent_to_history`` for every agent, ``_process_vision_request`` for VL Direct).
            if self._tts_streaming_wanted("aifred"):
                await self._wait_for_tts_finalize()
                from ..lib.api import browser_queue_clear
                browser_queue_clear(self.session_id)
            # SSE stream (re)start — idempotent if already connected.
            yield rx.call_script(  # type: ignore[misc]
                f"if(window.startBrowserStream) startBrowserStream('{self.session_id}');"
            )

            # vLLM-Eintraege werden von llama-swap on demand geladen —
            # kein eigener Lade-Schritt mehr (ehem. PHASE 4, Direkt-Pfad).
            # Das TTS-Profil (-tts-<engine>) steckt im Modellnamen, den der Load verlangt.

            # Register this coroutine in the pipeline registry so external stop
            # commands (UI stop button, future cross-channel stop) can cancel it.
            _pipeline_task = asyncio.current_task()
            if _pipeline_task is not None and self.session_id:
                register_pipeline(self.session_id, _pipeline_task)

            # ============================================================
            # IMAGES: who looks at them (SSOT _image_describer)
            # - Main model sees, single agent → VL Direct: it answers with
            #   the image itself (skips Intent Detection and Automatik).
            # - Otherwise the describer describes the image ONCE, the
            #   description joins the user turn, and the normal text
            #   pipeline answers below — main model as the agent with its
            #   full prompt, tools and history; Symposion likewise.
            # ============================================================
            if has_pending_images:
                self.clear_pending_images()

                # Use UI language (no Intent Detection)
                detected_language = get_language()
                self._last_detected_language = detected_language

                from ..lib.vision_routing import NoVisionModelError, eviction_notice
                try:
                    _describer = self._image_describer()
                    _eff_vl, _vl_evicts = _describer.model, _describer.evicts_chat_model
                except NoVisionModelError as exc:
                    _eff_vl, _vl_evicts = "", False
                    self.add_debug(f"⚠️ Vision: {exc} — the image goes on without a description")
                    yield
                if _vl_evicts:
                    self.add_debug(eviction_notice(_eff_vl))
                    yield

                # Cold start warning (llama-swap: llamacpp + vllm; a describer of another
                # machine loads there, this llama-swap knows nothing about it)
                from ..lib.remote_describers import endpoint as _remote_endpoint
                if _eff_vl and self.backend_type in LLAMASWAP_BACKENDS and _remote_endpoint(_eff_vl) is None:
                    from ..lib.service_status import LlamaSwapError
                    try:
                        running = await self._llamaswap_running_models()
                    except LlamaSwapError as exc:
                        # Only the cold-start hint is lost; the description itself reports its own error.
                        log_message(f"⚠️ VL cold-start check: llama-swap does not answer: {exc}", "warning")
                        running = None
                    if running is not None and _eff_vl not in running:
                        self.add_debug(f"🔄 VL Model Cold Start ({_eff_vl}) — loading...")
                        yield

                img_count = len(images)
                _symposion = self.multi_agent_mode == "symposion" and len(self.symposion_agents) >= 2
                if _eff_vl == self._effective_model_id("aifred") and not _symposion:
                    self.add_debug(f"📷 VL Direct ({img_count} image(s)) → {_eff_vl}")
                    yield

                    # Same agent and model as the request below (_process_vision_request).
                    from ..lib.agent_settings import THINKING_OFF, thinking_mode
                    content_parts = turn_images.seeing_content(
                        images, turn_images.seeing_question(images, user_msg, turn_stamp, detected_language),
                        no_think=thinking_mode(self, self.active_agent or "aifred", _eff_vl) == THINKING_OFF,
                    )
                    vision_task_addon = turn_images.seeing_task(user_msg, detected_language)

                    async for _ in self._process_vision_request(
                        user_msg, content_parts, detected_language, vision_task_addon,
                    ):
                        yield
                    return  # VL Direct complete

                if _eff_vl:
                    self.add_debug(f"📷 Image description ({img_count} image(s)) → {_eff_vl}")
                    yield
                    from ..lib.vision_routing import Describer, describe_with_second_attempt

                    def _choose_again() -> Describer:
                        describer = self._image_describer()
                        if describer.evicts_chat_model:
                            self.add_debug(eviction_notice(describer.model))
                        return describer

                    description = await describe_with_second_attempt(
                        Describer(_eff_vl, _vl_evicts), _choose_again,
                        lambda d: turn_images.describe_images(images, user_msg.strip(), detected_language, d.model, self),
                    )
                    llm_user_content = f"{llm_user_content}\n\n{turn_images.description_block(description, detected_language)}"
                    ch = self._chat_sub()
                    ch.llm_history = [*ch.llm_history[:-1], {"role": "user", "content": llm_user_content}]
                    yield

            # Create LLM client once - used for ALL LLM operations
            llm_client = LLMClient(
                backend_type=self.backend_type,
                base_url=self.backend_url,
            )

            # ============================================================
            # AUTOMATIK NUM_CTX CALCULATION (once, used for all Automatik calls)
            # ============================================================
            # When Automatik = AIfred (same model): don't set num_ctx → no model reload
            # When different models: use AUTOMATIK_LLM_NUM_CTX from config.py
            from ..lib.config import AUTOMATIK_LLM_NUM_CTX
            from ..lib.formatting import format_number
            effective_auto = self._effective_automatik_id
            if effective_auto == self._effective_model_id("aifred"):
                # Same model: MUST send same num_ctx as preload to prevent Ollama reload!
                # Ollama uses MODEL DEFAULT (not currently loaded context) when num_ctx is omitted.
                # Omitting num_ctx causes Ollama to reload with default → then main inference
                # sends calibrated num_ctx → Ollama reloads AGAIN. Two unnecessary reloads!
                auto_num_ctx: int | None = self.agent_tuning["aifred"].max_context if self.agent_tuning["aifred"].max_context else None
                log_message(f"🔧 Automatik = AIfred ({effective_auto}) → num_ctx={auto_num_ctx} (match preload)")

                # Warning if AIfred context is below recommended Automatik threshold
                effective_ctx = self.agent_tuning["aifred"].max_context or 0
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
            # COLD START DETECTION (llama.cpp only)
            # llama-swap loads models on-demand — first request triggers cold start.
            # Check /running BEFORE the first LLM call so the user knows why it's slow.
            # ============================================================
            if self.backend_type in LLAMASWAP_BACKENDS:
                from ..lib.service_status import LlamaSwapError
                try:
                    running_models = await self._llamaswap_running_models()
                    if effective_auto not in running_models:
                        # Extract model details from llama-swap config
                        details = ""
                        from ..lib.calibration import parse_llamaswap_config
                        from ..lib.config import LLAMASWAP_CONFIG_PATH
                        try:
                            model_info = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH).get(effective_auto, {})
                            parts: list[str] = []
                            if model_info.get("current_context"):
                                parts.append(f"Context: {format_number(model_info['current_context'])}")
                            if model_info.get("kv_cache_quant"):
                                parts.append(f"KV-Cache: {model_info['kv_cache_quant']}")
                            if parts:
                                details = f" ({', '.join(parts)})"
                        except (OSError, ValueError) as exc:
                            log_message(f"⚠️ Cold start: llama-swap config unreadable, no model details: {exc}", "warning")
                        self.add_debug(f"🔄 Model Cold Start ({effective_auto}){details} — loading into VRAM, this may take a while")
                        log_message(f"🔄 Cold Start: {effective_auto}{details}")
                        # Whisper's GPU worker and sidecars are cleared by the
                        # backend right before the load (_load_model).
                        yield
                except LlamaSwapError as exc:
                    # Can't check — proceed normally, don't show false warnings
                    log_message(f"⚠️ Cold-start check: llama-swap does not answer: {exc}", "warning")

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
                addressed_to = None
                detected_language = get_language()
                mode_switch_updates: dict = {}
                is_pure_command = False
                _reason = "URL-only" if _is_url_only else "image-only"
                self.add_debug(f"🎯 Intent skipped ({_reason}), Lang: {detected_language.upper()} (UI)")
                self._last_detected_language = detected_language
            else:
                # Beim Cold Start laedt das Backend das Modell fuer diesen
                # ersten LLM-Aufruf und misst die Ladezeit; sie landet als
                # "Load" an der Antwort (_add_agent_panel). Bild-/URL-only-Turns
                # ohne Intent-Aufruf laden im Hauptaufruf, dort misst die
                # Pipeline dieselbe Zeit.
                (
                    addressed_to,
                    detected_language,
                    mode_switch_updates,
                    is_pure_command,
                    _intent_raw,
                    self._pending_load_time,
                ) = await detect_query_intent_and_addressee(
                    user_msg,
                    effective_auto,
                    llm_client,
                    automatik_num_ctx=auto_num_ctx,
                )
                # Log Intent Detection result to UI debug console (always visible)
                from ..lib.intent_detector import (
                    format_intent_result,
                    format_mode_switch_summary,
                )
                switch_str = format_mode_switch_summary(mode_switch_updates) if mode_switch_updates else "–"
                pure_str = "yes" if is_pure_command else "no"
                self.add_debug(
                    f"🎯 {format_intent_result(addressed_to, detected_language)}"
                    f", Switch: {switch_str}, PureCmd: {pure_str}"
                )
                self._last_detected_language = detected_language

            # ============================================================
            # MODE SWITCH HANDLER
            # ============================================================
            # If the user requested a mode/config change (voice or text),
            # apply it here BEFORE the rest of the pipeline. The detection
            # happens inside Intent Detection (same LLM call, no extra latency).
            #
            # Cases:
            # - Pure command (is_pure_command): apply + confirmation message
            # - Combined: apply + continue mit der UNVERAENDERTEN user_msg.
            #   Der Intent-Detektor darf den Frage-Text NIE umschreiben.
            if mode_switch_updates:
                from ..lib.intent_detector import format_mode_switch_summary
                from ..lib.session_storage import update_session_config

                # Apply to state (so the rest of the pipeline uses the new mode).
                # ``_parse_mode_switch`` only ever returns agent / multi keys.
                if "active_agent" in mode_switch_updates:
                    self.active_agent = mode_switch_updates["active_agent"]
                if "multi_agent_mode" in mode_switch_updates:
                    self.multi_agent_mode = mode_switch_updates["multi_agent_mode"]
                if "symposion_agents" in mode_switch_updates:
                    self.symposion_agents = list(mode_switch_updates["symposion_agents"])

                # Persist to session file (SSOT) — no _last_session_mtime update
                # yet because _save_current_session below will do it
                if self.session_id:
                    update_session_config(
                        self.session_id,
                        **mode_switch_updates,
                    )

                summary = format_mode_switch_summary(mode_switch_updates, lang=detected_language)
                self.add_debug(f"🔧 Mode switch: {summary}")

                if is_pure_command:
                    # Pure command → confirmation message, skip normal agent pipeline
                    from datetime import datetime as _dt
                    # Always show who the active agent is after the switch
                    confirm_parts = [summary]
                    if "active_agent" not in mode_switch_updates:
                        from ..lib.agent_config import get_agent_config
                        _active = self.active_agent
                        _cfg = get_agent_config(_active)
                        _name = _cfg.display_name if _cfg else _active.capitalize()
                        confirm_parts.append(f"Agent: {_name}")
                    confirm_text = f"✅ **{' · '.join(confirm_parts)}**"
                    ch = self._chat_sub()
                    ch.chat_history = [
                        *ch.chat_history,
                        {
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
                            "browser_speech_json": "[]",
                            "tts_note": "",
                        },
                    ]
                    ch.llm_history = [
                        *ch.llm_history,
                        {"role": "assistant", "content": confirm_text},
                    ]
                    self._save_current_session()
                    yield
                    return

                # Combined: Mode wurde umgeschaltet, Pipeline laeuft weiter mit
                # der UNVERAENDERTEN user_msg. Der Intent-Detektor darf nur
                # Metadaten extrahieren, NICHT den Frage-Text umschreiben.
                yield

            # PRE-MESSAGE: History Compression Check
            async for _ in self._phase_pre_message_compression(llm_client, detected_language):
                yield

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
            if addressed_to and addressed_to != self.active_agent:
                self.active_agent = addressed_to
                self._persist_session_config()

            # Sticky routing is suspended in symposion mode: there the agent
            # SELECTION decides who speaks (run_symposion for >=2, the
            # single selected agent below for 1) — otherwise a stale
            # active_agent from an earlier turn logs a misleading
            # "Direct addressing: X" before the symposion override kicks in.
            if (
                not addressed_to
                and self.active_agent != "aifred"
                and self.multi_agent_mode != "symposion"
            ):
                addressed_to = self.active_agent

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
            # UNIFIED AGENT RESPONSE (Single Source of Truth)
            # All agents (AIfred, Sokrates, custom) use the same path, always
            # with the full toolkit — the agent decides itself whether to
            # research (web_search/web_fetch) or use any other tool.
            # ============================================================

            from ..lib.multi_agent import run_generic_agent_direct_response

            # Symposion mode: all selected agents respond in sequence
            if self.multi_agent_mode == "symposion" and len(self.symposion_agents) >= 2:
                from ..lib.multi_agent import run_symposion
                async for _ in run_symposion(self, user_msg, detected_language):  # type: ignore[arg-type]
                    yield
            else:
                # Symposion with fewer than 2 agents degrades to a plain
                # direct response — deliberately supported, so the user can
                # toggle agents on/off mid-session and continue with one.
                # With exactly one selected agent THAT agent must answer
                # (not whoever happens to be active); with zero we fall
                # back to the normal addressing logic, visibly.
                if self.multi_agent_mode == "symposion":
                    if len(self.symposion_agents) == 1:
                        responding_agent = self.symposion_agents[0]
                        self.add_debug(
                            f"🏛️ Symposion with a single agent → direct response by {responding_agent}"
                        )
                    else:
                        self.add_debug(
                            "⚠️ Symposion: no agents selected → falling back to direct response"
                        )
                    yield
                async for _ in run_generic_agent_direct_response(
                    self,  # type: ignore[arg-type]  # Mixin ist zur Laufzeit der AIState
                    responding_agent,
                    user_msg,
                    detected_language,
                    llm_user_text=llm_user_content,
                ):
                    yield

                # Multi-Agent analysis (Sokrates critique etc.) — only after
                # the standard single-agent response. Symposion has no
                # follow-up critique step.
                _last = self._chat_sub().chat_history[-1] if self._chat_sub().chat_history else {}
                ai_text_for_dispatch = _last.get("content", "") if _last.get("role") == "assistant" else ""

                async for _ in self._dispatch_multi_agent(
                    user_msg, ai_text_for_dispatch, detected_language, skip_sokrates_analysis,
                ):
                    yield

            # Single source of truth for ai_text (used by the finally block
            # to gate session-title generation): pull the most recent
            # assistant message from chat_history regardless of which mode
            # produced it. Without this the symposion branch left ai_text
            # empty and skipped title generation.
            _last = self._chat_sub().chat_history[-1] if self._chat_sub().chat_history else {}
            if _last.get("role") == "assistant":
                ai_text = _last.get("content", "")

            self._streaming_sub().current_ai_response = ""
            self.current_user_message = ""
            self.is_generating = False
            self._save_current_session()
            yield

        except asyncio.CancelledError:
            _cancelled = True
            raise
        except GeneratorExit:
            # Closed from outside: shutdown or worker respawn (Reflex 0.9 does
            # not end an event when the tab disconnects). A generator
            # that yields again while unwinding GeneratorExit raises
            # "async generator ignored GeneratorExit" and aborts the
            # finally block — so flag it and let finally skip its yields
            # (session save etc. still run).
            _client_gone = True
            raise
        except Exception as e:  # noqa: BLE001 — the chat turn's boundary: any failure becomes the visible answer
            error_msg = f"Error: {e!s}"
            self._js_chunk_buffer = ""
            self._streaming_sub().current_ai_response = error_msg

            # APPEND error as separate panel
            # Note: User panel was already created above with user_msg/display_user_msg
            self._add_agent_panel(
                agent="aifred",
                content=error_msg,
                mode="error",
                round_num=None,
                metadata=None,  # No metrics for errors
                sync_llm_history=True,  # Sync error to llm_history
            )

            self.add_debug(f"❌ Generation failed: {e}")
            # Expected, self-explaining failures carry their reason in the
            # message; a traceback would only bury it.
            from ..backends.base import BackendConnectionError, BackendModelStartError
            if not isinstance(e, (BackendConnectionError, BackendModelStartError)):
                import traceback
                self.add_debug(f"Traceback: {traceback.format_exc()}")

        finally:
            # Close the per-message LLM client — every backend holds a
            # persistent HTTP connection pool; without this the pool leaks
            # once per sent message (multi_agent closes its own clients).
            if llm_client is not None:
                await llm_client.close()

            # Unregister from pipeline registry — only if this is still the
            # registered task (a newer pipeline may have superseded us).
            if _pipeline_task is not None and self.session_id:
                unregister_pipeline(self.session_id, _pipeline_task)

            # Streaming-TTS finalize for every path that skipped the
            # multi-agent finish (vision fast path, pure-command return,
            # exception before/inside the agent stream) — otherwise
            # tts_streaming_in_flight stays True and blocks media resume.
            # No-op when the multi-agent path already spawned it.
            # A cancelled reply (stop, supersession, client gone) is not
            # read to the end: its reading is aborted, so the service stops.
            if _cancelled or _client_gone:
                self._abort_streaming_tts()
            else:
                self._spawn_tts_finalize()

            # Partial-response rescue: when the handler is aborted
            # mid-stream (CancelledError from the stop button or pipeline
            # supersession, GeneratorExit on shutdown/worker respawn), the
            # streamed text so far lives ONLY in current_ai_response —
            # _add_agent_panel never ran and a minutes-long answer would be
            # lost. Persist it as a visibly interrupted bubble. Gate on
            # "last chat entry is not an assistant panel": the success path
            # and the except-Exception path both appended one already (the
            # except path also rewrote current_ai_response to the error
            # text — without this gate we would duplicate it). No yield
            # here: safe during GeneratorExit unwinding.
            _last_entry = (
                self._chat_sub().chat_history[-1]
                if self._chat_sub().chat_history else {}
            )
            _partial = self._streaming_sub().current_ai_response
            if _last_entry.get("role") != "assistant" and _partial.strip():
                from ..lib.i18n import t
                _lang = self.ui_language if self.ui_language != "auto" else "de"
                self._add_agent_panel(
                    agent=self.current_agent or "aifred",
                    content=f"{_partial}\n\n{t('generation_interrupted', lang=_lang)}",
                    mode="standard",
                    round_num=None,
                    metadata=None,
                    sync_llm_history=True,
                )
                self.add_debug(
                    f"⚠️ Partial response rescued ({len(_partial)} chars) "
                    f"after aborted generation"
                )

            self.is_generating = False
            if not _client_gone:
                yield  # Let React update is_generating=False (button re-enables via Reflex binding)
            # NOTE: TTS polling stops automatically via data-polling attribute (MutationObserver)
            # Clear pending images after sending
            if len(self.pending_images) > 0:
                self.clear_pending_images()

            # TTS: handled by _queue_tts_for_agent (line ~390) — no duplicate here

            # Generate session title at end of flow (uses small Automatik model)
            # Only runs on first Q&A pair, skipped if title already exists
            # Skip if no AI response was generated (e.g. RPC connection error)
            if ai_text:
                # Use effective model ID to avoid llama-swap model swap.
                # Title generation runs fire-and-forget — it can take >100s
                # with a reasoning model and must not block the handler (and
                # the 500ms debug-refresh timer). The title reaches the
                # browser via the Browser Push Bus (kind="session_title").
                effective_id = self._effective_model_id("aifred")
                from ._base import track_orphan_task
                track_orphan_task(asyncio.create_task(
                    self._generate_session_title(title_model_override=effective_id)
                ))

            # Auto-Save: Session nach jeder Chat-Nachricht speichern
            # IMPORTANT: Save BEFORE refresh so message_count is up-to-date
            self._save_current_session()

            # Refresh session list to update sorting (last_seen changed) and message count
            self.refresh_session_list()
            if not _client_gone:
                yield

            # Final cleanup: Clear streaming state
            self._set_current_agent("")
            self._streaming_sub().current_ai_response = ""

    # ── Clear Chat ───────────────────────────────────────────────────

    @rx.event
    def clear_chat(self) -> None:
        """UI Event Handler: Clear chat history (shows debug message)."""
        if not self.logged_in_user:
            self.add_debug("⚠️ Bitte zuerst anmelden")
            return
        from ._tts_streaming_mixin import abort_tts_reading
        abort_tts_reading(self.session_id)
        self._clear_chat_internal(silent=False)

    # ── Save Session Memory ──────────────────────────────────────────

    @rx.event
    async def save_session_memory(self) -> AsyncGenerator[EventSpec | None, None]:
        """Generate a session summary and store it for all participating agents."""
        import re

        import reflex as rx

        from ..lib.agent_memory import get_agent_memory

        if not self.logged_in_user:
            return

        history = self._chat_sub().chat_history
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
        model = self._effective_model_id("aifred")

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

        self.add_debug(f"📌 Generating session summary ({len(history)} messages) for: {', '.join(agent_names)}")
        yield None

        llm_client = LLMClient(
            backend_type=self.backend_type,
            base_url=self.backend_url,
        )
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
            sid = self.session_id
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
            self.add_debug(f"📌 Session memory: {status} — {summary[:100]}...")
            yield rx.toast.success(f"Session memory: {status}", duration=3000, position="top-center")

        except Exception as e:  # noqa: BLE001 — the pin button's boundary: any failure is shown as a toast
            self.add_debug(f"❌ Session pin failed: {e}")
            yield rx.toast.error(f"Error: {e}", duration=3000, position="top-center")
        finally:
            await llm_client.close()
