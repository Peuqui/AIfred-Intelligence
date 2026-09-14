"""Replies to an announcement return to the announcing session.

Email threads by Message-ID; Telegram and Discord route a reply to one
specific bot message by that message's key (14.09.2026: answers to a
scheduler announcement landed in the chat's session instead)."""

from types import SimpleNamespace

from aifred.lib.routing_table import REPLY_TO_MESSAGE_KEY, RoutingTable, message_route_key


def test_sent_messages_route_to_their_session(tmp_path) -> None:
    table = RoutingTable(tmp_path / "routing.db")
    table.register_sent_messages("telegram", 42, [100, 101], "s" * 32)
    route = table.get_route("telegram", message_route_key(42, 101))
    assert route is not None and route.session_id == "s" * 32
    # No session → nothing registered
    table.register_sent_messages("telegram", 42, [102], None)
    assert table.get_route("telegram", message_route_key(42, 102)) is None


def test_telegram_reply_names_the_answered_message() -> None:
    from aifred.plugins.channels.telegram_channel import _build_inbound

    def update(reply_to):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=7, first_name="Peuqui", last_name=None, username="p"),
            effective_chat=SimpleNamespace(id=42, type="private"),
            message=SimpleNamespace(text="Danke", date=None, reply_to_message=reply_to),
        )

    replied = _build_inbound(update(SimpleNamespace(message_id=101)))  # type: ignore[arg-type]
    assert replied.metadata[REPLY_TO_MESSAGE_KEY] == message_route_key(42, 101)
    plain = _build_inbound(update(None))  # type: ignore[arg-type]
    assert REPLY_TO_MESSAGE_KEY not in plain.metadata
    assert plain.channel_id == replied.channel_id == "42"  # the reply still goes back to the chat
