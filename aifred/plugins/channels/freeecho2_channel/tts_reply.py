"""Reply-Pfad + TTS-Engine-Handling des FreeEcho.2-Channels.

``send_reply`` schickt die TTS-Antwort über den AudioOrchestrator an den
Puck (reaktiv) bzw. in die Alert-Queue (proaktiv). Die ``_ensure_tts_*``/
``_force_tts_switch``-Helfer verwalten den GPU-TTS-Engine-State (SSOT:
``tts_engine_manager``), ``_run_tts``/``_convert_to_pcm`` erzeugen das
Audio.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from ....lib.formatting import format_number
from ....lib.plugin_base import BaseChannel

from ._shared import _devices, _fmt_mib, channel_language
from .alert_queue import enqueue_alert

if TYPE_CHECKING:
    from ....lib.envelope import InboundMessage, OutboundMessage


class TtsReplyMixin(BaseChannel):
    """TTS-Erzeugung + Reply-Versand (reaktiv und proaktiv)."""

    # ── Reply ─────────────────────────────────────────────────

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Send TTS audio back to the FreeEcho.2 device.

        Geht ueber den AudioOrchestrator des FreeEcho2Channels. Der
        macht alles in einem Aufruf: TTS-Takeover bei laufender Music
        (mpv-pause + audio_flag(tts) + pump + audio_flag(music) +
        mpv-resume) oder TTS-standalone. Kein eigenes Pause/Resume-
        Handling mehr — der Orchestrator ist Single-Source-of-Truth
        fuer Audio-State pro Room.
        """
        room = outbound.channel_id
        ws = _devices.get(room)
        if not ws:
            self.channel_log(f"[FreeEcho.2 {room}] No connected device for reply", "warning")
            return

        # silent_reply: bei erfolgreichem Audio-Tool (audio_play/folder/
        # resume) skippt der Channel die TTS-Bestaetigung. Music laeuft
        # direkt los, kein "DJ labert in den Song". Reply-Text ist
        # bereits in der Session gespeichert (Browser-UI sichtbar).
        if outbound.metadata.get("silent_reply"):
            self.channel_log(
                f"[FreeEcho.2 {room}] silent_reply — TTS-Bestaetigung skipped"
            )
            return

        # If TTS was deferred (LLM was loaded without TTS, used for fast inference),
        # now switch: unload LLM → load TTS engine → restart LLM with TTS profile.
        # Must happen BEFORE _run_tts() which needs the TTS engine running.
        if original and original.metadata.get("tts_deferred"):
            # Vorab-Check: passt das gewünschte GPU-TTS überhaupt zum aktiven
            # LLM? Bei einem GPU-füllenden Modell (z.B. dem 397B) steht die
            # TTS-Kombo im vram-cache auf FAIL — ein Switch würde das LLM
            # verdrängen, das Base-Profil neu laden und das TTS TROTZDEM ohne
            # VRAM lassen ("produced no audio"). Statt 20s blind zu thrashen:
            # erkennen, klar melden, Ansage überspringen.
            if not self._gpu_tts_combo_fits():
                self.channel_log(
                    f"[FreeEcho.2 {room}] GPU-TTS '{self._get_wanted_tts()}' has "
                    f"no calibrated variant for the active model — skipping voice "
                    f"output (a switch would evict the LLM and still fail for lack "
                    f"of VRAM). Use a cloud TTS (DashScope) with large models.",
                    "error",
                )
                return
            self.channel_log(f"[FreeEcho.2 {room}] Deferred TTS switch starting")
            await self._force_tts_switch()

        # Proaktive Pushes (Vision-Alert, freeecho2_announce) kommen OHNE
        # vorausgegangene LLM-Inferenz → es gibt kein tts_deferred-Flag, und
        # die TTS-Engine ist evtl. gar nicht geladen (GPUs idle). Genau wie
        # bei einem echten Puck-Request den TTS-State sicherstellen und ggf.
        # den Modell-Swap (LLM → TTS) erzwingen, BEVOR _run_tts läuft — sonst
        # bleibt die Ansage stumm.
        is_proactive = (
            (original is not None and original.sender == "system")
            or bool(outbound.metadata.get("proactive"))
        )
        if is_proactive:
            await self._ensure_tts_ready(room)

        # Generate TTS audio (agent-specific voice if configured)
        agent = original.target_agent if original else "aifred"
        tts_path = await self._run_tts(outbound.text, agent=agent)
        if not tts_path:
            self.channel_log(f"[FreeEcho.2 {room}] TTS failed", "error")
            return

        try:
            pcm_data = await self._convert_to_pcm(tts_path, 48000)
            if not pcm_data:
                self.channel_log(f"[FreeEcho.2 {room}] TTS conversion failed", "error")
                return

            from ....lib import audio_channels
            ch = audio_channels.resolve(f"freeecho2:{room}")
            if ch is None or not hasattr(ch, "get_orchestrator"):
                self.channel_log(
                    f"[FreeEcho.2 {room}] FreeEcho2Channel unavailable — cannot send TTS",
                    "error",
                )
                return
            orc = ch.get_orchestrator(room)
            if orc is None:
                self.channel_log(
                    f"[FreeEcho.2 {room}] orchestrator unavailable", "error",
                )
                return

            # Proaktive Push-Nachricht? Erkannt am dummy-inbound
            # ``sender == "system"`` aus message_processor.announce_to_channel.
            # In dem Fall: lokaler Chime vor dem TTS, damit der User nicht
            # aus dem Nichts angesprochen wird. Welcher Chime — alarm_wav
            # (auffaellig) oder notification_wav (sanft) — kommt per
            # metadata.audio_type aus dem Caller (alert_bus mappt severity →
            # audio_type; explizite scheduler-Sends koennen es selbst setzen).
            # Default "notification" wenn unklar.
            # Frame-Sequenz: audio_flag(alarm|notification, with_tts=True) +
            # audio_flag(tts) + audio_start + chunks + audio_end — der
            # Orchestrator macht alles in einem Aufruf.
            # Normal-Reply (User hat selbst getriggert) bleibt ohne Chime.
            # is_proactive ist oben schon bestimmt (TTS-State-Sicherstellung).
            secs = format_number(len(pcm_data) / 96000, 1)
            if is_proactive:
                audio_type = str(
                    outbound.metadata.get("audio_type") or "notification"
                )
                if audio_type not in ("alarm", "notification"):
                    self.channel_log(
                        f"[FreeEcho.2 {room}] unknown audio_type "
                        f"{audio_type!r} — falling back to notification",
                        "warning",
                    )
                    audio_type = "notification"
                self.channel_log(
                    f"[FreeEcho.2 {room}] Proactive push ({audio_type}): "
                    f"chime + TTS {_fmt_mib(len(pcm_data))} ({secs}s) "
                    f"→ alert queue"
                )
                # In die room-Queue legen statt direkt abspielen: der Worker
                # serialisiert (ein Alarm nach dem anderen, je nach _done),
                # und der Emit-Pfad (Vision-Watcher) wird NICHT blockiert.
                await enqueue_alert(room, audio_type, pcm_data)
            else:
                self.channel_log(
                    f"[FreeEcho.2 {room}] Sending TTS: {_fmt_mib(len(pcm_data))} "
                    f"({secs}s) via orchestrator"
                )
                await orc.play_tts(pcm_data)
            self.channel_log(f"[FreeEcho.2 {room}] TTS playback complete")
        finally:
            Path(tts_path).unlink(missing_ok=True)

    def _get_wanted_tts(self) -> str:
        """Get the TTS engine this plugin wants."""
        from ....lib.credential_broker import broker
        return broker.get("freeecho2", "tts_engine") or "piper"

    def _get_backend_type(self) -> str:
        """Get the current LLM backend type."""
        from ....state._base import _global_backend_state
        return _global_backend_state.get("backend_type") or "llamacpp"

    def _gpu_tts_combo_fits(self) -> bool:
        """True if the wanted GPU-TTS engine actually fits alongside the active
        LLM — i.e. the model has a calibrated TTS variant in the vram cache.

        Lightweight/cloud engines (Edge/Piper/DashScope) need no GPU, so they
        always fit. Returns True optimistically when the model id is unknown
        (don't block on missing info).

        Guards the deferred TTS switch: for a GPU-filling model (e.g. the 397B)
        the TTS combo is FAIL in the cache; switching anyway would evict the
        LLM, reload its base profile and still leave the TTS without VRAM
        ("produced no audio"). Better to detect + skip than to thrash for 20s.
        """
        from ....lib.tts_engine_manager import GPU_ENGINES
        wanted = self._get_wanted_tts()
        if wanted not in GPU_ENGINES:
            return True
        from ....lib.settings import load_settings
        from ....lib.model_vram_cache import is_tts_variant_calibrated
        settings = load_settings() or {}
        backend = self._get_backend_type()
        model_id = str(
            settings.get("backend_models", {}).get(backend, {}).get("aifred", "")
        )
        if not model_id:
            return True
        return is_tts_variant_calibrated(model_id, wanted)

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
        """Force TTS switch after deferred inference (FreeEcho.2 optimization).

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

    async def _ensure_tts_ready(self, room: str) -> None:
        """SSoT für 'TTS-Engine jetzt synchron bereitstellen' — für proaktive
        Pushes (Vision-Alert, freeecho2_announce), die OHNE vorausgehende
        LLM-Inferenz kommen.

        Nutzt dieselben Primitive wie der Chat-Flow (``_ensure_tts_state`` +
        ``_force_tts_switch``), nur ohne Inferenz dazwischen: TTS-State
        sicherstellen, und falls das große LLM die GPU blockiert (deferred),
        den Modell-Swap LLM → TTS sofort erzwingen. Identisches Verhalten wie
        bei einem echten Puck-Request, nur ohne Antwort-Generierung."""
        deferred = await self._ensure_tts_state()
        if deferred:
            self.channel_log(
                f"[FreeEcho.2 {room}] Proactive TTS switch "
                f"(no prior inference — loading TTS engine)"
            )
            await self._force_tts_switch()

    async def _run_tts(self, text: str, agent: str = "aifred") -> str | None:
        """Generate TTS audio file from text. Returns absolute file path.

        Uses the FreeEcho.2 plugin's TTS engine setting combined with
        per-agent voice configuration from agents.json (``tts_voices``
        block). Independent of the browser UI TTS toggle.

        TTS container readiness is ensured by _ensure_tts_state() / _force_tts_switch()
        BEFORE this method is called. No VRAM management here.
        """
        from ....lib.credential_broker import broker
        from ....lib.config import PROJECT_ROOT
        from ....lib.agent_config import get_tts_voice_default
        from ....lib.settings import load_settings

        # Engine from plugin settings
        engine = broker.get("freeecho2", "tts_engine") or "piper"

        # Voice priority: 1. User settings (per engine+agent), 2. Defaults, 3. Fallback.
        # Fall back to "aifred" (the system's default agent) when the
        # requested agent has neither user setting nor default — keeps the
        # historical behaviour where custom agents inherited aifred's voice.
        settings = load_settings() or {}
        user_voices = settings.get("tts_agent_voices_per_engine", {}).get(engine, {})
        user_cfg = user_voices.get(agent) or user_voices.get("aifred", {})
        default_cfg = get_tts_voice_default(agent, engine)
        if not default_cfg.get("voice"):
            default_cfg = get_tts_voice_default("aifred", engine)

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

        pitch_str = str(default_cfg.get("pitch", "1.0"))
        if isinstance(user_cfg, dict) and user_cfg.get("pitch"):
            pitch_str = str(user_cfg["pitch"])

        try:
            speed = float(speed_str.replace("x", ""))
            pitch = float(pitch_str)
        except ValueError:
            # Fail-loud statt ungefangenem Crash des Reply-Pfads: ein kaputter
            # Settings-Wert wird klar benannt, das Gerät bleibt still.
            self.channel_log(
                f"Invalid TTS speed/pitch in settings for agent '{agent}' "
                f"(speed='{speed_str}', pitch='{pitch_str}') — no TTS", "error",
            )
            return None

        try:
            from ....lib.audio_processing import generate_tts
            # channel_language() = Haushaltssprache (FREEECHO2_LANGUAGE) —
            # ohne sie synthetisieren sprachsensitive Engines (xtts,
            # dashscope) mit dem "de"-Default der lib.
            result: str | None = await generate_tts(
                text, voice, speed, engine, pitch=pitch, agent=agent,
                language=channel_language(),
            )
            if not result:
                # No fallback to another engine (project rule) — the device
                # stays silent. The engine already logged the specific cause
                # (e.g. network/internet unreachable) to debug.log; this line
                # makes the failure visible in the session debug console.
                self.channel_log(
                    f"TTS engine '{engine}' produced no audio — device stays "
                    f"silent (no fallback by design)", "error",
                )
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
