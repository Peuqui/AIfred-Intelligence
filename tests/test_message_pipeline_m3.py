"""Tests for M3 — external messages must persist WRAPPED in llm_history.

The <external_message> injection fence must survive across turns. A message an agent answers is
saved by its turn, wrapped (tests/test_turn_service.py, the channel turns); save_inbound_to_session
writes the CHAT history only, for a spoken exchange AIfred only routed on.
"""

from datetime import datetime, UTC

import pytest

from aifred.lib.formatting import build_user_chat_entry
from aifred.lib import session_storage
from aifred.lib.envelope import InboundMessage
from aifred.lib.message_processor import save_inbound_to_session


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    return tmp_path


def _session_data(sid: str) -> dict:
    session = session_storage.load_session(sid)
    assert session is not None
    data: dict = session["data"]
    return data


def _make_message(text: str) -> InboundMessage:
    return InboundMessage(
        channel="email",
        channel_id="thread-1",
        sender="somebody@example.com",
        text=text,
        timestamp=datetime.now(UTC),
    )


class TestM3WrappedHistory:
    def test_save_inbound_to_session_writes_chat_only(self, session_dir):
        sid = "a" * 32
        session_storage.create_empty_session(sid, owner="mp")

        save_inbound_to_session(sid, _make_message("hallo"), from_user=True)

        data = _session_data(sid)
        assert len(data["chat_history"]) == 1
        assert data["chat_history"][0]["role"] == "user"
        assert "hallo" in data["chat_history"][0]["content"]
        # The llm_history entry is not this function's: the turn (or a routed voice turn) writes it, wrapped.
        assert "llm_history" not in data

    def test_the_users_message_from_a_channel_is_the_same_bubble_as_one_typed_in_the_browser(self, session_dir):
        """A question spoken at the puck has a timestamp like every bubble (the read mark and the chat tell bubbles
        apart by it; 21.11.2026 the puck's questions had none)."""
        sid = "e" * 32
        session_storage.create_empty_session(sid, owner="mp")

        save_inbound_to_session(sid, _make_message("hallo"), from_user=True)

        entry = _session_data(sid)["chat_history"][0]
        typed = build_user_chat_entry("x")
        assert set(entry) == set(typed)
        assert datetime.fromisoformat(entry["timestamp"])

    def test_inbound_from_non_user_is_a_request_under_its_own_name(self, session_dir):
        """Someone else's message the model answers is a request (role "user", as the model sees it), so the chat
        pins and folds it and never counts it unread; its sender keeps it on the left (23.11.2026: a job's long task
        stood as an answer, and the read line stood above it)."""
        sid = "d" * 32
        session_storage.create_empty_session(sid, owner="mp")

        peer = InboundMessage(
            channel="ai_connect", channel_id="Mini:Peer", sender="Mini:Peer",
            text="Verbindungstest", timestamp=datetime.now(UTC),
        )
        save_inbound_to_session(sid, peer, from_user=False)

        entry = _session_data(sid)["chat_history"][0]
        assert entry["role"] == "user"
        assert entry["agent"] == "ai_connect"
        assert entry["agent_display_name"] == "AI-Connect \u00b7 Mini:Peer"
        assert entry["agent_emoji"] == "\U0001f50c"
        assert entry["content"] == "Verbindungstest"


def test_announcement_carries_its_session_for_threading(monkeypatch, channels_on) -> None:
    """A scheduler/alert announcement names its session in the outbound
    metadata, so a threading channel (email) registers it and the answer to
    the announcement returns to that session (14.09.: it opened a new one)."""
    import asyncio

    import aifred.lib.message_processor as mp

    channels_on("email")
    sent = {}

    class FakePlugin:
        async def send_reply(self, outbound, original):
            sent["metadata"] = outbound.metadata

    monkeypatch.setattr("aifred.lib.plugin_registry.get_channel", lambda name: FakePlugin())
    monkeypatch.setattr(mp, "_resolve_channel_recipient", lambda channel, recipient: "peuqui@example.org")
    ok = asyncio.run(mp.announce_to_channel("email", "", "Psalm 143", session_id="s" * 32))
    assert ok and sent["metadata"]["session_id"] == "s" * 32


def test_history_entry_names_the_speaker_only_for_an_agent_message() -> None:
    from aifred.lib.message_builder import build_autonomous_history_entry

    automatic = build_autonomous_history_entry("scheduler", "Zeit für das Gebet")
    assert automatic["content"] == "[AUTOMATISCHE MELDUNG (scheduler)]: Zeit für das Gebet"
    named = build_autonomous_history_entry("freeecho2", "Commit ist durch", speaker="Codine")
    assert named["content"] == "[NACHRICHT VON Codine (über freeecho2)]: Commit ist durch"
