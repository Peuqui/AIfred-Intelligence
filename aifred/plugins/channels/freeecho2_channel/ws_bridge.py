"""Public WS-Bridge — Audio-Bus-Frame-API.

Audio-Bus-Protokoll (Phase 5.0): siehe docs/de/architecture/
audio-pipeline.md "Audio-Bus-Refactor" für Frame-Sequenzen und
Tupel-Whitelist. Vier Methoden:

  1. send_audio_flag(room, audio_type, **params)  — Type-Setting (LED+VU)
  2. send_audio_start(room, total_size?)          — PCM-Stream-Setup
  3. send_audio_chunk(room, bytes)                — beliebig oft
  4. send_audio_end(room)                         — End-Marker

audio_flag und audio_start sind GETRENNT mit unterschiedlicher
Semantik (audio_flag = Type-Wechsel ohne Stream-Reset). Frame-
Sequenzen pro Use-Case sind in der Doku tabellarisch festgehalten.

Per-Send-Timeout: wenn der FreeEcho.2 nicht mehr ACKt (WiFi-Drop,
Crash), würde Linux-TCP ~2 min brauchen um das zu bemerken — wir
geben nach 10 s auf und schließen die Verbindung, damit der Room-
Slot für den Reconnect frei wird.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ....lib.plugin_base import BaseChannel

from ._shared import _devices, _fmt_mib


class WsBridgeMixin(BaseChannel):
    """Frame-Sende-API des FreeEcho.2-Channels (audio_flag/start/chunk/end,
    heartbeat, done)."""

    _CHUNK_SEND_TIMEOUT_SEC = 10.0

    # Whitelist-Validation für audio_flag/audio_start. Schema-Verletzung
    # wird server-seitig per ValueError geblockt BEVOR sie ans Wire geht
    # — die Firmware-FATAL-Pfade sehen wir damit nur bei echter
    # Network-Korruption, nicht bei Server-Logik-Bugs. Strikt:
    # unbekannte Felder, falsche Typen, fehlende Pflicht-Felder → raise.
    _AUDIO_TYPE_SCHEMA: dict[str, set[str]] = {
        "music":        set(),          # Stereo-VU am Puck
        "speech":       set(),          # Voice-VU (Hoerbuch / Podcast / Lesung)
        "tts":          set(),          # Voice-VU (XTTS-Generator-Output)
        "alarm":        {"with_tts"},   # einmal abspielen; Server loopt
        "notification": {"with_tts"},   # einmal abspielen
    }

    @classmethod
    def _validate_audio_flag(cls, audio_type: str, params: dict[str, Any]) -> None:
        """Strikt: validate audio_flag-Tupel gegen Whitelist. Raise ValueError."""
        if audio_type not in cls._AUDIO_TYPE_SCHEMA:
            raise ValueError(
                f"audio_flag: unknown audio_type {audio_type!r} "
                f"(allowed: {sorted(cls._AUDIO_TYPE_SCHEMA.keys())})"
            )
        expected = cls._AUDIO_TYPE_SCHEMA[audio_type]
        provided = set(params.keys())
        if extra := provided - expected:
            raise ValueError(
                f"audio_flag({audio_type!r}): unexpected fields {sorted(extra)}"
            )
        if missing := expected - provided:
            raise ValueError(
                f"audio_flag({audio_type!r}): missing required fields {sorted(missing)}"
            )
        # Type-Checks pro Feld
        if "with_tts" in params:
            v = params["with_tts"]
            if not isinstance(v, bool):
                raise ValueError(
                    f"audio_flag({audio_type!r}): with_tts must be bool, got {v!r}"
                )

    async def send_audio_flag(
        self, room: str, audio_type: str, **params: Any
    ) -> bool:
        """Schickt ein audio_flag-Frame: Type-Setting (LED + VU + Source-Verhalten).

        Wird verwendet für (siehe Doku audio-pipeline.md):
        - vor audio_start bei music/tts (initial setting)
        - alleine für alarm/notification (kein PCM danach, falls with_tts=false)
        - mid-stream für Type-Switch (z.B. music → tts während Music läuft;
          gleiche Source bleibt, 30 ms Linear-Fade auf Puck-Seite)

        ``audio_type`` muss in der Whitelist sein. ``params`` sind type-
        spezifisch (siehe ``_AUDIO_TYPE_SCHEMA``). Server-side strikt
        validiert — ungültige Tupel raisen ValueError BEVOR sie ans Wire
        gehen, damit die Firmware-FATAL-Pfade nur Network-Korruption
        fangen.
        """
        # Validate strikt — raise wenn nicht konform
        self._validate_audio_flag(audio_type, params)

        ws = _devices.get(room)
        if ws is None:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_flag: not connected", "warning",
            )
            return False
        if ws.closed:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_flag: WS closed (id={id(ws)}) "
                f"— stale handle in _devices",
                "warning",
            )
            return False
        payload: dict[str, Any] = {
            "type": "audio_flag",
            "audio_type": audio_type,
            **params,
        }
        try:
            await asyncio.wait_for(
                ws.send_str(json.dumps(payload)),
                timeout=self._CHUNK_SEND_TIMEOUT_SEC,
            )
            self.channel_log(
                f"[FreeEcho.2 {room}] → audio_flag({audio_type}) sent "
                f"(ws id={id(ws)})"
            )
            return True
        except asyncio.TimeoutError:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_flag timeout — stream abort, "
                f"WS bleibt offen", "warning",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_flag error: {exc}", "warning",
            )
            return False

    async def send_audio_start(
        self,
        room: str,
        total_size: int | None = None,
        channels: int = 1,
    ) -> bool:
        """Signalisiert PCM-Stream-Setup an den FreeEcho.2.

        Wird IMMER nach einem ``audio_flag(music)`` oder ``audio_flag(tts)``
        gesendet, BEVOR die binary chunks fließen. Bei alarm/notification
        ohne TTS-Tail gibt's kein audio_start (Puck spielt lokale WAV).

        Format: 48 kHz int16 little-endian, Endpoint-Constraint der
        FreeEcho.2-Hardware (Rate ist fix, nicht verhandelbar). ``rate``
        wird nicht mitgeschickt — würde nur die Firmware-Whitelist-
        Validation FATAL triggern. ``channels`` ist 1 (mono, default für
        TTS/Speech) oder 2 (Stereo, für Music — echtes L/R an
        Stereo-BT-Speakern). Der Puck akzeptiert beide via
        ``freeecho2_client.c::audio_start``-Parser.

        ``total_size`` ist optional (typischerweise für TTS verfügbar,
        nicht für endlose Music-Streams). Puck nutzt es bisher nicht für
        Logik, kann aber für künftige Progress-LED genutzt werden.
        """
        ws = _devices.get(room)
        if ws is None:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_start: not connected", "warning",
            )
            return False
        if ws.closed:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_start: WS closed (id={id(ws)}) "
                f"— stale handle in _devices",
                "warning",
            )
            return False
        payload: dict[str, Any] = {"type": "audio_start"}
        if channels not in (1, 2):
            raise ValueError(
                f"send_audio_start: channels must be 1 or 2, got {channels!r}"
            )
        payload["channels"] = channels
        if total_size is not None:
            payload["total_size"] = int(total_size)
        try:
            await asyncio.wait_for(
                ws.send_str(json.dumps(payload)),
                timeout=self._CHUNK_SEND_TIMEOUT_SEC,
            )
            self.channel_log(
                f"[FreeEcho.2 {room}] → audio_start sent "
                f"(total_size={total_size}, ws id={id(ws)})"
            )
            return True
        except asyncio.TimeoutError:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_start timeout — stream abort, "
                f"WS bleibt offen", "warning",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_start error: {exc}", "warning",
            )
            return False

    async def send_audio_chunk(self, room: str, data: bytes) -> bool:
        ws = _devices.get(room)
        if ws is None:
            return False
        try:
            await asyncio.wait_for(
                ws.send_bytes(data),
                timeout=self._CHUNK_SEND_TIMEOUT_SEC,
            )
            return True
        except asyncio.TimeoutError:
            # KEIN WS-close mehr! Beim _resume kann der Puck kurz nicht
            # receive-ready sein (Source-Replace ~1-2s), TCP-Buffer voll,
            # send hängt 10s. Ein chunk-timeout heißt nur "dieser Stream
            # ist nicht mehr durchgekommen" — fifo_pump bricht via
            # ok=False ab, WS bleibt offen für Recovery (User-Wake,
            # neuer audio_play, etc.).
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_chunk timeout "
                f"({_fmt_mib(len(data))}) — stream abort, WS bleibt offen",
                "warning",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_chunk error: {exc}", "warning",
            )
            return False

    async def send_heartbeat(self, room: str) -> bool:
        """Heartbeat während aktivem Streaming an den FreeEcho.2 schicken.

        Wird vom FreeEcho2Stream alle 5 s aufgerufen, auch wenn die FIFO-Pump
        gerade pausiert (flow.pause / User-_pause). Liefert False bei
        Send-Timeout — dann ist der FreeEcho.2 nicht mehr erreichbar und der
        Stream räumt sich selbst auf.
        """
        ws = _devices.get(room)
        if ws is None:
            return False
        try:
            await asyncio.wait_for(
                ws.send_str(json.dumps({"type": "heartbeat"})),
                timeout=self._CHUNK_SEND_TIMEOUT_SEC,
            )
            return True
        except asyncio.TimeoutError:
            return False
        except Exception:  # noqa: BLE001
            return False

    async def send_audio_end(self, room: str) -> bool:
        ws = _devices.get(room)
        if ws is None:
            return False
        try:
            await asyncio.wait_for(
                ws.send_str(json.dumps({"type": "audio_end"})),
                timeout=self._CHUNK_SEND_TIMEOUT_SEC,
            )
            return True
        except asyncio.TimeoutError:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_end timeout — stream abort, "
                f"WS bleibt offen", "warning",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_end error: {exc}", "warning",
            )
            return False

    async def send_done(self, room: str, reason: str | None = None) -> bool:
        """SSoT for the ``done`` frame — the canonical turn boundary.

        ``done`` tells the puck the whole turn is finished: go back to IDLE
        and reply with the ``_done`` token. Both the reactive reply pipeline
        and the proactive alert queue send it after their audio_end so the
        terminal contract is symmetric (audio_end + done in every path)."""
        ws = _devices.get(room)
        if ws is None:
            return False
        payload: dict[str, str] = {"type": "done"}
        if reason is not None:
            payload["reason"] = reason
        try:
            await ws.send_str(json.dumps(payload))
            self.channel_log(f"[FreeEcho.2 {room}] → done sent (reason={reason})")
            return True
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"[FreeEcho.2 {room}] send_done error: {exc}", "warning",
            )
            return False
