"""freeecho2_announce: an agent's announcement gets a bubble in each room's
session (the agent as sender), like the announce API — one SSoT."""
from __future__ import annotations

import asyncio
import json

import pytest

from aifred.lib.plugin_base import PluginContext


@pytest.fixture
def announced(monkeypatch: pytest.MonkeyPatch):
    import aifred.lib.message_processor as message_processor

    calls: list[tuple] = []

    async def fake_into_rooms(channel, rooms, text, speaker, metadata):
        calls.append((channel, rooms, text, speaker, metadata))
        return list(rooms)

    monkeypatch.setattr(message_processor, "announce_into_rooms", fake_into_rooms)
    monkeypatch.setattr(message_processor, "resolve_announce_targets",
                        lambda channel, target: ["Buero-Puck-2"] if target in ("*", "Buero-Puck-2") else [])
    return calls


def _announce(agent_id: str, **arguments) -> dict:
    from aifred.plugins.channels.freeecho2_channel import FreeEchoChannel

    ctx = PluginContext(agent_id=agent_id, lang="de", session_id="browser-session")
    tool = next(t for t in FreeEchoChannel().get_tools(ctx) if t.name == "freeecho2_announce")
    return json.loads(asyncio.run(tool.executor(**arguments)))


def test_the_announcement_goes_into_the_rooms_with_the_agent_as_sender(announced) -> None:
    result = _announce("aifred", message="Das Essen ist fertig.")
    assert result == {"success": True, "audio_type": "notification", "rooms": ["Buero-Puck-2"]}
    assert announced == [("freeecho2", ["Buero-Puck-2"], "Das Essen ist fertig.", "AIfred",
                          {"audio_type": "notification", "proactive": True})]


def test_no_connected_room_announces_nothing(announced) -> None:
    result = _announce("aifred", message="Hallo", target="Keller")
    assert result["success"] is False and announced == []
