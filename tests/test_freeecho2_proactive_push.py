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
        ws = MagicMock(); ws.closed = False
        _devices["wohnzimmer"] = ws
        assert _resolve_channel_recipient("freeecho2", "") == "wohnzimmer"

    def test_multiple_devices_first_wins(self):
        # Wer gezielter pushen will, gibt recipient explizit an.
        # Sonst nehmen wir den first-connected — dict-Insertion-Order.
        from aifred.lib.message_processor import _resolve_channel_recipient
        ws1 = MagicMock(); ws1.closed = False
        ws2 = MagicMock(); ws2.closed = False
        _devices["wohnzimmer"] = ws1
        _devices["kueche"] = ws2
        assert _resolve_channel_recipient("freeecho2", "") == "wohnzimmer"


# ── send_reply proaktiv-Pfad ─────────────────────────────────────────

@pytest.fixture
def push_setup():
    """Channel + verbundener WS + gemockter Orchestrator."""
    _devices.clear()
    rid = "wohnzimmer"
    ws = MagicMock(); ws.closed = False
    _devices[rid] = ws

    orc = MagicMock()
    orc.play_tts = AsyncMock(return_value=None)
    orc.play_notification = AsyncMock(return_value=None)

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
