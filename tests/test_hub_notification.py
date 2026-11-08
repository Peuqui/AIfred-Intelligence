"""The phases of a channel's message reach the open pages (message_processor.push_hub_notification): until
08.11.2026 they went into data/message_hub/notification.json, which only the Reflex page read; the Vue app saw
neither the hint nor the new session a channel had made."""

import asyncio
from collections.abc import Iterator

import pytest

from aifred.lib import session_events
from aifred.lib.message_processor import HUB_NOTIFICATION_KIND, push_hub_notification

FOLLOWED = "a" * 32
UNSEEN = "b" * 32


@pytest.fixture(autouse=True)
def fresh_hub() -> Iterator[None]:
    """The test's loop is the hub's home only while it runs (a closed loop left bound would swallow later publishes)."""
    yield
    session_events._home_loop = None
    for session in (FOLLOWED, UNSEEN):
        session_events._channels.pop(session, None)


def test_every_followed_session_gets_the_newest_phase_only() -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        session_events.bind_home_loop(asyncio.get_running_loop())
        async with session_events.subscribe(FOLLOWED):
            push_hub_notification("c" * 32, "Post von Anna", "email", "Anna", status="received")
            push_hub_notification("c" * 32, "Post von Anna", "email", "Anna", status="done")
            await asyncio.sleep(0)
        followed = [event.data["status"] for event in session_events.replay_after(FOLLOWED, 0) if event.kind == HUB_NOTIFICATION_KIND]
        unseen = [event.kind for event in session_events.replay_after(UNSEEN, 0)]
        return followed, unseen

    assert asyncio.run(scenario()) == (["done"], [])
