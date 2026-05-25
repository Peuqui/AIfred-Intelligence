"""Tests für aifred.lib.frame_bus — Pub/Sub mit Backpressure."""

from __future__ import annotations

import asyncio
from datetime import datetime

from aifred.lib.frame_bus import FrameBus, get_default_bus
from aifred.lib.frame_sources import Frame


def run(coro):
    return asyncio.run(coro)


def _make_frame(idx: int = 0, source: str = "cam/test") -> Frame:
    return Frame(
        source_id=source,
        timestamp=datetime.now(),
        image_bytes=f"frame-{idx}".encode(),
        metadata={"frame_idx": idx},
    )


class TestPubSub:
    def test_publish_without_subscribers_is_silent(self):
        async def go():
            bus = FrameBus()
            await bus.publish("cam/x", _make_frame(0))
            # No subscribers — must not raise, must not block

        run(go())

    def test_single_subscriber_receives_published_frame(self):
        async def go():
            bus = FrameBus()
            received: list[Frame] = []

            async def consumer():
                async for f in bus.subscribe("cam/a", name="t1"):
                    received.append(f)
                    if len(received) >= 3:
                        break

            task = asyncio.create_task(consumer())
            # Give the subscriber a moment to register
            await asyncio.sleep(0.01)
            for i in range(3):
                await bus.publish("cam/a", _make_frame(i))
            await task

            assert [f.metadata["frame_idx"] for f in received] == [0, 1, 2]

        run(go())

    def test_two_subscribers_get_independent_streams(self):
        async def go():
            bus = FrameBus()
            r1: list[int] = []
            r2: list[int] = []

            async def cons(target: list[int], name: str):
                async for f in bus.subscribe("cam/b", name=name):
                    target.append(f.metadata["frame_idx"])
                    if len(target) >= 2:
                        break

            t1 = asyncio.create_task(cons(r1, "s1"))
            t2 = asyncio.create_task(cons(r2, "s2"))
            await asyncio.sleep(0.01)
            await bus.publish("cam/b", _make_frame(10))
            await bus.publish("cam/b", _make_frame(11))
            await t1
            await t2

            assert r1 == [10, 11]
            assert r2 == [10, 11]

        run(go())

    def test_subscriber_only_gets_its_topic(self):
        async def go():
            bus = FrameBus()
            got_a: list[int] = []

            async def cons():
                async for f in bus.subscribe("cam/a", name="a"):
                    got_a.append(f.metadata["frame_idx"])
                    if len(got_a) >= 1:
                        break

            task = asyncio.create_task(cons())
            await asyncio.sleep(0.01)
            await bus.publish("cam/b", _make_frame(999))  # different topic
            await bus.publish("cam/a", _make_frame(1))
            await task

            assert got_a == [1]

        run(go())


class TestBackpressure:
    def test_drop_oldest_discards_old_when_queue_full(self):
        async def go():
            bus = FrameBus()
            received: list[int] = []
            done = asyncio.Event()

            async def slow_consumer():
                async for f in bus.subscribe(
                    "cam/c", name="slow", maxsize=2, drop_policy="drop_oldest"
                ):
                    received.append(f.metadata["frame_idx"])
                    if len(received) >= 2:
                        done.set()
                        break
                    # Make consumer slow so producer overruns the queue
                    await asyncio.sleep(0.05)

            task = asyncio.create_task(slow_consumer())
            await asyncio.sleep(0.01)
            # Burst publish 5 frames; queue maxsize=2 → 3 must be dropped
            # (the oldest get evicted as new ones arrive)
            for i in range(5):
                await bus.publish("cam/c", _make_frame(i))
            await asyncio.wait_for(done.wait(), timeout=1.0)
            await task

            # With drop_oldest the consumer should see the *latest* frames
            # available at consumption time. We don't pin exact indices
            # (race-y) but newest (4) must appear and oldest (0) must not
            # be the only thing received.
            assert 4 in received or 3 in received
            assert received != [0, 1]  # would mean no dropping happened

        run(go())

    def test_drop_newest_keeps_initial_frames(self):
        async def go():
            bus = FrameBus()
            received: list[int] = []
            done = asyncio.Event()

            async def slow_consumer():
                async for f in bus.subscribe(
                    "cam/d", name="slow", maxsize=2, drop_policy="drop_newest"
                ):
                    received.append(f.metadata["frame_idx"])
                    if len(received) >= 2:
                        done.set()
                        break
                    await asyncio.sleep(0.05)

            task = asyncio.create_task(slow_consumer())
            await asyncio.sleep(0.01)
            for i in range(5):
                await bus.publish("cam/d", _make_frame(i))
            await asyncio.wait_for(done.wait(), timeout=1.0)
            await task

            # drop_newest keeps the early frames in the queue
            assert received[0] == 0

        run(go())


class TestSubscriberCleanup:
    def test_subscriber_removed_after_iterator_exit(self):
        async def go():
            bus = FrameBus()

            async def short_consumer():
                async for f in bus.subscribe("cam/e", name="brief"):
                    _ = f
                    break

            task = asyncio.create_task(short_consumer())
            await asyncio.sleep(0.01)
            await bus.publish("cam/e", _make_frame(0))
            await task

            # After the consumer exits, the topic should have no
            # subscribers — stats reflects that
            stats = bus.stats()
            assert "cam/e" not in stats.subscribers_per_topic


class TestStatsAndHeartbeat:
    def test_last_publish_age_after_publish(self):
        async def go():
            bus = FrameBus()
            assert bus.last_publish_age_seconds("cam/f") is None
            await bus.publish("cam/f", _make_frame(0))
            age = bus.last_publish_age_seconds("cam/f")
            assert age is not None
            assert age >= 0.0
            assert age < 1.0  # just published

        run(go())

    def test_default_bus_is_singleton(self):
        bus1 = get_default_bus()
        bus2 = get_default_bus()
        assert bus1 is bus2
