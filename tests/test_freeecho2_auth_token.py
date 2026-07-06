"""Tests für die A6-Token-Prüfung im FreeEcho.2-Register-Pfad.

Kontrakt (SECURITY_FINDINGS.md A6):
- Ist ``auth_token`` in den Channel-Credentials gesetzt, MUSS der
  Register-Frame ein passendes ``token``-Feld tragen — sonst wird die
  Verbindung mit App-Close-Code 4401 geschlossen, BEVOR sie einen
  Room-Slot belegen oder die Pipeline treiben kann.
- Ohne konfiguriertes Token bleibt das Bestandsverhalten (offen,
  Warnung im Log beim Serverstart).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import WSMsgType

from aifred.lib.credential_broker import broker
from aifred.plugins.channels.freeecho2_channel import FreeEchoChannel, _devices


def run(coro):
    return asyncio.run(coro)


class FakeWS:
    """Minimaler async-iterierbarer WebSocketResponse-Ersatz."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.close = AsyncMock()
        self.closed = False

    async def prepare(self, request):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        raise StopAsyncIteration


def _register_msg(room: str, token: "str | None" = None) -> MagicMock:
    payload = f'{{"type":"register","room":"{room}"'
    if token is not None:
        payload += f',"token":"{token}"'
    payload += "}"
    msg = MagicMock()
    msg.type = WSMsgType.TEXT
    msg.data = payload
    return msg


def _run_handshake(messages, configured_token: str, auth_required: str = ""):
    """_handle_ws mit gefaktem WS + Broker-Token durchspielen."""
    channel = FreeEchoChannel()
    channel.channel_log = MagicMock()
    fake_ws = FakeWS(messages)
    request = MagicMock()
    request.remote = "203.0.113.7"

    def fake_get(section, key):
        if section == "freeecho2" and key == "auth_token":
            return configured_token
        if section == "freeecho2" and key == "auth_required":
            return auth_required
        return ""

    with patch("aiohttp.web.WebSocketResponse", return_value=fake_ws), \
         patch.object(broker, "get", side_effect=fake_get):
        run(channel._handle_ws(request))
    return channel, fake_ws


class TestRegisterTokenCheck:
    def setup_method(self):
        _devices.clear()

    def teardown_method(self):
        _devices.clear()

    def _registered(self, channel) -> bool:
        # Genau die Erfolgs-Zeile matchen — der Disconnect-Log enthält
        # "(unregistered)" und würde einen Substring-Match "registered"
        # fälschlich treffen.
        return any(
            str(c.args[0]).startswith("FreeEcho.2 registered:")
            for c in channel.channel_log.call_args_list
        )

    def test_missing_token_rejected(self):
        channel, ws = _run_handshake(
            [_register_msg("kueche")], configured_token="geheim",
        )
        ws.close.assert_awaited_once()
        assert ws.close.call_args.kwargs.get("code") == 4401
        assert not self._registered(channel)
        assert _devices == {}

    def test_wrong_token_rejected(self):
        channel, ws = _run_handshake(
            [_register_msg("kueche", token="falsch")], configured_token="geheim",
        )
        ws.close.assert_awaited_once()
        assert ws.close.call_args.kwargs.get("code") == 4401
        assert not self._registered(channel)
        assert _devices == {}

    def test_correct_token_registers(self):
        channel, ws = _run_handshake(
            [_register_msg("kueche", token="geheim")], configured_token="geheim",
        )
        assert self._registered(channel)
        # Kein Auth-Close — 4401 darf nie gefallen sein.
        for c in ws.close.call_args_list:
            assert c.kwargs.get("code") != 4401

    def test_no_token_configured_keeps_open_behavior(self):
        channel, ws = _run_handshake(
            [_register_msg("kueche")], configured_token="",
        )
        assert self._registered(channel)
        for c in ws.close.call_args_list:
            assert c.kwargs.get("code") != 4401

    def test_auth_required_false_disables_check(self):
        # Expliziter Aus-Schalter: Token gesetzt, aber auth_required=false →
        # Register ohne Token wird akzeptiert (bewusste Admin-Entscheidung).
        channel, ws = _run_handshake(
            [_register_msg("kueche")], configured_token="geheim",
            auth_required="false",
        )
        assert self._registered(channel)
        for c in ws.close.call_args_list:
            assert c.kwargs.get("code") != 4401

    def test_auth_required_garbage_stays_on(self):
        # Fail-safe Richtung AN: nur das Literal "false" deaktiviert.
        channel, ws = _run_handshake(
            [_register_msg("kueche")], configured_token="geheim",
            auth_required="maybe",
        )
        ws.close.assert_awaited_once()
        assert ws.close.call_args.kwargs.get("code") == 4401
        assert not self._registered(channel)
