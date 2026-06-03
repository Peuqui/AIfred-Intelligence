"""Tests for the server-side proactive alert queue (FreeEcho.2).

The queue serialises alerts per room: one alert plays, the worker waits for
the puck's _done signal, then the next plays — nothing dropped. Decoupled
from the emit path (enqueue returns immediately)."""

from __future__ import annotations

import asyncio

import aifred.lib.audio_channels as ac
import aifred.plugins.channels.freeecho2_channel as fe


class _FakeOrc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

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
