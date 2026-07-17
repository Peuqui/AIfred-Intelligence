"""Proaktive Alarm-Warteschlange (server-owned, pro room).

"Smart server, stupid device": der Puck puffert nicht (Queue-0), der
Server serialisiert. Pro room eine Queue + ein langlebiger Worker, der je
Item den lokalen Chime + TTS abspielt und auf das Puck-Fertig-Signal
(wake.agent=_done → _playback_done[room].set()) wartet, bevor das nächste
Item rausgeht. So stauen sich N Alarme sauber auf und werden nacheinander
angesagt — nichts wird verworfen. Entkoppelt vom Emit-Pfad (enqueue kehrt
sofort zurück, blockiert den Vision-Watcher nicht).
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine

_alert_queues: dict[str, "asyncio.Queue[tuple[str, bytes | None]]"] = {}
_alert_workers: dict[str, asyncio.Task] = {}
_playback_done: dict[str, asyncio.Event] = {}
# Event-Loop des WebSocket-Servers (aiohttp). Der proaktive Emit-Pfad
# (Vision-Watcher → announce-Tool) läuft im Chat-/Tool-Pipeline-Loop, der
# WebSocket lebt aber hier. Queue, Worker und das TTS-Pumpen müssen im
# ws-Loop laufen, sonst sendet der Pump aus einem fremden Loop
# (RuntimeError: "got Future attached to a different loop" → Pump-Abbruch
# mitten in der Ansage). enqueue_alert marshallt deshalb auf diesen Loop.
_ws_loop: "asyncio.AbstractEventLoop | None" = None
# Fallback-Marge für das _done-Warten: der Worker wartet content-abhängig
# = tatsächliche Wiedergabe-Dauer (aus der PCM-Größe) + diese Marge (Chime +
# Netz + Sicherheit). So wird eine lange Ansage NIE abgeschnitten (Timeout
# immer > echte Wiedergabe), und bei ausbleibendem _done erholt sich die
# Queue trotzdem zügig statt 2 Minuten zu hängen.
_PLAYBACK_DONE_MARGIN_SEC = 15.0
# 48 kHz, int16, mono → Bytes pro Sekunde Wiedergabe.
_PCM_BYTES_PER_SEC = 96000.0


async def run_on_ws_loop(coro: "Coroutine[Any, Any, Any]") -> Any:
    """Eine Coroutine auf dem WebSocket-Server-Loop ausführen, awaitbar aus
    JEDEM Loop. SSoT für proaktive Puck-Audio-Ausgabe.

    Jegliches ``ws.send_bytes`` (TTS-Pump, mpv-FIFO-Pump) und jede mpv-IPC-
    Operation muss in dem Loop laufen, dem der WebSocket gehört — sonst wirft
    asyncio "got Future attached to a different loop" und der Pump bricht
    mitten in der Ausgabe ab. Proaktive Aufrufer (Announce-Queue, Browser-
    Audio-Befehle) laufen im Chat-/Tool-Pipeline-Loop; ihre Orchestrator-/
    Stream-Arbeit wird hierüber auf den ws-Loop geschoben. Läuft der Aufrufer
    bereits im ws-Loop (reaktiver Puck-Request), wird direkt awaited."""
    try:
        current: "asyncio.AbstractEventLoop | None" = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if _ws_loop is not None and _ws_loop is not current:
        fut = asyncio.run_coroutine_threadsafe(coro, _ws_loop)
        return await asyncio.wrap_future(fut)
    return await coro


def _playback_done_event(room: str) -> asyncio.Event:
    evt = _playback_done.get(room)
    if evt is None:
        evt = asyncio.Event()
        _playback_done[room] = evt
    return evt


def signal_playback_done(room: str) -> None:
    """Vom WS-Handler bei ``wake.agent=_done`` gerufen — weckt den Worker für
    das nächste Queue-Item."""
    _playback_done_event(room).set()


async def _enqueue_on_ws_loop(room: str, audio_type: str, tts_pcm: "bytes | None") -> None:
    """Put + Worker-Sicherstellung — läuft IMMER im ws-Loop, damit Queue,
    Worker und das spätere ws.send_bytes loop-konsistent sind."""
    queue = _alert_queues.get(room)
    if queue is None:
        queue = asyncio.Queue()
        _alert_queues[room] = queue
    await queue.put((audio_type, tts_pcm))
    worker = _alert_workers.get(room)
    if worker is None or worker.done():
        _alert_workers[room] = asyncio.create_task(
            _alert_worker(room), name=f"freeecho2-alert-worker-{room}",
        )


async def enqueue_alert(room: str, audio_type: str, tts_pcm: "bytes | None") -> None:
    """Proaktiven Alarm in die room-Queue legen + Worker sicherstellen.
    Kehrt sofort zurück (entkoppelt vom Emit-Pfad).

    Der Emit-Pfad läuft typischerweise in einem anderen Event-Loop als der
    WebSocket; ``run_on_ws_loop`` schiebt Queue/Worker auf den ws-Loop, damit
    der Pump nicht cross-loop sendet."""
    await run_on_ws_loop(_enqueue_on_ws_loop(room, audio_type, tts_pcm))


async def _alert_worker(room: str) -> None:
    """Langlebiger per-room-Worker: spielt Queue-Items seriell, wartet je
    Item auf das _done-Signal des Pucks (mit Timeout). Musik-Pause/Resume
    um den Queue-Lauf kommt mit Firmware-Phase 2 dazu."""
    from ....lib import audio_channels
    from ....lib.logging_utils import log_message

    queue = _alert_queues[room]
    while True:
        audio_type, tts_pcm = await queue.get()
        try:
            ch = audio_channels.resolve(f"freeecho2:{room}")
            orc = (
                ch.get_orchestrator(room)
                if ch is not None and hasattr(ch, "get_orchestrator")
                else None
            )
            if orc is None:
                log_message(
                    f"[FreeEcho.2 {room}] alert worker: no orchestrator — drop",
                    "warning",
                )
                continue
            with_tts = tts_pcm is not None
            # play_* wartet jetzt bis der Tail-Pump durch ist (audio_end raus).
            if audio_type == "alarm":
                await orc.play_alarm(with_tts=with_tts, tts_pcm=tts_pcm)
            else:
                await orc.play_notification(with_tts=with_tts, tts_pcm=tts_pcm)
            # FRISCHES Event pro Item, publiziert erst NACH der Wiedergabe und
            # direkt vor send_done: Das frühere clear-then-wait auf dem
            # wiederverwendeten per-Room-Event konnte während der gesamten
            # Abspieldauer von einem verspäteten _done eines FRÜHEREN Turns
            # geweckt werden. Ein stale _done setzt jetzt das alte
            # Event-Objekt — nicht dieses.
            evt = asyncio.Event()
            _playback_done[room] = evt
            # Design A: kanonische Turn-Grenze. done sagt dem Puck "geh auf
            # IDLE + quittiere mit _done". Symmetrisch zum reaktiven Pfad
            # (audio_end + done in jedem Pfad).
            await orc.bridge.send_done(room)
            # Content-abhängiger Timeout: echte Wiedergabe-Dauer (PCM-Größe)
            # + Marge als Fallback. Normal weckt das _done des Pucks (auf das
            # done-Frame) den Worker sofort; der Timeout greift nur wenn der
            # Puck stumm bleibt.
            playback_sec = (len(tts_pcm) / _PCM_BYTES_PER_SEC) if tts_pcm else 0.0
            done_timeout = playback_sec + _PLAYBACK_DONE_MARGIN_SEC
            try:
                await asyncio.wait_for(evt.wait(), timeout=done_timeout)
            except asyncio.TimeoutError:
                log_message(
                    f"[FreeEcho.2 {room}] alert worker: no _done within "
                    f"{done_timeout:.0f}s (playback ~{playback_sec:.0f}s) — proceeding",
                    "warning",
                )
        except Exception as e:  # noqa: BLE001
            log_message(
                f"[FreeEcho.2 {room}] alert worker error: {e}", "warning",
            )
        finally:
            queue.task_done()
