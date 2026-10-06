"""Wachsender TTSBuffer und Pumpe: satzweises Streaming mit Stille als Keepalive."""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import aifred.lib.audio_channels._audio_orchestrator as orch
from aifred.lib.audio_channels._audio_orchestrator import (
    KEEPALIVE_SILENCE, AudioOrchestrator, TTSBuffer, silence_pcm,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def bridge():
    b = MagicMock()
    b.send_audio_flag = AsyncMock(return_value=True)
    b.send_audio_start = AsyncMock(return_value=True)
    b.send_audio_chunk = AsyncMock(return_value=True)
    b.send_audio_end = AsyncMock(return_value=True)
    return b


class TestGrowingBuffer:
    def test_a_fixed_buffer_is_closed_and_knows_its_size(self):
        buffer = TTSBuffer(b"\x01" * 100)
        assert buffer.known_size == 100 and not buffer.done
        buffer.next_chunk()
        assert buffer.done

    def test_a_growing_buffer_is_done_only_when_closed_and_drained(self):
        buffer = TTSBuffer(growing=True)
        assert buffer.known_size is None
        buffer.append(b"\x01" * 10)
        assert buffer.next_chunk() == b"\x01" * 10
        assert not buffer.done                      # the producer may still add
        assert buffer.next_chunk() == b""
        buffer.append(b"\x02" * 5)
        buffer.close()
        assert buffer.known_size == 15 and not buffer.done
        assert buffer.next_chunk() == b"\x02" * 5
        assert buffer.done

    def test_wait_for_data_times_out_without_a_producer(self):
        buffer = TTSBuffer(growing=True)
        assert run(buffer.wait_for_data(0.05)) is False

    def test_wait_for_data_wakes_when_another_thread_appends(self):
        buffer = TTSBuffer(growing=True)

        async def go():
            threading.Timer(0.05, lambda: buffer.append(b"\x01")).start()
            started = time.monotonic()
            assert await buffer.wait_for_data(2.0) is True
            return time.monotonic() - started

        assert run(go()) < 1.0

    def test_wait_for_data_returns_at_once_when_data_is_there(self):
        buffer = TTSBuffer(growing=True)
        buffer.append(b"\x01")
        assert run(buffer.wait_for_data(2.0)) is True

    def test_close_wakes_the_waiter(self):
        buffer = TTSBuffer(growing=True)

        async def go():
            threading.Timer(0.05, buffer.close).start()
            return await buffer.wait_for_data(2.0)

        assert run(go()) is True

    def test_discard_cancels_the_producer(self):
        async def go():
            async def forever():
                await asyncio.sleep(60)
            buffer = TTSBuffer(growing=True)
            buffer.producer = asyncio.create_task(forever())
            await asyncio.sleep(0)
            buffer.discard()
            await asyncio.sleep(0.05)
            return buffer.producer.cancelled()

        assert run(go()) is True


def _announce(orc, buffer, **kwargs):
    return orc.play_notification(buffer, start_tone=True, end_tone=True, **kwargs)


class TestStreamingPump:
    def test_waiting_for_the_next_segment_sends_keepalive_silence(self, bridge, monkeypatch):
        monkeypatch.setattr(orch, "FREEECHO2_KEEPALIVE_SEC", 0.05)
        orc = AudioOrchestrator("room1", bridge)

        async def go():
            buffer = TTSBuffer(growing=True)
            buffer.append(b"\x07" * 100)           # first sentence is ready

            async def producer():
                await asyncio.sleep(0.3)           # the second one takes long
                buffer.append(b"\x08" * 100)
                buffer.close()
            buffer.producer = asyncio.create_task(producer())
            return await _announce(orc, buffer)

        assert run(go()) is True
        chunks = [c.args[1] for c in bridge.send_audio_chunk.await_args_list]
        assert chunks[0] == b"\x07" * 100 and chunks[-1] == b"\x08" * 100
        keepalives = [c for c in chunks if c == KEEPALIVE_SILENCE]
        assert len(keepalives) >= 2                # the stream stayed open the whole time
        assert bridge.send_audio_end.await_args.kwargs == {"end_tone": True}
        # growing: the total size is not known at audio_start
        assert bridge.send_audio_start.await_args.kwargs == {"total_size": None}

    def test_a_failed_producer_closes_the_stream_without_end_tone(self, bridge):
        orc = AudioOrchestrator("room1", bridge)

        async def go():
            buffer = TTSBuffer(growing=True)
            buffer.append(b"\x07" * 100)

            async def producer():
                await asyncio.sleep(0.05)
                buffer.close(failed=True)
            buffer.producer = asyncio.create_task(producer())
            return await _announce(orc, buffer)

        assert run(go()) is False
        assert bridge.send_audio_end.await_args.kwargs == {"end_tone": False}

    def test_stop_cancels_the_producer(self, bridge):
        orc = AudioOrchestrator("room1", bridge)

        async def go():
            buffer = TTSBuffer(growing=True)
            buffer.append(b"\x07" * 100)

            async def producer():
                await asyncio.sleep(60)
            buffer.producer = asyncio.create_task(producer())
            play = asyncio.create_task(_announce(orc, buffer))
            await asyncio.sleep(0.1)
            await orc.stop()
            result = await play
            await asyncio.sleep(0.05)
            return result, buffer.producer.cancelled()

        assert run(go()) == (False, True)

    def test_silence_between_paragraphs_is_real_zero_samples(self):
        assert silence_pcm(1000) == bytes(96000) and len(silence_pcm(20)) == len(KEEPALIVE_SILENCE)
