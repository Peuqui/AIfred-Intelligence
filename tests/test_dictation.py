"""Dictation on the server (lib/dictation.py): the upload is checked, the job runs on its own and
hands the text on as the browser's dictation did (straight to the AI, into the input field, a bubble,
a file for a long transcript), questions go to the clients and wait for the answer. The STT service
and the turn are stand-ins; session file and event hub run for real."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import (
    audio_processing,
    config,
    dictation,
    file_manager,
    runtime_settings,
    session_events,
    session_storage,
    settings,
    stt_client,
    turn_service,
)

SESSION = "d" * 32


@pytest.fixture
def stt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A session on disk, an STT service that says ``world["text"]``, a turn that only records."""
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    session_storage.save_session(SESSION, {"data": {"chat_history": [], "llm_history": []}}, owner="ki")
    session_events._channels.pop(SESSION, None)
    world: dict[str, Any] = {
        "text": "Wie wird das Wetter morgen?", "duration": 5.0, "gpu_busy": 0, "settings": {},
        "sent": [], "unloaded": [], "transcribed": [], "written": {},
    }

    class _Service:
        def is_ready(self) -> bool:
            return True

    def transcribe(path: str, language: str, diarize: bool) -> tuple[str, float, str]:
        world["transcribed"].append((language, diarize, Path(path).exists()))
        if world["gpu_busy"]:
            world["gpu_busy"] -= 1
            raise stt_client.STTGPUUnavailableError("503 no GPU with room")
        return world["text"], 1.2, "cuda"

    async def unload(backend: str) -> None:
        world["unloaded"].append(backend)

    def write_file(name: str, text: str) -> Any:
        world["written"][name] = text
        return SimpleNamespace(success=True, detail="")

    monkeypatch.setattr(stt_client, "get_default_client", lambda: _Service())
    monkeypatch.setattr(audio_processing, "get_audio_duration", lambda path: world["duration"])
    monkeypatch.setattr(audio_processing, "transcribe_audio_auto", transcribe)
    monkeypatch.setattr(dictation, "unload_chat_models", unload)
    monkeypatch.setattr(file_manager, "write_file", write_file)
    monkeypatch.setattr(settings, "persisted_settings", lambda: world["settings"])
    monkeypatch.setattr(runtime_settings, "load_runtime_settings", lambda: SimpleNamespace(backend_type="llamacpp"))
    def submit(request: turn_service.TurnRequest) -> bool:
        world["sent"].append(request)
        return False

    monkeypatch.setattr(turn_service, "submit_turn", submit)
    yield world
    session_storage._session_meta_cache.clear()
    session_events._channels.pop(SESSION, None)


def _events(kind: str) -> list[dict[str, Any]]:
    return [event.data for event in session_events.replay_after(SESSION, 0) if event.kind == kind]


def _chat() -> list[dict[str, Any]]:
    session = session_storage.load_session(SESSION)
    assert session is not None
    history: list[dict[str, Any]] = session["data"]["chat_history"]
    return history


def _dictate(source: str = "mic", answers: tuple[bool, ...] = (), name: str = "aufnahme.webm") -> dictation.DictationJob:
    async def scenario() -> dictation.DictationJob:
        job = dictation.accept(uuid.uuid4().hex, SESSION, name, b"audio", source, "de")
        assert job.task is not None
        for reply in answers:
            while job.answer is None:
                await asyncio.sleep(0)
            assert dictation.answer(SESSION, job.id, reply)
        await job.task
        return job

    return asyncio.run(scenario())


def test_a_dictation_goes_straight_to_the_ai(stt: dict[str, Any]) -> None:
    job = _dictate()
    assert [(request.session_id, request.text) for request in stt["sent"]] == [(SESSION, "Wie wird das Wetter morgen?")]
    assert stt["transcribed"] == [("de", False, True)]        # the interface's language, one speaker
    assert not job.path.exists()                                # the upload is gone
    assert _events("dictation_finished") == [{"job_id": job.id}]


def test_with_show_transcription_the_text_goes_into_the_input_field(stt: dict[str, Any]) -> None:
    stt["settings"] = {"show_transcription": True}
    _dictate()
    assert stt["sent"] == [] and _events("dictation_text")[0]["text"] == "Wie wird das Wetter morgen?"


def test_a_mail_address_is_shown_for_checking_instead_of_sent(stt: dict[str, Any]) -> None:
    stt["text"] = "Schreib an peter at beispiel punkt de"
    _dictate()
    assert stt["sent"] == [] and _events("dictation_text")


def test_an_audio_file_gets_its_transcript_as_a_bubble_the_agent_knows(stt: dict[str, Any]) -> None:
    _dictate("file", name="interview.mp3")
    assert stt["transcribed"] == [("auto", True, True)]       # own language, speaker labels
    started, transcript = _chat()
    assert "interview.mp3" in started["content"] and "Wie wird das Wetter morgen?" in transcript["content"]
    session = session_storage.load_session(SESSION)
    assert session is not None
    assert [entry["content"] for entry in session["data"]["llm_history"]] == [f"[AIFRED]: {transcript['content']}"]


def test_a_long_transcript_becomes_a_document_with_a_link(stt: dict[str, Any]) -> None:
    stt["text"] = "Wort " * (config.TRANSCRIPT_TO_WORKSPACE_THRESHOLD_CHARS // 4)
    _dictate()
    [name] = stt["written"]
    assert name.startswith("transcript-aufnahme-") and stt["sent"] == []
    assert f"/_upload/documents/{name}" in _chat()[-1]["content"]
    assert _events("notice")[-1]["level"] == "success"


def test_a_long_transcription_waits_for_the_users_yes(stt: dict[str, Any]) -> None:
    stt["duration"] = (config.WHISPER_CONFIRM_THRESHOLD_S + 60) / config.WHISPER_RTF_GPU
    _dictate(answers=(False,))
    assert stt["transcribed"] == [] and _events("dictation_question")[0]["kind"] == "long"
    _dictate(answers=(True,))
    assert len(stt["transcribed"]) == 1 and stt["sent"]


def test_a_transcription_above_the_time_limit_does_not_start(stt: dict[str, Any]) -> None:
    stt["duration"] = (config.WHISPER_TRANSCRIBE_TIMEOUT_S + 60) / config.WHISPER_RTF_GPU
    _dictate()
    assert stt["transcribed"] == [] and _events("notice")[0]["level"] == "error"


def test_no_room_on_the_gpu_asks_to_unload_the_llm_and_tries_once_more(stt: dict[str, Any]) -> None:
    stt["gpu_busy"] = 2                                          # also the second attempt finds no room
    _dictate(answers=(True,))
    assert stt["unloaded"] == ["llamacpp"] and len(stt["transcribed"]) == 2
    assert [q["kind"] for q in _events("dictation_question")] == ["unload"]   # asked once
    assert _events("notice")[-1]["level"] == "error" and stt["sent"] == []


def test_an_unknown_format_is_refused_before_anything_is_stored(stt: dict[str, Any]) -> None:
    with pytest.raises(dictation.DictationRejected):
        dictation.accept(uuid.uuid4().hex, SESSION, "notiz.txt", b"x", "mic", "de")
    with pytest.raises(dictation.DictationRejected):
        dictation.accept("kein-uuid", SESSION, "aufnahme.webm", b"x", "mic", "de")


def test_an_answer_needs_an_open_question_of_the_own_session(stt: dict[str, Any]) -> None:
    assert not dictation.answer(SESSION, "gibt-es-nicht", True)


# ── The endpoints ────────────────────────────────────────────────────────


@pytest.fixture
def client(stt: dict[str, Any]) -> TestClient:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    session_storage.create_empty_session("e" * 32, owner="lord")
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    return test_client


def test_the_endpoints_take_uploads_for_the_own_sessions_only(client: TestClient, stt: dict[str, Any]) -> None:
    audio = {"file": ("aufnahme.webm", b"audio", "audio/webm")}
    job_id = uuid.uuid4().hex
    response = client.post(f"/sessions/{SESSION}/dictation", files=audio, data={"source": "mic", "job_id": job_id})
    assert response.status_code == 202 and response.json()["job_id"] == job_id
    other = {"job_id": uuid.uuid4().hex}
    assert client.post(f"/sessions/{'e' * 32}/dictation", files=audio, data=other).status_code == 404
    assert client.post(f"/sessions/{SESSION}/dictation", files={"file": ("x.txt", b"x", "text/plain")}, data=other).status_code == 422
    assert client.post(f"/sessions/{SESSION}/dictation/nix/answer", json={"yes": True}).status_code == 404
