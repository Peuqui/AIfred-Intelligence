"""Reaktive Audio-Pipeline des FreeEcho.2-Channels (STT → LLM → TTS).

``_handle_audio`` nimmt das rohe PCM vom Puck entgegen, transkribiert es
(Whisper), sichert den TTS-Engine-State und schickt die Frage durch
``process_inbound``. Heartbeat + TTS-Keep-Alive laufen als Begleit-Tasks,
damit weder der Puck noch der TTS-Container während langer Inferenz
aussteigen.
"""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ....lib.formatting import format_number

from ._shared import _devices, _pending_wake_agent
from .tts_reply import TtsReplyMixin
from .ws_bridge import WsBridgeMixin

if TYPE_CHECKING:
    from aiohttp.web import WebSocketResponse


class AudioPipelineMixin(WsBridgeMixin, TtsReplyMixin):
    """Verarbeitung eingehender Audio-Frames (Binary) vom Puck."""

    async def _handle_audio(self, ws: WebSocketResponse, audio_data: bytes, room: str) -> None:
        """Handle binary audio from FreeEcho.2 device.

        Audio is raw PCM: 16kHz, mono, int16 (little-endian).
        """
        from ....lib.envelope import InboundMessage
        from ....lib.message_processor import process_inbound

        import time as _fe2_time
        _fe2_t0 = _fe2_time.monotonic()

        num_samples = len(audio_data) // 2
        duration = num_samples / 16000.0
        audio_kb = len(audio_data) / 1024
        self.channel_log(
            f"📨 [FreeEcho.2 {room}] audio received: "
            f"{format_number(duration, 1)} s, "
            f"{format_number(audio_kb, 0)} KB ({num_samples} samples)"
        )

        # Resolve wake-word hint from the preceding "wake" event. Agent IDs with
        # a leading underscore are command tokens (e.g. "_stop") — handle those
        # here and skip the full STT/LLM/TTS pipeline. The FreeEcho.2 stops its own
        # playback locally on wake detection (like Action-Button), so no reply
        # is sent back. Concrete server-side command handlers (abort running
        # inference, cancel injected music/TTS, …) are TODO.
        wake_agent = _pending_wake_agent.pop(room, None)
        if wake_agent and wake_agent.startswith("_"):
            self.channel_log(f"[FreeEcho.2 {room}] Command '{wake_agent}' received — pipeline skipped (stub)")
            return

        # Convert raw PCM to WAV for STT
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio_data)
        wav_bytes = wav_buffer.getvalue()

        # Resolve session IMMEDIATELY so all debug messages (STT, TTS loading,
        # model switching) reach the browser UI via session_scope.
        from ....lib.routing_table import routing_table
        from ....lib.session_storage import create_empty_session
        from ....lib.config import MESSAGE_HUB_OWNER
        from ....lib.debug_bus import debug, session_scope
        from ....lib.message_processor import hub_notification_scope
        import secrets as _secrets

        route = routing_table.get_route("freeecho2", room)
        if route:
            session_id = route.session_id
        else:
            session_id = _secrets.token_hex(16)
            # channel-Tag ist Pflicht: ohne es sieht die Session wie eine
            # interaktive Browser-Session aus und der Login-Autoload
            # adoptiert sie (Browser und Puck teilen sich dann eine
            # History) — siehe list_sessions(interactive_only=True).
            create_empty_session(
                session_id, owner=MESSAGE_HUB_OWNER, channel="freeecho2",
            )
            routing_table.set_route("freeecho2", room, session_id)

        # Heartbeat task — sends heartbeat every 5s while processing. The device
        # counts every silent ws_recv_timeout (200 ms) as a miss and gives up
        # after heartbeat_misses_max (75) * 200 ms = 15 s without ANY frame. STT
        # plus a cold model load can exceed that, so this lifeline must tick the
        # whole time. A send error is logged (not swallowed) so a broken socket
        # surfaces instead of the device silently timing out.
        heartbeat_running = True

        async def _heartbeat():
            while heartbeat_running:
                try:
                    await ws.send_str(json.dumps({"type": "heartbeat"}))
                except Exception as exc:  # noqa: BLE001
                    self.channel_log(
                        f"[FreeEcho.2 {room}] heartbeat send failed: "
                        f"{type(exc).__name__}: {exc}", "warning",
                    )
                    break
                await asyncio.sleep(5)

        # Track GPU TTS engines this pipeline acquires so the finally block
        # always releases them — even if the browser (or another channel)
        # tries to stop the engine mid-pipeline. Refcount > 0 makes
        # ensure_tts_state() defer the stop until our pipeline finishes.
        # The keep-alive task pings /keep_alive every 5 min so the container
        # idle-timer doesn't trigger during long inference / web research.
        acquired_tts_engines: list[str] = []
        tts_keepalive_task: Optional[asyncio.Task] = None
        heartbeat_task: Optional[asyncio.Task] = None
        wav_path: Optional[str] = None

        try:
            # Write the temp WAV INSIDE the try so the finally always unlinks it.
            # (Session resolution above can raise; creating the file earlier would
            # leak it on that path.)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir="/tmp") as f:
                f.write(wav_bytes)
                wav_path = f.name
            self.channel_log(f"[FreeEcho.2 {room}] WAV prepared ({_fe2_time.monotonic()-_fe2_t0:.2f}s)")
            # session_scope routes debug() messages to this session's UI.
            # hub_notification_scope owns the toast lifecycle: writes "received"
            # on entry, "error" on any exception leaving the block, and "done"
            # on a clean return — covers the STT-empty path and any failure
            # in the TTS-setup phase. We hand off to process_inbound's own
            # scope via .delegate() before calling it (otherwise we'd write
            # a brief "done" between our scope ending and process_inbound's
            # scope opening).
            with session_scope(session_id), hub_notification_scope(
                session_id, f"FreeEcho.2 {room}", "FreeEcho.2", room,
            ) as hub:
                debug(f"📨 FreeEcho.2: Audio from {room} ({duration:.1f}s)")
                debug("🎤 STT running...")

                # Start the heartbeat BEFORE STT so the device's lifeline ticks
                # from the moment we own the audio — covers a cold Whisper run
                # AND the cold model load that follows. Send one "processing"
                # frame first so the device immediately knows the server works.
                await ws.send_str(json.dumps({"type": "processing"}))
                heartbeat_task = asyncio.create_task(_heartbeat())

                # Run STT
                text = await self._run_stt(wav_path)
                if not text:
                    self.channel_log(f"[FreeEcho.2 {room}] STT returned empty text", "warning")
                    debug("❌ STT: no text recognized")
                    await self.send_done(room, reason="stt_empty")
                    return  # hub scope writes "done" on exit → toast closes after 5 s

                self.channel_log(f"[FreeEcho.2 {room}] STT ({_fe2_time.monotonic()-_fe2_t0:.1f}s): {text}")

                # Flush user question to session immediately so browser shows it
                # BEFORE TTS setup (which can take 25s+) and LLM inference.
                # Uses the same SSOT function as process_inbound.
                from ....lib.message_processor import save_user_to_session
                _early_msg = InboundMessage(
                    channel="freeecho2", channel_id=room, sender=room,
                    text=text, timestamp=datetime.now(timezone.utc),
                    metadata={"room": room},
                )
                save_user_to_session(session_id, _early_msg)

                # Ensure TTS state (MOSS/XTTS loading, VRAM management).
                # Messages go to UI via debug() (session context propagated to executor).
                hub.update("processing")
                tts_deferred = await self._ensure_tts_state()

                # Acquire the active GPU TTS engine for the duration of this
                # pipeline so concurrent channels can't stop it mid-flight.
                from ....lib.tts_engine_manager import (
                    _detect_running_tts_engine, acquire_tts,
                )
                _active_engine = _detect_running_tts_engine()
                if _active_engine:
                    acquire_tts(_active_engine)
                    acquired_tts_engines.append(_active_engine)
                    # Start TTS keep-alive ping while pipeline runs.
                    from ....lib.tts_engine_manager import tts_keepalive_loop
                    tts_keepalive_task = asyncio.create_task(
                        tts_keepalive_loop(
                            acquired_tts_engines,
                            on_warn=lambda m: self.channel_log(
                                f"[FreeEcho.2 {room}] {m}", "warning"
                            ),
                        )
                    )

                self.channel_log(f"[FreeEcho.2 {room}] → process_inbound ({_fe2_time.monotonic()-_fe2_t0:.1f}s)")
                # Hand off the notification lifecycle to process_inbound's own
                # hub_notification_scope — its received → processing → done is
                # the user-visible progress now. Without delegate() our scope
                # would briefly write "done" between leaving this block and
                # process_inbound's scope opening, causing a toast flicker.
                hub.delegate()

            # Create inbound message and process through AIfred engine
            # (process_inbound creates its own session_scope)
            # wake_agent was resolved at the top of this handler.
            inbound = InboundMessage(
                channel="freeecho2",
                channel_id=room,
                sender=room,
                text=text,
                timestamp=datetime.now(timezone.utc),
                metadata={
                    "wav_path": wav_path,
                    "room": room,
                    "tts_deferred": tts_deferred,
                    "wake_agent": wake_agent,
                },
            )

            _devices[room] = ws

            # process_inbound calls send_reply automatically (via auto_reply)
            # User question already flushed to session above (early browser update)
            outbound = await process_inbound(inbound, user_saved=True)

            # Kalibrier-Gate: process_inbound hat abgelehnt (GPUs gehören der
            # Messung). Kein TTS möglich/erwünscht — stattdessen den LOKALEN
            # Notification-Sound des Pucks triggern (audio_flag ohne TTS),
            # damit der User hört, dass der Service gerade nicht kann,
            # statt vor einem stummen Puck zu stehen.
            if outbound is not None and outbound.metadata.get("calibration_gate"):
                try:
                    from ....lib import audio_channels
                    ch = audio_channels.resolve(f"freeecho2:{room}")
                    orc = (
                        ch.get_orchestrator(room)
                        if ch is not None and hasattr(ch, "get_orchestrator")
                        else None
                    )
                    if orc is not None:
                        await orc.play_notification(with_tts=False)
                except Exception as e:  # noqa: BLE001
                    self.channel_log(
                        f"[FreeEcho.2 {room}] calibration-gate notification failed: {e}",
                        "warning",
                    )

            total_time = _fe2_time.monotonic() - _fe2_t0
            self.channel_log(f"[FreeEcho.2 {room}] ← Pipeline complete ({total_time:.1f}s)")

            # Signal client: all done, go back to IDLE
            await self.send_done(room)

        except Exception as e:
            self.channel_log(f"[FreeEcho.2 {room}] Pipeline error: {e}", "error")
            try:
                await self.send_done(room, reason="error")
            except Exception:
                pass
        finally:
            # Stop the heartbeat — single source of truth for ALL exit paths
            # (empty-STT early return, clean finish, exception). Setting the
            # flag stops the loop; cancel() ends the pending asyncio.sleep(5).
            heartbeat_running = False
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
            # Stop the TTS keep-alive task before releasing engine refcount.
            if tts_keepalive_task is not None and not tts_keepalive_task.done():
                tts_keepalive_task.cancel()
            # Always release any TTS engine acquisitions — survives crashes
            # and external cancellations (e.g. Action-Button stop event).
            if acquired_tts_engines:
                from ....lib.tts_engine_manager import release_tts
                for _engine in acquired_tts_engines:
                    release_tts(_engine)
            if wav_path:
                Path(wav_path).unlink(missing_ok=True)

    async def _run_stt(self, wav_path: str) -> str:
        """Run Speech-to-Text via Whisper Docker service."""
        from ....lib.audio_processing import transcribe_audio

        loop = asyncio.get_event_loop()
        text, stt_time = await loop.run_in_executor(
            None, transcribe_audio, wav_path, "de", "cpu", False,
        )
        return text or ""
