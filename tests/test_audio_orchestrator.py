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
    b.pause_stream = AsyncMock(return_value=None)
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

        # audio_flag(tts) ohne Beginn-Ton wurde gesendet
        flags = _flag_calls(bridge)
        assert ("tts", {"start_tone": False}) in flags

        # audio_start mit total_size
        start_call = bridge.send_audio_start.await_args_list[0]
        assert start_call.kwargs.get("total_size") == 1000

        # Mindestens ein chunk + audio_end am Ende
        assert bridge.send_audio_chunk.await_count >= 1
        assert bridge.send_audio_end.await_count == 1
        # TTS-Antwort ohne Ende-Ton
        assert bridge.send_audio_end.await_args.kwargs == {"end_tone": False}

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
    def test_without_speech_is_a_stream_without_chunks(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_alarm(None, start_tone=True, end_tone=False))

        # Protokoll v2: auch ein Alarm ohne Sprache ist Flag → audio_start → audio_end
        assert _flag_calls(bridge) == [("alarm", {"start_tone": True})]
        assert bridge.send_audio_start.await_args.kwargs == {"total_size": 0}
        bridge.send_audio_chunk.assert_not_awaited()
        assert bridge.send_audio_end.await_args.kwargs == {"end_tone": False}
        assert orc.is_idle is True

    def test_with_speech_is_one_stream(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_alarm(b"\x00" * 500, start_tone=True, end_tone=False))

        # EIN Flag (kein zweites audio_flag(tts)), ein Start, Chunks, EIN audio_end
        assert _flag_calls(bridge) == [("alarm", {"start_tone": True})]
        bridge.send_audio_start.assert_awaited_once()
        assert bridge.send_audio_start.await_args.kwargs == {"total_size": 500}
        assert bridge.send_audio_chunk.await_count >= 1
        assert bridge.send_audio_end.await_count == 1


class TestPlayNotification:
    def test_doorbell_is_a_stream_without_chunks(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_notification(None, start_tone=True, end_tone=True))

        # Türklingel: Beginn- und Ende-Ton ohne Sprache, keine Sonderfälle
        assert _flag_calls(bridge) == [("notification", {"start_tone": True})]
        assert bridge.send_audio_start.await_args.kwargs == {"total_size": 0}
        bridge.send_audio_chunk.assert_not_awaited()
        assert bridge.send_audio_end.await_args.kwargs == {"end_tone": True}
        assert orc.is_idle is True

    @pytest.mark.parametrize(("start_tone", "end_tone"), [
        (True, True), (True, False), (False, True), (False, False),
    ])
    def test_tones_reach_flag_and_end(self, bridge, start_tone, end_tone):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_notification(b"\x00" * 100, start_tone=start_tone, end_tone=end_tone))

        assert _flag_calls(bridge) == [("notification", {"start_tone": start_tone})]
        bridge.send_audio_start.assert_awaited_once()
        assert bridge.send_audio_end.await_count == 1
        assert bridge.send_audio_end.await_args.kwargs == {"end_tone": end_tone}

    def test_tones_are_required(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        with pytest.raises(TypeError):
            run(orc.play_notification(b"\x00" * 10))


class TestPlayResult:
    """Natürliches Ende → True (der Puck quittiert mit _done), Abbruch → False
    (nach einem Abbruch sendet der Puck KEIN _done)."""

    def test_natural_end_returns_true(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        assert run(orc.play_notification(b"\x00" * 100, start_tone=True, end_tone=True)) is True
        assert run(orc.play_alarm(None, start_tone=True, end_tone=False)) is True

    def test_send_failure_returns_false(self, bridge):
        bridge.send_audio_chunk.return_value = False
        orc = AudioOrchestrator("room1", bridge)
        assert run(orc.play_notification(b"\x00" * 100, start_tone=True, end_tone=True)) is False

    def test_stop_returns_false(self, bridge):
        async def slow_chunk(*args, **kwargs):
            await asyncio.sleep(0.01)
            return True
        bridge.send_audio_chunk.side_effect = slow_chunk
        orc = AudioOrchestrator("room1", bridge)
        pcm = b"\x00" * (TTSBuffer.CHUNK_SIZE * 5)

        async def scenario():
            task = asyncio.create_task(orc.play_notification(pcm, start_tone=True, end_tone=True))
            await asyncio.sleep(0.005)
            await orc.stop()
            return await task

        assert run(scenario()) is False


class TestPumpAbort:
    def test_failed_chunk_closes_the_stream_without_end_tone(self, bridge):
        bridge.send_audio_chunk.return_value = False
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_notification(b"\x00" * 100, start_tone=True, end_tone=True))

        # Server gibt nach dem Sende-Fehler auf: Strom sauber zu, KEIN Ende-Ton
        assert bridge.send_audio_end.await_count == 1
        assert bridge.send_audio_end.await_args.kwargs == {"end_tone": False}


class TestPauseForAnnouncement:
    def _music_orc(self, bridge):
        stream = MagicMock()
        stream.pause = AsyncMock(return_value=True)
        stream.stop = AsyncMock(return_value=True)
        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_music(stream))
        return orc

    def test_running_music_is_paused_and_acknowledged(self, bridge):
        orc = self._music_orc(bridge)
        run(orc.pause_for_announcement(3.0))
        bridge.pause_stream.assert_awaited_once_with("room1", 3.0)

    def test_idle_needs_no_pause(self, bridge):
        orc = AudioOrchestrator("room1", bridge)
        run(orc.pause_for_announcement(3.0))
        bridge.pause_stream.assert_not_awaited()

    def test_already_paused_needs_no_pause(self, bridge):
        orc = self._music_orc(bridge)
        run(orc.pause())
        run(orc.pause_for_announcement(3.0))
        bridge.pause_stream.assert_not_awaited()

    def test_running_tts_is_paused(self, bridge):
        async def slow_chunk(*args, **kwargs):
            await asyncio.sleep(0.01)
            return True
        bridge.send_audio_chunk.side_effect = slow_chunk
        orc = AudioOrchestrator("room1", bridge)
        pcm = b"\x00" * (TTSBuffer.CHUNK_SIZE * 5)

        async def scenario():
            play = asyncio.create_task(orc.play_tts(pcm))
            await asyncio.sleep(0.005)
            await orc.pause_for_announcement(3.0)
            play.cancel()
            await asyncio.gather(play, return_exceptions=True)

        run(scenario())
        bridge.pause_stream.assert_awaited_once_with("room1", 3.0)

    def test_missing_acknowledgement_propagates(self, bridge):
        bridge.pause_stream.side_effect = TimeoutError("no ack")
        orc = self._music_orc(bridge)
        with pytest.raises(TimeoutError):
            run(orc.pause_for_announcement(3.0))


# ── Pause / Resume / Stop Type-Awareness ─────────────────────────────

class TestPauseSemantics:
    @pytest.mark.parametrize("audio_type", ["alarm", "notification"])
    def test_pause_transient_is_stop(self, bridge, audio_type):
        # alarm/notification = transient → pause = stop (Stream läuft: Sprache)
        async def slow_chunk(*args, **kwargs):
            await asyncio.sleep(0.01)
            return True
        bridge.send_audio_chunk.side_effect = slow_chunk
        orc = AudioOrchestrator("room1", bridge)
        play = orc.play_alarm if audio_type == "alarm" else orc.play_notification
        pcm = b"\x00" * (TTSBuffer.CHUNK_SIZE * 5)

        async def scenario():
            task = asyncio.create_task(play(pcm, start_tone=True, end_tone=True))
            await asyncio.sleep(0.005)
            assert orc.active_type == audio_type
            await orc.pause()
            await task
            return orc.is_idle, orc.is_paused

        is_idle, is_paused = run(scenario())
        assert is_idle is True
        assert is_paused is False  # NICHT paused — wirklich gestoppt
        # Abbruch: audio_end ohne Ende-Ton
        assert bridge.send_audio_end.await_args.kwargs == {"end_tone": False}

    def test_pause_tts_is_real_pause(self, bridge):
        # play_tts ist synchron (wartet auf pump-Ende) — pause muss
        # daher aus einem parallelen Task kommen, genau wie im echten
        # Code: send_reply blockt in play_tts, der WS-receive-Loop
        # ruft pause() in einem anderen Task.
        async def slow_chunk(*args, **kwargs):
            await asyncio.sleep(0.01)
            return True
        bridge.send_audio_chunk.side_effect = slow_chunk

        orc = AudioOrchestrator("room1", bridge)
        pcm = b"\x00" * (TTSBuffer.CHUNK_SIZE * 5)

        async def scenario():
            play_task = asyncio.create_task(orc.play_tts(pcm))
            await asyncio.sleep(0.005)  # erster Chunk on the way
            await orc.pause()
            await play_task  # play_tts kehrt durch CancelledError zurueck
            return orc.active_type, orc.is_paused

        active_type, is_paused = run(scenario())
        assert active_type == "tts"
        assert is_paused is True

    def test_resume_pumps_remaining_bytes(self, bridge):
        async def slow_chunk(*args, **kwargs):
            await asyncio.sleep(0.01)
            return True
        bridge.send_audio_chunk.side_effect = slow_chunk

        orc = AudioOrchestrator("room1", bridge)
        pcm_size = TTSBuffer.CHUNK_SIZE * 3 + 100
        pcm = b"\x42" * pcm_size

        async def scenario():
            play_task = asyncio.create_task(orc.play_tts(pcm))
            await asyncio.sleep(0.005)
            await orc.pause()
            await play_task  # erster play_tts-Aufruf endet via Cancel
            chunks_at_pause = bridge.send_audio_chunk.await_count
            # resume startet pump-Task neu — wir warten auf den
            await orc.resume()
            for _ in range(200):
                if bridge.send_audio_end.await_count > 0:
                    break
                await asyncio.sleep(0.01)
            return chunks_at_pause

        chunks_at_pause = run(scenario())
        sent = b"".join(c.args[1] for c in bridge.send_audio_chunk.await_args_list)
        assert sent == pcm
        assert bridge.send_audio_end.await_count == 1
        assert bridge.send_audio_chunk.await_count > chunks_at_pause


class TestStop:
    def test_stop_alarm(self, bridge):
        async def slow_chunk(*args, **kwargs):
            await asyncio.sleep(0.01)
            return True
        bridge.send_audio_chunk.side_effect = slow_chunk
        orc = AudioOrchestrator("room1", bridge)
        pcm = b"\x00" * (TTSBuffer.CHUNK_SIZE * 5)

        async def scenario():
            task = asyncio.create_task(orc.play_alarm(pcm, start_tone=True, end_tone=False))
            await asyncio.sleep(0.005)
            assert await orc.stop() is True
            await task

        run(scenario())
        assert orc.is_idle is True
        assert bridge.send_audio_end.await_args.kwargs == {"end_tone": False}

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
        run(orc.play_alarm(None, start_tone=True, end_tone=False))
        run(orc.play_tts(b"\x00" * 100))

        # Erst war alarm aktiv, jetzt tts
        flags = _flag_calls(bridge)
        flag_types = [t for t, _ in flags]
        assert "alarm" in flag_types
        assert "tts" in flag_types
        assert flag_types.index("alarm") < flag_types.index("tts")


# ── TTS-Takeover ueber laufende Music ────────────────────────────────

class TestTTSReplacesMusic:
    """TTS waehrend Music laeuft: Music wird sauber beendet, TTS uebernimmt
    als alleinige Source. Konsens-Spec "Variante (ii)": kein Takeover-Mechanismus
    mehr — Music wird durch TTS ersetzt, User holt sie via audio_resume zurueck.
    """

    def test_play_tts_replaces_music(self, bridge):
        stream = MagicMock()
        stream.pause = AsyncMock(return_value=True)
        stream.resume = AsyncMock(return_value=True)
        stream.stop = AsyncMock(return_value=True)

        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_music(stream))
        assert orc.active_type == "music"

        run(orc.play_tts(b"\x00" * 200))

        # Music-Stream wurde sauber gestoppt (nicht nur pausiert)
        stream.stop.assert_awaited_once()
        # mpv-IPC pause/resume wurde NICHT mehr aufgerufen — kein Takeover
        stream.pause.assert_not_awaited()
        stream.resume.assert_not_awaited()
        # audio_flag(tts) und audio_start wurden gesendet (TTS-Standalone)
        flag_types = [t for t, _ in _flag_calls(bridge)]
        assert "tts" in flag_types
        bridge.send_audio_start.assert_awaited()
        # active_type ist nach komplettem Pump None (Buffer durch)

    def test_chunks_match_input_after_music_replace(self, bridge):
        stream = MagicMock()
        stream.pause = AsyncMock(return_value=True)
        stream.resume = AsyncMock(return_value=True)
        stream.stop = AsyncMock(return_value=True)

        orc = AudioOrchestrator("room1", bridge)
        run(orc.play_music(stream))

        pcm = b"".join(bytes([i % 256]) for i in range(2500))
        run(orc.play_tts(pcm))

        sent = b"".join(c.args[1] for c in bridge.send_audio_chunk.await_args_list)
        assert sent == pcm
        stream.resume.assert_not_awaited()
