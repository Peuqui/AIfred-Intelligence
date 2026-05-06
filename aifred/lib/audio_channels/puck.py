"""PuckChannel — Audio-Output an FreeEcho.2-Pucks via WebSocket-Bridge.

Eine ``PuckChannel``-Instanz verwaltet **alle** verbundenen Pucks. Pro
aktivem Target läuft genau eine ``PuckStream``-Instanz (mpv-Subprocess,
FIFO, IPC-Socket, FIFO-Reader).

``target_id``-Format: ``"freeecho2:<room>"``. Discovery der verbundenen Räume
läuft live über ``freeecho2_channel._devices``.

Format-Anforderung des Pucks (Hardware-fix): 48 kHz mono int16 LE PCM.
mpv resampled selbst, keine separate ffmpeg-Stage nötig.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ._puck_stream import PuckStream
from .base import AudioFormat, TargetInfo

if TYPE_CHECKING:
    from ..audio_sources import ResolvedSource
    from ..plugin_base import PluginContext

logger = logging.getLogger(__name__)


# Target-ID-Konvention: ``freeecho2:<room>`` — der Prefix ist gleich
# zum FreeEcho2-Channel-Plugin-Namen, damit Wake/Routing/Audio konsistent
# benannt sind. User-facing Label ist trotzdem "FreeEcho.2 <room>" mit Punkt.
TARGET_PREFIX = "freeecho2:"


def _parse_room(target_id: str) -> str | None:
    if not target_id.startswith(TARGET_PREFIX):
        return None
    return target_id[len(TARGET_PREFIX):] or None


def _connected_devices() -> dict[str, Any]:
    """Lazy-import um zirkuläre Imports beim Modul-Load zu vermeiden."""
    try:
        from ...plugins.channels.freeecho2_channel import _devices
        return _devices
    except ImportError:
        return {}


def _bridge() -> Any:
    """Liefere das FreeEchoChannel-Instanz oder None.

    Lazy-import — der Channel-Plugin ist beim Import des audio_channels-
    Moduls noch nicht zwingend geladen.
    """
    try:
        from ...plugins.channels.freeecho2_channel import FreeEchoChannel_instance
        return FreeEchoChannel_instance
    except ImportError:
        return None


class PuckChannel:
    """FreeEcho.2-Puck-Bridge mit eigener mpv-Pipeline pro Raum."""

    name = "freeecho2"
    required_format = AudioFormat(
        sample_rate=48000,
        channels=1,
        sample_format="s16le",
    )

    def __init__(self) -> None:
        self._streams: dict[str, PuckStream] = {}
        self._streams_lock = asyncio.Lock()

    def can_handle(self, target_id: str) -> bool:
        return target_id.startswith(TARGET_PREFIX)

    def list_targets(self, ctx: "PluginContext") -> list[TargetInfo]:
        return [
            TargetInfo(
                id=f"{TARGET_PREFIX}{room}",
                label=f"FreeEcho.2 {room}",
                ready=True,
            )
            for room in _connected_devices()
        ]

    async def _get_or_create_stream(self, room: str) -> PuckStream | None:
        bridge = _bridge()
        if bridge is None:
            logger.warning("PuckChannel: freeecho2 bridge not loaded — cannot stream")
            return None
        async with self._streams_lock:
            stream = self._streams.get(room)
            if stream is None:
                stream = PuckStream(
                    room,
                    send_start=bridge.send_audio_start,
                    send_chunk=bridge.send_audio_chunk,
                    send_end=bridge.send_audio_end,
                    send_heartbeat=bridge.send_heartbeat,
                )
                self._streams[room] = stream
            return stream

    async def play(
        self,
        src: "ResolvedSource",
        target_id: str,
        start_pos_sec: float | None,
        ctx: "PluginContext",
    ) -> dict[str, Any]:
        room = _parse_room(target_id)
        if not room:
            return {"success": False, "error": f"invalid puck target: {target_id}"}
        if room not in _connected_devices():
            return {
                "success": False,
                "target": target_id,
                "error": f"puck '{room}' is not connected",
            }
        stream = await self._get_or_create_stream(room)
        if stream is None:
            return {
                "success": False,
                "target": target_id,
                "error": "freeecho2 bridge unavailable",
            }
        # Streams haben keinen sinnvollen Resume-Punkt
        local_start = (
            start_pos_sec if (start_pos_sec and start_pos_sec > 0 and not src.is_stream) else None
        )
        try:
            result = await stream.start(
                src.uri, src.state_key, local_start,
                audio_type=getattr(src, "audio_type", "music"),
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "target": target_id,
                "error": f"puck stream start failed: {exc}",
            }
        return {
            "success": True,
            "label": src.label,
            "item": src.item,
            "uri": src.uri,
            "state_key": src.state_key,
            "is_stream": src.is_stream,
            "target": target_id,
            "resumed_at_sec": result.get("start_pos_sec", 0.0),
        }

    async def pause(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        room = _parse_room(target_id)
        if not room:
            return False
        stream = self._streams.get(room)
        if stream is None:
            return False
        return await stream.pause()

    async def resume(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        room = _parse_room(target_id)
        if not room:
            return False
        stream = self._streams.get(room)
        if stream is None:
            return False
        return await stream.resume()

    async def stop(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        room = _parse_room(target_id)
        if not room:
            return False
        async with self._streams_lock:
            stream = self._streams.pop(room, None)
        if stream is None:
            return False
        return await stream.stop()

    def supports_flow_control(self) -> bool:
        return True

    def notify_flow(self, target_id: str, state: str) -> None:  # type: ignore[override]
        """Bridge ruft das auf wenn der Puck einen flow-Frame schickt.

        ``state`` = "pause" → Pump des Streams blockt → mpv blockt am
        FIFO-write → kein OOM am Puck-Buffer. "resume" → weiterlaufen.

        ``target_id`` darf entweder die volle Form ``"freeecho2:<room>"``
        sein oder nur ``<room>`` (für den freeecho2_channel-WS-Receive-
        Loop einfacher). No-op wenn kein Stream für den Room aktiv ist.
        """
        room = _parse_room(target_id) or target_id
        stream = self._streams.get(room)
        if stream is None:
            logger.debug(
                "PuckChannel.notify_flow: no active stream for room=%s (state=%s)",
                room, state,
            )
            return
        stream.notify_flow(state)

    async def seek(
        self,
        target_id: str,
        position_sec: float,
        relative: bool = False,
        ctx: "PluginContext | None" = None,
    ) -> bool:
        room = _parse_room(target_id)
        if not room:
            return False
        stream = self._streams.get(room)
        if stream is None:
            return False
        return await stream.seek(float(position_sec), relative=relative)

    async def set_volume(
        self, target_id: str, percent: float, ctx: "PluginContext | None" = None
    ) -> bool:
        # Lautstärke wird am Puck selbst geregelt (Hardware-Knopf / Web-UI).
        # Server-seitige Volume-Anpassung würde mpv-Lautstärke ändern,
        # was aber den PCM-Stream am Reader trifft — fragil. Skip.
        return False

    async def set_speed(
        self, target_id: str, factor: float, ctx: "PluginContext | None" = None
    ) -> bool:
        room = _parse_room(target_id)
        if not room:
            return False
        stream = self._streams.get(room)
        if stream is None:
            return False
        # Speed via mpv-IPC funktioniert; PCM-Stream wird beim Resample-
        # Stage mit angepasstem Tempo geliefert.
        try:
            await stream._send({"command": ["set_property", "speed", float(factor)]})
            return True
        except Exception:  # noqa: BLE001
            return False

    async def status(self, target_id: str, ctx: "PluginContext | None" = None) -> dict[str, Any]:
        room = _parse_room(target_id)
        if not room:
            return {"running": False, "playing": False, "paused": False, "target": target_id}
        stream = self._streams.get(room)
        if stream is None:
            return {
                "running": False, "playing": False, "paused": False,
                "target": target_id,
                "ready": room in _connected_devices(),
            }
        return await stream.status()
