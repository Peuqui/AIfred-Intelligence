"""Chat mixin for AIfred state.

Handles message sending, AI response streaming, agent panel display,
and chat clearing.
"""

from __future__ import annotations

from ..lib.config import LLAMASWAP_BACKENDS

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any, Dict, List

import reflex as rx
from reflex.event import EventSpec

from ..lib import log_message
from ..lib.config import DEBUG_MESSAGES_MAX
from ..lib.context_manager import strip_thinking_blocks


class ChatMixin(rx.State, mixin=True):
    """Mixin for chat message sending and AI response streaming."""
    # Ladezeit des letzten Cold Starts (Sekunden), gemessen am ersten
    # LLM-Aufruf nach der Erkennung; add_agent_panel haengt sie an die
    # naechste Antwort und setzt sie zurueck — Warmstarts tragen nichts.
    _pending_load_time: float = 0.0
    # Tool-Schema-Token des letzten Turns (multi_agent setzt sie) — fuer die
    # Kompressionspruefung VOR dem Toolkit-Aufbau des naechsten Turns.
    _last_toolkit_tokens: int = 0


    # ── State Variables ──────────────────────────────────────────────
    current_user_input: str = ""
    current_user_message: str = ""  # The message currently being processed
    # current_ai_response lives on StreamingState (separate React context)
    current_agent: str = ""  # Current streaming agent ID
    current_agent_display_name: str = ""  # Display name for streaming UI
    current_agent_emoji: str = ""  # Emoji for streaming UI
    is_generating: bool = False
    # True when the current is_generating was triggered by an external
    # channel (Message Hub: FreeEcho.2, email, Telegram, …) rather than the
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
    # Source count of the last web research — consumed once by
    # _sync_to_llm_history to tag that assistant turn with a research marker,
    # so a follow-up turn knows the agent DID search (the tool_call/results
    # themselves are not kept in llm_history).
    _research_source_count: int = 0

    # ── Debug / Progress ─────────────────────────────────────────────

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
        return str(self.backend_url).rstrip("/").removesuffix("/v1")

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
            ch = self._chat_sub()
            # Append a compact research marker (llm_history only — not the UI)
            # so a follow-up turn knows the agent DID search. Consumed-and-reset
            # so it tags exactly the turn that researched and never carries over
            # to a later non-search turn.
            n = getattr(self, "_research_source_count", 0)
            if n > 0:
                lang = getattr(self, "ui_language", "de")
                marker = (
                    f"[Recherche: {n} Web-Quellen abgerufen und ausgewertet.]"
                    if lang == "de"
                    else f"[Research: {n} web sources retrieved and evaluated.]"
                )
                clean_content = f"{clean_content}\n\n{marker}"
                self._research_source_count = 0  # type: ignore[attr-defined]
            ch.llm_history = [
                *ch.llm_history,
                {"role": "assistant", "content": f"[{label}]: {clean_content}"},
            ]

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

        # 2. Format metadata footer — with the cold-start load time of this
        # turn, if there was one (the first answer after the load carries it)
        msg_metadata = metadata.copy() if metadata else {}
        if self._pending_load_time and msg_metadata:
            msg_metadata["load_time"] = self._pending_load_time
            self._pending_load_time = 0.0
        meta_footer = self._format_panel_metadata(msg_metadata)

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
        self._save_current_session()  # type: ignore[attr-defined]

        # 8. Generate TTS and add to queue (if enabled)
        # Implicit path (generate_tts=None): respect tts_autoplay too —
        # without autoplay there's no consumer for the audio, no point
        # spinning up XTTS/Moss for nothing. Explicit generate_tts=True
        # is a caller-side override (e.g. future "replay audio" button).
        # SKIP if streaming TTS is ACTIVE — text was already sent
        # sentence-by-sentence during inference.
        if generate_tts is None:
            should_generate_tts = self.enable_tts and self.tts_autoplay  # type: ignore[attr-defined]
        else:
            should_generate_tts = generate_tts
        streaming_active = self._tts_streaming_wanted(agent)  # type: ignore[attr-defined]
        if should_generate_tts and not streaming_active:
            # Check per-agent TTS enabled setting
            agent_tts_enabled = self.tts_agent_voices.get(agent, {}).get("enabled", True)  # type: ignore[attr-defined]
            if agent_tts_enabled:
                # Schedule TTS generation as background task
                # This runs async without blocking add_agent_panel().
                # Track the task in _orphan_tasks so the asyncio runtime
                # keeps a strong reference until completion — otherwise
                # the loop may GC the task object mid-run.
                try:
                    loop = asyncio.get_running_loop()
                    from ._base import track_orphan_task
                    task = loop.create_task(
                        self._queue_tts_for_agent(content, agent),  # type: ignore[attr-defined]
                        name=f"tts-{agent}",
                    )
                    track_orphan_task(task)
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

        # Skip if standard/symposion mode, no AI text, or explicitly skipped.
        # Symposion has NO follow-up critique step by design — before this
        # guard, a symposion with fewer than 2 selected agents fell through
        # to the single-agent path and then landed in the else-branch below,
        # spawning an unrequested Sokrates analysis (which also swapped the
        # model, because Sokrates resolves to the base variant).
        if self.multi_agent_mode in ("standard", "symposion") or not ai_text or skip_analysis:  # type: ignore[attr-defined]
            return

        # Generate TTS for AIfred's initial response BEFORE Multi-Agent starts
        # This ensures AIfred's voice is heard first, then Sokrates/Salomo follow
        # (Sokrates/Salomo TTS is generated via add_agent_panel() in multi_agent.py)
        # Same rules as add_agent_panel: autoplay required (no consumer
        # otherwise), skip if streaming TTS already spoke the sentences.
        if self.enable_tts and self.tts_autoplay and not self._tts_streaming_wanted("aifred"):  # type: ignore[attr-defined]
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

    async def _symposion_vision_handoff(
        self,
        local_images: list,
        detected_language: str,
    ) -> AsyncGenerator[None, None]:
        """Describe an uploaded image ONCE, then let Symposion discuss it.

        Symposion with >=2 agents normally never reaches the Vision Fast
        Path's single-agent response — before this, an attached image made
        it fall through to _process_vision_request(), which answers as
        exactly one agent (whichever active_agent happened to be) and
        bypasses run_symposion() entirely, regardless of the selected
        lineup. A shared, neutral image description is the right amount of
        vision here: the image is an objective fact all agents discuss from
        the same basis, not a matter of individual perspective — and one
        VL call instead of N avoids both the cost multiplication and the
        risk of agents arguing from subtly different descriptions of the
        same image.
        """
        from ..lib.llm_client import LLMClient
        from ..lib.prompt_loader import load_prompt
        from ..lib.vision_utils import load_image_as_base64
        from pathlib import Path

        # SSOT VL-model choice (vision-capable main model first) —
        # see _vl_choice. Sampling follows the model, not the role.
        effective_vision_id, vl_bucket = self._vl_choice()  # type: ignore[attr-defined]

        desc_content: list[dict] = []
        for img in local_images:
            base64_data = load_image_as_base64(Path(img["path"]))
            desc_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_data}"},
            })
        # SSOT vision perception, same body as the single-agent path — only
        # the follow-up instruction differs (shared/neutral vs. answer-a-
        # question), see vision/task_instruction_symposion.txt.
        task_instruction = load_prompt("vision/task_instruction_symposion", lang=detected_language)
        desc_content.append({
            "type": "text",
            "text": load_prompt("vision/task_adaptive", lang=detected_language, task_instruction=task_instruction),
        })

        llm_client = LLMClient(backend_type=self.backend_type, base_url=self.backend_url)  # type: ignore[attr-defined]
        from ..backends.base import LLMOptions
        response = await llm_client.chat(
            model=effective_vision_id,
            messages=[{"role": "user", "content": desc_content}],
            options=LLMOptions(
                temperature=self.agent_tuning[vl_bucket].temperature,  # type: ignore[attr-defined]
                num_ctx=self.agent_tuning[vl_bucket].max_context or None,  # type: ignore[attr-defined]
                # Deliberately no thinking: a shared, neutral image
                # description is a perception task, not a reasoning one.
                enable_thinking=False,
            ),
        )
        await llm_client.close()
        description = response.text.strip()

        from ..lib.prompt_loader import get_language
        _de = get_language() == "de"
        _label = "Bildinhalt" if _de else "Image content"
        ch = self._chat_sub()
        updated_content = f"{ch.llm_history[-1]['content']}\n\n[{_label}: {description}]"
        ch.llm_history = [*ch.llm_history[:-1], {"role": "user", "content": updated_content}]
        self.add_debug(f"📷 Symposion: shared image description ({effective_vision_id})")
        yield

        from ..lib.multi_agent import run_symposion
        async for _ in run_symposion(self, updated_content, detected_language):  # type: ignore[arg-type]
            yield

        # Same finalization as _process_vision_request()/send_message()'s
        # shared end-of-flow block: the Vision Fast Path always returns
        # early from send_message() (see the "return" after this method's
        # caller), so it never reaches that shared block and must trigger
        # title generation + save itself — missed here originally, which
        # is why Symposion-with-image sessions kept the placeholder title.
        from ._base import track_orphan_task
        track_orphan_task(asyncio.create_task(
            self._generate_session_title(title_model_override=effective_vision_id)  # type: ignore[attr-defined]
        ))
        self._save_current_session()  # type: ignore[attr-defined]
        self.refresh_session_list()  # type: ignore[attr-defined]

    async def _process_vision_request(
        self,
        user_msg: str,
        content_parts: list[dict],
        detected_intent: str,
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

        Model choice is SSOT ``_effective_vl_model_id``: a vision-capable
        main model handles the image itself; the vision role only steps in
        for non-vision-capable main models.
        """
        from ..lib.llm_engine import call_llm

        # SSOT VL-model choice (vision-capable main model first, variant
        # coupling included) — see _vl_choice. Sampling settings follow the
        # MODEL that runs the turn (its agent_tuning row), not the role.
        effective_vision_id, vl_bucket = self._vl_choice()  # type: ignore[attr-defined]
        if effective_vision_id != self.agent_tuning["vision"].model_id:  # type: ignore[attr-defined]
            self.add_debug(f"⚡ VL model: {effective_vision_id}")  # type: ignore[attr-defined]
            yield

        # VL acts as the active agent (memory, tools, personality)
        acting_agent = self.active_agent or "aifred"  # type: ignore[attr-defined]
        self._set_current_agent(acting_agent)
        from ..lib.agent_config import get_agent_config
        _acting_cfg = get_agent_config(acting_agent)
        _acting_name = _acting_cfg.display_name if _acting_cfg else acting_agent.capitalize()
        self.add_debug(f"📷 VL acting as: {_acting_name}")  # type: ignore[attr-defined]
        yield

        # Full toolkit like the text path — without this, call_llm falls back
        # to its memory-only toolkit (store_memory) and the model CANNOT act
        # on image content ("save contact to Google" ended as a hallucinated
        # completion note in agent memory + a store_memory retry loop).
        from ..lib.agent_memory import prepare_agent_toolkit
        memory_ctx, vl_toolkit = await prepare_agent_toolkit(
            acting_agent, user_msg,
            lang=detected_language,
            memory_enabled=self.agent_memory_enabled,  # type: ignore[attr-defined]
            research_tools_enabled=True,
            state=self,
            session_id=self.session_id,  # type: ignore[attr-defined]
        )
        if vl_toolkit:
            self.add_debug(f"🔧 Toolkit: {[t.name for t in vl_toolkit.tools]} for {_acting_name}")  # type: ignore[attr-defined]
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
            temperature=self.agent_tuning[vl_bucket].temperature,  # type: ignore[attr-defined]
            backend_type=self.backend_type,  # type: ignore[attr-defined]
            backend_url=self.backend_url,  # type: ignore[attr-defined]
            enable_thinking=self.agent_tuning[vl_bucket].thinking,  # type: ignore[attr-defined]
            state=self,
            multimodal_content=content_parts,
            vision_task_addon=vision_task_addon,
            provider=self.cloud_api_provider if self.backend_type == "cloud_api" else None,  # type: ignore[attr-defined]
            agent=acting_agent,
            external_toolkit=vl_toolkit,
            memory_ctx=memory_ctx if memory_ctx else None,
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

        # Title generation runs fire-and-forget (it can take >100s with a
        # reasoning model) — see _generate_session_title. Save + refresh run
        # right away; the finished title reaches the browser over the Browser
        # Push Bus (kind="session_title").
        from ._base import track_orphan_task
        track_orphan_task(asyncio.create_task(
            self._generate_session_title(title_model_override=effective_vision_id)  # type: ignore[attr-defined]
        ))
        self._save_current_session()  # type: ignore[attr-defined]
        self.refresh_session_list()  # type: ignore[attr-defined]
        yield

    # ── Main Send Message ────────────────────────────────────────────

    async def _phase_tts_container_checks(self) -> AsyncGenerator[None, None]:
        """Ensure VRAM state matches TTS requirements before LLM loads.

        Uses ensure_tts_state (SSOT) — same function as FreeEcho.2.
        Browser passes what it wants, function handles the rest.
        """
        from ..lib.tts_engine_manager import ensure_tts_state, GPU_ENGINES

        wanted = ""
        if self.enable_tts and self.tts_engine in GPU_ENGINES:  # type: ignore[attr-defined]
            if not (self.tts_engine == "xtts" and self.xtts_force_cpu):  # type: ignore[attr-defined]
                wanted = self.tts_engine  # type: ignore[attr-defined]

        # Gate: a GPU-TTS engine on llama.cpp needs a calibrated
        # <model>-tts-<engine> profile. Without it the LLM would load the
        # base profile (TTS GPU planned full) and the container start
        # would OOM. Force wanted="" so ensure_tts_state stops any
        # running container and the LLM keeps the base profile. Catches
        # the cases the dropdown can't (model switched after selecting,
        # leftover container, stale settings.json).
        if wanted and self.backend_type == "llamacpp":  # type: ignore[attr-defined]
            from ..lib.calibration import has_llamaswap_tts_variant
            from ..lib.config import LLAMASWAP_CONFIG_PATH
            if not has_llamaswap_tts_variant(
                LLAMASWAP_CONFIG_PATH, self.agent_tuning["aifred"].model_id, wanted,  # type: ignore[attr-defined]
            ):
                self.add_debug(  # type: ignore[attr-defined]
                    f"⚠️ No calibrated {wanted} profile for "
                    f"{self.agent_tuning["aifred"].model_id} — voice output disabled "  # type: ignore[attr-defined]
                    f"for this model (calibrate it first)"
                )
                wanted = ""

        gen = ensure_tts_state(
            wanted_tts=wanted,
            backend_type=self.backend_type,  # type: ignore[attr-defined]
            xtts_force_cpu=self.xtts_force_cpu,  # type: ignore[attr-defined]
        )
        # Jeder next()-Schritt des Generators ist ein KOMPLETTER blockierender
        # Brocken (Container-Start + Model-Load: bis zu Minuten). Ein nacktes
        # next(gen) fror hier den ganzen Event-Loop ein — granian hielt den
        # Worker für tot und killte ihn mitten in der Inferenz (Browser:
        # "Connection Timeout", Antwort verloren). to_thread hält den Loop
        # frei; next(gen, sentinel) statt StopIteration-Fangen, weil eine aus
        # to_thread propagierende StopIteration im async-Kontext zum
        # RuntimeError wird. Der FreeEcho2-Pfad konsumiert denselben
        # Generator bereits per Executor-Thread.
        _done = object()
        while True:
            msg = await asyncio.to_thread(next, gen, _done)
            if msg is _done:
                break
            self.add_debug(f"🔊 {msg}")  # type: ignore[attr-defined]
            yield

    async def _phase_pre_message_compression(
        self, llm_client: Any, detected_language: str,
    ) -> AsyncGenerator[None, None]:
        """Check if history compression is needed before adding new message."""
        if not self._chat_sub().chat_history:
            return

        from ..lib.context_manager import summarize_history_if_needed, get_largest_compression_model
        from ..lib.research.context_utils import get_agent_num_ctx
        from ..lib.prompt_loader import get_max_system_prompt_tokens

        # Determine effective context limit (minimum of all agents).
        # get_agent_num_ctx() runs resolve_variant_suffix itself — pass the
        # BASE model_id, not the already-resolved _effective_model_id, or
        # we get a double-suffix lookup that misses the YAML entry.
        context_limits: list[int] = []
        aifred_ctx, _ = get_agent_num_ctx("aifred", self, self.agent_tuning["aifred"].model_id)  # type: ignore[attr-defined, arg-type]
        context_limits.append(aifred_ctx)

        if self.multi_agent_mode != "standard":  # type: ignore[attr-defined]
            if self.agent_tuning["sokrates"].model_id:  # type: ignore[attr-defined]
                sokrates_ctx, _ = get_agent_num_ctx("sokrates", self, self.agent_tuning["sokrates"].model_id)  # type: ignore[attr-defined, arg-type]
                context_limits.append(sokrates_ctx)
            if self.agent_tuning["salomo"].model_id:  # type: ignore[attr-defined]
                salomo_ctx, _ = get_agent_num_ctx("salomo", self, self.agent_tuning["salomo"].model_id)  # type: ignore[attr-defined, arg-type]
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

        await self._ensure_backend_initialized()  # type: ignore[attr-defined]

        # No main model selected — e.g. the configured one was deleted and
        # got cleared on backend switch (we clear rather than silently
        # substitute). Stop here with a clear hint instead of crashing
        # downstream on an empty model id.
        if not self.agent_tuning["aifred"].model_id:  # type: ignore[attr-defined]
            self.add_debug("⚠️ No model selected — pick one in the settings panel")  # type: ignore[attr-defined]
            return

        # Coverage check: warn (debug console + log) when the active
        # TTS/VLM toggles point at a llama-swap profile that doesn't
        # exist in the YAML. The runtime resolver will silently fall
        # back to the next-best profile, which can OOM on first VLM
        # inference — surfacing the mismatch here gives the user a
        # chance to open the calibration matrix.
        if self.backend_type == "llamacpp":  # type: ignore[attr-defined]
            try:
                from ..lib.calibration import diagnose_uncalibrated_combo
                from ..lib.config import LLAMASWAP_CONFIG_PATH
                from ..lib.tts_engine_manager import GPU_ENGINES
                from ..lib.vision_prewarm import (
                    is_vision_active,
                    get_active_vlm_key,
                )
                _vlm_active_chat = is_vision_active()
                _vlm_key_chat = get_active_vlm_key() if _vlm_active_chat else ""
                _warn = diagnose_uncalibrated_combo(
                    LLAMASWAP_CONFIG_PATH,
                    self.agent_tuning["aifred"].model_id,  # type: ignore[attr-defined]
                    tts_active=bool(self.enable_tts),  # type: ignore[attr-defined]
                    tts_engine=self.tts_engine,  # type: ignore[attr-defined]
                    gpu_tts_engines=GPU_ENGINES,
                    vlm_active=_vlm_active_chat,
                    vlm_key=_vlm_key_chat,
                )
                if _warn:
                    self.add_debug(_warn)
                    log_message(_warn)
            except Exception as _e:  # noqa: BLE001
                # Diagnostic only — never block a chat submit on a check failure
                log_message(f"diagnose_uncalibrated_combo failed: {_e}")

        # ============================================================
        # PHASE 1: Spinner + textarea clear — yield IMMEDIATELY
        # ============================================================
        # Minimal state for instant UI feedback (spinner + correct agent indicator)
        # Textarea is already cleared client-side by the call_script in on_click
        self.is_generating = True
        self._set_current_agent("")
        self._streaming_sub().current_ai_response = ""  # type: ignore[attr-defined]

        # Cleanup bookkeeping referenced by the finally block — must exist
        # BEFORE the try so every abort point (including the early yields
        # below) resets is_generating and releases pipeline/TTS again.
        from ..lib.pipeline_registry import register_pipeline, unregister_pipeline
        from ..lib.tts_engine_manager import (
            GPU_ENGINES,
            _detect_running_tts_engine,
            acquire_tts,
            release_tts,
            tts_keepalive_loop,
        )
        from ..lib.llm_client import LLMClient
        ai_text = ""  # Used in finally block
        _pipeline_task: asyncio.Task | None = None
        acquired_tts_engines: list[str] = []
        tts_keepalive_task = None
        llm_client: LLMClient | None = None
        _client_gone = False  # set on GeneratorExit — finally must not yield then
        try:
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
                # Generate clickable image thumbnails as HTML. No <a> wrapper:
                # the /_upload/ URL is picked up by the global lightbox handler
                # (aifred.py lightbox_js) on click — a target="_blank" link would
                # additionally open a redundant browser tab. Lightbox only.
                image_html_parts: list[str] = []
                for img in self.pending_images:  # type: ignore[attr-defined]
                    url = img.get('url', '')
                    if url:
                        image_html_parts.append(
                            f'<img src="{url}" style="width:50px;height:50px;object-fit:cover;'
                            f'border-radius:4px;cursor:zoom-in;">'
                        )
                # Flex row so multiple thumbnails sit side by side with a
                # small gap instead of stacking. Class + inline style: the
                # class rule (aifred.py stylesheet) also pins the 50px
                # cover-crop look in case the markdown path drops inline
                # styles on the way to the DOM.
                image_html = (
                    '<div class="aifred-thumbrow" '
                    'style="display:flex;flex-wrap:wrap;gap:6px;">'
                    + "".join(image_html_parts) + "</div>"
                ) if image_html_parts else ""

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

            ch = self._chat_sub()
            ch.chat_history = [
                *ch.chat_history,
                {
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
                },
            ]
            # Anchor the image location in the user turn of llm_history: the image
            # itself only goes into the one-off multimodal call, never into the
            # history. Without the URL the model "forgets" on a follow-up that an
            # image ever existed and falsely accuses itself of hallucinating. With
            # the /_upload/ URL it can re-examine the image via vision_analyze (on
            # the big model when that is vision-capable).
            llm_user_content = user_msg
            if has_pending_images:
                _img_urls = [img.get("url", "") for img in self.pending_images if img.get("url")]  # type: ignore[attr-defined]
                if _img_urls:
                    from ..lib.prompt_loader import get_language
                    _de = get_language() == "de"
                    _label = (
                        ("Angehängtes Bild" if len(_img_urls) == 1 else "Angehängte Bilder")
                        if _de else
                        ("Attached image" if len(_img_urls) == 1 else "Attached images")
                    )
                    _hint = (
                        "mit dem vision_analyze-Tool erneut betrachtbar"
                        if _de else
                        "re-examine with the vision_analyze tool"
                    )
                    _marker = f"[{_label}: {', '.join(_img_urls)} — {_hint}]"
                    llm_user_content = f"{user_msg}\n\n{_marker}" if user_msg.strip() else _marker
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
            tts_streaming = self._tts_streaming_wanted("aifred")  # type: ignore[attr-defined]
            if tts_streaming:
                self._init_streaming_tts(agent="aifred")  # type: ignore[attr-defined]
                from ..lib.api import browser_queue_clear
                browser_queue_clear(self.session_id)  # type: ignore[attr-defined]
            # SSE stream (re)start — idempotent if already connected.
            yield rx.call_script(  # type: ignore[misc]
                f"if(window.startBrowserStream) startBrowserStream('{self.session_id}');"
            )

            # vLLM-Eintraege werden von llama-swap on demand geladen —
            # kein eigener Lade-Schritt mehr (ehem. PHASE 4, Direkt-Pfad).

            # TTS: Ensure Docker container is running BEFORE Ollama loads models (reserves VRAM)
            async for _ in self._phase_tts_container_checks():
                yield

            # Register this coroutine in the pipeline registry so external stop
            # commands (UI stop button, future cross-channel stop) can cancel it.
            _pipeline_task = asyncio.current_task()
            if _pipeline_task is not None and self.session_id:  # type: ignore[attr-defined]
                register_pipeline(self.session_id, _pipeline_task)  # type: ignore[attr-defined]

            # Acquire active GPU TTS engine + start keep-alive ping for the
            # duration of the pipeline. Prevents the container from idle-out
            # during long inference or web research (analogous to FreeEcho.2 channel).
            if self.enable_tts and self.tts_engine in GPU_ENGINES:  # type: ignore[attr-defined]
                _active_engine = _detect_running_tts_engine()
                if _active_engine:
                    acquire_tts(_active_engine)
                    acquired_tts_engines.append(_active_engine)
                    tts_keepalive_task = asyncio.create_task(
                        tts_keepalive_loop(
                            acquired_tts_engines,
                            on_warn=lambda m: self.add_debug(f"⚠️ {m}"),  # type: ignore[attr-defined]
                        )
                    )

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

                # SSOT VL-model choice (vision-capable main model first) —
                # see _vl_choice. Sampling follows the model, not the role.
                _eff_vl, _vl_bucket = self._vl_choice()  # type: ignore[attr-defined]

                # Cold start warning (llama-swap: llamacpp + vllm)
                if self.backend_type in LLAMASWAP_BACKENDS:  # type: ignore[attr-defined]
                    try:
                        running = await self._llamaswap_running_models()
                        if _eff_vl not in running:
                            self.add_debug(f"🔄 VL Model Cold Start ({_eff_vl}) — loading...")  # type: ignore[attr-defined]
                            # Same VRAM guard as the main-model cold start.
                            from ..lib.audio_processing import release_whisper_gpu, whisper_gpu_busy
                            _loop = asyncio.get_running_loop()
                            if await _loop.run_in_executor(None, whisper_gpu_busy):
                                self.add_debug("⏳ Whisper transcription in progress — model load waits for it to finish")  # type: ignore[attr-defined]
                                yield
                            if await _loop.run_in_executor(None, release_whisper_gpu):
                                self.add_debug("🎤 Whisper GPU worker released (VRAM for model load)")  # type: ignore[attr-defined]
                            yield
                    except Exception:
                        pass

                img_count = len(local_images)
                self.add_debug(f"📷 VL Direct ({img_count} image(s)) → {_eff_vl}")  # type: ignore[attr-defined]
                yield

                # Build multimodal content (images + text) for call_llm()
                from ..lib.vision_utils import load_image_as_base64
                from pathlib import Path

                content_parts: list[dict] = []

                # Qwen3-VL respects /no_think prefix (Ollama ignores API think param for VL)
                if not self.agent_tuning[_vl_bucket].thinking:  # type: ignore[attr-defined]
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
                prompt_text = stamp_user_turn(
                    user_msg.strip() if has_user_text else (
                        "Beschreibe und analysiere dieses Bild." if detected_language == "de"
                        else "Describe and analyze this image."
                    ),
                    turn_stamp,
                )
                # File-URL reference for the CURRENT turn: the image itself is
                # only base64 in this call — without its /_upload/ URL the
                # model cannot hand the file to a tool (e.g. telegram_send
                # attachment). Follow-up turns get the URL via the llm_history
                # anchor; this line is the first-turn equivalent. MERGED into
                # the single text part — a SECOND text part in the multimodal
                # content breaks the VL chat-template/mmproj alignment (the
                # model then only thinks and stops without tool calls).
                _img_urls = [img.get("url", "") for img in local_images if img.get("url")]
                if _img_urls:
                    _ref = (
                        "Datei-URL des Bildes — für Tool-Aufrufe verwenden, z.B. als attachment"
                        if detected_language == "de"
                        else "File URL of the image — use in tool calls, e.g. as attachment"
                    )
                    prompt_text = f"{prompt_text}\n\n[{_ref}: {', '.join(_img_urls)}]"
                content_parts.append({"type": "text", "text": prompt_text})

                # Symposion with >=2 agents: one shared image description,
                # then the normal multi-agent discussion — not a single
                # agent's private answer (see _symposion_vision_handoff).
                if self.multi_agent_mode == "symposion" and len(self.symposion_agents) >= 2:  # type: ignore[attr-defined]
                    async for _ in self._symposion_vision_handoff(local_images, detected_language):
                        yield
                    return

                # SSOT vision perception (vision/task_adaptive.txt): same
                # "look at the image thoroughly" body regardless of image
                # type; only the follow-up instruction differs by whether
                # the user asked something specific.
                from ..lib.prompt_loader import load_prompt
                task_instruction = (
                    load_prompt("vision/task_instruction_question", lang=detected_language, question=user_msg.strip())
                    if has_user_text
                    else load_prompt("vision/task_instruction_default", lang=detected_language)
                )
                vision_task_addon = load_prompt(
                    "vision/task_adaptive", lang=detected_language, task_instruction=task_instruction,
                )

                async for _ in self._process_vision_request(
                    user_msg, content_parts, "GEMISCHT", detected_language, vision_task_addon,
                ):
                    yield
                return  # Vision fast path complete

            # Create LLM client once - used for ALL LLM operations
            llm_client = LLMClient(
                backend_type=self.backend_type,  # type: ignore[attr-defined]
                base_url=self.backend_url,  # type: ignore[attr-defined]
            )

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
                auto_num_ctx: int | None = self.agent_tuning["aifred"].max_context if self.agent_tuning["aifred"].max_context else None  # type: ignore[attr-defined]
                log_message(f"🔧 Automatik = AIfred ({effective_auto}) → num_ctx={auto_num_ctx} (match preload)")

                # Warning if AIfred context is below recommended Automatik threshold
                effective_ctx = self.agent_tuning["aifred"].max_context or 0  # type: ignore[attr-defined]
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
            # SSOT VL-model choice — see _effective_vl_model_id. With a
            # vision-capable main model this equals the main model, so the
            # override below correctly becomes a no-op (no switch to save).
            _eff_vl_id = self._effective_vl_model_id()  # type: ignore[attr-defined]
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
            cold_start = False
            if self.backend_type in LLAMASWAP_BACKENDS:  # type: ignore[attr-defined]
                try:
                    running_models = await self._llamaswap_running_models()
                    if effective_auto not in running_models:
                        cold_start = True
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
                        # Whisper's GPU worker (~2 GB, 30 min TTL) would break
                        # the calibrated splits — release it before the load.
                        # A running transcription gets a grace period first.
                        from ..lib.audio_processing import release_whisper_gpu, whisper_gpu_busy
                        _loop = asyncio.get_running_loop()
                        if await _loop.run_in_executor(None, whisper_gpu_busy):
                            self.add_debug("⏳ Whisper transcription in progress — model load waits for it to finish")
                            yield
                        if await _loop.run_in_executor(None, release_whisper_gpu):
                            self.add_debug("🎤 Whisper GPU worker released (VRAM for model load)")
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
                is_pure_command = False
                _reason = "URL-only" if _is_url_only else "image-only"
                self.add_debug(f"🎯 Intent: {detected_intent} ({_reason}), Lang: {detected_language.upper()} (UI)")
                self._last_detected_language = detected_language  # type: ignore[attr-defined]
            else:
                # Beim Cold Start traegt dieser erste LLM-Aufruf das Laden des
                # Modells; seine Dauer ist die Ladezeit (die Intent-Inferenz
                # selbst liegt im Sekundenbereich) und landet als "Load" an
                # der Antwort. Bild-/URL-only-Turns ohne Intent-Aufruf laden im
                # Hauptaufruf und zeigen die Zeit in der TTFT.
                from ..lib.timer import Timer
                _intent_timer = Timer()
                (
                    detected_intent,
                    addressed_to,
                    detected_language,
                    mode_switch_updates,
                    is_pure_command,
                    intent_raw,
                ) = await detect_query_intent_and_addressee(
                    user_msg,
                    effective_auto,
                    llm_client,
                    automatik_num_ctx=auto_num_ctx,
                )
                if cold_start:
                    self._pending_load_time = _intent_timer.elapsed()
                # Log Intent Detection result to UI debug console (always visible)
                from ..lib.intent_detector import format_intent_result
                from ..lib.intent_detector import format_mode_switch_summary
                switch_str = format_mode_switch_summary(mode_switch_updates) if mode_switch_updates else "–"
                pure_str = "yes" if is_pure_command else "no"
                self.add_debug(
                    f"🎯 {format_intent_result(detected_intent, addressed_to, detected_language)}"
                    f", Switch: {switch_str}, PureCmd: {pure_str}"
                )
                self._last_detected_language = detected_language  # type: ignore[attr-defined]

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
                # NOTE: research_mode is intentionally NOT switchable here —
                # the user controls it via the UI toggle, and the answering
                # agent decides per query whether to invoke its web tools.
                # ``_parse_mode_switch`` already drops any ``research=…`` from
                # the LLM, so this dict only ever contains agent / multi keys.
                if "active_agent" in mode_switch_updates:
                    self.active_agent = mode_switch_updates["active_agent"]  # type: ignore[attr-defined]
                if "multi_agent_mode" in mode_switch_updates:
                    self.multi_agent_mode = mode_switch_updates["multi_agent_mode"]  # type: ignore[attr-defined]
                if "symposion_agents" in mode_switch_updates:
                    self.symposion_agents = list(mode_switch_updates["symposion_agents"])  # type: ignore[attr-defined]

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
                        _active = self.active_agent  # type: ignore[attr-defined]
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
            if addressed_to and addressed_to != self.active_agent:  # type: ignore[attr-defined]
                self.active_agent = addressed_to  # type: ignore[attr-defined]
                self._persist_session_config()  # type: ignore[attr-defined]

            # Sticky routing is suspended in symposion mode: there the agent
            # SELECTION decides who speaks (run_symposion for >=2, the
            # single selected agent below for 1) — otherwise a stale
            # active_agent from an earlier turn logs a misleading
            # "Direct addressing: X" before the symposion override kicks in.
            if (
                not addressed_to
                and self.active_agent != "aifred"  # type: ignore[attr-defined]
                and self.multi_agent_mode != "symposion"  # type: ignore[attr-defined]
            ):
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
                # Symposion with fewer than 2 agents degrades to a plain
                # direct response — deliberately supported, so the user can
                # toggle agents on/off mid-session and continue with one.
                # With exactly one selected agent THAT agent must answer
                # (not whoever happens to be active); with zero we fall
                # back to the normal addressing logic, visibly.
                if self.multi_agent_mode == "symposion":  # type: ignore[attr-defined]
                    if len(self.symposion_agents) == 1:  # type: ignore[attr-defined]
                        responding_agent = self.symposion_agents[0]  # type: ignore[attr-defined]
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
                    research_mode=effective_research_mode,
                    detected_intent=detected_intent,
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

            self._streaming_sub().current_ai_response = ""  # type: ignore[attr-defined]
            self.current_user_message = ""
            self.is_generating = False
            self._save_current_session()  # type: ignore[attr-defined]
            yield

        except GeneratorExit:
            # WebSocket disconnect cancelled this generator. A generator
            # that yields again while unwinding GeneratorExit raises
            # "async generator ignored GeneratorExit" and aborts the
            # finally block — so flag it and let finally skip its yields
            # (session save etc. still run).
            _client_gone = True
            raise
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
            # Close the per-message LLM client — every backend holds a
            # persistent HTTP connection pool; without this the pool leaks
            # once per sent message (multi_agent closes its own clients).
            if llm_client is not None:
                await llm_client.close()

            # Stop TTS keep-alive task and release engine refcounts before
            # any other cleanup runs (so a stuck downstream save doesn't
            # leak our acquisition).
            if tts_keepalive_task is not None and not tts_keepalive_task.done():
                tts_keepalive_task.cancel()
            for _engine in acquired_tts_engines:
                release_tts(_engine)

            # Unregister from pipeline registry — only if this is still the
            # registered task (a newer pipeline may have superseded us).
            if _pipeline_task is not None and self.session_id:  # type: ignore[attr-defined]
                unregister_pipeline(self.session_id, _pipeline_task)  # type: ignore[attr-defined]

            # Streaming-TTS finalize for every path that skipped the
            # multi-agent finish (vision fast path, pure-command return,
            # exception before/inside the agent stream) — otherwise
            # tts_streaming_in_flight stays True and blocks media resume.
            # No-op when the multi-agent path already spawned it.
            self._spawn_tts_finalize()  # type: ignore[attr-defined]

            # Per-turn semantics: without this reset a failed agent
            # inference AFTER successful research leaves the counter set
            # and the NEXT (research-less) turn gets a stale
            # "[Recherche: N Quellen]" marker in its llm_history.
            self._research_source_count = 0  # type: ignore[attr-defined]

            # Partial-response rescue: when the handler is aborted
            # mid-stream (CancelledError from the stop button or pipeline
            # supersession, GeneratorExit on shutdown/worker respawn), the
            # streamed text so far lives ONLY in current_ai_response —
            # add_agent_panel never ran and a minutes-long answer would be
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
            _partial = self._streaming_sub().current_ai_response  # type: ignore[attr-defined]
            if _last_entry.get("role") != "assistant" and _partial.strip():
                from ..lib.i18n import t
                _lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
                self.add_agent_panel(
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
            if len(self.pending_images) > 0:  # type: ignore[attr-defined]
                self.clear_pending_images()  # type: ignore[attr-defined]

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
                effective_id = self._effective_model_id("aifred")  # type: ignore[attr-defined]
                from ._base import track_orphan_task
                track_orphan_task(asyncio.create_task(
                    self._generate_session_title(title_model_override=effective_id)  # type: ignore[attr-defined]
                ))

            # Auto-Save: Session nach jeder Chat-Nachricht speichern
            # IMPORTANT: Save BEFORE refresh so message_count is up-to-date
            self._save_current_session()  # type: ignore[attr-defined]

            # Refresh session list to update sorting (last_seen changed) and message count
            self.refresh_session_list()  # type: ignore[attr-defined]
            if not _client_gone:
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

    async def save_session_memory(self) -> AsyncGenerator[EventSpec | None, None]:
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
        yield None

        llm_client = LLMClient(
            backend_type=self.backend_type,  # type: ignore[attr-defined]
            base_url=self.backend_url,  # type: ignore[attr-defined]
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
        finally:
            await llm_client.close()
