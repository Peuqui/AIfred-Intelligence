"""FreeEcho2Channel — Audio-Output an FreeEcho.2-Speaker via WebSocket-Bridge.

Eine ``FreeEcho2Channel``-Instanz verwaltet **alle** verbundenen Speaker.
Pro aktivem Target läuft genau eine ``FreeEcho2Stream``-Instanz (mpv-
Subprocess, FIFO, IPC-Socket, FIFO-Reader).

``target_id``-Format: ``"freeecho2:<room>"``. Discovery der verbundenen
Räume läuft live über ``freeecho2_channel._devices``.

Format-Anforderung des Speakers (Hardware-fix): 48 kHz mono int16 LE PCM.
mpv resampled selbst, keine separate ffmpeg-Stage nötig.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import TYPE_CHECKING, Any, Callable, Coroutine, TypeVar

from ._audio_orchestrator import AudioOrchestrator
from ._freeecho2_stream import FreeEcho2Stream
from ..logging_utils import log_message
from ..loudness import build_music_filter_chain
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
        return dict(_devices)
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


_F = TypeVar("_F", bound=Callable[..., Coroutine[Any, Any, Any]])


def _on_ws_loop(method: _F) -> _F:
    """Public-Methode auf dem WebSocket-Server-Loop ausführen.

    Browser-/Scheduler-initiierte Audio-Befehle (play, pause, …) laufen im
    Chat-/Tool-Pipeline-Loop, der WebSocket + mpv-FIFO-Pump + mpv-IPC leben
    aber im aiohttp-Loop. Ohne Pinning sendet der Pump cross-loop und bricht
    ab. ``run_on_ws_loop`` ist die SSoT-Marshalling-Funktion; läuft der
    Aufruf bereits im ws-Loop (reaktiver Puck-Request), awaited sie direkt."""
    @functools.wraps(method)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        from ...plugins.channels.freeecho2_channel import run_on_ws_loop
        return await run_on_ws_loop(method(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


class FreeEcho2Channel:
    """FreeEcho.2-Bridge mit eigener mpv-Pipeline pro Raum."""

    name = "freeecho2"
    required_format = AudioFormat(
        sample_rate=48000,
        channels=1,
        sample_format="s16le",
    )

    def __init__(self) -> None:
        self._streams: dict[str, FreeEcho2Stream] = {}
        self._streams_lock = asyncio.Lock()
        # Per-room queue state for sequential playback (audio_play_folder).
        # ``items``: list of {state_key, uri}. ``idx``: cursor into items.
        # ``audio_type``: VU/LED hint passed to each FreeEcho2Stream.start().
        self._queues: dict[str, dict[str, Any]] = {}
        # Per-room AudioOrchestrator: managed den single-pump-pfad und
        # type-aware pause/resume/stop. Wird lazy beim ersten Zugriff
        # angelegt damit Rooms ohne Audio-Aktivitaet keinen State haben.
        self._orchestrators: dict[str, AudioOrchestrator] = {}

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

    async def _get_or_create_stream(self, room: str) -> FreeEcho2Stream | None:
        bridge = _bridge()
        if bridge is None:
            logger.warning("FreeEcho2Channel: freeecho2 bridge not loaded — cannot stream")
            return None
        async with self._streams_lock:
            stream = self._streams.get(room)
            if stream is None:
                stream = FreeEcho2Stream(
                    room,
                    send_flag=bridge.send_audio_flag,
                    send_start=bridge.send_audio_start,
                    send_chunk=bridge.send_audio_chunk,
                    send_end=bridge.send_audio_end,
                    send_heartbeat=bridge.send_heartbeat,
                )
                # Beim Send-Fail (chunk timeout/error) sauber abbrechen:
                # Server-State und Puck-State synchron halten via orc.stop()
                # → mpv terminate + audio_end an Puck. Sonst koennte mpv
                # weiterlaufen waehrend der Puck nichts mehr empfaengt.
                stream._on_send_failed_cb = self._make_send_failed_cb(room)
                self._streams[room] = stream
            return stream

    def _make_send_failed_cb(self, room: str) -> Any:
        """Bind room-specific cleanup callback for stream-send-failures."""
        async def _cb() -> None:
            target_id = f"{TARGET_PREFIX}{room}"
            log_message(
                f"FreeEcho2Channel[{room}]: send failed → orc.stop() "
                f"(state-sync mit Puck)"
            )
            try:
                await self.stop(target_id)
            except Exception as exc:  # noqa: BLE001
                log_message(
                    f"FreeEcho2Channel[{room}]: stop() in send-failed-cb "
                    f"failed: {exc}", "warning",
                )
        return _cb

    def _make_natural_end_cb(self, target_id: str) -> Any:
        """Terminal-Sequenz wenn ein Einzeltitel von selbst ausläuft (mpv-EOF,
        FIFO leer): ``stop()`` → orc.stop → audio_end + done → Puck geht IDLE.
        Symmetrisch zum User-Stop und zum Alert-Pfad (Design A)."""
        room = _parse_room(target_id) or target_id
        async def _cb() -> None:
            log_message(
                f"FreeEcho2Channel[{room}]: track self-ended → stop() "
                f"(audio_end + done)"
            )
            try:
                await self.stop(target_id)
            except Exception as exc:  # noqa: BLE001
                log_message(
                    f"FreeEcho2Channel[{room}]: natural-end stop() failed: {exc}",
                    "warning",
                )
        return _cb

    def _get_or_create_orchestrator(self, room: str) -> AudioOrchestrator | None:
        """Lazy-Init des AudioOrchestrator pro Room. Returnt None wenn
        die Bridge nicht geladen ist."""
        if room in self._orchestrators:
            return self._orchestrators[room]
        bridge = _bridge()
        if bridge is None:
            logger.warning(
                "FreeEcho2Channel: freeecho2 bridge not loaded — cannot orchestrate"
            )
            return None
        orc = AudioOrchestrator(room, bridge)
        self._orchestrators[room] = orc
        return orc

    def get_orchestrator(self, room: str) -> AudioOrchestrator | None:
        """Public Accessor — vom freeecho2-Plugin (send_reply) genutzt
        um TTS via Orchestrator zu pumpen statt direkt am Wire."""
        return self._get_or_create_orchestrator(room)

    @_on_ws_loop
    async def play(
        self,
        src: "ResolvedSource",
        target_id: str,
        start_pos_sec: float | None,
        ctx: "PluginContext",
    ) -> dict[str, Any]:
        room = _parse_room(target_id)
        if not room:
            return {"success": False, "error": f"invalid FreeEcho.2 target: {target_id}"}
        if room not in _connected_devices():
            return {
                "success": False,
                "target": target_id,
                "error": f"FreeEcho.2 '{room}' is not connected",
            }
        stream = await self._get_or_create_stream(room)
        if stream is None:
            return {
                "success": False,
                "target": target_id,
                "error": "freeecho2 bridge unavailable",
            }
        # Einzeltitel: läuft er von selbst aus, schließt der natural-end-cb
        # die Turn-Grenze (audio_end + done). Bei Queue wird er in
        # _start_queue_item auf None gesetzt (dort steuert _on_eof_cb).
        stream._on_natural_end_cb = self._make_natural_end_cb(target_id)
        # Streams haben keinen sinnvollen Resume-Punkt
        local_start = (
            start_pos_sec if (start_pos_sec and start_pos_sec > 0 and not src.is_stream) else None
        )
        # Loudness-Normalisierung + Fade nur fuer lokale Music-Files —
        # HTTP-Streams haben kein File und keine bekannte Dauer, Speech/
        # Alarm sind kalibriert und sollen nicht angefasst werden.
        audio_type = getattr(src, "audio_type", "music")
        audio_filters: list[str] | None = None
        if audio_type == "music" and not src.is_stream:
            audio_filters = build_music_filter_chain(src.uri)
        try:
            result = await stream.start(
                src.uri, src.state_key, local_start,
                audio_type=audio_type,
                audio_filters=audio_filters,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "target": target_id,
                "error": f"FreeEcho.2 stream start failed: {exc}",
            }
        # AudioOrchestrator informieren: aktive Source ist jetzt music
        orc = self._get_or_create_orchestrator(room)
        if orc is not None:
            await orc.play_music(stream)
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

    @_on_ws_loop
    async def pause(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        """Type-aware pause via AudioOrchestrator.

        Music/TTS → echtes Pause (Position bleibt). Alarm/Notification →
        wird zum Stop (transient, kein sinnvolles Resume).
        """
        room = _parse_room(target_id)
        if not room:
            return False
        orc = self._orchestrators.get(room)
        if orc is None:
            return False
        return await orc.pause()

    @_on_ws_loop
    async def resume(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        """Resume via AudioOrchestrator. No-op wenn nichts pausiert ist."""
        room = _parse_room(target_id)
        if not room:
            return False
        orc = self._orchestrators.get(room)
        if orc is None:
            return False
        return await orc.resume()

    @_on_ws_loop
    async def stop(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        """Stop alles via AudioOrchestrator + cleanup queue + stream-state.

        Wake-word _stop / audio_stop beenden die ganze Session, nicht
        nur den aktuellen Track der play_queue.
        """
        room = _parse_room(target_id)
        if not room:
            return False
        # Folder-Queue (audio_play_folder) auch leeren
        self._queues.pop(room, None)
        # Orchestrator → stop (cancelled TTS-pump, raeumt mpv-stream auf
        # via _reset_active_unlocked, sendet audio_end)
        orc = self._orchestrators.get(room)
        stopped = False
        if orc is not None:
            stopped = await orc.stop()
        # Stream-Slot freigeben (Orchestrator hat ihn schon gestoppt
        # via _reset_active_unlocked, aber wir muessen den Pointer
        # auch aus _streams entfernen damit beim naechsten play() ein
        # frischer Stream entsteht)
        async with self._streams_lock:
            self._streams.pop(room, None)
        return stopped

    @_on_ws_loop
    async def play_queue(
        self,
        items: list[dict[str, str]],
        target_id: str,
        ctx: "PluginContext",
        audio_type: str = "music",
        shuffle: bool = False,
    ) -> dict[str, Any]:
        """Sequentielles Playback einer Item-Liste auf einem FreeEcho.2-Target.

        ``items`` ist ``[{"state_key": ..., "uri": ...}, ...]`` in der
        gewuenschten Abspiel-Reihenfolge (sortiert oder geshuffled vom
        Caller). Mit ``shuffle=True`` wird die Liste hier zusaetzlich
        gemischt — der Caller entscheidet ueber den Default.

        Mechanismus: erstes Item wird via ``FreeEcho2Stream.start()`` gestartet,
        plus ein on-EOF-Callback installiert. mpv signalisiert das natuerliche
        Ende des Tracks via ``eof-reached``-IPC-Event → Callback feuert →
        naechster Track wird mit eigenem FreeEcho2Stream.start() gestartet (neuer
        mpv-Subprocess pro Track, ~100 ms Pause dazwischen — kein gapless,
        aber sauber pro Track Position-Save und kein Playlist-State-Krampf).
        """
        import random

        room = _parse_room(target_id)
        if not room:
            return {"success": False, "error": f"invalid FreeEcho.2 target: {target_id}"}
        if room not in _connected_devices():
            return {
                "success": False,
                "target": target_id,
                "error": f"FreeEcho.2 '{room}' is not connected",
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
        # eof-reached. The callback is one-shot (FreeEcho2Stream snapshots and
        # clears it before invoking) — every track gets its own.
        async def _advance() -> None:
            await self._advance_queue(room)

        stream._on_eof_cb = _advance
        # Queue steuert Track-Übergänge selbst über _on_eof_cb — der
        # natural-end-cb (Einzeltitel-Terminal) darf hier NICHT feuern,
        # sonst Race zwischen advance() und stop() am Track-Wechsel.
        stream._on_natural_end_cb = None

        # Queue-Items koennen prinzipiell Streams sein (uri startet mit
        # http://). In der Praxis sind's lokale Files (audio_play_folder),
        # aber sicher ist sicher: nur lokale Music-Files normalisieren.
        is_local = not uri.startswith(("http://", "https://"))
        audio_filters: list[str] | None = None
        if audio_type == "music" and is_local:
            audio_filters = build_music_filter_chain(uri)
        try:
            await stream.start(
                uri, state_key, None,
                audio_type=audio_type,
                audio_filters=audio_filters,
            )
        except Exception as exc:  # noqa: BLE001
            stream._on_eof_cb = None
            return {"success": False, "error": f"FreeEcho.2 stream start failed: {exc}"}
        return {"success": True}

    async def _advance_queue(self, room: str) -> None:
        """Called from FreeEcho2Stream EOF callback — start next item or end."""
        queue_state = self._queues.get(room)
        if queue_state is None:
            return  # queue was stopped externally
        queue_state["idx"] += 1
        if queue_state["idx"] >= len(queue_state["items"]):
            log_message(f"FreeEcho2Channel[{room}]: queue finished ({len(queue_state['items'])} tracks)")
            self._queues.pop(room, None)
            return
        next_item = queue_state["items"][queue_state["idx"]]
        log_message(
            f"FreeEcho2Channel[{room}]: queue advance "
            f"{queue_state['idx'] + 1}/{len(queue_state['items'])} → {next_item['state_key']}"
        )
        await self._start_queue_item(room, next_item["uri"], next_item["state_key"])

    def supports_flow_control(self) -> bool:
        return True

    def get_stream_start_offset(self, target_id: str) -> float | None:
        """Return die Track-Position bei der der aktuelle Stream startete.

        Wird vom WS-Plugin gebraucht um Puck-``consumed_ms`` (= seit
        current-stream-start) auf eine absolute Track-Position
        umzurechnen. None wenn kein Stream aktiv ist.

        ``target_id`` darf voll (``freeecho2:<room>``) oder nur
        ``<room>`` sein.
        """
        room = _parse_room(target_id) or target_id
        stream = self._streams.get(room)
        if stream is None:
            return None
        return stream.stream_start_offset_sec

    def notify_flow(self, target_id: str, state: str) -> None:  # type: ignore[override]
        """Bridge ruft das auf wenn der FreeEcho.2 einen flow-Frame schickt.

        ``state`` = "pause" → Pump des Streams blockt → mpv blockt am
        FIFO-write → kein OOM am FreeEcho.2-Buffer. "resume" → weiterlaufen.

        ``target_id`` darf entweder die volle Form ``"freeecho2:<room>"``
        sein oder nur ``<room>`` (für den freeecho2_channel-WS-Receive-
        Loop einfacher). No-op wenn kein Stream für den Room aktiv ist.
        """
        room = _parse_room(target_id) or target_id
        stream = self._streams.get(room)
        if stream is None:
            logger.debug(
                "FreeEcho2Channel.notify_flow: no active stream for room=%s (state=%s)",
                room, state,
            )
            return
        stream.notify_flow(state)

    @_on_ws_loop
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
        return bool(await stream.seek(float(position_sec), relative=relative))

    @_on_ws_loop
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
            await stream._ipc.send({"command": ["set_property", "speed", float(factor)]})
            return True
        except Exception:  # noqa: BLE001
            return False

    @_on_ws_loop
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
        status: dict[str, Any] = await stream.status()
        return status
