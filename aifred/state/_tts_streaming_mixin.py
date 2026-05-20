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


# Module-level storage for DashScope WebSocket TTS instances (keyed by session_id).
# Cannot be stored in Reflex state because WebSocket/SSLSocket objects are not serializable.
_dashscope_rt_instances: dict[str, Any] = {}


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
    """Drop the per-session TTS backend state on logout / session delete.

    Best-effort cleanup; mirrors discard_dashscope_runtime() so the two
    per-session module-level stores share a single lifecycle.
    """
    if not session_id:
        return
    _tts_backend_states.pop(session_id, None)


def discard_dashscope_runtime(session_id: str) -> None:
    """Close and drop the per-session DashScope WebSocket instance.

    Called on logout / delete_session so dangling sockets don't pile up
    across the process lifetime.
    """
    if not session_id:
        return
    inst = _dashscope_rt_instances.pop(session_id, None)
    if inst is None:
        return
    for attr in ("close", "stop", "disconnect"):
        fn = getattr(inst, attr, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                # Best-effort cleanup; never let logout fail because of a
                # half-dead socket.
                pass
            return


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
    _tts_in_think_block: bool = False  # True when inside <think>...</think> block
    _tts_streaming_active: bool = False  # True during active streaming session
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

    # ── TTS Callback ──────────────────────────────────────────────────

    def handle_tts_callback(self, result: str):
        """Callback nach TTS rx.call_script() Ausführung.

        Wird aufgerufen wenn das JavaScript das TTS-Script ausgeführt hat.
        Dient hauptsächlich zum Debugging.
        """
        self.add_debug(f"🔊 TTS callback received: {result}")  # type: ignore[attr-defined]

    # ── TTS Generation (Full Response) ────────────────────────────────

    async def _generate_tts_for_response(self, ai_response: str, autoplay: bool = True, agent: str = "aifred"):
        """Generate TTS audio for AI response and store path for playback

        Args:
            ai_response: The AI response text to convert to speech
            autoplay: If True, set autoplay flag (respects user setting). If False, never autoplay.
            agent: Agent name for per-agent voice settings (aifred, sokrates, salomo)

        Note: This is a simple async function, NOT a generator. State updates happen directly.
        """
        try:
            from ..lib.audio_processing import (
                clean_text_for_tts,
                generate_tts,
                reset_content_hint_flags,
                set_tts_agent,
            )

            # Set agent name for audio filename prefixing
            set_tts_agent(agent)

            # Reset content-hint flags so this response starts clean.
            reset_content_hint_flags()

            # Clean text: Remove <think> tags, emojis, markdown, URLs, timing info
            clean_text = clean_text_for_tts(ai_response)

            if not clean_text or len(clean_text.strip()) < 5:
                self.add_debug("🔇 TTS: Text too short after cleanup")  # type: ignore[attr-defined]
                return

            self.add_debug(f"🔊 TTS: Generating audio ({len(clean_text)} chars)...")  # type: ignore[attr-defined]

            # Determine voice, pitch, and speed based on agent settings (generic for all engines)
            voice_choice = self.tts_voice  # type: ignore[attr-defined]
            pitch_value = float(self.tts_pitch) if self.tts_pitch else 1.0  # type: ignore[attr-defined]
            speed_value = 1.0  # Default speed

            # Use per-agent settings if configured (works with all TTS engines)
            if agent in self.tts_agent_voices:  # type: ignore[attr-defined]
                agent_settings = self.tts_agent_voices[agent]  # type: ignore[attr-defined]
                agent_voice = agent_settings.get("voice", "")
                agent_pitch = agent_settings.get("pitch", "")
                agent_speed = agent_settings.get("speed", "")

                if agent_voice:
                    voice_choice = agent_voice
                    self.add_debug(f"🎭 Using {agent}'s voice: {voice_choice}")  # type: ignore[attr-defined]
                if agent_pitch:
                    try:
                        pitch_value = float(agent_pitch)
                    except ValueError:
                        pass
                if agent_speed:
                    try:
                        # Parse speed like "1.1x" -> 1.1
                        speed_value = float(agent_speed.replace("x", ""))
                    except ValueError:
                        pass

            # Generate TTS audio (returns URL path like "/tts_audio/audio_123.mp3")
            # Per-agent speed is applied at generation time (different from browser playback rate)
            # Pitch adjustment is applied via ffmpeg post-processing
            tts_language = self._last_detected_language or self.ui_language  # type: ignore[attr-defined]
            audio_url = await generate_tts(
                text=clean_text,
                voice_choice=voice_choice,
                speed_choice=speed_value,
                tts_engine=self.tts_engine,  # type: ignore[attr-defined]
                pitch=pitch_value,
                language=tts_language
            )

            if audio_url:
                # Verify file exists on disk (convert URL to filesystem path)
                # URL: /_upload/tts_audio/audio_123.mp3 -> data/tts_audio/audio_123.mp3
                from ..lib.config import DATA_DIR
                filename = audio_url.split("/")[-1]
                file_path = DATA_DIR / "tts_audio" / filename

                if os.path.exists(file_path):
                    # Set browser playback rate from agent speed setting
                    self.tts_playback_rate = "1.0x"  # type: ignore[attr-defined]  # Speed is baked into audio via engine or ffmpeg
                    self.add_debug(f"🔊 TTS: Playback rate set to {speed_value}x")  # type: ignore[attr-defined]
                    # Store audio URL for playback (use temporary URL for autoplay)
                    self.tts_audio_path = audio_url
                    # Increment counter to trigger frontend playback via rx.use_effect
                    self.tts_trigger_counter += 1
                    file_size_kb = os.path.getsize(file_path) / 1024
                    self.add_debug(f"✅ TTS: Audio generated ({file_size_kb:.1f} KB) → {audio_url}")  # type: ignore[attr-defined]
                    self.add_debug(f"🔊 TTS: Trigger counter incremented to {self.tts_trigger_counter}")  # type: ignore[attr-defined]

                    # Save to session directory for permanent storage (replay button)
                    from ..lib.audio_processing import save_audio_to_session
                    session_audio_url = save_audio_to_session([audio_url], self.session_id)  # type: ignore[attr-defined]
                    if session_audio_url:
                        log_message(f"🔊 TTS: Saved to session → {session_audio_url}")

                        # Update last assistant message with session audio URL (for replay button).
                        # Rebuild the entry deep so Reflex registers the nested change.
                        _ch = self._chat_sub()
                        if _ch.chat_history:
                            new_history = list(_ch.chat_history)
                            for i in range(len(new_history) - 1, -1, -1):
                                if new_history[i].get("role") == "assistant":
                                    prev = new_history[i]
                                    new_metadata = dict(prev.get("metadata") or {})
                                    new_metadata["audio_urls"] = [session_audio_url]
                                    new_history[i] = {
                                        **prev,
                                        "metadata": new_metadata,
                                        "has_audio": True,
                                        "audio_urls_json": json.dumps([session_audio_url]),
                                    }
                                    log_message("🔊 TTS: Added audio URL to message metadata")
                                    break
                            _ch.chat_history = new_history
                            self._save_current_session()  # type: ignore[attr-defined]
                    else:
                        log_message("⚠️ TTS: Failed to save audio to session")

                    # Separator nach TTS-Ausgabe (Log-File + Debug-Konsole)
                    from aifred.lib.logging_utils import console_separator
                    console_separator()  # Schreibt in Log-File
                    self.add_debug("────────────────────")  # type: ignore[attr-defined]  # Zeigt in Debug-Console
                else:
                    self.tts_audio_path = ""
                    self.add_debug(f"⚠️ TTS: Audio file not found at {file_path}")  # type: ignore[attr-defined]
            else:
                self.tts_audio_path = ""
                self.add_debug("⚠️ TTS: Audio generation failed")  # type: ignore[attr-defined]

        except (FileNotFoundError, ValueError, RuntimeError) as e:
            self.add_debug(f"❌ TTS Error: {e}")  # type: ignore[attr-defined]
            log_message(f"❌ TTS generation error: {e}")

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

            # Determine voice, pitch, and speed based on agent settings.
            # Fallback: AIfred's voice for the current engine (always configured).
            aifred_settings = self.tts_agent_voices.get("aifred", {})  # type: ignore[attr-defined]
            voice_choice = aifred_settings.get("voice", "") or self.tts_voice  # type: ignore[attr-defined]
            pitch_value = float(self.tts_pitch) if self.tts_pitch else 1.0  # type: ignore[attr-defined]
            speed_value = 1.0

            # Use per-agent settings if configured
            if agent in self.tts_agent_voices:  # type: ignore[attr-defined]
                agent_settings = self.tts_agent_voices[agent]  # type: ignore[attr-defined]
                agent_voice = agent_settings.get("voice", "")
                agent_pitch = agent_settings.get("pitch", "")
                agent_speed = agent_settings.get("speed", "")

                if agent_voice:
                    voice_choice = agent_voice
                if agent_pitch:
                    try:
                        pitch_value = float(agent_pitch)
                    except ValueError:
                        pass
                if agent_speed:
                    try:
                        speed_value = float(agent_speed.replace("x", ""))
                    except ValueError:
                        pass

            # Set agent name for audio filename prefixing
            set_tts_agent(agent)

            # Generate TTS audio
            tts_language = self._last_detected_language or self.ui_language  # type: ignore[attr-defined]
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
        self._tts_in_think_block = False
        self._tts_streaming_active = True
        self._tts_streaming_agent = agent

        # Reset backend tracking (module-level, not in Reflex state).
        tts_state = get_tts_backend_state(self.session_id)  # type: ignore[attr-defined]
        tts_state.pending_requests = []
        tts_state.completed_urls = {}
        tts_state.order_buffer = {}
        tts_state.next_seq = 0
        tts_state.push_seq = 0

        # DashScope: Use sentence-level streaming (same as XTTS/Edge/Piper).
        # Better intonation per sentence, no chunk gaps.
        #
        # To re-enable realtime WebSocket streaming (word-level chunks, ~3s batches):
        # Uncomment the block below. This feeds tokens directly into the DashScope
        # WebSocket for immediate audio output during LLM inference, but produces
        # small audible gaps between chunks and slightly worse prosody.
        #
        # if self.tts_engine == "dashscope":
        #     self._init_dashscope_realtime(agent)
        # else:
        #     _dashscope_rt_instances.pop(self.session_id, None)
        _dashscope_rt_instances.pop(self.session_id, None)  # type: ignore[attr-defined]

        log_message("🔊 TTS Init: State initialized, ready for chunks")

    def _init_dashscope_realtime(self, agent: str) -> None:
        """Open DashScope WebSocket connection for realtime TTS streaming."""
        from ..lib.audio_processing import DashScopeRealtimeTTS

        # Resolve voice for this agent
        voice_choice = self.tts_voice  # type: ignore[attr-defined]
        speed_value = 1.0
        tts_agent_voices = dict(self.tts_agent_voices)  # type: ignore[attr-defined]
        if agent in tts_agent_voices:
            agent_settings = tts_agent_voices[agent]
            if agent_settings.get("voice"):
                voice_choice = agent_settings["voice"]
            agent_speed = agent_settings.get("speed", "")
            if agent_speed:
                try:
                    speed_value = float(agent_speed.replace("x", ""))
                except ValueError:
                    pass

        # Strip ★ prefix (same as generate_tts() does centrally)
        if voice_choice.startswith("★ "):
            voice_choice = voice_choice[2:]

        log_message(f"🔊 DashScope RT Init: Opening WebSocket for voice={voice_choice}, agent={agent}")
        self.add_debug(f"🎤 TTS: DashScope Realtime WebSocket → {voice_choice}")  # type: ignore[attr-defined]
        rt_tts = DashScopeRealtimeTTS(
            voice_choice=voice_choice,
            session_id=self.session_id,  # type: ignore[attr-defined]
            agent=agent,
            speed=speed_value,
        )
        _dashscope_rt_instances[self.session_id] = rt_tts  # type: ignore[attr-defined]
        # Connect in background thread - doesn't block event loop
        # This allows the yield to happen immediately so the browser can set up SSE
        # TTFT is ~3s, so WebSocket connect (~0.6s) will be done before first token
        import threading
        threading.Thread(target=rt_tts.connect, daemon=True).start()

    async def _finalize_streaming_tts(self) -> list[str]:
        """Wait for TTS tasks to complete and return combined audio URL.

        DashScope Realtime: Calls finish() on WebSocket, waits for final audio.
        Other engines: Waits for parallel create_task() TTS tasks to complete.

        Returns:
            List with single combined audio URL, or empty list if no audio
        """
        if not self._tts_streaming_active:
            log_message("🔊 TTS Finalize: Not active, skipping")
            return []

        # DashScope Realtime: Finish WebSocket and get combined WAV
        if self.session_id in _dashscope_rt_instances:  # type: ignore[attr-defined]
            return await self._finalize_dashscope_realtime()

        # --- Other engines: Sentence-based parallel TTS ---

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
            asyncio.create_task(self._tts_generate_sentence_async(
                final_text, agent, request_id, self.session_id, seq  # type: ignore[attr-defined]
            ))

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

        # Reset streaming state
        self._tts_sentence_buffer = ""
        self._tts_in_think_block = False
        self._tts_streaming_active = False
        tts_state.pending_requests = []
        tts_state.completed_urls = {}
        self._pending_audio_urls = []
        log_message("🔊 TTS Finalize: State reset complete")

        return [combined_url] if combined_url else []

    async def _finalize_streaming_tts_in_background(self, agent: str, text_snippet: str) -> None:
        """Fire-and-forget finalize: wait for all pending TTS tasks in the
        background, then patch the resulting combined-URL onto the bubble
        that has just been added to chat_history.

        The streaming TTS chunks are already on the way to the browser via
        audio_queue_push() in _drain_tts_order_buffer — this method only
        handles the "after-the-fact" combined-WAV save for replay/export.
        Running it as a create_task means the multi_agent stream generator
        can yield immediately, the bubble renders complete (text + sources
        + sandbox), and AIfred is ready for the next prompt right away.

        We identify the right bubble by scanning chat_history for the most
        recent assistant message with a matching agent + text-prefix; the
        chat is single-user/single-conversation so the most recent match
        is always the one we just emitted.
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
                # Light sanity check on content prefix so we don't accidentally
                # patch an older bubble that happens to share the agent name.
                content = msg.get("content", "") or ""
                if text_snippet and text_snippet[:40] not in content:
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

            # Persist so a session reload still has the audio URL.
            try:
                self._save_current_session()  # type: ignore[attr-defined]
            except Exception as e:
                log_message(f"🔊 TTS Background: session save failed: {e}")
        except Exception as e:
            log_message(f"🔊 TTS Background: ❌ Bubble patch failed: {e}")

    async def _finalize_dashscope_realtime(self) -> list[str]:
        """Finalize DashScope WebSocket streaming and return combined WAV URL.

        Audio batches (sentence-aligned) are pushed to the browser during synthesis
        via _flush_push_buffer(). This method waits for the final audio, saves the
        combined WAV to the session for re-play, and cleans up.
        """
        from ..lib.audio_processing import save_audio_to_session

        rt_tts = _dashscope_rt_instances.pop(self.session_id, None)  # type: ignore[attr-defined]
        if not rt_tts:
            log_message("🔊 DashScope RT Finalize: No active WebSocket for this session")
            return []

        log_message("🔊 DashScope RT Finalize: Finishing WebSocket stream...")
        self.add_debug("🎤 TTS: Waiting for remaining audio...")  # type: ignore[attr-defined]

        # finish() flushes remaining text, signals end, waits for remaining audio, saves WAV
        # Audio batches (sentence-aligned) are already pushed to browser during synthesis
        combined_url = await rt_tts.finish()

        # Save combined WAV to session directory (permanent storage for re-play)
        session_url: str | None = None
        if combined_url:
            session_url = save_audio_to_session([combined_url], self.session_id)  # type: ignore[attr-defined]
            if session_url:
                log_message(f"🔊 DashScope RT Finalize: Saved to session → {session_url}")

        self.add_debug(f"🎤 TTS: Streaming done ({rt_tts._push_count} chunks)")  # type: ignore[attr-defined]

        # Cleanup WebSocket
        rt_tts.close()

        # Reset streaming state
        self._tts_sentence_buffer = ""
        self._tts_in_think_block = False
        self._tts_streaming_active = False
        tts_state = get_tts_backend_state(self.session_id)  # type: ignore[attr-defined]
        tts_state.pending_requests = []
        tts_state.completed_urls = {}
        self._pending_audio_urls = []
        log_message("🔊 DashScope RT Finalize: State reset complete")

        return [session_url] if session_url else []

    def _process_streaming_tts_chunk(self, chunk: str) -> None:
        """Process a streaming chunk for TTS.

        This is called for each content chunk during LLM streaming.

        DashScope: Feeds tokens directly into WebSocket (natural ordering).
        Other engines: Extracts sentences and generates TTS in parallel via create_task().

        Args:
            chunk: Text chunk from LLM streaming
        """
        if not self._tts_streaming_active or not self.enable_tts or not self.tts_streaming_enabled:  # type: ignore[attr-defined]
            return

        from ..lib.audio_processing import (
            extract_complete_sentences,
            strip_think_content_streaming,
        )

        # Add chunk to buffer (used for think-block detection in all modes)
        self._tts_sentence_buffer += chunk
        log_message(f"🔊 TTS Chunk: +{len(chunk)} chars, buffer now {len(self._tts_sentence_buffer)} chars")

        # Check for <think> blocks - don't process content inside them
        if "<think>" in self._tts_sentence_buffer.lower():
            self._tts_in_think_block = True
            log_message("🔊 TTS Chunk: Entered <think> block")
        if "</think>" in self._tts_sentence_buffer.lower():
            self._tts_in_think_block = False
            log_message("🔊 TTS Chunk: Exited </think> block")
            self._tts_sentence_buffer = strip_think_content_streaming(self._tts_sentence_buffer)
            log_message(f"🔊 TTS Chunk: After strip, buffer now {len(self._tts_sentence_buffer)} chars")

        # Don't process content while inside think block
        if self._tts_in_think_block:
            log_message("🔊 TTS Chunk: Inside think block, waiting...")
            return

        # DashScope Realtime: Feed raw tokens into WebSocket buffer
        # Cleaning happens inside DashScopeRealtimeTTS on the accumulated buffer
        # (clean_text_for_tts needs complete text, not individual tokens)
        rt_tts = _dashscope_rt_instances.get(self.session_id)  # type: ignore[attr-defined]
        if rt_tts is not None:
            rt_tts.append_text(chunk)
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
            asyncio.create_task(self._tts_generate_sentence_async(sentence, agent, request_id, session_id, seq))

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

            # Read voice settings (snapshot for this task)
            voice_choice = self.tts_voice  # type: ignore[attr-defined]
            pitch_value = float(self.tts_pitch) if self.tts_pitch else 1.0  # type: ignore[attr-defined]
            speed_value = 1.0
            tts_engine = self.tts_engine  # type: ignore[attr-defined]
            tts_agent_voices = dict(self.tts_agent_voices)  # type: ignore[attr-defined]  # Copy to avoid issues

            if agent in tts_agent_voices:
                agent_settings = tts_agent_voices[agent]
                agent_voice = agent_settings.get("voice", "")
                agent_pitch = agent_settings.get("pitch", "")
                agent_speed = agent_settings.get("speed", "")

                if agent_voice:
                    voice_choice = agent_voice
                if agent_pitch:
                    try:
                        pitch_value = float(agent_pitch)
                    except ValueError:
                        pass
                if agent_speed:
                    try:
                        speed_value = float(agent_speed.replace("x", ""))
                    except ValueError:
                        pass

            # Generate TTS audio (this is the slow part - runs in parallel)
            tts_language = self._last_detected_language or self.ui_language  # type: ignore[attr-defined]
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
        from ..lib.api import audio_queue_push

        tts_state = get_tts_backend_state(session_id)

        # Auto-pause media playback before the first TTS chunk goes to the
        # browser. Position is saved by the browser via /api/audio/position
        # on the source-switch — here we just flip the resume flag so the
        # player picks media back up after the TTS queue drains.
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
                audio_queue_push(session_id, "tts", audio_url, playback_rate=playback_rate)
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

    async def _regenerate_bubble_tts_core(self, bubble_index: int, save_session: bool = True) -> bool:
        """Core TTS regeneration logic for a single bubble.

        Args:
            bubble_index: Index of the bubble in chat_history
            save_session: Whether to save session after regeneration (False when called from resynthesize_all)

        Returns:
            True if successful, False otherwise
        """
        from ..lib.audio_processing import clean_text_for_tts, generate_tts, set_tts_agent, save_audio_to_session

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
            return False

        set_tts_agent(agent)
        # llm_history has format "[AGENT]: content" - remove the label
        content_without_label = re.sub(r'^\[(AIFRED|SOKRATES|SALOMO)\]:\s*', '', llm_content, flags=re.IGNORECASE)
        clean_text = clean_text_for_tts(content_without_label)

        if not clean_text or len(clean_text.strip()) < 5:
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} text too short after cleanup")
            return False

        # Get agent voice settings
        voice_choice = self.tts_voice  # type: ignore[attr-defined]
        pitch_value = float(self.tts_pitch) if self.tts_pitch else 1.0  # type: ignore[attr-defined]
        speed_value = 1.0

        if agent in self.tts_agent_voices:  # type: ignore[attr-defined]
            agent_settings = self.tts_agent_voices[agent]  # type: ignore[attr-defined]
            if agent_settings.get("voice"):
                voice_choice = agent_settings["voice"]
            if agent_settings.get("pitch"):
                try:
                    pitch_value = float(agent_settings["pitch"])
                except ValueError:
                    pass
            if agent_settings.get("speed"):
                try:
                    speed_value = float(agent_settings["speed"].replace("x", ""))
                except ValueError:
                    pass

        # Generate TTS (complete bubble at once for best quality)
        tts_language = self._last_detected_language or self.ui_language  # type: ignore[attr-defined]
        audio_url = await generate_tts(
            text=clean_text,
            voice_choice=voice_choice,
            speed_choice=speed_value,
            tts_engine=self.tts_engine,  # type: ignore[attr-defined]
            pitch=pitch_value,
            language=tts_language
        )

        if not audio_url:
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} audio generation failed")
            return False

        # Save to session directory for permanent storage
        session_audio_url = save_audio_to_session([audio_url], self.session_id)  # type: ignore[attr-defined]
        if not session_audio_url:
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} failed to save to session")
            return False

        log_message(f"🔊 TTS: Bubble {bubble_index} saved → {session_audio_url}")

        # Update message with new audio URL — rebuild the bubble entry deep
        # so Reflex registers the change at the list level.
        new_history = list(_ch.chat_history)
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

    async def resynthesize_bubble_tts(self, timestamp: str):
        """Re-synthesize TTS for a specific chat bubble.

        Args:
            timestamp: Timestamp of the message to regenerate
        """
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
        yield rx.call_script("stopTts()")  # type: ignore[misc]

        # Auto-start TTS backend if not running
        if self.tts_engine == "xtts":  # type: ignore[attr-defined]
            self.add_debug("🔄 TTS Re-Synth: Starte XTTS Backend...")  # type: ignore[attr-defined]
            yield  # type: ignore[misc]
            from ..lib.process_utils import ensure_xtts_ready
            ok, tts_msg = ensure_xtts_ready()
        elif self.tts_engine == "moss":  # type: ignore[attr-defined]
            self.add_debug("🔄 TTS Re-Synth: Starte MOSS-TTS Backend...")  # type: ignore[attr-defined]
            yield  # type: ignore[misc]
            from ..lib.process_utils import ensure_moss_ready
            ok, tts_msg, _device = ensure_moss_ready()
        elif self.tts_engine == "qwen3local":  # type: ignore[attr-defined]
            self.add_debug("🔄 TTS Re-Synth: Starte Qwen3-TTS Backend...")  # type: ignore[attr-defined]
            yield  # type: ignore[misc]
            from ..lib.process_utils import ensure_qwen3local_ready
            ok, tts_msg, _device = ensure_qwen3local_ready()
        else:
            ok, tts_msg = True, "OK"

        if not ok:
            self.add_debug(f"❌ TTS Re-Synth: {tts_msg}")  # type: ignore[attr-defined]
            self.tts_regenerating = False
            return

        agent = _ch.chat_history[bubble_index].get("agent", "aifred")
        self.add_debug(f"🔄 TTS Re-Synth: Regenerating bubble {bubble_index} ({agent})...")  # type: ignore[attr-defined]
        yield  # type: ignore[misc]

        try:
            success = await self._regenerate_bubble_tts_core(bubble_index, save_session=True)
            if success:
                self.add_debug(f"✅ TTS: Bubble {bubble_index} regenerated")  # type: ignore[attr-defined]
            else:
                self.add_debug(f"⚠️ TTS: Bubble {bubble_index} regeneration failed")  # type: ignore[attr-defined]
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            self.add_debug(f"❌ TTS Error: {e}")  # type: ignore[attr-defined]
            log_message(f"❌ TTS regeneration error: {e}")
        finally:
            self.tts_regenerating = False

    async def resynthesize_all_tts(self):
        """Re-synthesize TTS for all assistant messages in chat history."""
        if self.tts_regenerating:
            return

        _ch = self._chat_sub()
        if not _ch.chat_history:
            self.add_debug("⚠️ TTS Re-Synth: No chat history available")  # type: ignore[attr-defined]
            return

        assistant_indices = [i for i, msg in enumerate(_ch.chat_history) if msg.get("role") == "assistant"]
        if not assistant_indices:
            self.add_debug("⚠️ TTS Re-Synth: No assistant messages found")  # type: ignore[attr-defined]
            return

        self.tts_regenerating = True
        yield rx.call_script("stopTts()")  # type: ignore[misc]

        # Auto-start TTS backend if not running
        if self.tts_engine == "xtts":  # type: ignore[attr-defined]
            self.add_debug("🔄 TTS Re-Synth (alle): Starte XTTS Backend...")  # type: ignore[attr-defined]
            yield  # type: ignore[misc]
            from ..lib.process_utils import ensure_xtts_ready
            ok, msg = ensure_xtts_ready()
        elif self.tts_engine == "moss":  # type: ignore[attr-defined]
            self.add_debug("🔄 TTS Re-Synth (alle): Starte MOSS-TTS Backend...")  # type: ignore[attr-defined]
            yield  # type: ignore[misc]
            from ..lib.process_utils import ensure_moss_ready
            ok, msg, _device = ensure_moss_ready()
        elif self.tts_engine == "qwen3local":  # type: ignore[attr-defined]
            self.add_debug("🔄 TTS Re-Synth (alle): Starte Qwen3-TTS Backend...")  # type: ignore[attr-defined]
            yield  # type: ignore[misc]
            from ..lib.process_utils import ensure_qwen3local_ready
            ok, msg, _device = ensure_qwen3local_ready()
        else:
            ok, msg = True, "OK"

        if not ok:
            self.add_debug(f"❌ TTS Re-Synth: {msg}")  # type: ignore[attr-defined]
            self.tts_regenerating = False
            return

        self.add_debug(f"🔄 TTS Re-Synth: Regenerating all {len(assistant_indices)} bubbles...")  # type: ignore[attr-defined]
        yield  # type: ignore[misc]

        try:
            success_count = 0
            failed_bubbles = []
            for i, bubble_idx in enumerate(assistant_indices):
                self.add_debug(f"🔄 Processing bubble {i+1}/{len(assistant_indices)}...")  # type: ignore[attr-defined]
                yield  # type: ignore[misc]

                # Use core method (don't save session after each - save once at end)
                success = await self._regenerate_bubble_tts_core(bubble_idx, save_session=False)
                if success:
                    success_count += 1
                else:
                    failed_bubbles.append(i + 1)
                    self.add_debug(f"⚠️ Bubble {i+1}/{len(assistant_indices)} failed (chat index {bubble_idx})")  # type: ignore[attr-defined]

            # Save session once after all regenerations.
            # Each _regenerate_bubble_tts_core() already reassigned chat_history,
            # so no extra trigger needed here.
            self._save_current_session()  # type: ignore[attr-defined]

            if failed_bubbles:
                self.add_debug(f"⚠️ TTS: {success_count}/{len(assistant_indices)} bubbles regenerated — failed: {failed_bubbles}")  # type: ignore[attr-defined]
            else:
                self.add_debug(f"✅ TTS: {success_count}/{len(assistant_indices)} bubbles regenerated")  # type: ignore[attr-defined]

        except (FileNotFoundError, ValueError, RuntimeError) as e:
            self.add_debug(f"❌ TTS Error: {e}")  # type: ignore[attr-defined]
            log_message(f"❌ TTS regeneration error: {e}")
        finally:
            self.tts_regenerating = False
