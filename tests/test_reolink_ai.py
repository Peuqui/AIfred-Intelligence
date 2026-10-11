"""Tests für aifred.lib.reolink_ai (Befehle ohne echte Kamera)."""

from __future__ import annotations

import asyncio

from aifred.lib.reolink_ai import ReolinkAIClient


def test_goto_guard_sends_to_pos_and_never_set_pos(monkeypatch):
    """Die Ausgangsposition wird angefahren (toPos); ``setPos`` würde die in der
    Kamera gespeicherte Wächterposition überschreiben."""
    client = ReolinkAIClient("kamera.local", cred="x")
    sent: dict = {}

    async def fake_call(cmd, payload):
        sent["cmd"] = cmd
        sent["payload"] = payload
        return {}

    monkeypatch.setattr(client, "_call_with_relogin", fake_call)
    asyncio.run(client.ptz_goto_guard(1))
    assert sent["cmd"] == "SetPtzGuard"
    guard = sent["payload"][0]["param"]["PtzGuard"]
    assert guard == {"channel": 1, "cmdStr": "toPos"}
