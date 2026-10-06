"""Reply-Pfad des FreeEcho.2-Channels.

``send_reply`` schickt die TTS-Antwort über den AudioOrchestrator an den
Puck (reaktiv) bzw. in die Alert-Queue (proaktiv). Die Sprach-Erzeugung
(Engine-Bereitschaft, Synthese, satzweiser Strom) liegt im Kern
(``lib.speech_synthesis``); das Plugin gibt nur Engine und Sprache vor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ....lib.audio_processing import build_speech_segments
from ....lib.plugin_base import BaseChannel
from ....lib.speech_synthesis import (
    ensure_tts_ready, force_tts_switch, gpu_tts_combo_fits, start_speech_stream,
)
from ....lib.tts_engines import speech_unit_for

from ._shared import _devices, channel_language, notification_tone_enabled
from .alert_queue import enqueue_alert

if TYPE_CHECKING:
    from ....lib.envelope import InboundMessage, OutboundMessage


class TtsReplyMixin(BaseChannel):
    """TTS-Erzeugung + Reply-Versand (reaktiv und proaktiv)."""

    # ── Reply ─────────────────────────────────────────────────

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Send TTS audio back to the FreeEcho.2 device.

        Geht ueber den AudioOrchestrator des FreeEcho2Channels
        (``play_tts``): TTS laeuft immer standalone — laufende Music wird
        dabei beendet (Position gespeichert, spaeter via ``audio_resume``).
        Kein eigenes Pause/Resume-Handling — der Orchestrator ist
        Single-Source-of-Truth fuer Audio-State pro Room.
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
                f"[FreeEcho.2 {room}] silent_reply — TTS confirmation skipped"
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
            if not gpu_tts_combo_fits(self._get_wanted_tts()):
                self.channel_log(
                    f"[FreeEcho.2 {room}] GPU-TTS '{self._get_wanted_tts()}' has "
                    f"no calibrated variant for the active model — skipping voice "
                    f"output (a switch would evict the LLM and still fail for lack "
                    f"of VRAM). Use a cloud TTS (DashScope) with large models.",
                    "error",
                )
                return
            self.channel_log(f"[FreeEcho.2 {room}] Deferred TTS switch starting")
            await force_tts_switch(self._get_wanted_tts())

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
            await ensure_tts_ready(self._get_wanted_tts(), f"FreeEcho.2 {room}")

        # Satzweises Streaming: der erste Satz wird erzeugt und läuft los, während die
        # übrigen noch erzeugt werden (SSOT der Satzaufteilung: lib.audio_processing).
        # channel_language() = Haushaltssprache — ohne sie synthetisieren sprachsensitive
        # Engines (xtts, dashscope) mit dem "de"-Default der lib.
        agent = original.target_agent if original else "aifred"
        buffer = await start_speech_stream(
            self._speech_segments(outbound), agent, self._get_wanted_tts(),
            channel_language(), f"FreeEcho.2 {room}",
        )
        if buffer is None:
            return

        try:
            from ....lib import audio_channels
            ch = audio_channels.resolve(f"freeecho2:{room}")
            if ch is None or not hasattr(ch, "get_orchestrator"):
                self.channel_log(
                    f"[FreeEcho.2 {room}] FreeEcho2Channel unavailable — cannot send TTS",
                    "error",
                )
                buffer.discard()
                return
            orc = ch.get_orchestrator(room)
            if orc is None:
                self.channel_log(
                    f"[FreeEcho.2 {room}] orchestrator unavailable", "error",
                )
                buffer.discard()
                return

            # Proaktive Push-Nachricht? Erkannt am dummy-inbound
            # ``sender == "system"`` aus message_processor.announce_to_channel.
            # In dem Fall: lokaler Chime vor dem TTS, damit der User nicht
            # aus dem Nichts angesprochen wird. Welcher Chime — alarm_wav
            # (auffaellig) oder notification_wav (sanft) — kommt per
            # metadata.audio_type aus dem Caller (alert_bus mappt severity →
            # audio_type; explizite scheduler-Sends koennen es selbst setzen).
            # Default "notification" wenn unklar.
            # Frame-Sequenz: audio_flag(alarm|notification, start_tone) +
            # audio_start + chunks + audio_end(end_tone) — der
            # Orchestrator macht alles in einem Aufruf.
            # Normal-Reply (User hat selbst getriggert) bleibt ohne Chime.
            # is_proactive ist oben schon bestimmt (TTS-State-Sicherstellung).
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
                    f"chime + streaming TTS → alert queue"
                )
                # In die room-Queue legen statt direkt abspielen: der Worker
                # serialisiert (ein Alarm nach dem anderen, je nach _done),
                # und der Emit-Pfad (Vision-Watcher) wird NICHT blockiert.
                # Töne am Puck: Ansagen laut Plugin-Einstellung (welcher Ton, legt der Puck
                # fest); beim Alarm IST der Alarm-Ton der Beginn-Ton, einen Ende-Ton gibt es nicht.
                if audio_type == "alarm":
                    start_tone, end_tone = True, False
                else:
                    start_tone = notification_tone_enabled("start")
                    end_tone = notification_tone_enabled("end")
                await enqueue_alert(room, audio_type, buffer, start_tone=start_tone, end_tone=end_tone)
            else:
                self.channel_log(f"[FreeEcho.2 {room}] Sending TTS (streaming) via orchestrator")
                await orc.play_tts(buffer)
            self.channel_log(f"[FreeEcho.2 {room}] TTS playback complete")
        except BaseException:
            buffer.discard()
            raise

    # ── Satzweises Sprach-Streaming ────────────────────────────

    def _speech_segments(self, outbound: "OutboundMessage") -> "list[str | int]":
        """Was gesprochen wird, in Reihenfolge: Texte (str) und Stille zwischen Absätzen
        (int, ms). Wie fein der Text für die TTS-Engine zerlegt wird (satzweise, absatzweise,
        am Stück), ist die Einstellung der gewählten Engine (SSOT: ``speech_unit_for``) —
        das Plugin kennt sie nicht, es liest sie nur."""
        paragraphs = outbound.metadata.get("paragraphs") or [outbound.text]
        return build_speech_segments(
            paragraphs, int(outbound.metadata.get("pause_ms", 0)),
            speech_unit_for(self._get_wanted_tts()),
        )

    def _get_wanted_tts(self) -> str:
        """Die TTS-Engine, die dieses Plugin will (Plugin-Einstellung)."""
        from ....lib.credential_broker import broker
        return str(broker.get("freeecho2", "tts_engine") or "piper")
