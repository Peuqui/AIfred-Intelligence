"""TTS streaming mixin for AIfred state.

The spoken reply in a browser window: while the language model writes, the text goes to a
``BrowserReading`` (aifred/lib/browser_reading.py), which reads it through the TTS service and
puts what comes back on the browser's playlist. This mixin only connects that reading to the
state: start at the beginning of a reply, feed the text, finish at the end, abort when the reply
or the page is left, and keep what was spoken on the reply's bubble. It also re-synthesizes
existing bubbles.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, List

import reflex as rx

from ..lib.browser_reading import BrowserReading
from ..lib.logging_utils import log_message
from ..lib.tts_client import TTSSuiteError
from ._tts_config_mixin import _lang


@dataclass
class TTSBackendState:
    """Per-session backend coordination state of the spoken reply.

    Held outside the Reflex state because none of these fields drive UI: they only coordinate the
    reading, which runs in its own task, with the finalize wait. Mutations here would bump the state
    version and force the chat bubble to re-render.
    """
    reading: BrowserReading | None = None  # reading of the reply being spoken
    # Background finalize of the previous reply: the next reply must not start speaking before
    # the previous one is complete (their audio would interleave on the playlist).
    finalize_task: asyncio.Task[None] | None = None
    # Messages for the user (kind, text) that a task outside any event wants shown; the periodic
    # refresh (refresh_debug_console) turns them into toasts.
    notices: list[tuple[str, str]] = field(default_factory=list)


_tts_backend_states: dict[str, TTSBackendState] = {}


def get_tts_backend_state(session_id: str) -> TTSBackendState:
    """Get (creating on first access) the per-session TTS backend state."""
    state = _tts_backend_states.get(session_id)
    if state is None:
        state = TTSBackendState()
        _tts_backend_states[session_id] = state
    return state


def abort_tts_reading(session_id: str) -> None:
    """End the reading of this session now (page left, reply cancelled): its stream is closed, so
    the service stops speaking."""
    state = _tts_backend_states.get(session_id)
    if state is not None and state.reading is not None:
        state.reading.abort()


def discard_tts_backend_state(session_id: str) -> None:
    """Drop the per-session TTS backend state on logout / session delete (ends a running reading)."""
    if not session_id:
        return
    abort_tts_reading(session_id)
    _tts_backend_states.pop(session_id, None)


def take_tts_notices(session_id: str) -> list[tuple[str, str]]:
    """The messages waiting for this session, removed from the list."""
    state = _tts_backend_states.get(session_id)
    if state is None:
        return []
    notices, state.notices = state.notices, []
    return notices


class TTSStreamingMixin(rx.State, mixin=True):
    """Mixin for the spoken reply: reading, playlist, bubble audio and re-synthesis."""

    # ── State Variables ──────────────────────────────────────────────

    # TTS Audio Output
    tts_audio_path: str = ""  # Path to generated TTS audio file
    tts_trigger_counter: int = 0  # Incremented to trigger TTS playback in frontend

    # TTS Audio Queue - for sequential playback of multiple agent responses
    # Frontend plays queue items sequentially (first in, first out)
    tts_audio_queue: List[str] = []  # Queue of audio URLs to play
    tts_queue_version: int = 0  # Incremented when queue changes (triggers frontend update)

    _tts_streaming_active: bool = False  # True from the start of a reply's reading until its finalize is done
    _tts_finalize_spawned: bool = False  # background finalize already started for this init
    _tts_streaming_agent: str = "aifred"  # Current agent for voice selection (aifred/sokrates/salomo)
    _pending_audio_urls: List[str] = []  # Audio URLs collected during streaming, for message assignment

    # TTS Regeneration
    tts_regenerating: bool = False  # True while TTS regeneration is in progress (for spinner)

    # NOTE: the reading and its coordination live in a module-level TTSBackendState (see
    # get_tts_backend_state), not in Reflex state attributes, so the per-chunk work during
    # streaming does not re-render the chat bubble.

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

        True from _init_streaming_tts() until the reading is over and its audio is kept
        on the bubble. The frontend uses this as the authoritative "more TTS may still
        arrive" signal — empty tts_audio_queue alone is not enough (it's transiently empty
        between chunks during streaming, which made audioOnEnded resume media too early
        and chop off the last sentence).
        """
        return bool(self._tts_streaming_active)

    # ── TTS Helpers ──────────────────────────────────────────────────

    def _resolve_tts_language(self, agent: str) -> str:
        """Pick the synthesis language for an agent.

        Order of precedence:
          1. Per-agent override (tts_agents[agent]["language"]),
             skipped when empty or "auto".
          2. Language the LLM detected from the user prompt.
          3. UI language.

        Returns a two-letter ISO code ("de", "en", "zh", …) — the language of the
        reading request.
        """
        override = str(self.tts_agents.get(agent, {}).get("language", "") or "").lower()  # type: ignore[attr-defined]
        if override and override != "auto":
            return override
        return str(self._last_detected_language or self.ui_language)  # type: ignore[attr-defined]

    def clear_tts_queue(self) -> None:
        """Clear the TTS audio queue (called when starting new message)."""
        if self.tts_audio_queue:
            self.tts_audio_queue = []
            self.tts_queue_version += 1
            self.add_debug("🔊 TTS Queue: Cleared")  # type: ignore[attr-defined]

    # ============================================================
    # THE READING OF A REPLY
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

        The chunk also goes to the reply's reading while one is active.

        Returns:
            True when state was updated and caller should yield.
        """
        import time

        self._js_chunk_buffer += chunk

        if self._tts_streaming_active:
            reading = get_tts_backend_state(self.session_id).reading  # type: ignore[attr-defined]
            if reading is not None:
                reading.feed(chunk)

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
        """SSOT for "should this reply be spoken while it is written?".

        Global TTS on, autoplay on (no consumer otherwise), and the per-agent voice not disabled.
        How much the service reads at once (sentence, paragraph, whole) is its unit's business,
        not a reason to read the reply in another way.
        """
        if not (self.enable_tts and self.tts_autoplay):  # type: ignore[attr-defined]
            return False
        return self.tts_agent_enabled(agent)  # type: ignore[attr-defined, no-any-return]

    def _pause_media_for_tts(self) -> None:
        """Auto-pause media playback before the first TTS entry goes to the browser. Position is
        saved by the browser via /api/audio/position on the source-switch — here we just flip the
        resume flag so the player picks media back up after the TTS queue drains.

        KNOWN QUIRK (reviewed 2026-07-16, deliberately kept): this write happens inside the
        reading's task, outside Reflex's event system — Reflex never pushes the delta to the browser.
        It works anyway because audio_channels/browser.py reads the flag server-side via getattr on
        every position poll. If media pause/resume is ever reworked, route this through the API
        queue like the audio URLs.
        """
        if getattr(self, "media_audio_url", "") != "" and not getattr(self, "media_paused_for_tts", False):
            self.media_paused_for_tts = True  # type: ignore[attr-defined]

    def _init_streaming_tts(self, agent: str = "aifred") -> None:
        """Start the reading of a new reply (the text follows via ``stream_text_to_ui``).

        Call this at the start of a reply's stream, after ``_wait_for_tts_finalize``.
        """
        from ..lib.api import browser_push

        log_message(f"🔊 TTS Init: Starting reading for agent={agent}")
        tts_state = get_tts_backend_state(self.session_id)  # type: ignore[attr-defined]
        if tts_state.reading is not None:
            tts_state.reading.abort()  # a reading that is still open would wait for text for ever
        reading = BrowserReading(
            session_id=self.session_id,  # type: ignore[attr-defined]
            agent=agent,
            language=self._resolve_tts_language(agent),
            ui_language=_lang(self),
            push=browser_push,
            report=self.add_debug,  # type: ignore[attr-defined]
            notify=lambda kind, message: tts_state.notices.append((kind, message)),
            on_first_output=self._pause_media_for_tts,
        )
        tts_state.reading = reading
        self._tts_streaming_active = True
        self._tts_finalize_spawned = False
        self._tts_streaming_agent = agent
        reading.start()

    def _abort_streaming_tts(self) -> None:
        """The reply was cancelled (or the client is gone): end its reading, so the service stops."""
        if not self._tts_streaming_active:
            return
        abort_tts_reading(self.session_id)  # type: ignore[attr-defined]
        self._tts_streaming_active = False

    def _spawn_tts_finalize(self) -> None:
        """The reply's text is complete: tell its reading, and keep what was spoken on the bubble
        in the background once the reading is over. Exactly once per streaming init.

        Callable from every exit path of send_message (multi-agent finish, vision fast path,
        pure-command return, exception → finally): the claim flag makes repeated calls a no-op,
        so the finally block can invoke this unconditionally without racing the multi-agent path.
        """
        if not self._tts_streaming_active or self._tts_finalize_spawned:
            return
        self._tts_finalize_spawned = True
        tts_state = get_tts_backend_state(self.session_id)  # type: ignore[attr-defined]
        reading = tts_state.reading
        assert reading is not None, "streaming TTS without a reading"
        reading.finish()
        from ._base import track_orphan_task
        task = asyncio.create_task(self._finalize_streaming_tts_in_background(self._tts_streaming_agent, reading))
        tts_state.finalize_task = task
        track_orphan_task(task)

    async def _wait_for_tts_finalize(self) -> None:
        """Let the previous reply's reading finish before a new reply starts speaking: both put
        their audio on the same playlist, so they would interleave."""
        task = get_tts_backend_state(self.session_id).finalize_task  # type: ignore[attr-defined]
        if task is not None and not task.done():
            log_message("🔊 TTS Init: waiting for the previous reply's reading")
            await task

    async def _keep_reading_audio(self, reading: BrowserReading) -> list[str]:
        """The segments of the reading joined into one audio of the session (replay, export)."""
        if not reading.audio_urls:
            return []
        from ..lib.audio_processing import save_audio_to_session

        if len(reading.audio_urls) > 1:
            self.add_debug(f"🔗 TTS: Combining {len(reading.audio_urls)} audio chunks...")  # type: ignore[attr-defined]
        combined = await asyncio.to_thread(save_audio_to_session, list(reading.audio_urls), self.session_id)  # type: ignore[attr-defined]
        if combined is None:
            self.add_debug(f"⚠️ TTS: Combining {len(reading.audio_urls)} chunks failed")  # type: ignore[attr-defined]
            return []
        if len(reading.audio_urls) > 1:
            self.add_debug(f"🔗 TTS: Combined {len(reading.audio_urls)} chunks → replay ready")  # type: ignore[attr-defined]
        log_message(f"🔊 TTS Finalize: Saved to session → {combined}")
        return [combined]

    async def _finalize_streaming_tts_in_background(self, agent: str, reading: BrowserReading) -> None:
        """Fire-and-forget finalize: wait until the reading is over, then patch what was spoken
        (the joined audio, the sentences the browser spoke itself, who spoke) onto the bubble that
        has just been added to chat_history.

        The audio itself is already on its way to the browser via the push bus — this only
        handles the "after-the-fact" save for replay/export. Running it as a create_task means
        the multi_agent stream generator can yield immediately, the bubble renders complete
        (text + sources + sandbox), and AIfred is ready for the next prompt right away.

        We identify the right bubble as the most recent assistant message from this agent that
        still has no audio — its combined URL is what we are about to attach. No text matching
        against the rendered content, which carries <details> think-blocks for reasoning models
        and would never match the raw pipeline text.
        """
        try:
            await reading.wait()
            audio_urls = await self._keep_reading_audio(reading)
        except Exception as e:
            log_message(f"🔊 TTS Background: ❌ Finalize raised: {e}")
            return
        finally:
            self._tts_streaming_active = False

        browser_speech = reading.browser_speech
        if reading.aborted or (not audio_urls and not browser_speech):
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
            tts_note = reading.note
            metadata = dict(msg.get("metadata", {}))
            metadata["audio_urls"] = audio_urls
            metadata["browser_speech"] = browser_speech
            metadata["tts_note"] = tts_note
            metadata.setdefault("playback_rate", "1.0x")
            history[target] = {
                **msg,
                "metadata": metadata,
                "has_audio": True,
                "audio_urls_json": json.dumps(audio_urls),
                "browser_speech_json": json.dumps(browser_speech, ensure_ascii=False),
                "tts_note": tts_note,
            }
            ch.chat_history = history
            log_message(
                f"🔊 TTS Background: ✅ Patched bubble #{target} with {len(audio_urls)} audio URL(s), "
                f"{len(browser_speech)} browser sentence(s)"
            )

            # Reflex-independent live push: this bare create_task mutates
            # the server state above, but Reflex never pushes that delta to
            # the browser. So announce the combined URL over the existing
            # SSE audio bus — custom.js attaches it to the bubble's audio
            # button itself, no Reflex round-trip needed.
            try:
                from ..lib.api import browser_push
                # Browser speech first: custom.js attaches it to the newest
                # bubble without playback, then bubble_audio finds the same one.
                if browser_speech:
                    browser_push(
                        self.session_id,  # type: ignore[attr-defined]
                        kind="bubble_browser_speech",
                        url=json.dumps(browser_speech, ensure_ascii=False),
                    )
                if audio_urls:
                    browser_push(
                        self.session_id,  # type: ignore[attr-defined]
                        kind="bubble_audio",
                        url=audio_urls[0],
                    )
                browser_push(
                    self.session_id,  # type: ignore[attr-defined]
                    kind="bubble_tts_note",
                    url=tts_note,
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
        self`` aufrufen): Text und Sprache als reine Werte einsammeln.
        ``None`` wenn die Bubble nichts Synthetisierbares hergibt."""
        from ..lib.tts_text_filter import strip_ui_only_content

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
        tts_language = self._resolve_tts_language(agent)
        text = strip_ui_only_content(content_without_label)

        if len(text) < 5:
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} text too short after cleanup")
            return None

        return {
            "bubble_index": bubble_index,
            # Timestamp der Bubble: Während der lock-freien Synthese kann
            # sich die History ändern (neuer Turn, Löschung) — beim Patchen
            # wird die Bubble darüber wiedergefunden statt blind per Index.
            "timestamp": msg.get("timestamp", ""),
            "agent": agent,
            "text": text,
            "language": tts_language,
            "ui_language": _lang(self),
            "session_id": str(self.session_id),  # type: ignore[attr-defined]
        }


    @staticmethod
    async def _synthesize_bubble_audio(request: dict[str, Any]) -> tuple[str, str] | None:
        """Phase 2 (LANGSAM — läuft OHNE State-Lock): Lesung über den TTS-Dienst + Ablage im
        Session-Verzeichnis. Liefert URL und Chat-Vermerk, ``None`` wenn nichts zu hören war oder
        das Ablegen scheiterte. Ein Fehler des Dienstes (``TTSSuiteError``) geht an den Aufrufer.
        Bewusst ohne jeden ``self``-State-Zugriff."""
        from ..lib.audio_processing import save_audio_to_session
        from ..lib.speech_synthesis import speak_to_files
        from ..lib.tts_service import spoken_note

        bubble_index = request["bubble_index"]
        reading = await speak_to_files(request["text"], language=request["language"], agent=request["agent"])
        if not reading.audio_urls:
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} the service delivered no audio")
            return None
        session_audio_url = await asyncio.to_thread(save_audio_to_session, reading.audio_urls, request["session_id"])
        if not session_audio_url:
            log_message(f"⚠️ TTS Re-Synth: Bubble {bubble_index} failed to save to session")
            return None

        log_message(f"🔊 TTS: Bubble {bubble_index} saved → {session_audio_url}")
        note = await asyncio.to_thread(spoken_note, reading.summary.trace, request["ui_language"]) if reading.summary else ""
        return session_audio_url, note


    def _apply_bubble_audio(
        self, bubble_index: int, timestamp: str, session_audio_url: str,
        tts_note: str, save_session: bool,
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
        new_metadata["browser_speech"] = []  # the new audio replaces what the browser spoke
        new_metadata["tts_note"] = tts_note
        new_history[bubble_index] = {
            **prev,
            "metadata": new_metadata,
            "has_audio": True,
            "audio_urls_json": json.dumps([session_audio_url]),
            "browser_speech_json": "[]",
            "tts_note": tts_note,
        }
        _ch.chat_history = new_history

        if save_session:
            self._save_current_session()  # type: ignore[attr-defined]

        return True

    @rx.event(background=True)  # type: ignore[operator]
    async def resynthesize_bubble_tts(self, timestamp: str):
        """Re-synthesize TTS for a specific chat bubble (background event).

        Als normaler Handler hielt das den State-Lock über die GESAMTE
        Synthese (Minuten bei langen Bubbles) — die UI fror ein, und starb
        währenddessen die Verbindung, ging das finale Delta verloren (Audio
        erst nach F5 sichtbar). Jetzt: Lock nur für kurze Lese-/Schreib-Phasen,
        Synthese lock-frei — die UI bleibt bedienbar, der nächste Prompt kann
        parallel laufen.

        Args:
            timestamp: Timestamp of the message to regenerate
        """
        from ..lib.i18n import t
        from ..lib.toast import toast

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
            yield rx.call_script("stopTts()")  # type: ignore[misc]

        try:
            async with self:
                request = self._extract_bubble_tts_request(target_index)
                if request is not None:
                    self.add_debug(  # type: ignore[attr-defined]
                        f"🔄 TTS Re-Synth: Regenerating bubble {target_index} ({request['agent']})..."
                    )

            success = False
            if request is not None:
                synthesized = await self._synthesize_bubble_audio(request)
                async with self:
                    if synthesized:
                        success = self._apply_bubble_audio(
                            target_index, request["timestamp"],
                            *synthesized, save_session=True,
                        )

            async with self:
                if success:
                    self.add_debug(f"✅ TTS: Bubble {target_index} regenerated")  # type: ignore[attr-defined]
                else:
                    self.add_debug(f"⚠️ TTS: Bubble {target_index} regeneration failed")  # type: ignore[attr-defined]
        except TTSSuiteError as error:
            log_message(f"❌ TTS regeneration: service error: {error}", "error")
            async with self:
                self.add_debug(f"❌ TTS Error: {type(error).__name__}: {error}")  # type: ignore[attr-defined]
                yield toast("error", t("tts_reading_service_failed_silent", lang=_lang(self), reason=str(error)))
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
        fließt live in die Debug-Messages). Ein Fehler des Dienstes bricht den
        Durchlauf ab (jede weitere Bubble scheiterte genauso)."""
        from ..lib.i18n import t
        from ..lib.toast import toast

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
            yield rx.call_script("stopTts()")  # type: ignore[misc]

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

                synthesized = await self._synthesize_bubble_audio(request) if request is not None else None

                applied = False
                async with self:
                    if synthesized and request is not None:
                        # Session erst am Ende EINMAL speichern (wie zuvor).
                        applied = self._apply_bubble_audio(
                            bubble_idx, request["timestamp"],
                            *synthesized, save_session=False,
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

        except TTSSuiteError as error:
            log_message(f"❌ TTS regeneration: service error: {error}", "error")
            async with self:
                self._save_current_session()  # type: ignore[attr-defined]  # what was regenerated so far stays
                self.add_debug(f"❌ TTS Error: {type(error).__name__}: {error}")  # type: ignore[attr-defined]
                yield toast("error", t("tts_reading_service_failed_silent", lang=_lang(self), reason=str(error)))
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            async with self:
                self.add_debug(f"❌ TTS Error: {e}")  # type: ignore[attr-defined]
            log_message(f"❌ TTS regeneration error: {e}")
        finally:
            async with self:
                self.tts_regenerating = False
