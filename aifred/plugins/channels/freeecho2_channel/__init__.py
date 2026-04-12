"""FreeEcho.2 Channel Plugin — WebSocket server for voice terminals.

FreeEcho.2 devices (Echo Dot 2nd Gen with custom firmware) connect via
WebSocket and send audio after wake word detection. AIfred processes
the audio (STT → LLM → TTS) and streams the response back.

Protocol:
  Client → Server:
    Text:   {"type":"register","room":"wohnzimmer","capabilities":["audio_in","audio_out"]}
    Text:   {"type":"wake","room":"wohnzimmer"}
    Binary: Raw PCM audio (16kHz mono int16) after recording
  Server → Client:
    Binary: TTS audio (48kHz mono int16) for playback
    Text:   {"type":"status","message":"processing"}
"""

from __future__ import annotations

import asyncio
import json
import wave
import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ....lib.plugin_base import BaseChannel, CredentialField

if TYPE_CHECKING:
    from aiohttp.web import Request, WebSocketResponse
    from ....lib.envelope import InboundMessage, OutboundMessage

# Connected FreeEcho.2 devices: room_name → WebSocketResponse
_devices: dict[str, WebSocketResponse] = {}
# Pending TTS responses: room_name → asyncio.Future
_pending_responses: dict[str, asyncio.Future] = {}

# WebSocket server port
_DEFAULT_PORT = 9777
_DEFAULT_PATH = "/ws/freeecho2"


class FreeEchoChannel(BaseChannel):
    """FreeEcho.2 voice terminal channel via WebSocket."""

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "freeecho2"

    @property
    def display_name(self) -> str:
        return "FreeEcho.2"

    @property
    def icon(self) -> str:
        return "radio"

    @property
    def always_reply(self) -> bool:
        return True

    @property
    def has_allowlist(self) -> bool:
        return False

    # ── Credentials ───────────────────────────────────────────

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="FREEECHO2_PORT",
                label_key="freeecho2_cred_port",
                placeholder="9777",
            ),
            CredentialField(
                env_key="FREEECHO2_TTS_ENGINE",
                label_key="freeecho2_cred_tts_engine",
                placeholder="piper",
                options=[("piper", "Piper"), ("edge", "Edge"), ("xtts", "XTTS"), ("moss", "MOSS-TTS"), ("espeak", "eSpeak")],
            ),
        ]

    def is_configured(self) -> bool:
        return True  # No credentials needed — local WebSocket server

    def apply_credentials(self, values: dict[str, str]) -> None:
        from ....lib.credential_broker import broker

        broker.set_runtime("freeecho2", "enabled", "true")
        port = values.get("FREEECHO2_PORT", str(_DEFAULT_PORT))
        broker.set_runtime("freeecho2", "port", port)

        ssl_cert = values.get("FREEECHO2_SSL_CERT", "")
        ssl_key = values.get("FREEECHO2_SSL_KEY", "")
        if ssl_cert:
            broker.set_runtime("freeecho2", "ssl_cert", ssl_cert)
        if ssl_key:
            broker.set_runtime("freeecho2", "ssl_key", ssl_key)

        # Engine setting is saved here, actual start happens on first Puck request
        # via ensure_engine_ready() in _run_tts()
        new_engine = values.get("FREEECHO2_TTS_ENGINE", "piper")
        broker.set_runtime("freeecho2", "tts_engine", new_engine)

        tts_voice = values.get("FREEECHO2_TTS_VOICE", "de_DE-thorsten-high")
        if tts_voice:
            broker.set_runtime("freeecho2", "tts_voice", tts_voice)

    # ── Listener (WebSocket server) ───────────────────────────

    async def listener_loop(self) -> None:
        """Run WebSocket server for FreeEcho.2 devices."""
        import ssl
        from ....lib.credential_broker import broker

        port = int(broker.get("freeecho2", "port") or str(_DEFAULT_PORT))
        cert_file = broker.get("freeecho2", "ssl_cert") or ""
        key_file = broker.get("freeecho2", "ssl_key") or ""

        try:
            from aiohttp import web
        except ImportError:
            self.channel_log("aiohttp not installed, FreeEcho.2 disabled", "error")
            return

        app = web.Application()
        app.router.add_get(_DEFAULT_PATH, self._handle_ws)

        runner = web.AppRunner(app)
        await runner.setup()

        # TLS setup
        ssl_ctx = None
        if cert_file and key_file:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(cert_file, key_file)
            self.channel_log(f"TLS enabled (cert: {cert_file})")

        site = web.TCPSite(runner, "0.0.0.0", port, ssl_context=ssl_ctx)
        await site.start()
        proto = "wss" if ssl_ctx else "ws"
        self.channel_log(f"WebSocket server listening on {proto}://0.0.0.0:{port}{_DEFAULT_PATH}")

        try:
            # Keep running until cancelled
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.channel_log("Shutting down WebSocket server")
        finally:
            await runner.cleanup()

    async def _handle_ws(self, request: Request) -> WebSocketResponse:
        """Handle a single FreeEcho.2 WebSocket connection."""
        from aiohttp import web, WSMsgType

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        room = "unknown"
        self.channel_log(f"FreeEcho.2 connection from {request.remote}")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_text(ws, msg.data, room)
                    # Update room from register message
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "register":
                            room = data.get("room", room)
                            _devices[room] = ws
                            self.channel_log(f"FreeEcho.2 registered: room={room}")
                    except json.JSONDecodeError:
                        pass

                elif msg.type == WSMsgType.BINARY:
                    # Audio data from device
                    await self._handle_audio(ws, msg.data, room)

                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break

        except Exception as e:
            self.channel_log(f"WebSocket error ({room}): {e}", "error")
        finally:
            if room in _devices and _devices[room] is ws:
                del _devices[room]
            self.channel_log(f"FreeEcho.2 disconnected: room={room}")

        return ws

    async def _handle_text(self, ws: WebSocketResponse, data: str, room: str) -> None:
        """Handle text message from FreeEcho.2 device."""
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")

        if msg_type == "register":
            self.channel_log(f"[FreeEcho.2 {room}] Register: {msg}")

        elif msg_type == "wake":
            self.channel_log(f"[FreeEcho.2 {room}] Wake word detected")
            # Pre-signal: could trigger model warmup here
            # For now just acknowledge
            await ws.send_str(json.dumps({"type": "status", "message": "ready"}))

    async def _handle_audio(self, ws: WebSocketResponse, audio_data: bytes, room: str) -> None:
        """Handle binary audio from FreeEcho.2 device.

        Audio is raw PCM: 16kHz, mono, int16 (little-endian).
        """
        from ....lib.envelope import InboundMessage
        from ....lib.message_processor import process_inbound

        import time as _puck_time
        _puck_t0 = _puck_time.monotonic()

        num_samples = len(audio_data) // 2
        duration = num_samples / 16000.0
        audio_kb = len(audio_data) / 1024
        self.channel_log(f"[FreeEcho.2 {room}] Audio received: {num_samples} samples ({duration:.1f}s, {audio_kb:.0f} KB)")

        # Convert raw PCM to WAV for STT
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio_data)
        wav_bytes = wav_buffer.getvalue()

        # Save temp WAV for STT
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir="/tmp") as f:
            f.write(wav_bytes)
            wav_path = f.name

        self.channel_log(f"[FreeEcho.2 {room}] WAV prepared ({_puck_time.monotonic()-_puck_t0:.2f}s)")

        # Resolve session IMMEDIATELY so all debug messages (STT, TTS loading,
        # model switching) reach the browser UI via session_scope.
        from ....lib.routing_table import routing_table
        from ....lib.session_storage import create_empty_session
        from ....lib.config import MESSAGE_HUB_OWNER
        from ....lib.debug_bus import debug, session_scope
        from ....lib.message_processor import write_hub_notification
        import secrets as _secrets

        route = routing_table.get_route("freeecho2", room)
        if route:
            session_id = route.session_id
        else:
            session_id = _secrets.token_hex(16)
            create_empty_session(session_id, owner=MESSAGE_HUB_OWNER)
            routing_table.set_route("freeecho2", room, session_id)

        # Heartbeat task — sends heartbeat every 5s while processing
        heartbeat_running = True

        async def _heartbeat():
            while heartbeat_running:
                try:
                    await ws.send_str(json.dumps({"type": "heartbeat"}))
                except Exception:
                    break
                await asyncio.sleep(5)

        try:
            # Start session_scope so ALL debug messages go to the browser UI
            with session_scope(session_id):
                # Notify UI immediately — toast + ghosting BEFORE STT
                write_hub_notification(session_id, f"FreeEcho.2 {room}", "FreeEcho.2", room, status="received")
                debug(f"📨 FreeEcho.2: Audio from {room} ({duration:.1f}s)")
                debug("🎤 STT running...")

                # Run STT
                text = await self._run_stt(wav_path)
                if not text:
                    self.channel_log(f"[FreeEcho.2 {room}] STT returned empty text", "warning")
                    debug("❌ STT: no text recognized")
                    await ws.send_str(json.dumps({"type": "done", "reason": "stt_empty"}))
                    return

                self.channel_log(f"[FreeEcho.2 {room}] STT ({_puck_time.monotonic()-_puck_t0:.1f}s): {text}")
                debug(f"🎤 STT: \"{text}\" ({_puck_time.monotonic()-_puck_t0:.1f}s)")

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

                # Start heartbeat BEFORE TTS check — TTS loading can take 30s+
                await ws.send_str(json.dumps({"type": "processing"}))
                heartbeat_task = asyncio.create_task(_heartbeat())

                # Ensure TTS state (MOSS/XTTS loading, VRAM management).
                # Messages go to UI via debug() (session context propagated to executor).
                write_hub_notification(session_id, f"FreeEcho.2 {room}", "FreeEcho.2", room, status="processing")
                tts_deferred = await self._ensure_tts_state()

                self.channel_log(f"[FreeEcho.2 {room}] → process_inbound ({_puck_time.monotonic()-_puck_t0:.1f}s)")

            # Create inbound message and process through AIfred engine
            # (process_inbound creates its own session_scope)
            inbound = InboundMessage(
                channel="freeecho2",
                channel_id=room,
                sender=room,
                text=text,
                timestamp=datetime.now(timezone.utc),
                metadata={"wav_path": wav_path, "room": room, "tts_deferred": tts_deferred},
            )

            _devices[room] = ws

            # process_inbound calls send_reply automatically (via auto_reply)
            # User question already flushed to session above (early browser update)
            await process_inbound(inbound, user_saved=True)

            # Stop heartbeat
            heartbeat_running = False
            heartbeat_task.cancel()

            total_time = _puck_time.monotonic() - _puck_t0
            self.channel_log(f"[FreeEcho.2 {room}] ← Pipeline complete ({total_time:.1f}s)")

            # Signal client: all done, go back to IDLE
            await ws.send_str(json.dumps({"type": "done"}))

        except Exception as e:
            self.channel_log(f"[FreeEcho.2 {room}] Pipeline error: {e}", "error")
            heartbeat_running = False
            try:
                await ws.send_str(json.dumps({"type": "done", "reason": "error"}))
            except Exception:
                pass
        finally:
            Path(wav_path).unlink(missing_ok=True)

    async def _run_stt(self, wav_path: str) -> str:
        """Run Speech-to-Text via Whisper Docker service."""
        from ....lib.audio_processing import transcribe_audio

        loop = asyncio.get_event_loop()
        text, stt_time = await loop.run_in_executor(
            None, transcribe_audio, wav_path, "de", "cpu",
        )
        self.channel_log(f"STT: '{text[:80]}' ({stt_time:.1f}s)")
        return text or ""

    # ── Reply ─────────────────────────────────────────────────

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Send TTS audio back to the FreeEcho.2 device."""
        room = outbound.channel_id
        ws = _devices.get(room)
        if not ws:
            self.channel_log(f"[FreeEcho.2 {room}] No connected device for reply", "warning")
            return

        # If TTS was deferred (LLM was loaded without TTS, used for fast inference),
        # now switch: unload LLM → load TTS engine → restart LLM with TTS profile.
        # Must happen BEFORE _run_tts() which needs the TTS engine running.
        if original and original.metadata.get("tts_deferred"):
            self.channel_log(f"[FreeEcho.2 {room}] Deferred TTS switch starting")
            await self._force_tts_switch()

        # Generate TTS audio (agent-specific voice if configured)
        agent = original.target_agent if original else "aifred"
        tts_path = await self._run_tts(outbound.text, agent=agent)
        if not tts_path:
            self.channel_log(f"[FreeEcho.2 {room}] TTS failed", "error")
            return

        # Read TTS audio and send in chunks (512 KB each)
        # The device expects 48kHz mono int16 PCM
        pcm_data = await self._convert_to_pcm(tts_path, 48000)
        if pcm_data:
            chunk_size = 512 * 1024
            total = len(pcm_data)
            num_chunks = (total + chunk_size - 1) // chunk_size
            self.channel_log(f"[FreeEcho.2 {room}] Sending TTS: {total} bytes ({total/96000:.1f}s) in {num_chunks} chunks")
            await ws.send_str(json.dumps({"type": "audio_start", "total_size": total}))
            offset = 0
            chunk_num = 0
            while offset < total:
                end = min(offset + chunk_size, total)
                await ws.send_bytes(pcm_data[offset:end])
                chunk_num += 1
                self.channel_log(f"[FreeEcho.2 {room}] Chunk {chunk_num}/{num_chunks}: {end-offset} bytes sent")
                offset = end
            await ws.send_str(json.dumps({"type": "audio_end"}))
            self.channel_log(f"[FreeEcho.2 {room}] Audio transfer complete")
        else:
            self.channel_log(f"[FreeEcho.2 {room}] TTS conversion failed", "error")

        Path(tts_path).unlink(missing_ok=True)

    def _get_wanted_tts(self) -> str:
        """Get the TTS engine this plugin wants."""
        from ....lib.credential_broker import broker
        return broker.get("freeecho2", "tts_engine") or "piper"

    def _get_backend_type(self) -> str:
        """Get the current LLM backend type."""
        from ....state._base import _global_backend_state
        return _global_backend_state.get("backend_type") or "llamacpp"

    async def _ensure_tts_state(self) -> bool:
        """Ensure TTS state before LLM inference (SSOT: ensure_tts_state).

        Returns True if deferred (LLM loaded, caller should inferize first).
        Returns False if TTS is ready and LLM will load with correct profile.
        """
        from ....lib.tts_engine_manager import ensure_tts_state, GPU_ENGINES
        from ....lib.debug_bus import debug, _current_session

        wanted = self._get_wanted_tts()
        # Map lightweight engines to "" (no GPU TTS needed).
        # The SSOT still needs to run: if a GPU TTS container is in VRAM
        # but we switched to Edge/Piper/eSpeak, it must be cleaned up.
        wanted_gpu = wanted if wanted in GPU_ENGINES else ""

        backend_type = self._get_backend_type()

        # Capture session_id from the calling coroutine's context
        # so debug() in the executor thread can route to the session.
        caller_session_id = _current_session.get()

        def _run() -> bool:
            # Propagate session context into executor thread
            token = _current_session.set(caller_session_id) if caller_session_id else None
            try:
                gen = ensure_tts_state(
                    wanted_tts=wanted_gpu,
                    backend_type=backend_type,
                    check_defer=True,
                )
                deferred = False
                try:
                    while True:
                        msg = next(gen)
                        debug(f"🔊 {msg}")
                except StopIteration as e:
                    if e.value:
                        deferred = e.value.deferred
                return deferred
            finally:
                if token is not None:
                    _current_session.reset(token)

        return await asyncio.get_event_loop().run_in_executor(None, _run)

    async def _force_tts_switch(self) -> None:
        """Force TTS switch after deferred inference (Puck optimization).

        Called after LLM used existing model. Now: switch TTS, then
        restart LLM with TTS-calibrated profile. All blocking, sequential.
        """
        from ....lib.tts_engine_manager import force_tts_switch, GPU_ENGINES
        from ....lib.debug_bus import debug, _current_session

        wanted = self._get_wanted_tts()
        # Map lightweight engines to "" — force_tts_switch needs GPU key or ""
        wanted_gpu = wanted if wanted in GPU_ENGINES else ""
        backend_type = self._get_backend_type()
        caller_session_id = _current_session.get()

        def _run() -> None:
            token = _current_session.set(caller_session_id) if caller_session_id else None
            try:
                gen = force_tts_switch(wanted_gpu, backend_type)
                try:
                    while True:
                        msg = next(gen)
                        debug(f"🔊 {msg}")
                except StopIteration:
                    pass
            finally:
                if token is not None:
                    _current_session.reset(token)

        await asyncio.get_event_loop().run_in_executor(None, _run)

    async def _run_tts(self, text: str, agent: str = "aifred") -> str | None:
        """Generate TTS audio file from text. Returns absolute file path.

        Uses the FreeEcho.2 plugin's TTS engine setting combined with
        per-agent voice configuration from TTS_AGENT_VOICE_DEFAULTS.
        Independent of the browser UI TTS toggle.

        TTS container readiness is ensured by _ensure_tts_state() / _force_tts_switch()
        BEFORE this method is called. No VRAM management here.
        """
        from ....lib.credential_broker import broker
        from ....lib.config import PROJECT_ROOT, TTS_AGENT_VOICE_DEFAULTS
        from ....lib.settings import load_settings

        # Engine from plugin settings
        engine = broker.get("freeecho2", "tts_engine") or "piper"

        # Voice priority: 1. User settings (per engine+agent), 2. Defaults, 3. Fallback
        settings = load_settings() or {}
        user_voices = settings.get("tts_agent_voices_per_engine", {}).get(engine, {})
        user_cfg = user_voices.get(agent) or user_voices.get("aifred", {})
        default_voices = TTS_AGENT_VOICE_DEFAULTS.get(engine, {})
        default_cfg = default_voices.get(agent) or default_voices.get("aifred", {})

        # User setting wins, then default, then hardcoded fallback
        voice = ""
        if isinstance(user_cfg, dict):
            voice = str(user_cfg.get("voice", ""))
        elif isinstance(user_cfg, str):
            voice = user_cfg
        if not voice:
            from ....lib.config import PUCK_TTS_FALLBACK_VOICE
            voice = str(default_cfg.get("voice", PUCK_TTS_FALLBACK_VOICE))

        speed_str = str(default_cfg.get("speed", "1.0"))
        if isinstance(user_cfg, dict) and user_cfg.get("speed"):
            speed_str = str(user_cfg["speed"])
        speed = float(speed_str.replace("x", ""))

        pitch_str = str(default_cfg.get("pitch", "1.0"))
        if isinstance(user_cfg, dict) and user_cfg.get("pitch"):
            pitch_str = str(user_cfg["pitch"])
        pitch = float(pitch_str)

        self.channel_log(f"TTS: engine={engine}, agent={agent}, voice={voice}, speed={speed}, pitch={pitch}")

        try:
            from ....lib.audio_processing import generate_tts
            result: str | None = await generate_tts(text, voice, speed, engine, pitch=pitch, agent=agent)
            if not result:
                return None
            # Convert URL path (/_upload/tts_audio/xxx.wav) to absolute file path
            if result.startswith("/_upload/"):
                return str(PROJECT_ROOT / "data" / result.removeprefix("/_upload/"))
            return result
        except Exception as e:
            self.channel_log(f"TTS ({engine}) failed: {e}", "error")
            return None

    async def _convert_to_pcm(self, audio_path: str, target_rate: int) -> bytes | None:
        """Convert audio file to raw PCM (mono, int16, target_rate)."""
        # Use ffmpeg to convert any audio format to raw PCM
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-ar", str(target_rate),
            "-ac", "1",
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "pipe:1",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return stdout
            self.channel_log(f"ffmpeg error: {stderr.decode()[:200]}", "error")
        except FileNotFoundError:
            self.channel_log("ffmpeg not found", "error")
        return None

    # ── Context ───────────────────────────────────────────────

    def build_context(self, message: "InboundMessage") -> str:
        """Format message for LLM context."""
        room = message.metadata.get("room", "unknown")
        return (
            f"Sprachnachricht von FreeEcho.2 Gerät im Raum '{room}'. "
            f"Der User hat gesprochen und die Sprache wurde per STT transkribiert. "
            f"Antworte kurz und prägnant — die Antwort wird per TTS vorgelesen."
        )


# Module-level singleton — auto-discovered by plugin registry
FreeEchoChannel_instance = FreeEchoChannel()
