"""Ansage über laufender Musik: echte Alarm-Warteschlange + Orchestrator + Bridge
gegen einen nachgestellten Puck. Prüft die komplette Frame-Folge auf dem Draht."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import aifred.lib.audio_channels as ac
import aifred.plugins.channels.freeecho2_channel.alert_queue as fe
from aifred.lib.audio_channels._audio_orchestrator import AudioOrchestrator
from aifred.plugins.channels.freeecho2_channel import FreeEchoChannel, _devices
from aifred.plugins.channels.freeecho2_channel._shared import signal_pause_ack

ROOM = "buero"


def _frames(ws) -> list[dict]:
    return [json.loads(c.args[0]) for c in ws.send_str.await_args_list]


def _summary(frames: list[dict]) -> list[tuple]:
    out = []
    for frame in frames:
        kind = frame["type"]
        if kind == "audio_flag":
            out.append((kind, frame["audio_type"], frame["start_tone"]))
        elif kind == "audio_end":
            out.append((kind, frame["end_tone"]))
        elif kind == "wake":
            out.append((kind, frame["agent"]))
        else:
            out.append((kind,))
    return out


def _setup(monkeypatch):
    _devices.clear()
    ws = MagicMock()
    ws.closed = False
    ws.send_str = AsyncMock(return_value=None)
    ws.send_bytes = AsyncMock(return_value=None)
    _devices[ROOM] = ws
    bridge = FreeEchoChannel()
    orc = AudioOrchestrator(ROOM, bridge)
    channel = MagicMock()
    channel.get_orchestrator = MagicMock(return_value=orc)
    monkeypatch.setattr(ac, "resolve", lambda key: channel)
    fe._alert_queues.clear()
    fe._alert_workers.clear()
    fe._playback_done.clear()
    return ws, orc


async def _puck(ws, orc, answer_pause: bool):
    """Der nachgestellte Puck: bestätigt ein _pause (der Server stoppt dabei den Strom,
    wie beim gesprochenen „Bitte Pause“) und quittiert das done mit _done."""
    seen = 0
    while True:
        await asyncio.sleep(0.005)
        frames = _frames(ws)
        for frame in frames[seen:]:
            if frame["type"] == "wake" and frame["agent"] == "_pause" and answer_pause:
                await orc.channel_stop()
                signal_pause_ack(ROOM)
            if frame["type"] == "done":
                fe.signal_playback_done(ROOM)
        seen = len(frames)


def _run(monkeypatch, *, music_running: bool, answer_pause: bool = True) -> list[tuple]:
    ws, orc = _setup(monkeypatch)

    async def stop_like_command_handler():
        await orc.stop()   # wie der _pause-Handler (channel.stop → orchestrator.stop)
    orc.channel_stop = stop_like_command_handler

    async def go():
        if music_running:
            stream = MagicMock()
            stream.pause = AsyncMock(return_value=True)
            stream.stop = AsyncMock(return_value=True)
            await orc.play_music(stream)
        puck = asyncio.create_task(_puck(ws, orc, answer_pause))
        await fe.enqueue_alert(ROOM, "notification", b"\x00\x01" * 2000, start_tone=True, end_tone=True)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if "done" in [f["type"] for f in _frames(ws)] and fe._alert_queues[ROOM].empty():
                break
        await asyncio.sleep(0.05)
        puck.cancel()
        worker = fe._alert_workers[ROOM]
        worker.cancel()
        await asyncio.gather(puck, worker, return_exceptions=True)

    asyncio.run(go())
    return _summary(_frames(ws))


def test_announcement_over_running_music(monkeypatch):
    sequence = _run(monkeypatch, music_running=True)
    assert sequence == [
        ("wake", "_pause"),                      # Server bittet den Puck um die Pause …
        ("audio_end", False),                    # … Bestätigung: Musik gestoppt (Position gespeichert)
        ("done",),
        ("audio_flag", "notification", True),    # dann Beginn-Ton + Ansage + Ende-Ton
        ("audio_start",),
        ("audio_end", True),
        ("done",),
    ]


def test_announcement_without_music_needs_no_pause(monkeypatch):
    sequence = _run(monkeypatch, music_running=False)
    assert sequence == [
        ("audio_flag", "notification", True),
        ("audio_start",),
        ("audio_end", True),
        ("done",),
    ]


def test_unacknowledged_pause_drops_the_announcement(monkeypatch):
    monkeypatch.setattr(fe, "FREEECHO2_PAUSE_ACK_TIMEOUT_SEC", 0.1)
    ws, orc = _setup(monkeypatch)

    async def go():
        stream = MagicMock()
        stream.pause = AsyncMock(return_value=True)
        stream.stop = AsyncMock(return_value=True)
        await orc.play_music(stream)
        await fe.enqueue_alert(ROOM, "notification", b"\x00\x01" * 100, start_tone=True, end_tone=True)
        await asyncio.sleep(0.4)
        fe._alert_workers[ROOM].cancel()
        await asyncio.gather(fe._alert_workers[ROOM], return_exceptions=True)

    asyncio.run(go())
    # Nur die Bitte um die Pause; keine Ansage, kein done (verworfen, laut geloggt)
    assert _summary(_frames(ws)) == [("wake", "_pause")]
