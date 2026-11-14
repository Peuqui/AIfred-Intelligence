"""Den Player abschalten beendet am Puck nur seine Wiedergabe, nie die Sprachausgabe des Raums."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aifred.lib import audio_channels
from aifred.lib.audio_channels import freeecho2
from aifred.lib.audio_channels._audio_orchestrator import AudioOrchestrator, TTSBuffer
from aifred.lib.audio_channels.freeecho2 import FreeEcho2Channel

if TYPE_CHECKING:
    from aifred.lib.audio_channels._freeecho2_stream import FreeEcho2Stream as _StreamBase
else:
    _StreamBase = object

ROOM = "buero"
TARGET = f"freeecho2:{ROOM}"


class _FakeStream(_StreamBase):
    def __init__(self, running: bool = True) -> None:
        self._running = running
        self.stops = 0

    @property
    def is_running(self) -> bool:
        return self._running

    async def stop(self) -> bool:
        self.stops += 1
        self._running = False
        return True


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> tuple[FreeEcho2Channel, MagicMock, AudioOrchestrator]:
    channel = FreeEcho2Channel()
    bridge = MagicMock()
    for name in ("send_audio_flag", "send_audio_start", "send_audio_chunk", "send_audio_end", "send_done"):
        setattr(bridge, name, AsyncMock(return_value=True))
    orchestrator = AudioOrchestrator(ROOM, bridge)
    channel._orchestrators[ROOM] = orchestrator
    monkeypatch.setattr(freeecho2, "_connected_devices", lambda: {ROOM: object()})
    monkeypatch.setattr(audio_channels, "_REGISTRY", [channel])
    return channel, bridge, orchestrator


async def _speech_running(orchestrator: AudioOrchestrator) -> asyncio.Task[None]:
    """Speech that has not ended: a growing buffer nobody closes."""
    speech = asyncio.create_task(orchestrator.play_tts(TTSBuffer(b"", growing=True)))
    await asyncio.sleep(0.05)
    assert orchestrator.active_type == "tts"
    return speech


async def _end_speech(speech: asyncio.Task[Any], orchestrator: AudioOrchestrator) -> None:
    await orchestrator.stop()
    await asyncio.gather(speech, return_exceptions=True)


def test_switching_off_stops_the_players_music(setup: tuple[FreeEcho2Channel, MagicMock, AudioOrchestrator]) -> None:
    channel, bridge, orchestrator = setup
    stream = _FakeStream()
    channel._streams[ROOM] = stream

    async def scenario() -> None:
        await orchestrator.play_music(stream)
        await audio_channels.stop_everything()

    asyncio.run(scenario())
    assert stream.stops == 1 and orchestrator.is_idle and ROOM not in channel._streams
    bridge.send_done.assert_awaited_once_with(ROOM)


def test_switching_off_stops_a_folder_queue_the_orchestrator_does_not_know(
    setup: tuple[FreeEcho2Channel, MagicMock, AudioOrchestrator],
) -> None:
    channel, _bridge, _orchestrator = setup
    stream = _FakeStream()
    channel._streams[ROOM] = stream
    channel._queues[ROOM] = {"items": [], "idx": 0}
    asyncio.run(audio_channels.stop_everything())
    assert stream.stops == 1 and ROOM not in channel._queues and ROOM not in channel._streams


def test_switching_off_leaves_speech_without_the_player_alone(
    setup: tuple[FreeEcho2Channel, MagicMock, AudioOrchestrator],
) -> None:
    channel, bridge, orchestrator = setup

    async def scenario() -> None:
        speech = await _speech_running(orchestrator)
        bridge.send_audio_end.reset_mock()
        await audio_channels.stop_everything()
        assert orchestrator.active_type == "tts" and not speech.done()
        bridge.send_audio_end.assert_not_awaited()
        bridge.send_done.assert_not_awaited()
        await _end_speech(speech, orchestrator)

    assert channel.running_targets() == []
    asyncio.run(scenario())


def test_switching_off_stops_only_the_queue_beside_running_speech(
    setup: tuple[FreeEcho2Channel, MagicMock, AudioOrchestrator],
) -> None:
    channel, bridge, orchestrator = setup
    stream = _FakeStream()
    channel._streams[ROOM] = stream

    async def scenario() -> None:
        speech = await _speech_running(orchestrator)
        await audio_channels.stop_everything()
        assert stream.stops == 1 and orchestrator.active_type == "tts" and not speech.done()
        bridge.send_done.assert_not_awaited()
        await _end_speech(speech, orchestrator)

    asyncio.run(scenario())


def test_a_room_without_playback_is_not_touched(setup: tuple[FreeEcho2Channel, MagicMock, AudioOrchestrator]) -> None:
    channel, bridge, _orchestrator = setup
    finished = _FakeStream(running=False)
    channel._streams[ROOM] = finished
    assert channel.running_targets() == []
    asyncio.run(audio_channels.stop_everything())
    assert finished.stops == 0 and ROOM in channel._streams
    bridge.send_audio_end.assert_not_awaited()
    bridge.send_done.assert_not_awaited()


def test_stop_ends_a_folder_queue_the_orchestrator_does_not_know(
    setup: tuple[FreeEcho2Channel, MagicMock, AudioOrchestrator],
) -> None:
    """Wake word and puck `_stop` end up in `stop`: a running folder queue must end there too."""
    channel, _bridge, orchestrator = setup
    stream = _FakeStream()
    channel._streams[ROOM] = stream
    channel._queues[ROOM] = {"items": [{"uri": "a", "state_key": "a"}], "idx": 0}
    assert orchestrator.active_type is None
    assert audio_channels.resolve(TARGET) is channel

    assert asyncio.run(channel.stop(TARGET)) is True
    assert stream.stops == 1 and ROOM not in channel._queues and ROOM not in channel._streams
