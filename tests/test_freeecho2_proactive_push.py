"""Tests fuer den proaktiven Push-Pfad in den FreeEcho.2-Channel.

Architektur (siehe docs/de/architecture/proactive-alerts.md):
- ``announce_to_channel`` ist die SSoT fuer autonome Sends (Alerts +
  Scheduler). Sie loest den Recipient auf, baut OutboundMessage +
  dummy InboundMessage (sender="system"), und ruft ``plugin.send_reply``.
- FreeEcho.2 hat keine Allowlist. Der Resolver muss daher auf den
  ersten gerade verbundenen Geraete-Room fallen, sonst landet
  ``recipient=""`` und der Push schlaegt silent fehl.
- ``send_reply`` erkennt den autonomen Aufruf an ``sender == "system"``
  und routet ueber ``play_notification(with_tts=True)`` statt
  ``play_tts`` — das gibt dem User einen lokalen Chime vor der
  TTS-Sprache, damit Push nicht "aus dem Nichts" passiert.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aifred.lib.envelope import InboundMessage, OutboundMessage
from aifred.plugins.channels.freeecho2_channel import FreeEchoChannel, _devices


def run(coro):
    return asyncio.run(coro)


# ── Recipient-Resolution ────────────────────────────────────────────

class TestResolveFreeechoRecipient:
    """``_resolve_channel_recipient`` darf fuer freeecho2 nicht "" liefern,
    solange ein Geraet verbunden ist — sonst stoppt ``announce_to_channel``
    den Push silent mit "no recipient/allowlist for 'freeecho2'"."""

    def setup_method(self):
        _devices.clear()

    def teardown_method(self):
        _devices.clear()

    def test_no_device_no_recipient(self):
        from aifred.lib.message_processor import _resolve_channel_recipient
        assert _resolve_channel_recipient("freeecho2", "") == ""

    def test_explicit_recipient_passes_through(self):
        from aifred.lib.message_processor import _resolve_channel_recipient
        # Explizit angegebener Room geht durch — auch wenn nicht
        # connected. Sender kann sich auf eine known-room verlassen.
        assert _resolve_channel_recipient("freeecho2", "wohnzimmer") == "wohnzimmer"

    def test_auto_resolves_to_first_connected_room(self):
        from aifred.lib.message_processor import _resolve_channel_recipient
        ws = MagicMock()
        ws.closed = False
        _devices["wohnzimmer"] = ws
        assert _resolve_channel_recipient("freeecho2", "") == "wohnzimmer"

    def test_multiple_devices_first_wins(self):
        # Wer gezielter pushen will, gibt recipient explizit an.
        # Sonst nehmen wir den first-connected — dict-Insertion-Order.
        from aifred.lib.message_processor import _resolve_channel_recipient
        ws1 = MagicMock()
        ws1.closed = False
        ws2 = MagicMock()
        ws2.closed = False
        _devices["wohnzimmer"] = ws1
        _devices["kueche"] = ws2
        assert _resolve_channel_recipient("freeecho2", "") == "wohnzimmer"


# ── send_reply proaktiv-Pfad ─────────────────────────────────────────

@pytest.fixture
def push_setup():
    """Channel + verbundener WS + gemockter Orchestrator."""
    _devices.clear()
    rid = "wohnzimmer"
    ws = MagicMock()
    ws.closed = False
    _devices[rid] = ws

    orc = MagicMock()
    orc.play_tts = AsyncMock(return_value=None)
    orc.play_notification = AsyncMock(return_value=None)
    orc.play_alarm = AsyncMock(return_value=None)
    # Design A: der Worker schickt nach play_* das done-Frame via bridge.
    orc.bridge.send_done = AsyncMock(return_value=True)

    audio_ch = MagicMock()
    audio_ch.get_orchestrator = MagicMock(return_value=orc)

    yield rid, ws, orc, audio_ch
    _devices.clear()


def _make_outbound(text: str = "Test", **metadata) -> OutboundMessage:
    return OutboundMessage(
        channel="freeecho2",
        channel_id="wohnzimmer",
        recipient="wohnzimmer",
        text=text,
        media=None,
        metadata=metadata or {},
    )


def _make_inbound(sender: str) -> InboundMessage:
    return InboundMessage(
        channel="freeecho2",
        channel_id="wohnzimmer",
        sender=sender,
        text="",
        timestamp=datetime.now(),
    )


class TestSendReplyProactive:
    """Konkret die Routing-Logik im send_reply, mit gemockten TTS-
    und Audio-Channel-Auflosungen. Vermeidet GPU/Whisper-Calls."""

    def _patched_call(self, audio_ch_mock, outbound, original):
        """send_reply mit den teuren Teilen gemockt: _run_tts gibt
        einen Stub-Pfad, _convert_to_pcm gibt nicht-leere Bytes, der
        FreeEcho2Channel-Resolver liefert unseren Mock-Orchestrator.
        Path.unlink ist no-op."""
        ch = FreeEchoChannel()
        with patch.object(
            ch, "_run_tts", AsyncMock(return_value="/tmp/dummy.wav")
        ), patch.object(
            ch, "_convert_to_pcm", AsyncMock(return_value=b"\x00\x01" * 48000)
        ), patch(
            "aifred.lib.audio_channels.resolve", return_value=audio_ch_mock
        ), patch(
            "pathlib.Path.unlink"
        ):
            run(ch.send_reply(outbound, original))

    def test_normal_reply_uses_play_tts(self, push_setup):
        """User-Wake-Reply (sender != "system") → klassischer TTS-Stream
        ohne Chime."""
        _rid, _ws, orc, audio_ch = push_setup
        outbound = _make_outbound("Hallo Welt")
        original = _make_inbound(sender="wohnzimmer")
        self._patched_call(audio_ch, outbound, original)
        orc.play_tts.assert_awaited_once()
        orc.play_notification.assert_not_awaited()

    def test_announce_via_system_inbound_uses_play_notification(self, push_setup):
        """``announce_to_channel`` baut dummy mit sender="system" —
        der Channel soll daraufhin Chime + TTS (play_notification)
        statt nackten TTS spielen."""
        _rid, _ws, orc, audio_ch = push_setup
        outbound = _make_outbound("Achtung, Vision-Alert!")
        dummy = _make_inbound(sender="system")
        self._patched_call(audio_ch, outbound, dummy)
        orc.play_notification.assert_awaited_once()
        # play_notification(with_tts=True, tts_pcm=...) — pruefe Args.
        kwargs = orc.play_notification.await_args.kwargs
        assert kwargs.get("with_tts") is True
        assert kwargs.get("tts_pcm")  # nicht-leer
        orc.play_tts.assert_not_awaited()

    def test_explicit_proactive_metadata_uses_play_notification(self, push_setup):
        """Alternative: Caller markiert outbound.metadata.proactive=True
        — gleicher Push-Pfad, falls man system-sender mal nicht hat."""
        _rid, _ws, orc, audio_ch = push_setup
        outbound = _make_outbound("Manueller Push", proactive=True)
        original = _make_inbound(sender="wohnzimmer")
        self._patched_call(audio_ch, outbound, original)
        orc.play_notification.assert_awaited_once()
        orc.play_tts.assert_not_awaited()

    def test_silent_reply_skips_both(self, push_setup):
        """``silent_reply``-Flag (audio_player-Tools) skippt jede
        TTS-Bestaetigung — vor der proactive-Detection, weil "ich habe
        gerade Music gestartet" auch bei Push nichts zu kommentieren
        haette."""
        _rid, _ws, orc, audio_ch = push_setup
        outbound = _make_outbound("...", silent_reply=True)
        dummy = _make_inbound(sender="system")
        self._patched_call(audio_ch, outbound, dummy)
        orc.play_notification.assert_not_awaited()
        orc.play_tts.assert_not_awaited()

    def test_no_device_skips_silently(self, push_setup):
        """Push an nicht-verbundenes Geraet darf nicht crashen — nur
        die Warning im channel_log."""
        _rid, _ws, orc, _audio_ch = push_setup
        _devices.clear()  # niemand mehr da
        ch = FreeEchoChannel()
        outbound = _make_outbound("never arrives")
        dummy = _make_inbound(sender="system")
        run(ch.send_reply(outbound, dummy))
        orc.play_notification.assert_not_awaited()
        orc.play_tts.assert_not_awaited()


class TestSendReplyAudioType:
    """``outbound.metadata.audio_type`` bestimmt welcher Chime vor dem TTS
    gespielt wird — passend zur Schwere des Events. Default ohne explizite
    Angabe ist ``notification`` (sanfter Sound)."""

    def _patched_call(self, audio_ch_mock, outbound, original):
        ch = FreeEchoChannel()
        with patch.object(
            ch, "_run_tts", AsyncMock(return_value="/tmp/dummy.wav")
        ), patch.object(
            ch, "_convert_to_pcm", AsyncMock(return_value=b"\x00\x01" * 48000)
        ), patch(
            "aifred.lib.audio_channels.resolve", return_value=audio_ch_mock
        ), patch(
            "pathlib.Path.unlink"
        ):
            run(ch.send_reply(outbound, original))

    def test_audio_type_alarm_uses_play_alarm(self, push_setup):
        _rid, _ws, orc, audio_ch = push_setup
        orc.play_alarm = AsyncMock(return_value=None)
        outbound = _make_outbound("Brand!", audio_type="alarm")
        dummy = _make_inbound(sender="system")
        self._patched_call(audio_ch, outbound, dummy)
        orc.play_alarm.assert_awaited_once()
        kwargs = orc.play_alarm.await_args.kwargs
        assert kwargs.get("with_tts") is True
        assert kwargs.get("tts_pcm")
        orc.play_notification.assert_not_awaited()

    def test_audio_type_notification_uses_play_notification(self, push_setup):
        _rid, _ws, orc, audio_ch = push_setup
        orc.play_alarm = AsyncMock(return_value=None)
        outbound = _make_outbound("FYI", audio_type="notification")
        dummy = _make_inbound(sender="system")
        self._patched_call(audio_ch, outbound, dummy)
        orc.play_notification.assert_awaited_once()
        orc.play_alarm.assert_not_awaited()

    def test_audio_type_unknown_falls_back_to_notification(self, push_setup):
        """Unbekannte Audio-Types werden zum Default ``notification`` —
        Schema-Drift im Caller bricht den Push nicht."""
        _rid, _ws, orc, audio_ch = push_setup
        orc.play_alarm = AsyncMock(return_value=None)
        outbound = _make_outbound("Test", audio_type="bogus")
        dummy = _make_inbound(sender="system")
        self._patched_call(audio_ch, outbound, dummy)
        orc.play_notification.assert_awaited_once()
        orc.play_alarm.assert_not_awaited()

    def test_audio_type_missing_defaults_to_notification(self, push_setup):
        """Kein audio_type-Feld → notification (sanfter Default)."""
        _rid, _ws, orc, audio_ch = push_setup
        orc.play_alarm = AsyncMock(return_value=None)
        outbound = _make_outbound("Test")  # kein audio_type
        dummy = _make_inbound(sender="system")
        self._patched_call(audio_ch, outbound, dummy)
        orc.play_notification.assert_awaited_once()
        orc.play_alarm.assert_not_awaited()


class TestAlertBusSeverityMapping:
    """alert_bus._default_deliver mappt severity → audio_type und reicht
    das via announce_to_channel-metadata an die Sinks weiter."""

    def test_critical_maps_to_alarm(self):
        from aifred.lib.alert_bus import _default_deliver, AlertEvent, AlertRule

        ev = AlertEvent(
            producer="vision", category="intruder", severity="critical",
            title="Eindringling", body="An der Garage",
        )
        rule = AlertRule(producer="vision", sinks=["freeecho2"])

        captured: dict = {}

        async def fake_announce(channel, recipient, text, *, media=None, metadata=None):
            captured["channel"] = channel
            captured["metadata"] = metadata or {}
            return True

        # Auch die Browser-Session muss stubbed sein — wir testen nur
        # die Metadata-Weiterreichung.
        with patch(
            "aifred.lib.message_processor.announce_to_channel", new=fake_announce
        ), patch(
            "aifred.lib.message_processor.record_autonomous_turn", return_value="sess"
        ):
            ok = run(_default_deliver(ev, rule))

        assert ok is True
        assert captured["channel"] == "freeecho2"
        assert captured["metadata"].get("audio_type") == "alarm"
        assert captured["metadata"].get("severity") == "critical"

    def test_warning_maps_to_alarm(self):
        # warning (z.B. unbekannte Person an der Tür) → auffälliger
        # alarm-Sound, nicht notification. SSoT: alert_bus._default_deliver
        # mappt critical UND warning auf "alarm".
        from aifred.lib.alert_bus import _default_deliver, AlertEvent, AlertRule

        ev = AlertEvent(
            producer="vision", category="face_unknown", severity="warning",
            title="Unbekannt", body="An der Tür",
        )
        rule = AlertRule(producer="vision", sinks=["freeecho2"])

        captured: dict = {}

        async def fake_announce(channel, recipient, text, *, media=None, metadata=None):
            captured["metadata"] = metadata or {}
            return True

        with patch(
            "aifred.lib.message_processor.announce_to_channel", new=fake_announce
        ), patch(
            "aifred.lib.message_processor.record_autonomous_turn", return_value="sess"
        ):
            run(_default_deliver(ev, rule))

        assert captured["metadata"].get("audio_type") == "alarm"
        assert captured["metadata"].get("severity") == "warning"

    def test_info_maps_to_notification(self):
        from aifred.lib.alert_bus import _default_deliver, AlertEvent, AlertRule

        ev = AlertEvent(
            producer="scheduler", category="reminder", severity="info",
            title="Erinnerung", body="Tagesplan",
        )
        rule = AlertRule(producer="scheduler", sinks=["freeecho2"])

        captured: dict = {}

        async def fake_announce(channel, recipient, text, *, media=None, metadata=None):
            captured["metadata"] = metadata or {}
            return True

        with patch(
            "aifred.lib.message_processor.announce_to_channel", new=fake_announce
        ), patch(
            "aifred.lib.message_processor.record_autonomous_turn", return_value="sess"
        ):
            run(_default_deliver(ev, rule))

        assert captured["metadata"].get("audio_type") == "notification"
