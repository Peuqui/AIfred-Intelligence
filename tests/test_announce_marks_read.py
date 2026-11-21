"""What an announcement the puck played to its end does to its bubble: the session's read mark moves there
(``read_until``). Heard to the end = read; aborted or unconfirmed = not."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from aifred.lib import audio_channels
from aifred.plugins.channels.freeecho2_channel import alert_queue
from aifred.lib import message_processor, plugin_registry, session_events, session_storage, session_trash
from aifred.lib.envelope import OutboundMessage, ReplyPart
from aifred.lib.read_mark import BubbleRef, mark_read
from aifred.plugins.channels.freeecho2_channel.tts_reply import _announced_bubble

SESSION = "c" * 32


class _Bridge:
    async def send_done(self, room: str, reason: Any = None) -> bool:
        return True


class _Orc:
    def __init__(self) -> None:
        self.completed = True
        self.bridge = _Bridge()

    async def pause_for_announcement(self, timeout_sec: float) -> None:
        return None

    async def play_notification(self, buffer: Any, *, start_tone: bool, end_tone: bool) -> bool:
        return self.completed


class _Channel:
    def __init__(self, orc: _Orc) -> None:
        self._orc = orc

    def get_orchestrator(self, room: str) -> _Orc:
        return self._orc


@pytest.fixture
def marks(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """The queue's worker with a puck that can be steered; every read mark it sets, in order."""
    set_marks: list[tuple[str, str]] = []

    def record(session_id: str, timestamp: str) -> bool:
        set_marks.append((session_id, timestamp))
        return True

    monkeypatch.setattr("aifred.lib.read_mark.mark_read", record)
    monkeypatch.setattr(alert_queue, "_ws_loop", None)
    for state in (alert_queue._alert_queues, alert_queue._alert_workers, alert_queue._playback_done):
        state.clear()
    return set_marks


def _play(monkeypatch: pytest.MonkeyPatch, orc: _Orc, steps: Any) -> None:
    monkeypatch.setattr(audio_channels, "resolve", lambda key: _Channel(orc))
    asyncio.run(steps())


async def _stop(room: str) -> None:
    worker = alert_queue._alert_workers[room]
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass


def test_heard_to_the_end_marks_the_bubble(marks: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    async def steps() -> None:
        await alert_queue.enqueue_alert("buero", "notification", b"pcm", start_tone=True, end_tone=True, bubble=BubbleRef("s1", "t1"))
        await asyncio.sleep(0.05)
        assert marks == []                      # played, but the puck has not confirmed yet
        alert_queue.signal_playback_done("buero")
        await asyncio.sleep(0.05)
        assert marks == [("s1", "t1")]
        await _stop("buero")

    _play(monkeypatch, _Orc(), steps)


def test_an_aborted_announcement_marks_nothing(marks: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    orc = _Orc()
    orc.completed = False

    async def steps() -> None:
        await alert_queue.enqueue_alert("buero", "notification", b"pcm", start_tone=True, end_tone=True, bubble=BubbleRef("s1", "t1"))
        await asyncio.sleep(0.05)
        alert_queue.signal_playback_done("buero")      # a late _done of someone else must not count either
        await asyncio.sleep(0.05)
        assert marks == []
        await _stop("buero")

    _play(monkeypatch, orc, steps)


def test_no_done_within_the_time_limit_marks_nothing(marks: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alert_queue, "_PLAYBACK_DONE_MARGIN_SEC", 0.05)

    async def steps() -> None:
        await alert_queue.enqueue_alert("buero", "notification", b"", start_tone=True, end_tone=True, bubble=BubbleRef("s1", "t1"))
        await asyncio.sleep(0.3)                        # the limit has passed without a _done
        assert alert_queue._alert_queues["buero"].empty() and marks == []
        await _stop("buero")

    _play(monkeypatch, _Orc(), steps)


def test_an_announcement_without_a_bubble_marks_nothing(marks: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    async def steps() -> None:
        await alert_queue.enqueue_alert("buero", "notification", None, start_tone=True, end_tone=True)
        await asyncio.sleep(0.05)
        alert_queue.signal_playback_done("buero")
        await asyncio.sleep(0.05)
        assert marks == []
        await _stop("buero")

    _play(monkeypatch, _Orc(), steps)


def test_two_announcements_in_a_row_mark_each_its_own_bubble(marks: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    async def steps() -> None:
        await alert_queue.enqueue_alert("buero", "notification", b"p1", start_tone=True, end_tone=True, bubble=BubbleRef("s1", "t1"))
        await alert_queue.enqueue_alert("buero", "notification", b"p2", start_tone=True, end_tone=True, bubble=BubbleRef("s1", "t2"))
        await asyncio.sleep(0.05)
        alert_queue.signal_playback_done("buero")
        await asyncio.sleep(0.05)
        assert marks == [("s1", "t1")]                  # the second plays, not yet confirmed
        alert_queue.signal_playback_done("buero")
        await asyncio.sleep(0.05)
        assert marks == [("s1", "t1"), ("s1", "t2")]
        await _stop("buero")

    _play(monkeypatch, _Orc(), steps)


def test_a_failing_mark_does_not_stop_the_queue(marks: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(session_id: str, timestamp: str) -> bool:
        raise OSError("disk")

    monkeypatch.setattr("aifred.lib.read_mark.mark_read", broken)

    async def steps() -> None:
        await alert_queue.enqueue_alert("buero", "notification", b"p1", start_tone=True, end_tone=True, bubble=BubbleRef("s1", "t1"))
        await alert_queue.enqueue_alert("buero", "notification", b"p2", start_tone=True, end_tone=True, bubble=BubbleRef("s1", "t2"))
        await asyncio.sleep(0.05)
        alert_queue.signal_playback_done("buero")
        await asyncio.sleep(0.05)
        assert alert_queue._alert_queues["buero"].qsize() == 0      # the second one is playing
        await _stop("buero")

    _play(monkeypatch, _Orc(), steps)


class TestTheBubbleOfAnOutboundMessage:
    @staticmethod
    def _outbound(bubble: str | None, session_id: str | None) -> OutboundMessage:
        return OutboundMessage(
            channel="freeecho2", channel_id="buero", recipient="buero", text="Hallo", media=None,
            metadata={"session_id": session_id}, parts=[ReplyPart("aifred", "Hallo", bubble)],
        )

    def test_session_and_bubble_make_the_reference(self) -> None:
        assert _announced_bubble(self._outbound("t1", "s1")) == BubbleRef("s1", "t1")

    def test_without_a_bubble_or_a_session_there_is_none(self) -> None:
        assert _announced_bubble(self._outbound(None, "s1")) is None
        assert _announced_bubble(self._outbound("t1", None)) is None


class _RecordingChannel:
    """A channel plugin that takes the announcement: what ``announce_to_channel`` hands it."""

    emoji = "🔊"

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    async def send_reply(self, outbound: OutboundMessage, original: Any) -> None:
        self.sent.append(outbound)


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _RecordingChannel:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_trash, "TRASH_DIR", tmp_path / "trash")
    session_storage._session_meta_cache.clear()
    channel = _RecordingChannel()
    monkeypatch.setattr(plugin_registry, "get_channel", lambda name: channel)
    monkeypatch.setattr(plugin_registry, "require_channel_on", lambda name: None)
    monkeypatch.setattr(message_processor, "_resolve_channel_recipient", lambda channel, recipient: recipient)
    monkeypatch.setattr(message_processor, "channel_display_label", lambda channel: "Echo")
    monkeypatch.setattr(message_processor, "push_hub_notification", lambda *args, **kwargs: None)
    return channel


def _bubbles(session_id: str) -> list[str]:
    session = session_storage.load_session(session_id)
    assert session is not None
    return [entry["timestamp"] for entry in session["data"]["chat_history"]]


def test_each_announcement_carries_its_own_bubble(storage: _RecordingChannel) -> None:
    asyncio.run(message_processor.announce_into_rooms("freeecho2", ["buero"], "Eins", "Codine", {}))
    asyncio.run(message_processor.announce_into_rooms("freeecho2", ["buero"], "Zwei", "Codine", {}))
    session_id = storage.sent[0].metadata["session_id"]
    first, second = (outbound.parts[0].bubble for outbound in storage.sent)
    assert [first, second] == _bubbles(session_id) and first != second
    assert [outbound.parts[0].text for outbound in storage.sent] == ["Eins", "Zwei"]


def test_an_announcement_into_several_rooms_marks_each_room_session_for_itself(
    storage: _RecordingChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reached = asyncio.run(message_processor.announce_into_rooms("freeecho2", ["buero", "kueche"], "Hallo", "Codine", {}))
    assert reached == ["buero", "kueche"]
    refs = [BubbleRef(outbound.metadata["session_id"], str(outbound.parts[0].bubble)) for outbound in storage.sent]
    assert refs[0].session_id != refs[1].session_id
    for ref in refs:
        assert _bubbles(ref.session_id) == [ref.timestamp]
    monkeypatch.setattr(alert_queue, "_ws_loop", None)
    for ref in refs:
        asyncio.run(alert_queue.mark_heard(ref, "any"))
    for ref in refs:
        stored = session_storage.load_session(ref.session_id)
        assert stored is not None and stored["read_until"] == ref.timestamp
        session_events._channels.pop(ref.session_id, None)


def test_mark_read_tells_the_open_tabs_only_when_the_mark_moved(storage: _RecordingChannel) -> None:
    asyncio.run(message_processor.announce_into_rooms("freeecho2", ["buero"], "Hallo", "Codine", {}))
    session_id = storage.sent[0].metadata["session_id"]
    timestamp = _bubbles(session_id)[0]
    assert mark_read(session_id, timestamp) is True
    assert mark_read(session_id, timestamp) is False
    events = [event for event in session_events._channels[session_id].replay if event.kind == "read_until"]
    assert [event.data for event in events] == [{"timestamp": timestamp}]
    session_events._channels.pop(session_id, None)
