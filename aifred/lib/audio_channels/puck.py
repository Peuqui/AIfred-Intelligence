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
from ..logging_utils import log_message
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
        # Per-room queue state for sequential playback (audio_play_folder).
        # ``items``: list of {state_key, uri}. ``idx``: cursor into items.
        # ``audio_type``: VU/LED hint passed to each PuckStream.start().
        self._queues: dict[str, dict[str, Any]] = {}

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
        # Stop also clears any queued items — wake-word _stop / audio_stop
        # both end the entire playback session, not just the current track.
        self._queues.pop(room, None)
        async with self._streams_lock:
            stream = self._streams.pop(room, None)
        if stream is None:
            return False
        return await stream.stop()

    async def play_queue(
        self,
        items: list[dict[str, str]],
        target_id: str,
        ctx: "PluginContext",
        audio_type: str = "music",
        shuffle: bool = False,
    ) -> dict[str, Any]:
        """Sequentielles Playback einer Item-Liste auf einem Puck-Target.

        ``items`` ist ``[{"state_key": ..., "uri": ...}, ...]`` in der
        gewuenschten Abspiel-Reihenfolge (sortiert oder geshuffled vom
        Caller). Mit ``shuffle=True`` wird die Liste hier zusaetzlich
        gemischt — der Caller entscheidet ueber den Default.

        Mechanismus: erstes Item wird via ``PuckStream.start()`` gestartet,
        plus ein on-EOF-Callback installiert. mpv signalisiert das natuerliche
        Ende des Tracks via ``eof-reached``-IPC-Event → Callback feuert →
        naechster Track wird mit eigenem PuckStream.start() gestartet (neuer
        mpv-Subprocess pro Track, ~100 ms Pause dazwischen — kein gapless,
        aber sauber pro Track Position-Save und kein Playlist-State-Krampf).
        """
        import random

        room = _parse_room(target_id)
        if not room:
            return {"success": False, "error": f"invalid puck target: {target_id}"}
        if room not in _connected_devices():
            return {
                "success": False,
                "target": target_id,
                "error": f"puck '{room}' is not connected",
            }
        if not items:
            return {"success": False, "error": "empty queue"}

        ordered = list(items)
        if shuffle:
            random.shuffle(ordered)

        self._queues[room] = {
            "items": ordered,
            "idx": 0,
            "audio_type": audio_type,
            "ctx": ctx,
        }

        first_uri = ordered[0]["uri"]
        first_key = ordered[0]["state_key"]
        result = await self._start_queue_item(room, first_uri, first_key)
        if not result.get("success"):
            self._queues.pop(room, None)
            return result

        return {
            "success": True,
            "target": target_id,
            "queued_count": len(ordered),
            "shuffle": shuffle,
            "first": {"state_key": first_key, "uri": first_uri},
        }

    async def _start_queue_item(
        self, room: str, uri: str, state_key: str,
    ) -> dict[str, Any]:
        """Start a single queue item and wire up the EOF-advance callback."""
        stream = await self._get_or_create_stream(room)
        if stream is None:
            return {"success": False, "error": "freeecho2 bridge unavailable"}

        queue_state = self._queues.get(room, {})
        audio_type = queue_state.get("audio_type", "music")

        # Install advance callback BEFORE start so it's set when mpv fires
        # eof-reached. The callback is one-shot (PuckStream snapshots and
        # clears it before invoking) — every track gets its own.
        async def _advance() -> None:
            await self._advance_queue(room)

        stream._on_eof_cb = _advance

        try:
            await stream.start(uri, state_key, None, audio_type=audio_type)
        except Exception as exc:  # noqa: BLE001
            stream._on_eof_cb = None
            return {"success": False, "error": f"puck stream start failed: {exc}"}
        return {"success": True}

    async def _advance_queue(self, room: str) -> None:
        """Called from PuckStream EOF callback — start next item or end."""
        queue_state = self._queues.get(room)
        if queue_state is None:
            return  # queue was stopped externally
        queue_state["idx"] += 1
        if queue_state["idx"] >= len(queue_state["items"]):
            log_message(f"PuckChannel[{room}]: queue finished ({len(queue_state['items'])} tracks)")
            self._queues.pop(room, None)
            return
        next_item = queue_state["items"][queue_state["idx"]]
        log_message(
            f"PuckChannel[{room}]: queue advance "
            f"{queue_state['idx'] + 1}/{len(queue_state['items'])} → {next_item['state_key']}"
        )
        await self._start_queue_item(room, next_item["uri"], next_item["state_key"])

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
