"""Tests für aifred.lib.vision_frames.LiveFrames — latest, Vorlauf-Ring, Takt-Iterator."""

from __future__ import annotations

import asyncio
import itertools
import time
from datetime import datetime, timedelta

import pytest

from aifred.lib.frame_sources import Frame
from aifred.lib.vision_frames import LiveFrames

T0 = datetime(2026, 1, 1, 12, 0, 0)


def _frame(second: float, source_id: str = "cam/x") -> Frame:
    return Frame(source_id, T0 + timedelta(seconds=second), b"jpeg")


class TestPushAndLatest:
    def test_latest_is_none_initially(self):
        assert LiveFrames(preroll_sec=1.0).latest is None

    def test_latest_is_last_pushed(self):
        live = LiveFrames(preroll_sec=0.0)
        first, second = _frame(0), _frame(1)
        live.push(first)
        assert live.latest is first
        live.push(second)
        assert live.latest is second


class TestPreroll:
    def test_snapshot_oldest_first_and_trimmed_to_window(self):
        live = LiveFrames(preroll_sec=2.0)
        frames = [_frame(s) for s in range(6)]
        for frame in frames:
            live.push(frame)
        # Letzter Frame bei 5 s → behalten wird, was höchstens 2 s zurückliegt.
        assert live.preroll_snapshot() == frames[3:]

    def test_frame_exactly_at_window_edge_is_kept(self):
        live = LiveFrames(preroll_sec=2.0)
        frames = [_frame(0), _frame(2)]
        for frame in frames:
            live.push(frame)
        assert live.preroll_snapshot() == frames

    def test_snapshot_is_a_copy(self):
        live = LiveFrames(preroll_sec=5.0)
        live.push(_frame(0))
        snapshot = live.preroll_snapshot()
        snapshot.clear()
        assert len(live.preroll_snapshot()) == 1

    def test_zero_preroll_keeps_buffer_empty(self):
        live = LiveFrames(preroll_sec=0.0)
        for s in range(3):
            live.push(_frame(s))
        assert live.preroll_snapshot() == []
        assert live.latest is not None

    def test_single_frame_survives_even_with_tiny_window(self):
        live = LiveFrames(preroll_sec=0.1)
        live.push(_frame(0))
        live.push(_frame(10))
        assert [f.timestamp for f in live.preroll_snapshot()] == [T0 + timedelta(seconds=10)]


class TestFreshFrames:
    def test_yields_newest_when_several_pushed_before_first_read(self):
        async def go():
            live = LiveFrames(preroll_sec=0.0)
            for s in range(3):
                live.push(_frame(s))
            return await anext(live.fresh_frames(0.0)), live.latest

        got, latest = asyncio.run(go())
        assert got is latest

    def test_never_yields_the_same_frame_twice(self):
        async def go():
            live = LiveFrames(preroll_sec=0.0)
            first = _frame(0)
            live.push(first)
            frames = live.fresh_frames(0.0)
            assert await anext(frames) is first
            # Ohne neues Frame darf nichts mehr kommen.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(anext(frames), timeout=0.15)

        asyncio.run(go())

    def test_waits_for_new_frame_and_yields_it(self):
        async def go():
            live = LiveFrames(preroll_sec=0.0)
            live.push(_frame(0))
            frames = live.fresh_frames(0.0)
            await anext(frames)
            second = _frame(1)
            asyncio.get_running_loop().call_later(0.05, live.push, second)
            return await asyncio.wait_for(anext(frames), timeout=2.0), second

        got, second = asyncio.run(go())
        assert got is second

    def test_waits_for_first_frame(self):
        async def go():
            live = LiveFrames(preroll_sec=0.0)
            frame = _frame(0)
            asyncio.get_running_loop().call_later(0.05, live.push, frame)
            return await asyncio.wait_for(anext(live.fresh_frames(0.0)), timeout=2.0), frame

        got, frame = asyncio.run(go())
        assert got is frame

    def test_respects_min_interval_and_yields_newest(self):
        interval = 0.2

        async def go():
            live = LiveFrames(preroll_sec=0.0)
            counter = 0

            async def producer():
                nonlocal counter
                while True:
                    counter += 1
                    live.push(_frame(counter))
                    await asyncio.sleep(0.01)

            task = asyncio.create_task(producer())
            stamps: list[float] = []
            seconds: list[float] = []
            try:
                async for frame in live.fresh_frames(interval):
                    stamps.append(time.monotonic())
                    seconds.append((frame.timestamp - T0).total_seconds())
                    if len(stamps) == 4:
                        break
            finally:
                task.cancel()
            return stamps, seconds

        stamps, seconds = asyncio.run(go())
        gaps = [b - a for a, b in itertools.pairwise(stamps)]
        assert all(gap >= interval * 0.9 for gap in gaps), gaps
        assert seconds == sorted(set(seconds)), "Frames müssen streng aufsteigend sein"
        # Pro Takt entstehen ~20 neue Frames; geliefert wird das jeweils neueste,
        # also springt die Nummer um deutlich mehr als 1.
        assert all(b - a > 3 for a, b in itertools.pairwise(seconds)), seconds
