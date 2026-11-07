"""The spoken reply of a server turn (lib/reply_speech.py): the rules, one reading per session at a
time, what stays on the bubble, stop. The TTS reading itself is a stand-in (its own tests are in
test_browser_reading.py); session file, event hub and bubble lookup run for real."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fake_reading import FakeReading

from aifred.backends.base import LLMOptions
from aifred.lib import audio_processing, reply_speech, session_events, session_storage
from aifred.lib.formatting import build_assistant_chat_entry
from aifred.lib.turn_service import AgentCall

SESSION = "e" * 32

@dataclass
class Settings:
    enable_tts: bool = True
    tts_autoplay: bool = True
    tts_agents: dict[str, dict[str, Any]] = field(default_factory=dict)


def _call(agent: str = "aifred") -> AgentCall:
    return AgentCall(agent, agent.capitalize(), "🎩", "m", [], LLMOptions(), None, 0, 0, agent)


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[list[str]]]:
    """A session on disk, the reading replaced; yields the audio lists given to save_audio_to_session."""
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    session_storage.save_session(SESSION, {"data": {"chat_history": [], "llm_history": []}}, owner="ki")
    session_events._channels.pop(SESSION, None)
    reply_speech._readings.pop(SESSION, None)
    FakeReading.made.clear()
    monkeypatch.setattr(reply_speech, "BrowserReading", FakeReading)
    combined: list[list[str]] = []

    def save_audio(urls: list[str], session_id: str) -> str:
        combined.append(urls)
        return f"/_upload/audio/{session_id}/reply.wav"

    monkeypatch.setattr(audio_processing, "save_audio_to_session", save_audio)
    yield combined
    session_storage._session_meta_cache.clear()
    session_events._channels.pop(SESSION, None)
    reply_speech._readings.pop(SESSION, None)


def _bubble(agent: str = "aifred") -> dict[str, Any]:
    """A reply bubble in the session file, as finish_call leaves it."""
    entry = build_assistant_chat_entry("Hallo Peuqui.", agent)
    with session_storage.session_rmw_lock:
        session = session_storage.load_session(SESSION)
        assert session is not None
        session["data"]["chat_history"].append(entry)
        session_storage.save_session(SESSION, session)
    return entry


def _kinds() -> list[str]:
    return [event.kind for event in session_events.replay_after(SESSION, 0) if event.kind != "debug"]


async def _begin(call: AgentCall, detected: str = "de") -> reply_speech.ReplySpeech:
    """The reading of a reply that is read aloud."""
    speech = await reply_speech.begin(SESSION, call, Settings(), detected, "de")
    assert speech is not None
    return speech


def _reading(speech: reply_speech.ReplySpeech) -> FakeReading:
    reading = speech.reading
    assert isinstance(reading, FakeReading)
    return reading


# ── The rules ────────────────────────────────────────────────────────────


def test_a_reply_is_read_only_with_speech_and_autoplay_on_and_the_agents_voice_not_off() -> None:
    assert reply_speech.speech_wanted(Settings(), "aifred")
    assert not reply_speech.speech_wanted(Settings(enable_tts=False), "aifred")
    assert not reply_speech.speech_wanted(Settings(tts_autoplay=False), "aifred")
    off = Settings(tts_agents={"sokrates": {"enabled": False}})
    assert not reply_speech.speech_wanted(off, "sokrates")
    assert reply_speech.speech_wanted(off, "salomo")       # an agent without a switch speaks


def test_the_agents_own_language_comes_first_then_the_question_then_the_interface() -> None:
    agents = {"rabbi": {"language": "HE"}, "hal": {"language": "auto"}, "pater": {"language": ""}}
    assert reply_speech.speech_language(agents, "rabbi", "de", "de") == "he"
    assert reply_speech.speech_language(agents, "hal", "en", "de") == "en"
    assert reply_speech.speech_language(agents, "pater", "", "de") == "de"


# ── A reading and its bubble ─────────────────────────────────────────────


def test_no_reading_when_the_reply_is_not_read_aloud(session: list[list[str]]) -> None:
    async def scenario() -> None:
        assert await reply_speech.begin(SESSION, _call(), Settings(tts_autoplay=False), "de", "de") is None

    asyncio.run(scenario())
    assert FakeReading.made == [] and _kinds() == []


def test_what_was_spoken_goes_onto_the_bubble_and_the_segments_leave_the_replay(session: list[list[str]]) -> None:
    async def scenario() -> dict[str, Any]:
        call = _call()
        speech = await _begin(call, "en")
        reading = _reading(speech)
        assert reading.language == "en"
        speech.feed("Hallo ")
        speech.end(complete=True)
        entry = _bubble()
        reply_speech.keep_on(SESSION, call, entry)
        assert "tts" in _kinds()                        # the segment is on the playlist
        reading.over.set()
        await speech.settled()
        return entry

    entry = asyncio.run(scenario())
    assert FakeReading.made[0].fed == ["Hallo "]
    assert session == [["/_upload/tts_audio/aifred-1.wav"]]
    saved = session_storage.load_session(SESSION)
    assert saved is not None
    bubble = saved["data"]["chat_history"][-1]
    assert bubble["has_audio"] and bubble["tts_note"] == "Edge · Cloud"
    assert bubble["metadata"]["audio_urls"] == [f"/_upload/audio/{SESSION}/reply.wav"]
    updated = [e for e in session_events.replay_after(SESSION, 0) if e.kind == "message_updated"]
    assert updated[0].data["entry"]["timestamp"] == entry["timestamp"]
    assert "tts" not in _kinds()                        # superseded by the combined audio


def test_the_bubble_keeps_the_parts_the_browser_spoke(session: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeReading, "browser_voice", True)

    async def scenario() -> None:
        call = _call()
        speech = await _begin(call)
        reading = _reading(speech)
        speech.end(complete=True)
        reply_speech.keep_on(SESSION, call, _bubble())
        reading.over.set()
        await speech.settled()

    asyncio.run(scenario())
    saved = session_storage.load_session(SESSION)
    assert saved is not None
    bubble = saved["data"]["chat_history"][-1]
    assert bubble["metadata"]["browser_speech"][0]["text"] == "Hallo." and bubble["metadata"]["audio_urls"] == []
    assert session == []                                # no audio to combine


def test_a_reply_without_a_result_is_not_read_to_its_end_and_keeps_nothing(session: list[list[str]]) -> None:
    async def scenario() -> None:
        speech = await _begin(_call())
        speech.end(complete=False)
        await speech.settled()

    asyncio.run(scenario())
    assert FakeReading.made[0].aborted and not FakeReading.made[0].finished
    assert "message_updated" not in _kinds()


def test_another_calls_bubble_does_not_take_the_reading(session: list[list[str]]) -> None:
    async def scenario() -> None:
        speech = await _begin(_call())
        reading = _reading(speech)
        speech.end(complete=True)
        reply_speech.keep_on(SESSION, _call("sokrates"), _bubble("sokrates"))   # a call that was not read
        reading.over.set()
        await speech.settled()

    asyncio.run(scenario())
    assert "message_updated" not in _kinds()


# ── One reading at a time, stop ──────────────────────────────────────────


def test_the_next_reading_waits_until_the_previous_one_is_kept(session: list[list[str]]) -> None:
    async def scenario() -> None:
        first_call = _call()
        first = await _begin(first_call)
        first_reading = _reading(first)
        first.end(complete=True)
        reply_speech.keep_on(SESSION, first_call, _bubble())
        second = asyncio.create_task(reply_speech.begin(SESSION, _call("sokrates"), Settings(), "de", "de"))
        await asyncio.sleep(0.01)
        assert not second.done() and len(FakeReading.made) == 1
        first_reading.over.set()
        speech = await asyncio.wait_for(second, timeout=5)
        assert speech is not None
        assert "message_updated" in _kinds()            # the first bubble had its audio before
        speech.end(complete=False)

    asyncio.run(scenario())
    assert [reading.agent for reading in FakeReading.made] == ["aifred", "sokrates"]


def test_stop_ends_the_reading_and_tells_the_clients(session: list[list[str]]) -> None:
    async def scenario() -> None:
        speech = await _begin(_call())
        speech.end(complete=True)
        reply_speech.stop(SESSION)
        await speech.settled()

    asyncio.run(scenario())
    assert FakeReading.made[0].aborted
    assert _kinds()[-1] == "stop" and "tts" not in _kinds()
