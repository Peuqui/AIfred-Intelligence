"""Reply-Pfad des FreeEcho.2-Channels.

``send_reply`` schickt die TTS-Antwort über den AudioOrchestrator an den
Puck (reaktiv) bzw. in die Alert-Queue (proaktiv). Die Sprach-Erzeugung
(TTS-Dienst, Umwandlung in PCM, wachsender Puffer) liegt im Kern
(``lib.speech_synthesis``); das Plugin gibt nur Text, Pause und Sprache vor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ....lib.plugin_base import BaseChannel
from ....lib.speech_synthesis import start_speech_stream

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

        # Proaktive Pushes (Vision-Alert, freeecho2_announce) kommen ohne
        # vorausgegangene LLM-Inferenz; sie bekommen Chime und Alert-Queue.
        is_proactive = (
            (original is not None and original.sender == "system")
            or bool(outbound.metadata.get("proactive"))
        )

        # Wer spricht und wie der Text zerlegt wird, entscheidet der TTS-Dienst nach AIfreds Liste;
        # das Hauptmodell wird dafür nie neu geladen. channel_language() = Haushaltssprache —
        # ohne sie synthetisieren sprachsensitive Engines (xtts, dashscope_audio3) mit dem Default.
        # Der erste Abschnitt wird erzeugt und läuft los, während die übrigen noch erzeugt werden.
        # session_id: Die Blase dieser Antwort bekommt, was der Puck gesprochen hat, damit es im
        # Browser wie eine Browser-Antwort nachgehört werden kann.
        agent = original.target_agent if original else "aifred"
        buffer = await start_speech_stream(
            outbound.metadata.get("paragraphs") or [outbound.text],
            int(outbound.metadata.get("paragraph_pause_ms", 0)),
            agent, channel_language(), f"FreeEcho.2 {room}",
            session_id=outbound.metadata.get("session_id"),
        )
        if buffer is None:
            self.channel_log(f"[FreeEcho.2 {room}] nothing spoken — reply stays silent", "error")
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
            # is_proactive ist oben schon bestimmt.
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
