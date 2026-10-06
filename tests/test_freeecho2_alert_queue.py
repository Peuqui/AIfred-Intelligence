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


PAUSE_TIMEOUT = fe.FREEECHO2_PAUSE_ACK_TIMEOUT_SEC


class _FakeOrc:
    def __init__(self) -> None:
        # ("pause", timeout) | (audio_type, has_speech, start_tone, end_tone)
        self.calls: list[tuple] = []
        self.pause_error: Exception | None = None
        self.completed = True   # False = Abbruch (der Puck quittiert dann nicht mit _done)
        self.bridge = _FakeBridge()

    async def pause_for_announcement(self, timeout_sec):
        self.calls.append(("pause", timeout_sec))
        if self.pause_error is not None:
            raise self.pause_error

    async def play_alarm(self, tts_pcm=None, *, start_tone, end_tone):
        self.calls.append(("alarm", tts_pcm is not None, start_tone, end_tone))
        return self.completed

    async def play_notification(self, tts_pcm=None, *, start_tone, end_tone):
        self.calls.append(("notification", tts_pcm is not None, start_tone, end_tone))
        return self.completed


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
        await fe.enqueue_alert("wohnzimmer", "alarm", b"pcm-1", start_tone=True, end_tone=False)
        await fe.enqueue_alert("wohnzimmer", "notification", b"pcm-2", start_tone=True, end_tone=True)

        # Worker plays #1 and then BLOCKS waiting for _done.
        await asyncio.sleep(0.05)
        assert orc.calls == [
            ("pause", PAUSE_TIMEOUT), ("alarm", True, True, False),
        ], "only #1 played, waiting for _done"

        # Puck reports done → #2 may play.
        fe.signal_playback_done("wohnzimmer")
        await asyncio.sleep(0.05)
        assert orc.calls == [
            ("pause", PAUSE_TIMEOUT), ("alarm", True, True, False),
            ("pause", PAUSE_TIMEOUT), ("notification", True, True, True),
        ]

        fe.signal_playback_done("wohnzimmer")
        await asyncio.sleep(0.02)

        worker = fe._alert_workers["wohnzimmer"]
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(go())


def test_doorbell_without_speech(monkeypatch):
    orc = _FakeOrc()
    monkeypatch.setattr(ac, "resolve", lambda key: _FakeCh(orc))
    _reset_state()

    async def go():
        await fe.enqueue_alert("kueche", "notification", None, start_tone=True, end_tone=True)
        await asyncio.sleep(0.05)
        assert orc.calls == [("pause", PAUSE_TIMEOUT), ("notification", False, True, True)]
        fe.signal_playback_done("kueche")
        await asyncio.sleep(0.02)
        w = fe._alert_workers["kueche"]
        w.cancel()
        try:
            await w
        except asyncio.CancelledError:
            pass

    asyncio.run(go())


def test_aborted_announcement_does_not_wait_for_done(monkeypatch):
    """Nach einem Abbruch (Stopp/Standby) sendet der Puck KEIN _done: die Queue
    wartet nicht darauf, die nächste Ansage läuft sofort."""
    orc = _FakeOrc()
    orc.completed = False
    monkeypatch.setattr(ac, "resolve", lambda key: _FakeCh(orc))
    _reset_state()

    async def go():
        await fe.enqueue_alert("diele", "notification", b"pcm-1", start_tone=True, end_tone=True)
        await fe.enqueue_alert("diele", "notification", b"pcm-2", start_tone=True, end_tone=True)
        await asyncio.sleep(0.1)    # viel kürzer als die _done-Wartezeit (>= 15 s)
        assert [c for c in orc.calls if c[0] == "notification"] == [
            ("notification", True, True, True), ("notification", True, True, True),
        ]
        assert orc.bridge.done_calls == []   # der Stopp-Pfad hat sein done schon geschickt
        w = fe._alert_workers["diele"]
        w.cancel()
        try:
            await w
        except asyncio.CancelledError:
            pass

    asyncio.run(go())


def test_unacknowledged_pause_drops_only_that_announcement(monkeypatch):
    """Bestätigt der Puck die Pause nicht, wird die Ansage verworfen (laut
    geloggt), ohne _done-Wartezeit; die nächste Ansage der Queue läuft."""
    orc = _FakeOrc()
    orc.pause_error = TimeoutError("no ack")
    monkeypatch.setattr(ac, "resolve", lambda key: _FakeCh(orc))
    _reset_state()

    async def go():
        await fe.enqueue_alert("flur", "notification", b"pcm-1", start_tone=True, end_tone=True)
        await asyncio.sleep(0.05)
        # verworfen: kein play_*, kein done-Frame
        assert orc.calls == [("pause", PAUSE_TIMEOUT)]
        assert orc.bridge.done_calls == []

        orc.pause_error = None
        await fe.enqueue_alert("flur", "notification", b"pcm-2", start_tone=True, end_tone=True)
        await asyncio.sleep(0.05)
        assert orc.calls[-1] == ("notification", True, True, True)

        fe.signal_playback_done("flur")
        await asyncio.sleep(0.02)
        w = fe._alert_workers["flur"]
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
