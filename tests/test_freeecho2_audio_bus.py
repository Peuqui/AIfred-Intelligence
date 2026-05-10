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

class TestValidateAudioFlag:
    def test_music_no_params_ok(self):
        FreeEchoChannel._validate_audio_flag("music", {})

    def test_tts_no_params_ok(self):
        FreeEchoChannel._validate_audio_flag("tts", {})

    def test_alarm_complete_ok(self):
        FreeEchoChannel._validate_audio_flag(
            "alarm", {"repeats": 3, "max_duration": 60, "with_tts": False}
        )

    def test_notification_complete_ok(self):
        FreeEchoChannel._validate_audio_flag("notification", {"with_tts": True})

    def test_alarm_repeats_zero_means_unlimited(self):
        FreeEchoChannel._validate_audio_flag(
            "alarm", {"repeats": 0, "max_duration": 0, "with_tts": False}
        )

    def test_unknown_audio_type_raises(self):
        with pytest.raises(ValueError, match="unknown audio_type"):
            FreeEchoChannel._validate_audio_flag("speech", {})

    def test_music_with_extra_field_raises(self):
        with pytest.raises(ValueError, match="unexpected fields"):
            FreeEchoChannel._validate_audio_flag("music", {"with_tts": True})

    def test_alarm_missing_field_raises(self):
        with pytest.raises(ValueError, match="missing required fields"):
            FreeEchoChannel._validate_audio_flag(
                "alarm", {"max_duration": 60, "with_tts": False}
            )

    def test_notification_missing_with_tts_raises(self):
        with pytest.raises(ValueError, match="missing required fields"):
            FreeEchoChannel._validate_audio_flag("notification", {})

    def test_alarm_repeats_negative_raises(self):
        with pytest.raises(ValueError, match="repeats must be int>=0"):
            FreeEchoChannel._validate_audio_flag(
                "alarm", {"repeats": -1, "max_duration": 0, "with_tts": False}
            )

    def test_alarm_repeats_string_raises(self):
        with pytest.raises(ValueError, match="repeats must be int>=0"):
            FreeEchoChannel._validate_audio_flag(
                "alarm", {"repeats": "3", "max_duration": 0, "with_tts": False}
            )

    def test_alarm_max_duration_negative_raises(self):
        with pytest.raises(ValueError, match="max_duration must be int>=0"):
            FreeEchoChannel._validate_audio_flag(
                "alarm", {"repeats": 3, "max_duration": -10, "with_tts": False}
            )

    def test_with_tts_string_raises(self):
        with pytest.raises(ValueError, match="with_tts must be bool"):
            FreeEchoChannel._validate_audio_flag(
                "alarm",
                {"repeats": 3, "max_duration": 60, "with_tts": "yes"},
            )

    def test_with_tts_int_raises(self):
        # bool ist subclass von int in Python — wir wollen aber strikt
        # bool, kein int-Aliasing (1/0 als with_tts ist ungültig).
        with pytest.raises(ValueError, match="with_tts must be bool"):
            FreeEchoChannel._validate_audio_flag(
                "notification", {"with_tts": 1}
            )


# ── JSON-Frame-Struktur (mock ws, async via run()) ──────────────────

@pytest.fixture
def room():
    """Mock-WebSocket im _devices-Dict registriert + cleanup."""
    rid = "test-room"
    ws = MagicMock()
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
    def test_music(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        assert run(ch.send_audio_flag(rid, "music")) is True
        assert _last_json(ws) == {"type": "audio_flag", "audio_type": "music"}

    def test_tts(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "tts"))
        assert _last_json(ws) == {"type": "audio_flag", "audio_type": "tts"}

    def test_alarm(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(
            rid, "alarm", repeats=3, max_duration=60, with_tts=False,
        ))
        assert _last_json(ws) == {
            "type": "audio_flag",
            "audio_type": "alarm",
            "repeats": 3,
            "max_duration": 60,
            "with_tts": False,
        }

    def test_notification(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "notification", with_tts=True))
        assert _last_json(ws) == {
            "type": "audio_flag",
            "audio_type": "notification",
            "with_tts": True,
        }

    def test_invalid_raises_before_wire(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        with pytest.raises(ValueError):
            run(ch.send_audio_flag(rid, "speech"))
        ws.send_str.assert_not_awaited()

    def test_no_room_returns_false(self):
        ch = FreeEchoChannel()
        assert run(ch.send_audio_flag("no-such-room", "music")) is False


class TestSendAudioStart:
    def test_no_total_size(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_start(rid))
        sent = _last_json(ws)
        assert sent == {"type": "audio_start"}
        # Hardware-Constraint: kein channels/rate (Puck fest 48k mono)
        assert "channels" not in sent
        assert "rate" not in sent

    def test_with_total_size(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_start(rid, total_size=1024))
        assert _last_json(ws) == {"type": "audio_start", "total_size": 1024}


class TestSendAudioEnd:
    def test_frame(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_end(rid))
        assert _last_json(ws) == {"type": "audio_end"}


# ── Frame-Sequenzen pro Use-Case (Spec-Tabelle aus der Doku) ────────

class TestFrameSequences:
    def test_music(self, room):
        # Spec: audio_flag(music) -> audio_start -> chunks -> audio_end
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "music"))
        run(ch.send_audio_start(rid))
        run(ch.send_audio_chunk(rid, b"\x00" * 100))
        run(ch.send_audio_end(rid))
        types = [f["type"] for f in _all_jsons(ws)]
        assert types == ["audio_flag", "audio_start", "audio_end"]
        assert ws.send_bytes.await_count == 1

    def test_tts_standalone(self, room):
        # Spec: audio_flag(tts) -> audio_start(total_size) -> chunks -> audio_end
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "tts"))
        run(ch.send_audio_start(rid, total_size=500))
        run(ch.send_audio_chunk(rid, b"\x00" * 500))
        run(ch.send_audio_end(rid))
        frames = _all_jsons(ws)
        assert [f["type"] for f in frames] == [
            "audio_flag", "audio_start", "audio_end",
        ]
        assert frames[1]["total_size"] == 500

    def test_alarm_no_tail(self, room):
        # Spec: nur audio_flag(alarm,...), kein PCM, kein audio_end
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(
            rid, "alarm", repeats=3, max_duration=60, with_tts=False,
        ))
        assert ws.send_str.await_count == 1
        assert ws.send_bytes.await_count == 0

    def test_alarm_with_tail(self, room):
        # Spec: audio_flag(alarm,with_tts=true) -> audio_flag(tts) ->
        #       audio_start -> chunks -> audio_end
        # Wichtig: GENAU EIN audio_end am Ende, nicht zwischen Phasen.
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(
            rid, "alarm", repeats=3, max_duration=60, with_tts=True,
        ))
        run(ch.send_audio_flag(rid, "tts"))
        run(ch.send_audio_start(rid, total_size=500))
        run(ch.send_audio_chunk(rid, b"\x00" * 500))
        run(ch.send_audio_end(rid))
        types = [f["type"] for f in _all_jsons(ws)]
        assert types == ["audio_flag", "audio_flag", "audio_start", "audio_end"]

    def test_notification_no_tail(self, room):
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "notification", with_tts=False))
        assert ws.send_str.await_count == 1
        assert ws.send_bytes.await_count == 0

    def test_type_switch_mid_stream(self, room):
        # Spec: mid-stream Type-Switch -> nur audio_flag(neuer_type),
        # kein neuer audio_start, gleiche Source bleibt.
        rid, ws = room
        ch = FreeEchoChannel()
        run(ch.send_audio_flag(rid, "music"))
        run(ch.send_audio_start(rid))
        run(ch.send_audio_chunk(rid, b"\x00" * 100))
        run(ch.send_audio_flag(rid, "tts"))   # Switch ohne neuen audio_start
        run(ch.send_audio_chunk(rid, b"\x00" * 100))
        run(ch.send_audio_end(rid))
        types = [f["type"] for f in _all_jsons(ws)]
        assert types == ["audio_flag", "audio_start", "audio_flag", "audio_end"]
