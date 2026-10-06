"""Public WS-Bridge — Audio-Bus-Frame-API.

Audio-Bus-Protokoll (Phase 5.0): siehe docs/de/architecture/
audio-pipeline.md "Audio-Bus-Refactor" für Frame-Sequenzen und
Tupel-Whitelist. Protokoll v2 — jeder Strom, auch Alarm/Ansage/Türklingel:

  1. send_audio_flag(room, audio_type, start_tone=B) — Type-Setting (LED+VU)
  2. send_audio_start(room, total_size?)             — PCM-Stream-Setup
  3. send_audio_chunk(room, bytes)                   — 0..n mal
  4. send_audio_end(room, end_tone=B)                — End-Marker

dazu pause_stream(room, timeout): server-initiierte Pause vor einer Ansage.

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

from ._shared import _devices, _fmt_mib, _pause_acks


class WsBridgeMixin(BaseChannel):
    """Frame-Sende-API des FreeEcho.2-Channels (audio_flag/start/chunk/end,
    heartbeat, done)."""

    _CHUNK_SEND_TIMEOUT_SEC = 10.0

    # Whitelist-Validation für audio_flag/audio_start. Schema-Verletzung
    # wird server-seitig per ValueError geblockt BEVOR sie ans Wire geht
    # — die Firmware-FATAL-Pfade sehen wir damit nur bei echter
    # Network-Korruption, nicht bei Server-Logik-Bugs. Strikt:
    # unbekannte Felder, falsche Typen, fehlende Pflicht-Felder → raise.
    # Protokoll v2: jeder Typ trägt start_tone (Beginn-Ton am Puck); danach folgt
    # IMMER audio_start, 0..n Chunks und audio_end(end_tone).
    _AUDIO_TYPE_SCHEMA: dict[str, set[str]] = {
        "music":        {"start_tone"},   # Stereo-VU am Puck
        "speech":       {"start_tone"},   # Voice-VU (Hoerbuch / Podcast / Lesung)
        "tts":          {"start_tone"},   # Voice-VU (XTTS-Generator-Output)
        "alarm":        {"start_tone"},   # einmal abspielen; Server loopt
        "notification": {"start_tone"},   # Ansage / Türklingel
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
        # Type-Checks pro Feld: alle Felder sind strikt bool (kein int-Aliasing)
        for field, v in params.items():
            if not isinstance(v, bool):
                raise ValueError(
                    f"audio_flag({audio_type!r}): {field} must be bool, got {v!r}"
                )

    async def _send_frame(
        self,
        room: str,
        payload: "dict[str, Any] | bytes",
        frame_name: str,
        *,
        quiet: bool = False,
        timeout_note: str = "",
    ) -> bool:
        """SSOT für den Frame-Versand aller Sende-Methoden: Device-Lookup,
        Stale-Handle-Check (``ws.closed``), Send-Timeout und Fehler-Logging.

        Ein Timeout schließt den WS bewusst NICHT: beim _resume kann der
        Puck kurz nicht receive-ready sein (Source-Replace ~1-2 s), der
        TCP-Buffer läuft voll und der Send hängt. Timeout heißt nur "dieser
        Stream kam nicht durch" — der Aufrufer bricht via False ab, die
        Verbindung bleibt für Recovery offen (User-Wake, neues audio_play).
        ``quiet`` unterdrückt alle Logs (Heartbeat-Takt).
        """
        ws = _devices.get(room)
        if ws is None:
            if not quiet:
                self.channel_log(
                    f"[FreeEcho.2 {room}] {frame_name}: not connected", "warning",
                )
            return False
        if ws.closed:
            if not quiet:
                self.channel_log(
                    f"[FreeEcho.2 {room}] {frame_name}: WS closed (id={id(ws)}) "
                    f"— stale handle in _devices",
                    "warning",
                )
            return False
        try:
            send = (
                ws.send_bytes(payload)
                if isinstance(payload, bytes)
                else ws.send_str(json.dumps(payload))
            )
            await asyncio.wait_for(send, timeout=self._CHUNK_SEND_TIMEOUT_SEC)
            return True
        except asyncio.TimeoutError:
            if not quiet:
                self.channel_log(
                    f"[FreeEcho.2 {room}] {frame_name} timeout{timeout_note} "
                    f"— stream abort, WS stays open", "warning",
                )
            return False
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                self.channel_log(
                    f"[FreeEcho.2 {room}] {frame_name} error: {exc}", "warning",
                )
            return False

    async def send_audio_flag(
        self, room: str, audio_type: str, **params: Any
    ) -> bool:
        """Schickt ein audio_flag-Frame: Type-Setting (LED + VU + Source-Verhalten).

        Wird verwendet für (siehe Doku audio-pipeline.md):
        - vor audio_start bei JEDEM Typ (initial setting, ``start_tone`` Pflicht)
        - mid-stream für Type-Switch (z.B. music → tts während Music läuft;
          gleiche Source bleibt, 30 ms Linear-Fade auf Puck-Seite): dort
          ``start_tone=False``, ein Beginn-Ton würde die Source ersetzen

        ``audio_type`` muss in der Whitelist sein. ``params`` sind type-
        spezifisch (siehe ``_AUDIO_TYPE_SCHEMA``). Server-side strikt
        validiert — ungültige Tupel raisen ValueError BEVOR sie ans Wire
        gehen, damit die Firmware-FATAL-Pfade nur Network-Korruption
        fangen.
        """
        # Validate strikt — raise wenn nicht konform
        self._validate_audio_flag(audio_type, params)

        ok = await self._send_frame(
            room,
            {"type": "audio_flag", "audio_type": audio_type, **params},
            "send_audio_flag",
        )
        if ok:
            self.channel_log(f"[FreeEcho.2 {room}] → audio_flag({audio_type}) sent")
        return ok

    async def send_audio_start(
        self,
        room: str,
        total_size: int | None = None,
        channels: int = 1,
    ) -> bool:
        """Signalisiert PCM-Stream-Setup an den FreeEcho.2.

        Wird IMMER nach einem ``audio_flag`` gesendet, BEVOR die binary chunks
        fließen (auch bei alarm/notification; ohne Sprache folgen null Chunks
        und gleich das audio_end — Türklingel).

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
        if channels not in (1, 2):
            raise ValueError(
                f"send_audio_start: channels must be 1 or 2, got {channels!r}"
            )
        payload: dict[str, Any] = {"type": "audio_start", "channels": channels}
        if total_size is not None:
            payload["total_size"] = int(total_size)
        ok = await self._send_frame(room, payload, "send_audio_start")
        if ok:
            self.channel_log(
                f"[FreeEcho.2 {room}] → audio_start sent (total_size={total_size})"
            )
        return ok

    async def send_audio_chunk(self, room: str, data: bytes) -> bool:
        return await self._send_frame(
            room, data, "send_audio_chunk",
            timeout_note=f" ({_fmt_mib(len(data))})",
        )

    async def send_heartbeat(self, room: str) -> bool:
        """Heartbeat während aktivem Streaming an den FreeEcho.2 schicken.

        Wird vom FreeEcho2Stream alle 5 s aufgerufen, auch wenn die FIFO-Pump
        gerade pausiert (flow.pause / User-_pause). Liefert False bei
        Send-Timeout — dann ist der FreeEcho.2 nicht mehr erreichbar und der
        Stream räumt sich selbst auf. ``quiet``: der 5-s-Takt würde sonst
        das Log fluten.
        """
        return await self._send_frame(
            room, {"type": "heartbeat"}, "send_heartbeat", quiet=True,
        )

    async def send_audio_end(self, room: str, *, end_tone: bool) -> bool:
        """``audio_end`` mit Pflichtfeld ``end_tone`` (Ende-Ton am Puck). Bei Stopp
        und Abbruch ``False``: ein Abbruch spielt keinen Ende-Ton."""
        if not isinstance(end_tone, bool):
            raise ValueError(f"audio_end: end_tone must be bool, got {end_tone!r}")
        return await self._send_frame(
            room, {"type": "audio_end", "end_tone": end_tone}, "send_audio_end",
        )

    async def pause_stream(self, room: str, timeout_sec: float) -> None:
        """Server-initiierte Pause: den Puck bitten, den laufenden Strom
        anzuhalten, und auf seine Bestätigung warten (commands.py löst das
        Future, sobald Stream gestoppt und Position gespeichert sind).

        Der Puck nimmt eine Ansage nur im Leerlauf oder in der Pause an;
        ohne Bestätigung wird sie verworfen. Raises bei Sendefehler/Timeout.
        """
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        _pause_acks[room] = future
        try:
            if not await self._send_frame(
                room, {"type": "wake", "room": room, "agent": "_pause"}, "send_pause",
            ):
                raise ConnectionError(f"pause command to '{room}' could not be sent")
            await asyncio.wait_for(future, timeout=timeout_sec)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"puck '{room}' did not acknowledge _pause within {timeout_sec:.1f} s"
            ) from exc
        finally:
            _pause_acks.pop(room, None)

    async def send_done(self, room: str, reason: str | None = None) -> bool:
        """SSoT for the ``done`` frame — the canonical turn boundary.

        ``done`` tells the puck the whole turn is finished: go back to IDLE
        and reply with the ``_done`` token. Both the reactive reply pipeline
        and the proactive alert queue send it after their audio_end so the
        terminal contract is symmetric (audio_end + done in every path)."""
        payload: dict[str, str] = {"type": "done"}
        if reason is not None:
            payload["reason"] = reason
        ok = await self._send_frame(room, payload, "send_done")
        if ok:
            self.channel_log(f"[FreeEcho.2 {room}] → done sent (reason={reason})")
        return ok
