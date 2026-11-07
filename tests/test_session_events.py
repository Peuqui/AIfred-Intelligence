"""The event stream per session (lib/session_events.py) and the browser bus on top of it.

Before the hub, the browser bus held one queue per session: a second tab took the stream from the
first, and a full queue dropped events silently.
"""

import asyncio
import json
from collections.abc import Iterator

import pytest

from aifred.lib import session_events
from aifred.lib.api import browser_bus

SESSION = "test-session-events"


@pytest.fixture(autouse=True)
def _fresh_channel() -> Iterator[None]:
    session_events._channels.pop(SESSION, None)
    yield
    session_events._channels.pop(SESSION, None)


def test_every_subscriber_gets_every_event() -> None:
    async def scenario() -> tuple[list[int], list[int]]:
        async with session_events.subscribe(SESSION) as (_, first), session_events.subscribe(SESSION) as (_, second):
            for n in range(3):
                session_events.publish(SESSION, "token", {"n": n})
            return [first.get_nowait().data["n"] for _ in range(3)], [second.get_nowait().data["n"] for _ in range(3)]

    assert asyncio.run(scenario()) == ([0, 1, 2], [0, 1, 2])


def test_a_late_subscriber_gets_the_missed_events_once() -> None:
    first = session_events.publish(SESSION, "a", {})
    session_events.publish(SESSION, "b", {})

    async def scenario() -> tuple[list[str], int]:
        async with session_events.subscribe(SESSION, after_id=first.id) as (missed, queue):
            return [event.kind for event in missed], queue.qsize()

    assert asyncio.run(scenario()) == (["b"], 0)


def test_ids_keep_rising_across_a_clear() -> None:
    before = session_events.publish(SESSION, "a", {})
    session_events.clear_replay(SESSION)
    after = session_events.publish(SESSION, "b", {})
    assert after.id > before.id
    assert [event.kind for event in session_events.replay_after(SESSION, 0)] == ["b"]


def test_clear_keeps_the_kinds_asked_for() -> None:
    session_events.publish(SESSION, "token", {})
    session_events.publish(SESSION, "status", {})
    session_events.clear_replay(SESSION, keep=frozenset({"status"}))
    assert [event.kind for event in session_events.replay_after(SESSION, 0)] == ["status"]


def test_an_event_replaces_earlier_ones_of_the_given_kinds() -> None:
    session_events.publish(SESSION, "tts", {"url": "1"})
    session_events.publish(SESSION, "title", {})
    session_events.publish(SESSION, "bubble_audio", {}, replaces=frozenset({"tts"}))
    assert [event.kind for event in session_events.replay_after(SESSION, 0)] == ["title", "bubble_audio"]


def test_sessions_with_subscribers_lists_only_listened_sessions() -> None:
    async def scenario() -> tuple[bool, bool]:
        async with session_events.subscribe(SESSION):
            listening = SESSION in session_events.sessions_with_subscribers()
        return listening, SESSION in session_events.sessions_with_subscribers()

    assert asyncio.run(scenario()) == (True, False)


def test_the_browser_bus_keeps_the_wire_format_of_custom_js() -> None:
    browser_bus.browser_push(SESSION, "media", "http://x/a.mp3", state_key="k", start_pos_sec=3, is_stream=True)
    (event,) = session_events.replay_after(SESSION, 0)
    line_id, line_data, *_ = browser_bus._sse_item(event).split("\n")
    assert line_id == f"id: {event.id}"
    assert json.loads(line_data.removeprefix("data: ")) == {
        "kind": "media", "version": event.id, "url": "http://x/a.mp3", "playback_rate": "1.0x",
        "state_key": "k", "start_pos_sec": 3.0, "is_stream": True, "audio_type": "music",
    }


def test_the_combined_reply_audio_drops_the_buffered_chunks() -> None:
    browser_bus.browser_push(SESSION, "tts", "http://x/1.wav")
    browser_bus.browser_push(SESSION, "tts_pause", "500")
    browser_bus.browser_push(SESSION, "bubble_audio", "http://x/all.wav")
    assert [event.kind for event in session_events.replay_after(SESSION, 0)] == ["bubble_audio"]
