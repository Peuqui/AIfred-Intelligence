"""TTS streaming mixin for AIfred state.

Handles TTS audio generation, sentence buffering, queue management,
and audio regeneration.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, List

import reflex as rx

from ..lib.logging_utils import log_message


@dataclass
class TTSBackendState:
    """Per-session backend coordination state for parallel TTS task tracking.

    Held outside the Reflex state because none of these fields drive UI:
    they only coordinate the async TTS generation tasks between the
    create_task call sites and the finalize wait. When these lived inside
    the Reflex state, every mutation (and there are many per response —
    one per sentence start, completion, and order-buffer drain) bumped
    the state version and forced the chat bubble to re-render. Plain
    Python attrs keyed by session_id give us the same per-session
    semantics without the UI churn.
    """
    pending_requests: list[str] = field(default_factory=list)        # Request-IDs of TTS tasks in flight
    completed_urls: dict[str, str] = field(default_factory=dict)     # {request_id: audio_url}
    order_buffer: dict[int, tuple | None] = field(default_factory=dict)  # {seq: (url, rate, req_id) | None}
    next_seq: int = 0   # Next sequence number to assign to a sentence
    push_seq: int = 0   # Next sequence number expected for queue push


_tts_backend_states: dict[str, TTSBackendState] = {}


def get_tts_backend_state(session_id: str) -> TTSBackendState:
    """Get (creating on first access) the per-session TTS backend state."""
    state = _tts_backend_states.get(session_id)
    if state is None:
        state = TTSBackendState()
        _tts_backend_states[session_id] = state
    return state


def discard_tts_backend_state(session_id: str) -> None:
    """Drop the per-session TTS backend state on logout / session delete."""
    if not session_id:
        return
    _tts_backend_states.pop(session_id, None)


# Concurrency-Throttle fuer TTS-HTTP-Calls. Verhindert dass AIfred bei langen
# Antworten 80+ parallele Requests auf den XTTS-Container kippt — der hat
# einen einzigen GPU-Worker, alle weiteren stapeln sich in der Gunicorn-Queue
# und stauen GPU-Memory bis zum CUDA-device-side-assert. Mit Semaphore(N)
# laufen maximal N Requests gleichzeitig, der Rest wartet brav in der FIFO.
# LLM-Stream wird NICHT blockiert (Tasks werden weiterhin sofort per
# create_task gestartet), nur der HTTP-Call innerhalb des Tasks wartet.
# Die Sentence-Order bleibt durch _tts_order_buffer erzwungen.
TTS_CONCURRENT_REQUESTS = 2
_tts_concurrency_sema: asyncio.Semaphore = asyncio.Semaphore(TTS_CONCURRENT_REQUESTS)


class TTSStreamingMixin(rx.State, mixin=True):
    """Mixin for TTS streaming, generation, and queue management."""

    # ── State Variables ──────────────────────────────────────────────

    # TTS Audio Output
    tts_audio_path: str = ""  # Path to generated TTS audio file
    tts_trigger_counter: int = 0  # Incremented to trigger TTS playback in frontend

    # TTS Audio Queue - for sequential playback of multiple agent responses
    # Queue URLs are added when add_agent_panel() generates TTS
    # Frontend plays queue items sequentially (first in, first out)
    tts_audio_queue: List[str] = []  # Queue of audio URLs to play
    tts_queue_version: int = 0  # Incremented when queue changes (triggers frontend update)

    # Streaming TTS - send sentences to TTS as they are generated
    _tts_sentence_buffer: str = ""  # Accumulates tokens until sentence boundary detected
    _tts_short_carry: str = ""  # Short sentences (< 3 words) waiting to merge with next
    _tts_in_collapsible_block: bool = False  # True while a collapsible block (think/vlm_output/data/…) is still streaming in
    _tts_streaming_active: bool = False  # True during active streaming session
    _tts_finalize_spawned: bool = False  # background finalize already started for this init
    _tts_streaming_agent: str = "aifred"  # Current agent for voice selection (aifred/sokrates/salomo)
    _pending_audio_urls: List[str] = []  # Audio URLs collected during streaming, for message assignment

    # TTS Regeneration
    tts_regenerating: bool = False  # True while TTS regeneration is in progress (for spinner)

    # NOTE: TTS task tracking + sentence-order buffer used to live here as
    # Reflex state attributes (_pending_tts_requests, _completed_tts_urls,
    # _tts_order_buffer, _tts_next_seq, _tts_push_seq). They have been moved
    # to a module-level TTSBackendState (see get_tts_backend_state) so the
    # per-sentence churn during streaming no longer re-renders the chat
    # bubble. Access via get_tts_backend_state(self.session_id).

    # ── Computed Properties ───────────────────────────────────────────

    @rx.var(deps=["tts_audio_queue"], auto_deps=False)
    def tts_queue_json(self) -> str:
        """Returns TTS audio queue as JSON string for frontend.

        The frontend JavaScript reads this to update its local queue
        for sequential playback of multi-agent responses.
        """
        return json.dumps(self.tts_audio_queue)

    @rx.var(deps=["_tts_streaming_active"], auto_deps=False)
    def tts_streaming_in_flight(self) -> bool:
        """Public proxy for the internal streaming-lifetime flag.

        True from _init_streaming_tts() until finalize_tts_streaming()
        has awaited every pending sentence task. The frontend uses this
        as the authoritative "more TTS may still arrive" signal — empty
        tts_audio_queue alone is not enough (it's transiently empty
        between chunks during streaming, which made audioOnEnded resume
        media too early and chop off the last sentence).
        """
        return bool(self._tts_streaming_active)

    # ── TTS Helpers ──────────────────────────────────────────────────

    def _resolve_tts_language(self, agent: str) -> str:
        """Pick the synthesis language for an agent.

        Order of precedence:
          1. Per-agent override (tts_agent_voices[agent]["language"]),
             skipped when empty or "auto".
          2. Language the LLM detected from the user prompt.
          3. UI language.

        Returns a two-letter ISO code ("de", "en", "zh", …) — the same
        shape the engine adapters in audio_processing expect.
        """
        agent_settings = self.tts_agent_voices.get(agent, {})  # type: ignore[attr-defined]
        override = str(agent_settings.get("language", "") or "").lower()
        if override and override != "auto":
            return override
        return str(self._last_detected_language or self.ui_language)  # type: ignore[attr-defined]

    def _resolve_agent_tts(self, agent: str) -> tuple[str, float, float]:
        """SSOT for per-agent (voice, speed, pitch) at the active engine.

        A bubble for a named agent must NEVER borrow another agent's
        voice. The old inline logic fell back straight to the global
        ``tts_voice`` when an agent had no voice for the current engine —
        which produced HAL bubbles spoken in AIfred's voice after an
        engine switch (HAL's xtts voice was saved empty, AIfred's wasn't).

        Voice precedence:
          1. User's per-agent voice for this engine
             (``tts_agent_voices[agent]["voice"]``), if set.
          2. The agent's engine default from agents.json — so e.g. HAL
             resolves to ``★ HAL9000`` even when the saved prefs left its
             voice empty.
          3. The global ``tts_voice`` as last resort — only for agents
             with no engine default at all (custom agents lacking a
             ``tts_voices`` entry for this engine).

        Speed/pitch: per-agent override (``"1.25x"`` → 1.25), else the
        agent's engine default, else neutral 1.0.
        """
        from ..lib.agent_config import get_tts_voice_defaults_for_engine

        settings = self.tts_agent_voices.get(agent, {})  # type: ignore[attr-defined]
        eng_default = get_tts_voice_defaults_for_engine(
            self.tts_engine  # type: ignore[attr-defined]
        ).get(agent, {})

        def _as_float(raw: Any, fallback: float) -> float:
            # Parser = lib-SSOT; der Default-Fallback bei Müll ist bewusste
            # Browser-Policy (FreeEcho2 bricht stattdessen fail-loud ab).
            from ..lib.tts_engines import parse_speed_factor
            parsed = parse_speed_factor(raw)
            return fallback if parsed is None else parsed

        # voice: per-agent → agent's engine default → global (last resort)
        voice = (
            str(settings.get("voice", "") or "")
            or str(eng_default.get("voice", "") or "")
            or self.tts_voice  # type: ignore[attr-defined]
        )
        # pitch: per-agent → engine default → global tts_pitch → neutral
        global_pitch = _as_float(self.tts_pitch, 1.0)  # type: ignore[attr-defined]
        pitch = _as_float(
            settings.get("pitch"),
            _as_float(eng_default.get("pitch"), global_pitch),
        )
        # speed: per-agent → engine default → neutral (no global speed)
        speed = _as_float(settings.get("speed"), _as_float(eng_default.get("speed"), 1.0))
        return voice, speed, pitch

    # ── TTS Callback ──────────────────────────────────────────────────


    # ── TTS Generation (Full Response) ────────────────────────────────


    # ── TTS Queue Management ─────────────────────────────────────────

    async def _queue_tts_for_agent(self, content: str, agent: str) -> None:
        """Generate TTS and add to queue for sequential playback.

        This is called by add_agent_panel() when TTS is enabled.
        The audio is generated and added to tts_audio_queue.
        Frontend plays queue items sequentially.

        Args:
            content: The text content to convert to speech (will be cleaned)
            agent: Agent name for per-agent voice settings (aifred, sokrates, salomo)
        """
        from ..lib.audio_processing import (
            clean_text_for_tts,
            generate_tts,
            reset_content_hint_flags,
            set_tts_agent,
        )
        from ..lib.config import DATA_DIR

        try:
            # Reset content-hint flags so this response starts clean.
            reset_content_hint_flags()

            # Clean text: Remove <think> tags, emojis, markdown, URLs, timing info
            clean_text = clean_text_for_tts(content)

            if not clean_text or len(clean_text.strip()) < 5:
                self.add_debug(f"🔇 TTS Queue: Text too short for {agent}")  # type: ignore[attr-defined]
                return

            from ..lib.agent_config import get_agent_config
            _agent_cfg = get_agent_config(agent)
            _agent_name = _agent_cfg.display_name if _agent_cfg else agent.capitalize()
            self.add_debug(f"🔊 TTS Queue: Generating audio for {_agent_name} ({len(clean_text)} chars)...")  # type: ignore[attr-defined]

            # Voice/speed/pitch via the SSOT resolver (per-agent → agent's
            # engine default → global).
            voice_choice, speed_value, pitch_value = self._resolve_agent_tts(agent)

            # Set agent name for audio filename prefixing
            set_tts_agent(agent)

            # Generate TTS audio
            tts_language = self._resolve_tts_language(agent)
            audio_url = await generate_tts(
                text=clean_text,
                voice_choice=voice_choice,
                speed_choice=speed_value,
                tts_engine=self.tts_engine,  # type: ignore[attr-defined]
                pitch=pitch_value,
                language=tts_language
            )

            if audio_url:
                # Verify file exists
                filename = audio_url.split("/")[-1]
                file_path = DATA_DIR / "tts_audio" / filename

                if os.path.exists(file_path):
                    # Add to queue (use temporary URL for autoplay)
                    self.tts_audio_queue = self.tts_audio_queue + [audio_url]
                    self.tts_queue_version += 1
                    # NOTE: Do NOT add to _pending_audio_urls here!
                    # _pending_audio_urls is for Streaming-TTS only, where URLs are collected
                    # during streaming and then passed to add_agent_panel().
                    # For Queue-TTS, we save directly to the agent's message below.
                    # Also set tts_audio_path so HTML5 player shows current audio
                    self.tts_audio_path = audio_url
                    # Set browser playback rate from agent speed setting
                    self.tts_playback_rate = "1.0x"  # type: ignore[attr-defined]  # Speed is baked into audio via engine or ffmpeg
                    file_size_kb = os.path.getsize(file_path) / 1024
                    self.add_debug(f"✅ TTS Queue: Added {_agent_name} audio ({file_size_kb:.1f} KB), queue size: {len(self.tts_audio_queue)}")  # type: ignore[attr-defined]

                    # Save to session directory for permanent storage (replay button)
                    from ..lib.audio_processing import save_audio_to_session
                    session_audio_url = save_audio_to_session([audio_url], self.session_id)  # type: ignore[attr-defined]
                    if session_audio_url:
                        log_message(f"🔊 TTS Queue: Saved to session → {session_audio_url}")

                        # Update THIS agent's message with session audio URL (for replay button).
                        # IMPORTANT: Find message by agent name, not "last assistant-message"!
                        # Multi-Agent runs TTS async, so other agents may have added messages already.
                        # Rebuild the matched entry deep so Reflex registers the change.
                        _ch = self._chat_sub()
                        if _ch.chat_history:
                            new_history = list(_ch.chat_history)
                            for i in range(len(new_history) - 1, -1, -1):
                                msg = new_history[i]
                                if msg.get("role") == "assistant" and msg.get("agent") == agent:
                                    new_metadata = dict(msg.get("metadata") or {})
                                    new_metadata["audio_urls"] = [session_audio_url]
                                    new_metadata["playback_rate"] = f"{speed_value}x"
                                    new_history[i] = {
                                        **msg,
                                        "metadata": new_metadata,
                                        "has_audio": True,
                                        "audio_urls_json": json.dumps([session_audio_url]),
                                    }
                                    log_message(f"🔊 TTS Queue: Added audio URL + playback_rate to {agent}'s message")
                                    break
                            _ch.chat_history = new_history
                            self._save_current_session()  # type: ignore[attr-defined]
                    else:
                        log_message(f"⚠️ TTS Queue: Failed to save audio to session for {agent}")
                else:
                    self.add_debug(f"⚠️ TTS Queue: Audio file not found at {file_path}")  # type: ignore[attr-defined]
            else:
                self.add_debug(f"⚠️ TTS Queue: Generation failed for {agent}")  # type: ignore[attr-defined]

        except (FileNotFoundError, ValueError, RuntimeError) as e:
            self.add_debug(f"❌ TTS Queue Error ({agent}): {e}")  # type: ignore[attr-defined]
            log_message(f"❌ TTS queue generation error for {agent}: {e}")

        from ..lib.logging_utils import CONSOLE_SEPARATOR
        self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]

    def clear_tts_queue(self) -> None:
        """Clear the TTS audio queue (called when starting new message)."""
        if self.tts_audio_queue:
            self.tts_audio_queue = []
            self.tts_queue_version += 1
            self.add_debug("🔊 TTS Queue: Cleared")  # type: ignore[attr-defined]

    # ============================================================
    # STREAMING TTS - Sentence-by-Sentence Generation
    # ============================================================

    _ui_yield_ts: float = 0.0  # Timestamp of last UI yield

    def _streaming_sub(self):  # type: ignore[override]
        """Get StreamingState substate instance (sync, for current_ai_response)."""
        from aifred.state._streaming_state import StreamingState
        return self._get_state_from_cache(StreamingState)

    # Batch tokens before yielding state delta to reduce React re-renders.
    # StreamingText component uses useEffect + DOM append for O(1) updates,
    # but each yield still triggers 363 React context re-renders.
    # At 40 tok/s and 50ms interval → ~2 tokens per batch (word-level).
    _UI_YIELD_INTERVAL: float = 0.05  # 50ms — word-level batching
    _js_chunk_buffer: str = ""  # Accumulates chunks between yields

    def stream_text_to_ui(self, chunk: str) -> bool:
        """Accumulate streaming chunk and update state var when interval elapses.

        Batches tokens to reduce the number of React state deltas.
        Callers yield when this returns True::

            if self.stream_text_to_ui(chunk):
                yield

        Returns:
            True when state was updated and caller should yield.
        """
        import time

        self._js_chunk_buffer += chunk

        if self.enable_tts and self.tts_autoplay and self.tts_streaming_enabled:  # type: ignore[attr-defined]
            self._process_streaming_tts_chunk(chunk)

        now = time.monotonic()
        if now - self._ui_yield_ts >= self._UI_YIELD_INTERVAL:
            self._ui_yield_ts = now
            batch = self._js_chunk_buffer
            self._js_chunk_buffer = ""
            self._streaming_sub().current_ai_response += batch  # type: ignore[attr-defined]
            return True
        return False

    def flush_stream_to_ui(self) -> bool:
        """Flush remaining buffer to current_ai_response. Call at end of streaming."""
        if self._js_chunk_buffer:
            self._streaming_sub().current_ai_response += self._js_chunk_buffer  # type: ignore[attr-defined]
            self._js_chunk_buffer = ""
            return True
        return False

    def _tts_streaming_wanted(self, agent: str = "aifred") -> bool:
        """SSOT for "should streaming TTS run for this agent?".

        All four former call sites combined these switches differently
        (some without enable_tts, some without the per-agent toggle) —
        this is the one authoritative definition: global TTS on, autoplay
        on (no consumer otherwise), streaming mode on, and the per-agent
        voice not disabled.
        """
        if not (self.enable_tts and self.tts_autoplay and self.tts_streaming_enabled):  # type: ignore[attr-defined]
            return False
        return bool(self.tts_agent_voices.get(agent, {}).get("enabled", True))  # type: ignore[attr-defined]

    def _init_streaming_tts(self, agent: str = "aifred"):
        """Initialize streaming TTS state for a new response.

        Call this at the start of send_message() when streaming TTS is enabled.
        For DashScope: Opens a WebSocket connection for realtime token-feeding.
        For other engines: Initializes sentence buffer for parallel sentence TTS.

        Args:
            agent: Agent name for per-agent voice settings
        """
        log_message(f"🔊 TTS Init: Starting streaming TTS for agent={agent}")
        log_message(f"🔊 TTS Init: enable_tts={self.enable_tts}, tts_streaming_enabled={self.tts_streaming_enabled}, engine={self.tts_engine}")  # type: ignore[attr-defined]
        # Reset content-hint flags so a new response starts with a clean slate.
        # Otherwise stale streaming state from the previous response (e.g. a
        # list counter stuck above threshold) would suppress early sentences.
        from ..lib.audio_processing import reset_content_hint_flags
        reset_content_hint_flags()
        self._tts_sentence_buffer = ""
        self._tts_short_carry = ""
        self._tts_in_collapsible_block = False
        self._tts_streaming_active = True
        self._tts_finalize_spawned = False
        self._tts_streaming_agent = agent

        # Reset backend tracking (module-level, not in Reflex state).
        tts_state = get_tts_backend_state(self.session_id)  # type: ignore[attr-defined]
        tts_state.pending_requests = []
        tts_state.completed_urls = {}
        tts_state.order_buffer = {}
        tts_state.next_seq = 0
        tts_state.push_seq = 0

        log_message("🔊 TTS Init: State initialized, ready for chunks")

    async def _finalize_streaming_tts(self) -> list[str]:
        """Wait for the parallel sentence-based TTS tasks to complete and
        return the combined audio URL. All engines run the same path —
        no per-engine special cases here.

        Returns:
            List with single combined audio URL, or empty list if no audio
        """
        if not self._tts_streaming_active:
            log_message("🔊 TTS Finalize: Not active, skipping")
            return []

        # --- Sentence-based parallel TTS (all engines) ---

        # Merge carried-over short sentence with remaining buffer
        final_text = ""
        if self._tts_short_carry:
            final_text = self._tts_short_carry
            self._tts_short_carry = ""
        if self._tts_sentence_buffer and self._tts_sentence_buffer.strip():
            final_text = (final_text + " " + self._tts_sentence_buffer).strip() if final_text else self._tts_sentence_buffer
        self._tts_sentence_buffer = ""

        tts_state = get_tts_backend_state(self.session_id)  # type: ignore[attr-defined]

        # Send remaining text to TTS (even if short - finalize sends everything)
        if final_text and final_text.strip():
            agent = getattr(self, '_tts_streaming_agent', 'aifred')
            seq = tts_state.next_seq
            tts_state.next_seq = seq + 1
            request_id = f"tts_{uuid.uuid4().hex[:8]}"
            tts_state.pending_requests.append(request_id)
            log_message(f"🔊 TTS Finalize: Adding remaining text seq={seq} ({len(final_text)} chars): {repr(final_text[:50])}")
            from ._base import track_orphan_task
            track_orphan_task(asyncio.create_task(self._tts_generate_sentence_async(
                final_text, agent, request_id, self.session_id, seq  # type: ignore[attr-defined]
            )))

        # Wait for all pending TTS tasks to complete.
        # 60 s war zu knapp: bei ~RTF-1 (Qwen3-TTS auf V100, XTTS auf P40 etc.)
        # produziert ein 2k-Zeichen-Bubble ~120 s Audio — wenn der letzte
        # Chunk gerade erst startet, brauchen wir ggü. der Reaktionszeit
        # Puffer. 300 s deckt Bubbles bis ~5 Min Audio bei RTF~1 ab.
        log_message(f"🔊 TTS Finalize: Waiting for {len(tts_state.pending_requests)} pending tasks...")
        max_wait = 300.0
        wait_interval = 0.2  # Check every 200ms
        waited = 0.0
        while tts_state.pending_requests and waited < max_wait:
            await asyncio.sleep(wait_interval)
            waited += wait_interval
            if waited % 2.0 < wait_interval:  # Log every 2 seconds
                log_message(f"🔊 TTS Finalize: Still waiting... pending={len(tts_state.pending_requests)}, completed={len(tts_state.completed_urls)}, waited={waited:.1f}s")

        if tts_state.pending_requests:
            log_message(f"🔊 TTS Finalize: ⚠️ Timeout! {len(tts_state.pending_requests)} tasks still pending after {max_wait}s")
        else:
            log_message(f"🔊 TTS Finalize: ✅ All {len(tts_state.completed_urls)} tasks completed in {waited:.1f}s")

        # Collect completed URLs
        completed_urls = list(tts_state.completed_urls.values())
        log_message(f"🔊 TTS Finalize: {len(completed_urls)} audio chunks collected")

        # Save audio to session directory (permanent storage)
        combined_url: str | None = None
        if completed_urls:
            if len(completed_urls) > 1:
                self.add_debug(f"🔗 TTS: Combining {len(completed_urls)} audio chunks...")  # type: ignore[attr-defined]
            from ..lib.audio_processing import save_audio_to_session
            combined_url = save_audio_to_session(completed_urls, self.session_id)  # type: ignore[attr-defined]
            if combined_url:
                log_message(f"🔊 TTS Finalize: Saved to session → {combined_url}")
                if len(completed_urls) > 1:
                    from ..lib.logging_utils import console_separator, CONSOLE_SEPARATOR
                    self.add_debug(f"🔗 TTS: Combined {len(completed_urls)} chunks → replay ready")  # type: ignore[attr-defined]
                    console_separator()
                    self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
            else:
                from ..lib.logging_utils import console_separator, CONSOLE_SEPARATOR
                self.add_debug(f"⚠️ TTS: Combining {len(completed_urls)} chunks failed")  # type: ignore[attr-defined]
                console_separator()
                self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]

        # Reset streaming state
        self._tts_sentence_buffer = ""
        self._tts_in_collapsible_block = False
        self._tts_streaming_active = False
        tts_state.pending_requests = []
        tts_state.completed_urls = {}
        self._pending_audio_urls = []
        log_message("🔊 TTS Finalize: State reset complete")

        return [combined_url] if combined_url else []

    def _spawn_tts_finalize(self) -> None:
        """Start the background TTS finalize exactly once per streaming init.

        Callable from every exit path of send_message (multi-agent finish,
        vision fast path, pure-command return, exception → finally): the
        claim flag makes repeated calls a no-op, so the finally block can
        invoke this unconditionally without racing the multi-agent path.
        """
        if not self._tts_streaming_active or self._tts_finalize_spawned:
            return
        self._tts_finalize_spawned = True
        from ._base import track_orphan_task
        track_orphan_task(asyncio.create_task(
            self._finalize_streaming_tts_in_background(self._tts_streaming_agent)
        ))

    async def _finalize_streaming_tts_in_background(self, agent: str) -> None:
        """Fire-and-forget finalize: wait for all pending TTS tasks in the
        background, then patch the resulting combined-URL onto the bubble
        that has just been added to chat_history.

        The streaming TTS chunks are already on the way to the browser via
        browser_push() in _drain_tts_order_buffer — this method only
        handles the "after-the-fact" combined-WAV save for replay/export.
        Running it as a create_task means the multi_agent stream generator
        can yield immediately, the bubble renders complete (text + sources
        + sandbox), and AIfred is ready for the next prompt right away.

        We identify the right bubble as the most recent assistant message
        from this agent that still has no audio — its combined URL is what
        we are about to attach. No text matching against the rendered
        content, which carries <details> think-blocks for reasoning models
        and would never match the raw pipeline text.
        """
        try:
            audio_urls = await self._finalize_streaming_tts()
        except Exception as e:
            log_message(f"🔊 TTS Background: ❌ Finalize raised: {e}")
            return

        if not audio_urls:
            return

        try:
            ch = self._chat_sub()  # type: ignore[attr-defined]
            history = list(ch.chat_history)
            target = -1
            for i in range(len(history) - 1, -1, -1):
                msg = history[i]
                if msg.get("role") != "assistant":
                    continue
                if msg.get("agent") != agent:
                    continue
                # The bubble we just produced is the most recent one from
                # this agent without audio yet — older ones were already
                # patched by their own finalize task.
                if msg.get("has_audio"):
                    continue
                target = i
                break

            if target < 0:
                log_message(f"🔊 TTS Background: ⚠️ No bubble found to patch (agent={agent})")
                return

            msg = history[target]
            metadata = dict(msg.get("metadata", {}))
            metadata["audio_urls"] = audio_urls
            metadata.setdefault("playback_rate", "1.0x")
            history[target] = {
                **msg,
                "metadata": metadata,
                "has_audio": True,
                "audio_urls_json": json.dumps(audio_urls),
            }
            ch.chat_history = history
            log_message(f"🔊 TTS Background: ✅ Patched bubble #{target} with {len(audio_urls)} audio URL(s)")

            # Reflex-independent live push: this bare create_task mutates
            # the server state above, but Reflex never pushes that delta to
            # the browser. So announce the combined URL over the existing
            # SSE audio bus — custom.js attaches it to the bubble's audio
            # button itself, no Reflex round-trip needed.
            if audio_urls:
                try:
                    from ..lib.api import browser_push
                    browser_push(
                        self.session_id,  # type: ignore[attr-defined]
                        kind="bubble_audio",
                        url=audio_urls[0],
                    )
                except Exception as e:
                    log_message(f"🔊 TTS Background: bubble_audio push failed: {e}")

            # Persist so a session reload still has the audio URL.
            try:
                self._save_current_session()  # type: ignore[attr-defined]
            except Exception as e:
                log_message(f"🔊 TTS Background: session save failed: {e}")
        except Exception as e:
            log_message(f"🔊 TTS Background: ❌ Bubble patch failed: {e}")

    def _process_streaming_tts_chunk(self, chunk: str) -> None:
        """Process a streaming chunk for TTS.

        Called for each content chunk during LLM streaming. Extracts
        complete sentences from the rolling buffer and kicks off TTS
        synthesis in parallel via create_task() — all engines (XTTS /
        MOSS / Qwen3 / Fish-Speech / Piper / eSpeak / Edge / DashScope)
        use the same sentence-based path.

        Args:
            chunk: Text chunk from LLM streaming
        """
        if not self._tts_streaming_active or not self.enable_tts or not self.tts_streaming_enabled:  # type: ignore[attr-defined]
            return

        from ..lib.audio_processing import (
            extract_complete_sentences,
            strip_collapsible_content_streaming,
            buffer_has_open_collapsible,
        )

        # Add chunk to buffer (used for collapsible-block detection in all modes)
        self._tts_sentence_buffer += chunk
        log_message(f"🔊 TTS Chunk: +{len(chunk)} chars, buffer now {len(self._tts_sentence_buffer)} chars")

        # Strip COMPLETE collapsible blocks (think, vlm_output, data, …) from the
        # buffer now — their content is hidden in the UI and must never be spoken.
        # Generic + config-driven (SSoT), replacing the old <think>-only special
        # case. Preserves whitespace (runs every chunk).
        self._tts_sentence_buffer = strip_collapsible_content_streaming(self._tts_sentence_buffer)

        # A collapsible block still streaming in (opened, close not yet arrived) →
        # wait for the rest before extracting sentences, so a half-open block is
        # never spoken. Recomputed from the buffer each chunk (no special-casing).
        self._tts_in_collapsible_block = buffer_has_open_collapsible(self._tts_sentence_buffer)
        if self._tts_in_collapsible_block:
            log_message("🔊 TTS Chunk: Inside collapsible block, waiting...")
            return

        # Try to extract complete sentences
        sentences, remaining = extract_complete_sentences(self._tts_sentence_buffer)
        self._tts_sentence_buffer = remaining

        if sentences:
            log_message(f"🔊 TTS Chunk: Extracted {len(sentences)} sentence(s), remaining buffer: {len(remaining)} chars")
            for i, s in enumerate(sentences):
                log_message(f"🔊 TTS Chunk: Sentence {i+1}: {repr(s)}")

        # Prepend any carried-over short sentence to the first extracted sentence
        if self._tts_short_carry and sentences:
            sentences[0] = self._tts_short_carry + " " + sentences[0]
            self._tts_short_carry = ""

        # XTTS hallucinates on very short text (< 3 words).
        # Carry short sentences over to be merged with the next batch.
        min_tts_words = 3

        # Send each complete sentence to TTS IMMEDIATELY via create_task
        agent = getattr(self, '_tts_streaming_agent', 'aifred')
        for sentence in sentences:
            # Skip empty/whitespace-only content
            if not sentence.strip():
                continue

            # Carry over short sentences to avoid XTTS hallucination
            if len(sentence.split()) < min_tts_words:
                self._tts_short_carry = sentence
                log_message(f"🔊 TTS Chunk: Carrying short sentence ({len(sentence.split())} words): {repr(sentence)}")
                continue

            # Assign sequence number for ordered queue push (module-level state).
            session_id = self.session_id  # type: ignore[attr-defined]
            tts_state = get_tts_backend_state(session_id)
            seq = tts_state.next_seq
            tts_state.next_seq = seq + 1
            log_message(f"🔊 TTS Chunk: Starting TTS task seq={seq} (agent={agent}): {repr(sentence)}")
            # Track pending request
            request_id = f"tts_{uuid.uuid4().hex[:8]}"
            tts_state.pending_requests.append(request_id)
            log_message(f"🔊 TTS Chunk: Created request {request_id}, pending={len(tts_state.pending_requests)}")
            # Start TTS generation IMMEDIATELY in parallel - no waiting!
            # Pass session_id for API-based queue push (create_task can't use Reflex state)
            # track_orphan_task: without a strong reference the loop may GC
            # the task mid-run — its request_id would then hang in
            # pending_requests until the 300s finalize timeout.
            from ._base import track_orphan_task
            track_orphan_task(asyncio.create_task(
                self._tts_generate_sentence_async(sentence, agent, request_id, session_id, seq)
            ))

    async def _tts_generate_sentence_async(self, sentence: str, agent: str, request_id: str, session_id: str, seq: int) -> None:
        """Generate TTS for a single sentence - runs in parallel via create_task.

        This is a plain async function called via asyncio.create_task() from
        _process_streaming_tts_chunk(). It runs truly in parallel with streaming,
        without waiting for event handler completion.

        Since create_task runs outside Reflex's event system, we can't use
        `async with self:` to push state. Instead, we push to a global API queue
        that the frontend polls via HTTP.

        Sentences are generated in parallel but pushed to the queue in sequence
        order (by seq number). If a later sentence finishes first, it waits in
        _tts_order_buffer until all earlier sentences have been pushed.

        Args:
            sentence: Clean sentence text to synthesize
            agent: Agent name for per-agent voice settings and filename prefix
            request_id: Unique ID for tracking (removed from pending on completion)
            session_id: Session ID for API-based queue push
            seq: Sequence number for ordered queue push
        """
        from ..lib.audio_processing import clean_text_for_tts, generate_tts
        from ..lib.config import DATA_DIR

        tts_state = get_tts_backend_state(session_id)

        try:
            # Light cleanup - remove markdown, emojis, but keep the text mostly intact
            clean_text = clean_text_for_tts(sentence)

            if not clean_text or not clean_text.strip():
                # Empty sentence: mark as done and drain buffer
                tts_state.pending_requests = [r for r in tts_state.pending_requests if r != request_id]
                tts_state.order_buffer[seq] = None
                self._drain_tts_order_buffer(session_id)
                return

            # Voice/speed/pitch via the SSOT resolver (per-agent → agent's
            # engine default → global).
            voice_choice, speed_value, pitch_value = self._resolve_agent_tts(agent)
            tts_engine = self.tts_engine  # type: ignore[attr-defined]

            # Generate TTS audio (this is the slow part - runs in parallel)
            tts_language = self._resolve_tts_language(agent)
            log_message(f"🔊 TTS Generate: Calling generate_tts() seq={seq} for agent={agent}: {repr(clean_text)}")
            log_message(f"🔊 TTS Generate: voice={voice_choice}, speed={speed_value}, pitch={pitch_value}, engine={tts_engine}, lang={tts_language}")

            # Concurrency-Throttle: max TTS_CONCURRENT_REQUESTS parallel an TTS-Backend.
            # Verhindert GPU-Memory-Pile-Up wenn die LLM in Sekunden 80+ Sentences
            # produziert. Tasks warten in der Semaphore-FIFO statt am Container.
            async with _tts_concurrency_sema:
                audio_url = await generate_tts(
                    text=clean_text,
                    voice_choice=voice_choice,
                    speed_choice=speed_value,
                    tts_engine=tts_engine,
                    pitch=pitch_value,
                    agent=agent,  # Pass agent for correct filename prefix
                    language=tts_language
                )

            if audio_url:
                filename = audio_url.split("/")[-1]
                file_path = DATA_DIR / "tts_audio" / filename
                log_message(f"🔊 TTS Generate: Got audio_url={audio_url}, filename={filename}")

                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    log_message(f"🔊 TTS Generate: File exists, size={file_size} bytes")

                    playback_rate = "1.0x"  # Speed is baked into audio via engine or ffmpeg

                    # Buffer result for ordered push (module-level state).
                    tts_state.order_buffer[seq] = (audio_url, playback_rate, request_id)
                    self._drain_tts_order_buffer(session_id)

                    log_message(f"🔊 TTS Generate: pending_requests={len(tts_state.pending_requests)}")
                else:
                    log_message(f"🔊 TTS Generate: ⚠️ File does not exist: {file_path}")
                    tts_state.pending_requests = [r for r in tts_state.pending_requests if r != request_id]
                    tts_state.order_buffer[seq] = None
                    self._drain_tts_order_buffer(session_id)
            else:
                log_message("🔊 TTS Generate: ⚠️ No audio_url returned from generate_tts()")
                tts_state.pending_requests = [r for r in tts_state.pending_requests if r != request_id]
                tts_state.order_buffer[seq] = None
                self._drain_tts_order_buffer(session_id)

        except Exception as e:
            log_message(f"❌ TTS Stream Error: {e}")
            import traceback
            log_message(f"❌ TTS Stream Traceback: {traceback.format_exc()}")
            # Remove request from pending on error and mark seq as done
            tts_state.pending_requests = [r for r in tts_state.pending_requests if r != request_id]
            tts_state.order_buffer[seq] = None
            self._drain_tts_order_buffer(session_id)

    def _drain_tts_order_buffer(self, session_id: str) -> None:
        """Push completed TTS results to the queue in sequence order.

        Called after each sentence completes (success, error, or empty).
        Drains all consecutive entries starting from push_seq.
        Skips entries marked as None (empty/failed sentences).
        """
        from ..lib.api import browser_push

        tts_state = get_tts_backend_state(session_id)

        # Auto-pause media playback before the first TTS chunk goes to the
        # browser. Position is saved by the browser via /api/audio/position
        # on the source-switch — here we just flip the resume flag so the
        # player picks media back up after the TTS queue drains.
        #
        # KNOWN QUIRK (reviewed 2026-07-16, deliberately kept): this write
        # happens inside a bare create_task, outside Reflex's event system —
        # Reflex never pushes the delta to the browser. It works anyway
        # because audio_channels/browser.py reads the flag server-side via
        # getattr on every position poll. If media pause/resume is ever
        # reworked, route this through the API queue like the audio URLs.
        if (
            getattr(self, "media_audio_url", "") != ""
            and not getattr(self, "media_paused_for_tts", False)
            and tts_state.push_seq in tts_state.order_buffer
        ):
            self.media_paused_for_tts = True  # type: ignore[attr-defined]

        while tts_state.push_seq in tts_state.order_buffer:
            entry = tts_state.order_buffer[tts_state.push_seq]

            if entry is not None:
                audio_url, playback_rate, request_id = entry
                browser_push(session_id, "tts", audio_url, playback_rate=playback_rate)
                log_message(f"🔊 TTS Order: ✅ Pushed seq={tts_state.push_seq} to queue")
                # Track completion
                tts_state.completed_urls[request_id] = audio_url
                tts_state.pending_requests = [r for r in tts_state.pending_requests if r != request_id]
            else:
                log_message(f"🔊 TTS Order: Skipping seq={tts_state.push_seq} (empty/failed)")

            # Remove from buffer and advance
            del tts_state.order_buffer[tts_state.push_seq]
            tts_state.push_seq += 1

    # ============================================================
    # TTS Regeneration
    # ============================================================

    # ── Re-Synth in drei Phasen ──────────────────────────────────────
    # Die Re-Synth-Handler sind Background-Events: der State-Lock darf nur
    # für die kurzen Lese-/Schreib-Phasen gehalten werden, die minutenlange
    # Synthese läuft dazwischen lock-frei. Deshalb ist der frühere
    # _regenerate_bubble_tts_core in extract (READ) / synthesize (SLOW) /
    # apply (WRITE) zerlegt.

    def _extract_bubble_tts_request(self, bubble_index: int) -> dict[str, Any] | None:
        """Phase 1 (State-READS — im Background-Event nur unter ``async with
        self`` aufrufen): Text, Stimme und Engine als reine Werte einsammeln.
        ``None`` wenn die Bubble nichts Synthetisierbares hergibt."""
        from ..lib.audio_processing import clean_text_for_tts

        _ch = self._chat_sub()
        msg = _ch.chat_history[bubble_index]
        agent = msg.get("agent", "aifred")

        # Use llm_history instead of chat_history - it's already cleaned
        # Find the corresponding entry in llm_history by counting assistant messages
        assistant_count = 0
        for i in range(bubble_index + 1):
            if _ch.chat_history[i].get("role") == "assistant":
                assistant_count += 1

        # Find the N-th assistant message in llm_history
        llm_content = None
        llm_assistant_count = 0
        for entry in _ch.llm_history:
            if entry.get("role") == "assistant":
                llm_assistant_count += 1
                if llm_assistant_count == assistant_count:
                    llm_content = entry.get("content", "")
                    break

        if not llm_content:
            # Fallback to chat_history if llm_history entry not found
            llm_content = msg.get("content", "")
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} using chat_history fallback")

        if not llm_content or not llm_content.strip():
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} content is empty")
            return None

        # llm_history has format "[AGENT]: content" - remove the label
        content_without_label = re.sub(r'^\[(AIFRED|SOKRATES|SALOMO)\]:\s*', '', llm_content, flags=re.IGNORECASE)
        clean_text = clean_text_for_tts(content_without_label)

        if not clean_text or len(clean_text.strip()) < 5:
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} text too short after cleanup")
            return None

        # Voice/speed/pitch via the SSOT resolver. This is the bug the
        # whole resolver exists for: re-synthing a HAL bubble after an
        # engine switch used to grab the global (AIfred) voice because
        # HAL's saved xtts voice was empty. Now it resolves HAL's engine
        # default (★ HAL9000) instead.
        voice_choice, speed_value, pitch_value = self._resolve_agent_tts(agent)
        # Hard evidence for the "wrong voice on re-synth" report: log the
        # engine, the bubble's agent, the per-agent voice still in state,
        # and the voice the resolver actually picked. Lets us see whether
        # a stale / wrong-engine name (e.g. "★ HAL9000" while on
        # qwen3local) or an empty state slot is the culprit.
        _state_voice = self.tts_agent_voices.get(agent, {}).get("voice", "")  # type: ignore[attr-defined]
        log_message(
            f"🎭 TTS Re-Synth resolve: engine={self.tts_engine} "  # type: ignore[attr-defined]
            f"agent={agent} state_voice={_state_voice!r} → voice={voice_choice!r} "
            f"speed={speed_value} pitch={pitch_value}"
        )

        return {
            "bubble_index": bubble_index,
            # Timestamp der Bubble: Während der lock-freien Synthese kann
            # sich die History ändern (neuer Turn, Löschung) — beim Patchen
            # wird die Bubble darüber wiedergefunden statt blind per Index.
            "timestamp": msg.get("timestamp", ""),
            "agent": agent,
            "clean_text": clean_text,
            "voice": voice_choice,
            "speed": speed_value,
            "pitch": pitch_value,
            "engine": str(self.tts_engine),  # type: ignore[attr-defined]
            "language": self._last_detected_language or self.ui_language,  # type: ignore[attr-defined]
            "session_id": str(self.session_id),  # type: ignore[attr-defined]
        }

    @staticmethod
    async def _synthesize_bubble_audio(request: dict[str, Any]) -> str | None:
        """Phase 2 (LANGSAM — läuft OHNE State-Lock): Synthese + Ablage im
        Session-Verzeichnis. Bewusst ohne jeden ``self``-State-Zugriff."""
        from ..lib.audio_processing import generate_tts, save_audio_to_session, set_tts_agent

        bubble_index = request["bubble_index"]
        set_tts_agent(request["agent"])
        # Generate TTS (complete bubble at once for best quality)
        audio_url = await generate_tts(
            text=request["clean_text"],
            voice_choice=request["voice"],
            speed_choice=request["speed"],
            tts_engine=request["engine"],
            pitch=request["pitch"],
            language=request["language"],
        )
        if not audio_url:
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} audio generation failed")
            return None

        # Save to session directory for permanent storage
        session_audio_url = save_audio_to_session([audio_url], request["session_id"])
        if not session_audio_url:
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} failed to save to session")
            return None

        log_message(f"🔊 TTS: Bubble {bubble_index} saved → {session_audio_url}")
        return session_audio_url

    def _apply_bubble_audio(
        self, bubble_index: int, timestamp: str, session_audio_url: str,
        save_session: bool,
    ) -> bool:
        """Phase 3 (State-WRITE — nur unter ``async with self``): URL an die
        Bubble patchen. Deep-rebuild, damit Reflex die Änderung auf
        Listen-Ebene registriert. Verifiziert den Index per Timestamp —
        die History kann sich während der lock-freien Synthese verschoben
        haben."""
        _ch = self._chat_sub()
        new_history = list(_ch.chat_history)

        if not (
            0 <= bubble_index < len(new_history)
            and new_history[bubble_index].get("timestamp") == timestamp
        ):
            # Index verrutscht (Nachricht gelöscht/History geändert) —
            # Bubble über ihren Timestamp wiederfinden.
            bubble_index = next(
                (i for i, m in enumerate(new_history) if m.get("timestamp") == timestamp),
                -1,
            )
            if bubble_index < 0:
                log_message(
                    f"⚠️ TTS Re-Synth: Bubble (timestamp={timestamp}) vanished "
                    "during synthesis — audio not attached"
                )
                return False

        prev = new_history[bubble_index]
        new_metadata = dict(prev.get("metadata") or {})
        new_metadata["audio_urls"] = [session_audio_url]
        new_history[bubble_index] = {
            **prev,
            "metadata": new_metadata,
            "has_audio": True,
            "audio_urls_json": json.dumps([session_audio_url]),
        }
        _ch.chat_history = new_history

        if save_session:
            self._save_current_session()  # type: ignore[attr-defined]

        return True

    @rx.event(background=True)  # type: ignore[operator]
    async def resynthesize_bubble_tts(self, timestamp: str):
        """Re-synthesize TTS for a specific chat bubble (background event).

        Als normaler Handler hielt das den State-Lock über die GESAMTE
        Synthese (Minuten bei langen Bubbles) und rief ensure_engine_ready
        synchron im Event-Loop — der Container-Start (bis 240 s) fror die
        komplette App ein, und starb währenddessen die Verbindung, ging das
        finale Delta verloren (Audio erst nach F5 sichtbar). Jetzt: Lock nur
        für kurze Lese-/Schreib-Phasen, Engine-Start via to_thread, Synthese
        lock-frei — die UI bleibt bedienbar, der nächste Prompt kann parallel
        laufen.

        Args:
            timestamp: Timestamp of the message to regenerate
        """
        from ..lib.tts_engine_manager import GPU_ENGINES, ensure_engine_ready

        engine = ""
        target_index = -1
        async with self:
            if self.tts_regenerating:
                return

            # Find message by timestamp
            _ch = self._chat_sub()
            bubble_index = None
            for i, msg in enumerate(_ch.chat_history):
                if msg.get("timestamp") == timestamp:
                    bubble_index = i
                    break

            if bubble_index is None:
                self.add_debug(f"⚠️ TTS Re-Synth: Message not found (timestamp: {timestamp})")  # type: ignore[attr-defined]
                return

            if _ch.chat_history[bubble_index].get("role") != "assistant":
                self.add_debug("⚠️ TTS Re-Synth: Message is not an assistant response")  # type: ignore[attr-defined]
                return

            self.tts_regenerating = True
            target_index = bubble_index
            engine = str(self.tts_engine)  # type: ignore[attr-defined]
            yield rx.call_script("stopTts()")  # type: ignore[misc]
            if engine in GPU_ENGINES:
                self.add_debug(f"🔄 TTS Re-Synth: Starte {engine.upper()} Backend...")  # type: ignore[attr-defined]

        # Auto-start TTS backend if not running — single dispatch via SSOT.
        # to_thread: Container-Start + Model-Load dürfen weder Event-Loop
        # noch State-Lock halten.
        if engine in GPU_ENGINES:
            ok, tts_msg, _device = await asyncio.to_thread(ensure_engine_ready, engine)
        else:
            ok, tts_msg = True, "OK"

        if not ok:
            async with self:
                self.add_debug(f"❌ TTS Re-Synth: {tts_msg}")  # type: ignore[attr-defined]
                self.tts_regenerating = False
            return

        try:
            async with self:
                request = self._extract_bubble_tts_request(target_index)
                if request is not None:
                    self.add_debug(  # type: ignore[attr-defined]
                        f"🔄 TTS Re-Synth: Regenerating bubble {target_index} ({request['agent']})..."
                    )

            success = False
            if request is not None:
                session_audio_url = await self._synthesize_bubble_audio(request)
                if session_audio_url:
                    async with self:
                        success = self._apply_bubble_audio(
                            target_index, request["timestamp"],
                            session_audio_url, save_session=True,
                        )

            async with self:
                if success:
                    self.add_debug(f"✅ TTS: Bubble {target_index} regenerated")  # type: ignore[attr-defined]
                else:
                    self.add_debug(f"⚠️ TTS: Bubble {target_index} regeneration failed")  # type: ignore[attr-defined]
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            async with self:
                self.add_debug(f"❌ TTS Error: {e}")  # type: ignore[attr-defined]
            log_message(f"❌ TTS regeneration error: {e}")
        finally:
            async with self:
                self.tts_regenerating = False

    @rx.event(background=True)  # type: ignore[operator]
    async def resynthesize_all_tts(self):
        """Re-synthesize TTS for all assistant messages (background event —
        gleiche Struktur wie resynthesize_bubble_tts: Lock nur für kurze
        Lese-/Schreib-Phasen, Synthesen lock-frei, Fortschritt pro Bubble
        fließt live in die Debug-Messages)."""
        from ..lib.tts_engine_manager import GPU_ENGINES, ensure_engine_ready

        engine = ""
        assistant_indices: list[int] = []
        async with self:
            if self.tts_regenerating:
                return

            _ch = self._chat_sub()
            if not _ch.chat_history:
                self.add_debug("⚠️ TTS Re-Synth: No chat history available")  # type: ignore[attr-defined]
                return

            assistant_indices = [
                i for i, msg in enumerate(_ch.chat_history)
                if msg.get("role") == "assistant"
            ]
            if not assistant_indices:
                self.add_debug("⚠️ TTS Re-Synth: No assistant messages found")  # type: ignore[attr-defined]
                return

            self.tts_regenerating = True
            engine = str(self.tts_engine)  # type: ignore[attr-defined]
            yield rx.call_script("stopTts()")  # type: ignore[misc]
            if engine in GPU_ENGINES:
                self.add_debug(f"🔄 TTS Re-Synth (alle): Starte {engine.upper()} Backend...")  # type: ignore[attr-defined]

        # Auto-start TTS backend if not running — single dispatch via SSOT.
        # to_thread: siehe resynthesize_bubble_tts.
        if engine in GPU_ENGINES:
            ok, msg_txt, _device = await asyncio.to_thread(ensure_engine_ready, engine)
        else:
            ok, msg_txt = True, "OK"

        if not ok:
            async with self:
                self.add_debug(f"❌ TTS Re-Synth: {msg_txt}")  # type: ignore[attr-defined]
                self.tts_regenerating = False
            return

        total = len(assistant_indices)
        async with self:
            self.add_debug(f"🔄 TTS Re-Synth: Regenerating all {total} bubbles...")  # type: ignore[attr-defined]

        try:
            success_count = 0
            failed_bubbles = []
            for i, bubble_idx in enumerate(assistant_indices):
                async with self:
                    self.add_debug(f"🔄 Processing bubble {i+1}/{total}...")  # type: ignore[attr-defined]
                    request = self._extract_bubble_tts_request(bubble_idx)

                session_audio_url = (
                    await self._synthesize_bubble_audio(request)
                    if request is not None else None
                )

                applied = False
                if session_audio_url and request is not None:
                    async with self:
                        # Session erst am Ende EINMAL speichern (wie zuvor).
                        applied = self._apply_bubble_audio(
                            bubble_idx, request["timestamp"],
                            session_audio_url, save_session=False,
                        )
                if applied:
                    success_count += 1
                else:
                    failed_bubbles.append(i + 1)
                    async with self:
                        self.add_debug(f"⚠️ Bubble {i+1}/{total} failed (chat index {bubble_idx})")  # type: ignore[attr-defined]

            async with self:
                # Save session once after all regenerations.
                self._save_current_session()  # type: ignore[attr-defined]
                if failed_bubbles:
                    self.add_debug(f"⚠️ TTS: {success_count}/{total} bubbles regenerated — failed: {failed_bubbles}")  # type: ignore[attr-defined]
                else:
                    self.add_debug(f"✅ TTS: {success_count}/{total} bubbles regenerated")  # type: ignore[attr-defined]

        except (FileNotFoundError, ValueError, RuntimeError) as e:
            async with self:
                self.add_debug(f"❌ TTS Error: {e}")  # type: ignore[attr-defined]
            log_message(f"❌ TTS regeneration error: {e}")
        finally:
            async with self:
                self.tts_regenerating = False
