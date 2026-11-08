"""The spoken reply of a server turn (lib/reply_speech.py): the rules, one reading per session at a
time, what stays on the bubble, stop. The TTS reading itself is a stand-in (its own tests are in
test_browser_reading.py); session file, event hub and bubble lookup run for real."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fake_reading import FakeReading
from starlette.testclient import TestClient

from aifred.backends.base import LLMOptions
from aifred.lib import audio_processing, reply_speech, session_events, session_storage
from aifred.lib.formatting import build_assistant_chat_entry
from aifred.lib.speech_synthesis import SpokenReading
from aifred.lib.tts_client import TTSSuiteError
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


# ── A finished bubble read aloud again ───────────────────────────────────


def _bubble_with_audio(old_url: str) -> dict[str, Any]:
    entry = _bubble()
    session_storage.keep_reply_audio(SESSION, entry["timestamp"], [old_url], [], "Alt")
    return entry


def test_a_bubble_read_again_gets_the_new_audio_and_what_played_stops(
    session: list[list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _bubble_with_audio("/_upload/audio/alt.wav")
    monkeypatch.setattr(FakeReading, "ends_on_finish", True)

    async def scenario() -> None:
        await reply_speech.read_bubble(SESSION, entry, "Hallo Peuqui.", Settings(), "de")
        await reply_speech._readings[SESSION].settled()

    asyncio.run(scenario())
    assert FakeReading.made[0].fed == ["Hallo Peuqui."] and _kinds()[0] == "stop"
    saved = session_storage.load_session(SESSION)
    assert saved is not None
    assert saved["data"]["chat_history"][-1]["metadata"]["audio_urls"] == [f"/_upload/audio/{SESSION}/reply.wav"]


def test_a_bubble_read_again_keeps_its_audio_when_the_service_breaks_off(session: list[list[str]]) -> None:
    entry = _bubble_with_audio("/_upload/audio/alt.wav")

    async def scenario() -> None:
        await reply_speech.read_bubble(SESSION, entry, "Hallo Peuqui.", Settings(), "de")
        reading = _reading(reply_speech._readings[SESSION])
        reading.broke_off = True
        reading.over.set()
        await reply_speech._readings[SESSION].settled()

    asyncio.run(scenario())
    saved = session_storage.load_session(SESSION)
    assert saved is not None
    assert saved["data"]["chat_history"][-1]["metadata"]["audio_urls"] == ["/_upload/audio/alt.wav"]
    assert "message_updated" not in _kinds()


def _api(monkeypatch: pytest.MonkeyPatch, view: SimpleNamespace) -> TestClient:
    """The API as the session's owner, with ``view`` as the runtime settings."""
    from aifred.lib import runtime_settings
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    monkeypatch.setattr(runtime_settings, "load_runtime_settings", lambda: view)
    client = TestClient(api_app)
    client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    return client


def _speech_off() -> SimpleNamespace:
    return SimpleNamespace(enable_tts=False, tts_autoplay=True, tts_agents={}, ui_language="de")


def test_reading_a_bubble_again_needs_speech_on_and_a_reply_with_text(session: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _bubble()
    view = _speech_off()
    client = _api(monkeypatch, view)
    url = f"/sessions/{SESSION}/messages/speech"
    assert client.post(url, json={"timestamp": entry["timestamp"]}).status_code == 409
    view.enable_tts = True
    assert client.post(url, json={"timestamp": "gibt-es-nicht"}).status_code == 404
    read: list[tuple[str, str]] = []

    async def read_bubble(session_id: str, _entry: dict[str, Any], text: str, _settings: Any, _ui_language: str) -> None:
        read.append((session_id, text))

    monkeypatch.setattr(reply_speech, "read_bubble", read_bubble)
    # No body: the reading comes over the event stream (the client reads a body only from other codes).
    answer = client.post(url, json={"timestamp": entry["timestamp"]})
    assert answer.status_code == 204 and answer.content == b""
    assert read == [(SESSION, "Hallo Peuqui.")]


# ── Every reply read into new audio ──────────────────────────────────────


def _replies(*texts: str) -> list[dict[str, Any]]:
    """A question and one reply bubble per text (distinct timestamps), in the session file."""
    entries = []
    for number, text in enumerate(texts):
        entry = build_assistant_chat_entry(text, "aifred")
        entry["timestamp"] = f"2026-11-07T10:00:0{number}"
        entries.append(entry)
    with session_storage.session_rmw_lock:
        saved = session_storage.load_session(SESSION)
        assert saved is not None
        saved["data"]["chat_history"] = [{"role": "user", "content": "Frage", "timestamp": "2026-11-07T09:59:59"}, *entries]
        session_storage.save_session(SESSION, saved)
    return entries


def _speak_to_files(monkeypatch: pytest.MonkeyPatch, spoken: list[str], fails_on: str = "") -> None:
    """The TTS service as a stand-in: each text becomes one audio file, ``fails_on`` a service error."""
    from aifred.lib import speech_synthesis

    async def speak(text: str, *, language: str, agent: str) -> SpokenReading:
        spoken.append(text)
        if text == fails_on:
            raise TTSSuiteError("Dienst weg")
        return SpokenReading(audio_urls=[f"/_upload/tts_audio/{agent}-{len(spoken)}.wav"])

    monkeypatch.setattr(speech_synthesis, "speak_to_files", speak)


def _metadata(timestamp: str) -> dict[str, Any]:
    saved = session_storage.load_session(SESSION)
    assert saved is not None
    metadata: dict[str, Any] = next(m for m in saved["data"]["chat_history"] if m["timestamp"] == timestamp)["metadata"]
    return metadata


def test_every_reply_gets_new_audio_and_loses_what_the_browser_spoke(
    session: list[list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, short, last = _replies("Erste Antwort.", "Hi", "Letzte Antwort.")
    session_storage.keep_reply_audio(SESSION, first["timestamp"], [], [{"text": "Erste Antwort."}], "Browser · Gerät")
    spoken: list[str] = []
    _speak_to_files(monkeypatch, spoken)

    async def scenario() -> None:
        assert reply_speech.regenerate_all(SESSION, Settings(), "de")
        assert reply_speech.regenerating(SESSION)
        assert not reply_speech.regenerate_all(SESSION, Settings(), "de")    # one run per session
        await reply_speech._regenerations[SESSION]

    asyncio.run(scenario())
    assert spoken == ["Erste Antwort.", "Letzte Antwort."]                  # too little to read in "Hi"
    assert session == [["/_upload/tts_audio/aifred-1.wav"], ["/_upload/tts_audio/aifred-2.wav"]]
    for reply in (first, last):
        assert _metadata(reply["timestamp"])["audio_urls"] == [f"/_upload/audio/{SESSION}/reply.wav"]
        assert _metadata(reply["timestamp"])["browser_speech"] == []
    assert not _metadata(short["timestamp"]).get("audio_urls")
    assert not reply_speech.regenerating(SESSION)
    assert _kinds() == ["stop", "message_updated", "message_updated", "speech_regenerating"]
    last_state = [e.data for e in session_events.replay_after(SESSION, 0) if e.kind == "speech_regenerating"]
    assert last_state == [{"running": False}]                               # a late client sees only the end


def test_a_service_error_ends_the_run_and_what_was_made_stays(
    session: list[list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, failing, never = _replies("Eins klappt.", "Zwei bricht.", "Drei kommt nie dran.")
    spoken: list[str] = []
    _speak_to_files(monkeypatch, spoken, fails_on="Zwei bricht.")

    async def scenario() -> None:
        assert reply_speech.regenerate_all(SESSION, Settings(), "de")
        await reply_speech._regenerations[SESSION]

    asyncio.run(scenario())
    assert spoken == ["Eins klappt.", "Zwei bricht."]
    assert _metadata(first["timestamp"])["audio_urls"] == [f"/_upload/audio/{SESSION}/reply.wav"]
    for reply in (failing, never):
        assert not _metadata(reply["timestamp"]).get("audio_urls")
    notices = [e.data for e in session_events.replay_after(SESSION, 0) if e.kind == "notice"]
    assert len(notices) == 1 and notices[0]["level"] == "error" and "Dienst weg" in notices[0]["text"]
    assert not reply_speech.regenerating(SESSION)


def test_deleting_the_session_ends_the_run(session: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import speech_synthesis

    (reply,) = _replies("Eine lange Antwort.")
    started = asyncio.Event()

    async def speak(text: str, *, language: str, agent: str) -> SpokenReading:
        started.set()
        await asyncio.Event().wait()                                        # the service takes its time
        raise AssertionError("not reached")

    monkeypatch.setattr(speech_synthesis, "speak_to_files", speak)

    async def scenario() -> asyncio.Task[None]:
        assert reply_speech.regenerate_all(SESSION, Settings(), "de")
        task = reply_speech._regenerations[SESSION]
        await started.wait()
        reply_speech.discard(SESSION)
        await asyncio.wait({task})
        return task

    assert asyncio.run(scenario()).cancelled()
    assert not _metadata(reply["timestamp"]).get("audio_urls")
    assert not reply_speech.regenerating(SESSION)


def test_regenerating_needs_speech_on_and_shows_in_the_session(session: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    view = _speech_off()
    client = _api(monkeypatch, view)
    url = f"/sessions/{SESSION}/speech/regenerate"
    assert client.post(url).status_code == 409
    view.enable_tts = True
    started: list[str] = []
    running = {"now": False}

    def regenerate_all(session_id: str, _settings: Any, ui_language: str) -> bool:
        if running["now"]:
            return False
        started.append(ui_language)
        running["now"] = True
        return True

    monkeypatch.setattr(reply_speech, "regenerate_all", regenerate_all)
    monkeypatch.setattr(reply_speech, "regenerating", lambda _session_id: running["now"])
    answer = client.post(url)
    assert answer.status_code == 204 and answer.content == b""
    assert client.post(url).status_code == 409                              # it runs already
    assert started == ["de"]
    assert client.get(f"/sessions/{SESSION}").json()["speech_regenerating"] is True
