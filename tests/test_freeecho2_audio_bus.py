"""Tests fuer Audio-Bus-Frame-API (Phase 5.0).

Konsens-Spec: docs/de/architecture/audio-pipeline.md "Audio-Bus-Refactor".
Wir testen die Whitelist-Validation und das JSON-Wire-Format. Damit
faellt jede Schema-Drift sofort auf — nicht erst beim Live-Test wenn
die Firmware eine Connection mit FATAL-Log schliesst.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aifred.plugins.channels.freeecho2_channel import FreeEchoChannel, _devices


def run(coro):
    """asyncio.run-Wrapper — pytest-asyncio ist nicht installiert."""
    return asyncio.run(coro)


# ── Whitelist-Validation (synchron) ──────────────────────────────────

ALL_TYPES = ("music", "speech", "tts", "alarm", "notification")


class TestValidateAudioFlag:
    @pytest.mark.parametrize("audio_type", ALL_TYPES)
    @pytest.mark.parametrize("start_tone", [True, False])
    def test_every_type_takes_start_tone(self, audio_type, start_tone):
        FreeEchoChannel._validate_audio_flag(audio_type, {"start_tone": start_tone})

    def test_unknown_audio_type_raises(self):
        with pytest.raises(ValueError, match="unknown audio_type"):
            FreeEchoChannel._validate_audio_flag("nonsense", {"start_tone": True})

    @pytest.mark.parametrize("audio_type", ALL_TYPES)
    def test_missing_start_tone_raises(self, audio_type):
        with pytest.raises(ValueError, match="missing required fields"):
            FreeEchoChannel._validate_audio_flag(audio_type, {})

    @pytest.mark.parametrize("field", ["with_tts", "end_tone", "repeats"])
    def test_other_fields_raise(self, field):
        # with_tts entfällt (Protokoll v2), end_tone gehört ins audio_end
        with pytest.raises(ValueError, match="unexpected fields"):
            FreeEchoChannel._validate_audio_flag(
                "alarm", {"start_tone": True, field: True},
            )

    @pytest.mark.parametrize("bad", [1, 0, "yes", None])
    def test_start_tone_must_be_strict_bool(self, bad):
        # bool ist subclass von int in Python — wir wollen aber strikt bool
        with pytest.raises(ValueError, match="start_tone must be bool"):
            FreeEchoChannel._validate_audio_flag("notification", {"start_tone": bad})


# ── JSON-Frame-Struktur (mock ws, async via run()) ──────────────────

@pytest.fixture
def room():
    """Mock-WebSocket im _devices-Dict registriert + cleanup."""
    rid = "test-room"
    ws = MagicMock()
    ws.closed = False  # WS lebendig (sonst skipt der Send-Pfad)
    ws.send_str = AsyncMock(return_value=None)
    ws.send_bytes = AsyncMock(return_value=None)
    _devices[rid] = ws
    yield rid, ws
    _devices.pop(rid, None)


def _last_json(ws):
    """Letztes JSON-Frame das ueber send_str gesendet wurde."""
    return json.loads(ws.send_str.await_args.args[0])


def _all_jsons(ws):
    """Alle JSON-Frames in Reihenfolge."""
    return [json.loads(c.args[0]) for c in ws.send_str.await_args_list]


class TestSendAudioFlag:
    @pytest.mark.parametrize("audio_type", ALL_TYPES)
    def test_frame_per_type(self, room, audio_type):
        rid, ws = room
        ch = FreeEchoChannel()
        assert run(ch.send_audio_flag(rid, audio_type, start_tone=True)) is True
        assert _last_json(ws) == {
            "type": "audio_flag", "audio_type": audio_type, "start_tone": True,
        }

    def test_start_tone_false(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "music", start_tone=False))
        assert _last_json(ws)["start_tone"] is False

    def test_invalid_raises_before_wire(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        with pytest.raises(ValueError):
            run(ch.send_audio_flag(rid, "nonsense", start_tone=True))
        with pytest.raises(ValueError):
            run(ch.send_audio_flag(rid, "music"))   # start_tone fehlt
        ws.send_str.assert_not_awaited()

    def test_no_room_returns_false(self):
        ch = FreeEchoChannel()
        assert run(ch.send_audio_flag("no-such-room", "music", start_tone=False)) is False


class TestSendAudioStart:
    def test_no_total_size(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_start(rid))
        sent = _last_json(ws)
        # channels wird immer mitgeschickt (Default mono=1); rate NICHT
        # (Puck-Hardware fest 48 kHz — rate würde die Firmware-Whitelist
        # FATAL triggern).
        assert sent == {"type": "audio_start", "channels": 1}
        assert "rate" not in sent

    def test_with_total_size(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_start(rid, total_size=1024))
        assert _last_json(ws) == {
            "type": "audio_start", "channels": 1, "total_size": 1024,
        }


class TestSendAudioEnd:
    @pytest.mark.parametrize("end_tone", [True, False])
    def test_frame(self, room, end_tone):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_end(rid, end_tone=end_tone))
        assert _last_json(ws) == {"type": "audio_end", "end_tone": end_tone}

    def test_end_tone_is_required(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        with pytest.raises(TypeError):
            run(ch.send_audio_end(rid))
        ws.send_str.assert_not_awaited()

    @pytest.mark.parametrize("bad", [1, "yes", None])
    def test_end_tone_must_be_strict_bool(self, room, bad):
        rid, ws = room
        ch = FreeEchoChannel()
        with pytest.raises(ValueError, match="end_tone must be bool"):
            run(ch.send_audio_end(rid, end_tone=bad))
        ws.send_str.assert_not_awaited()


# ── Frame-Sequenzen pro Use-Case (Spec-Tabelle aus der Doku) ────────

class TestFrameSequences:
    def test_music(self, room):
        # Spec v2: audio_flag(music) -> audio_start -> chunks -> audio_end
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "music", start_tone=False))
        run(ch.send_audio_start(rid))
        run(ch.send_audio_chunk(rid, b"\x00" * 100))
        run(ch.send_audio_end(rid, end_tone=False))
        types = [f["type"] for f in _all_jsons(ws)]
        assert types == ["audio_flag", "audio_start", "audio_end"]
        assert ws.send_bytes.await_count == 1

    def test_tts_standalone(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "tts", start_tone=False))
        run(ch.send_audio_start(rid, total_size=500))
        run(ch.send_audio_chunk(rid, b"\x00" * 500))
        run(ch.send_audio_end(rid, end_tone=False))
        frames = _all_jsons(ws)
        assert [f["type"] for f in frames] == [
            "audio_flag", "audio_start", "audio_end",
        ]
        assert frames[1]["total_size"] == 500

    def test_notification_with_speech(self, room):
        # Ansage: ein Strom, Beginn-Ton, Sprache, Ende-Ton — GENAU EIN audio_end
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "notification", start_tone=True))
        run(ch.send_audio_start(rid, total_size=500))
        run(ch.send_audio_chunk(rid, b"\x00" * 500))
        run(ch.send_audio_end(rid, end_tone=True))
        frames = _all_jsons(ws)
        assert [f["type"] for f in frames] == ["audio_flag", "audio_start", "audio_end"]
        assert frames[0]["start_tone"] is True and frames[2]["end_tone"] is True

    def test_doorbell_is_a_stream_without_chunks(self, room):
        # Türklingel: Beginn- und Ende-Ton ohne Sprache = null Chunks, keine Sonderfälle
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "notification", start_tone=True))
        run(ch.send_audio_start(rid, total_size=0))
        run(ch.send_audio_end(rid, end_tone=True))
        assert [f["type"] for f in _all_jsons(ws)] == [
            "audio_flag", "audio_start", "audio_end",
        ]
        assert ws.send_bytes.await_count == 0

    def test_type_switch_mid_stream(self, room):
        # Spec: mid-stream Type-Switch -> nur audio_flag(neuer_type, start_tone=false),
        # kein neuer audio_start, gleiche Source bleibt.
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "music", start_tone=False))
        run(ch.send_audio_start(rid))
        run(ch.send_audio_chunk(rid, b"\x00" * 100))
        run(ch.send_audio_flag(rid, "tts", start_tone=False))   # Switch ohne neuen audio_start
        run(ch.send_audio_chunk(rid, b"\x00" * 100))
        run(ch.send_audio_end(rid, end_tone=False))
        types = [f["type"] for f in _all_jsons(ws)]
        assert types == ["audio_flag", "audio_start", "audio_flag", "audio_end"]


# ── Server-initiierte Pause vor einer Ansage ─────────────────────────

class TestPauseStream:
    def test_sends_pause_and_waits_for_ack(self, room):
        from aifred.plugins.channels.freeecho2_channel._shared import signal_pause_ack
        rid, ws = room
        ch = FreeEchoChannel()

        async def go():
            task = asyncio.create_task(ch.pause_stream(rid, timeout_sec=2.0))
            await asyncio.sleep(0.05)
            assert not task.done(), "must wait for the puck's acknowledgement"
            assert _last_json(ws) == {"type": "wake", "room": rid, "agent": "_pause"}
            signal_pause_ack(rid)
            await task

        run(go())

    def test_missing_ack_raises(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        with pytest.raises(TimeoutError, match="did not acknowledge"):
            run(ch.pause_stream(rid, timeout_sec=0.1))

    def test_no_room_raises(self):
        ch = FreeEchoChannel()
        with pytest.raises(ConnectionError):
            run(ch.pause_stream("no-such-room", timeout_sec=0.1))

    def test_ack_without_pending_pause_is_ignored(self):
        from aifred.plugins.channels.freeecho2_channel._shared import signal_pause_ack
        signal_pause_ack("test-room")   # ein gesprochenes „Bitte Pause“: darf nichts auslösen
