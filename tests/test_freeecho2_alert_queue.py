"""Tests for the server-side proactive alert queue (FreeEcho.2).

The queue serialises alerts per room: one alert plays, the worker waits for
the puck's _done signal, then the next plays — nothing dropped. Decoupled
from the emit path (enqueue returns immediately)."""

from __future__ import annotations

import asyncio

import aifred.lib.audio_channels as ac
# Direkt das alert_queue-Modul, nicht das Paket: die Tests REBINDEN
# fe._ws_loop, und run_on_ws_loop löst das Global zur Laufzeit in
# alert_queue auf — ein Rebind am Paket-Re-Export käme dort nie an.
import aifred.plugins.channels.freeecho2_channel.alert_queue as fe


class _FakeBridge:
    def __init__(self) -> None:
        self.done_calls: list[str] = []

    async def send_done(self, room, reason=None):
        self.done_calls.append(room)
        return True


class _FakeOrc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.bridge = _FakeBridge()

    async def play_alarm(self, with_tts, tts_pcm=None):
        self.calls.append(("alarm", with_tts))

    async def play_notification(self, with_tts, tts_pcm=None):
        self.calls.append(("notification", with_tts))


class _FakeCh:
    def __init__(self, orc: _FakeOrc) -> None:
        self._orc = orc

    def get_orchestrator(self, room: str) -> _FakeOrc:
        return self._orc


def _reset_state():
    fe._alert_queues.clear()
    fe._alert_workers.clear()
    fe._playback_done.clear()


def test_alerts_play_sequentially_paced_by_done(monkeypatch):
    orc = _FakeOrc()
    monkeypatch.setattr(ac, "resolve", lambda key: _FakeCh(orc))
    _reset_state()

    async def go():
        # Two alerts queued back-to-back (different occurrences).
        await fe.enqueue_alert("wohnzimmer", "alarm", b"pcm-1")
        await fe.enqueue_alert("wohnzimmer", "notification", b"pcm-2")

        # Worker plays #1 and then BLOCKS waiting for _done.
        await asyncio.sleep(0.05)
        assert orc.calls == [("alarm", True)], "only #1 played, waiting for _done"

        # Puck reports done → #2 may play.
        fe.signal_playback_done("wohnzimmer")
        await asyncio.sleep(0.05)
        assert orc.calls == [("alarm", True), ("notification", True)]

        fe.signal_playback_done("wohnzimmer")
        await asyncio.sleep(0.02)

        worker = fe._alert_workers["wohnzimmer"]
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(go())


def test_with_tts_false_when_no_pcm(monkeypatch):
    orc = _FakeOrc()
    monkeypatch.setattr(ac, "resolve", lambda key: _FakeCh(orc))
    _reset_state()

    async def go():
        await fe.enqueue_alert("kueche", "notification", None)
        await asyncio.sleep(0.05)
        assert orc.calls == [("notification", False)]
        fe.signal_playback_done("kueche")
        await asyncio.sleep(0.02)
        w = fe._alert_workers["kueche"]
        w.cancel()
        try:
            await w
        except asyncio.CancelledError:
            pass

    asyncio.run(go())


def test_signal_playback_done_sets_event():
    _reset_state()

    async def go():
        evt = fe._playback_done_event("schlafzimmer")
        assert not evt.is_set()
        fe.signal_playback_done("schlafzimmer")
        assert evt.is_set()

    asyncio.run(go())


def test_run_on_ws_loop_marshals_to_ws_loop():
    """run_on_ws_loop führt die Coroutine im ws-Loop aus, auch wenn sie aus
    einem fremden Loop aufgerufen wird — der eigentliche cross-loop-Fix.
    Ohne Marshalling sendet der TTS-Pump aus dem falschen Loop und bricht ab
    ("got Future attached to a different loop")."""
    import threading

    ws_loop = asyncio.new_event_loop()
    started = threading.Event()
    ran: dict = {}

    def _serve():
        asyncio.set_event_loop(ws_loop)
        ws_loop.call_soon(started.set)
        ws_loop.run_forever()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    assert started.wait(timeout=2.0)

    async def _record():
        ran["inner"] = asyncio.get_running_loop()

    async def caller():
        ran["caller"] = asyncio.get_running_loop()
        await fe.run_on_ws_loop(_record())

    saved = fe._ws_loop
    try:
        fe._ws_loop = ws_loop
        asyncio.run(caller())
        # Coroutine lief im ws-Loop, NICHT im Aufrufer-Loop.
        assert ran["inner"] is ws_loop
        assert ran["caller"] is not ws_loop
    finally:
        fe._ws_loop = saved
        ws_loop.call_soon_threadsafe(ws_loop.stop)
        t.join(timeout=2.0)
        ws_loop.close()


def test_run_on_ws_loop_direct_when_no_ws_loop():
    """Ohne gesetzten ws-Loop (oder im selben Loop) wird direkt awaited —
    keine Marshalling-Indirektion, kein Deadlock."""
    ran: dict = {}

    async def _record():
        ran["inner"] = asyncio.get_running_loop()

    async def go():
        await fe.run_on_ws_loop(_record())
        assert ran["inner"] is asyncio.get_running_loop()

    saved = fe._ws_loop
    try:
        fe._ws_loop = None
        asyncio.run(go())
    finally:
        fe._ws_loop = saved
