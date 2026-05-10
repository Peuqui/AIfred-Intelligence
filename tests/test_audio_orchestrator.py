"""Tests fuer AudioOrchestrator (Phase 5.0, Phase 2).

Konsens-Spec siehe ``docs/de/architecture/audio-pipeline.md``. Wir
testen die State-Machine + Frame-Sequenzen pro Use-Case mit einer
Mock-Bridge — kein echter WS-Round-Trip noetig.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aifred.lib.audio_channels._audio_orchestrator import (
    AudioOrchestrator,
    TTSBuffer,
)


def run(coro):
    return asyncio.run(coro)


# ── TTSBuffer (Cursor-Logik fuer Pause/Resume) ───────────────────────

class TestTTSBuffer:
    def test_empty(self):
        buf = TTSBuffer(b"")
        assert buf.total_bytes == 0
        assert buf.remaining_bytes == 0
        assert buf.done is True
        assert buf.next_chunk() == b""

    def test_single_chunk_smaller_than_chunksize(self):
        data = b"\x00" * 100
        buf = TTSBuffer(data)
        assert buf.total_bytes == 100
        assert buf.done is False
        chunk = buf.next_chunk()
        assert chunk == data
        assert buf.done is True
        assert buf.remaining_bytes == 0

    def test_multiple_chunks(self):
        # Erzeuge 3.5 chunks (1.5 MB)
        data = b"\xab" * (TTSBuffer.CHUNK_SIZE * 3 + 100)
        buf = TTSBuffer(data)
        chunks = []
        while not buf.done:
            chunks.append(buf.next_chunk())
        assert len(chunks) == 4
        assert all(c == b"\xab" * TTSBuffer.CHUNK_SIZE for c in chunks[:3])
        assert chunks[3] == b"\xab" * 100
        assert b"".join(chunks) == data

    def test_cursor_persists_between_calls(self):
        # Wichtig fuer pause/resume — Cursor darf nicht zurueckspringen
        data = b"\x00" * (TTSBuffer.CHUNK_SIZE * 2)
        buf = TTSBuffer(data)
        buf.next_chunk()  # 1. Chunk
        assert buf.remaining_bytes == TTSBuffer.CHUNK_SIZE
        buf.next_chunk()  # 2. Chunk
        assert buf.done is True


# ── Mock-Bridge ──────────────────────────────────────────────────────

@pytest.fixture
def bridge():
    """Mock-Bridge mit allen send_*-Methoden als AsyncMock."""
    b = MagicMock()
    b.send_audio_flag = AsyncMock(return_value=True)
    b.send_audio_start = AsyncMock(return_value=True)
    b.send_audio_chunk = AsyncMock(return_value=True)
    b.send_audio_end = AsyncMock(return_value=True)
    return b


def _flag_calls(bridge):
    """Liste der (audio_type, params) je send_audio_flag-Aufruf."""
    out = []
    for c in bridge.send_audio_flag.await_args_list:
        # call args: (room, audio_type, **params) — wir brauchen nur audio_type+kwargs
        audio_type = c.args[1]
        out.append((audio_type, dict(c.kwargs)))
    return out


# ── Orchestrator State-Machine ───────────────────────────────────────

class TestStateInitial:
    def test_idle_after_init(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        assert orc.is_idle is True
        assert orc.active_type is None
        assert orc.is_paused is False

    def test_pause_stop_when_idle_returns_false(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        assert run(orc.pause()) is False
        assert run(orc.stop()) is False
        assert run(orc.resume()) is False


class TestPlayTTS:
    def test_sends_flag_start_chunks_end(self, bridge):
        # asyncio.run() drainiert alle Tasks — pump-Task läuft komplett
        # durch, Orchestrator ist am Ende wieder IDLE. Wir testen die
        # Frame-Sequenz, nicht den Mid-Stream-State.
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_tts(b"\xab" * 1000))

        # audio_flag(tts) wurde gesendet
        flags = _flag_calls(bridge)
        assert ("tts", {}) in flags

        # audio_start mit total_size
        start_call = bridge.send_audio_start.await_args_list[0]
        assert start_call.kwargs.get("total_size") == 1000

        # Mindestens ein chunk + audio_end am Ende
        assert bridge.send_audio_chunk.await_count >= 1
        assert bridge.send_audio_end.await_count == 1

        # Nach komplettem Pump → IDLE (active_type clears in pump-finally)
        assert orc.is_idle is True

    def test_concatenated_chunks_match_input(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        pcm = b"".join(bytes([i % 256]) for i in range(2000))
        run(orc.play_tts(pcm))

        # Alle gesendeten Chunks zusammen = Input
        sent = b"".join(c.args[1] for c in bridge.send_audio_chunk.await_args_list)
        assert sent == pcm


class TestPlayAlarm:
    def test_no_tail_only_flag(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_alarm(repeats=3, max_duration=60, with_tts=False))

        assert orc.active_type == "alarm"
        flags = _flag_calls(bridge)
        # Genau 1 audio_flag mit alarm-Tupel
        assert flags == [
            ("alarm", {"repeats": 3, "max_duration": 60, "with_tts": False}),
        ]
        # Kein audio_start, kein chunk, kein audio_end
        bridge.send_audio_start.assert_not_awaited()
        bridge.send_audio_chunk.assert_not_awaited()
        bridge.send_audio_end.assert_not_awaited()

    def test_with_tail_sends_alarm_then_tts(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        tts_pcm = b"\x00" * 500
        run(orc.play_alarm(
            repeats=3, max_duration=60, with_tts=True, tts_pcm=tts_pcm,
        ))

        async def _wait_for_pump():
            for _ in range(50):
                if bridge.send_audio_end.await_count > 0:
                    return
                await asyncio.sleep(0.01)
        run(_wait_for_pump())

        # Spec-Sequenz: audio_flag(alarm,...) → audio_flag(tts) →
        #               audio_start → chunks → audio_end
        flags = _flag_calls(bridge)
        assert flags == [
            ("alarm", {"repeats": 3, "max_duration": 60, "with_tts": True}),
            ("tts", {}),
        ]
        bridge.send_audio_start.assert_awaited_once()
        assert bridge.send_audio_chunk.await_count >= 1
        # Genau EIN audio_end am Ende der Gesamt-Sequenz
        assert bridge.send_audio_end.await_count == 1


class TestPlayNotification:
    def test_no_tail(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_notification(with_tts=False))
        assert _flag_calls(bridge) == [("notification", {"with_tts": False})]
        bridge.send_audio_start.assert_not_awaited()

    def test_with_tail(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_notification(with_tts=True, tts_pcm=b"\x00" * 100))

        async def _wait_for_pump():
            for _ in range(50):
                if bridge.send_audio_end.await_count > 0:
                    return
                await asyncio.sleep(0.01)
        run(_wait_for_pump())

        flags = _flag_calls(bridge)
        assert flags == [
            ("notification", {"with_tts": True}),
            ("tts", {}),
        ]
        bridge.send_audio_start.assert_awaited_once()


# ── Pause / Resume / Stop Type-Awareness ─────────────────────────────

class TestPauseSemantics:
    def test_pause_alarm_is_stop(self, bridge):
        # alarm/notification = transient → pause = stop
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_alarm(repeats=1, max_duration=10, with_tts=False))
        assert orc.active_type == "alarm"

        run(orc.pause())
        assert orc.is_idle is True
        assert orc.is_paused is False  # NICHT paused — wirklich gestoppt

    def test_pause_notification_is_stop(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_notification(with_tts=False))
        run(orc.pause())
        assert orc.is_idle is True

    def test_pause_tts_is_real_pause(self, bridge):
        # Damit pause mid-pump greifen kann, mache send_audio_chunk
        # async-langsam (10 ms pro Chunk). Sonst pumpt asyncio.run alle
        # chunks synchron durch bevor wir pause aufrufen können.
        async def slow_chunk(*args, **kwargs):
            await asyncio.sleep(0.01)
            return True
        bridge.send_audio_chunk.side_effect = slow_chunk

        orc = AudioOrchestrator("room1", bridge)
        pcm = b"\x00" * (TTSBuffer.CHUNK_SIZE * 5)

        async def scenario():
            await orc.play_tts(pcm)
            # mini sleep damit pump-Task den ersten chunk anfängt
            await asyncio.sleep(0.005)
            await orc.pause()
            return orc.active_type, orc.is_paused

        active_type, is_paused = run(scenario())
        assert active_type == "tts"
        assert is_paused is True

    def test_resume_pumps_remaining_bytes(self, bridge):
        # Slow chunks damit pause mid-pump greift
        async def slow_chunk(*args, **kwargs):
            await asyncio.sleep(0.01)
            return True
        bridge.send_audio_chunk.side_effect = slow_chunk

        orc = AudioOrchestrator("room1", bridge)
        pcm_size = TTSBuffer.CHUNK_SIZE * 3 + 100
        pcm = b"\x42" * pcm_size

        async def scenario():
            await orc.play_tts(pcm)
            await asyncio.sleep(0.005)  # erster Chunk on the way
            await orc.pause()
            chunks_at_pause = bridge.send_audio_chunk.await_count
            await orc.resume()
            # Auf Pump-Ende warten
            for _ in range(100):
                if bridge.send_audio_end.await_count > 0:
                    break
                await asyncio.sleep(0.01)
            return chunks_at_pause

        chunks_at_pause = run(scenario())
        # Insgesamt sollten alle Bytes durch sein, audio_end gesendet
        sent = b"".join(c.args[1] for c in bridge.send_audio_chunk.await_args_list)
        assert sent == pcm
        assert bridge.send_audio_end.await_count == 1
        # Pause hat irgendwo unterbrochen, resume hat den Rest gepumpt
        assert bridge.send_audio_chunk.await_count > chunks_at_pause


class TestStop:
    def test_stop_alarm(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_alarm(repeats=1, max_duration=10, with_tts=False))
        run(orc.stop())
        assert orc.is_idle is True

    def test_stop_tts_sends_audio_end(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        pcm = b"\x00" * (TTSBuffer.CHUNK_SIZE * 5)
        run(orc.play_tts(pcm))
        run(orc.stop())
        assert orc.is_idle is True
        # audio_end wurde gesendet (entweder vom pump-cleanup oder vom reset)
        assert bridge.send_audio_end.await_count >= 1

    def test_stop_idempotent(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        assert run(orc.stop()) is False
        assert run(orc.stop()) is False


class TestTypeSwitch:
    def test_alarm_then_tts_resets_alarm(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_alarm(repeats=1, max_duration=10, with_tts=False))
        run(orc.play_tts(b"\x00" * 100))

        async def _wait_for_pump():
            for _ in range(50):
                if bridge.send_audio_end.await_count > 0:
                    return
                await asyncio.sleep(0.01)
        run(_wait_for_pump())

        # Erst war alarm aktiv, jetzt tts
        flags = _flag_calls(bridge)
        # Reihenfolge: alarm, dann tts (mit reset dazwischen — kein
        # explizites Audio-Frame für Reset, das passiert intern)
        flag_types = [t for t, _ in flags]
        assert "alarm" in flag_types
        assert "tts" in flag_types
        assert flag_types.index("alarm") < flag_types.index("tts")
