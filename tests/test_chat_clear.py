"""Clearing a chat (POST /api/sessions/{id}/clear, session_storage.clear_chat): until 08.11.2026 only the Reflex
interface could; the old POST /api/chat/clear emptied the histories alone and left the chat's files behind. The rule
for the files is the one of deleting a session: images, spoken audio and screenshots go, plots and pages stay. The
Reflex button also deleted the spoken audio of every other session (cleanup_old_tts_audio with age 0)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import audio_processing, config, pipeline_registry, reply_speech, sandbox, session_events, session_storage, vision_utils
from aifred.lib.api.app import api_app
from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

MINE = "a" * 32
THEIRS = "b" * 32


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", tmp_path / "sandbox_output")
    monkeypatch.setattr(vision_utils, "UPLOAD_IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(vision_utils, "VIGILANTIA_DIR", tmp_path / "vigilantia")
    monkeypatch.setattr(audio_processing, "SESSION_AUDIO_DIR", tmp_path / "audio")
    (tmp_path / "sessions").mkdir()
    session_storage._session_meta_cache.clear()
    files: dict[str, Path] = {}
    for session in (MINE, THEIRS):
        session_storage.create_empty_session(session, owner="ki" if session == MINE else "lord")
        session_storage.update_chat_data(session, [{"role": "user", "content": "Hallo"}],
                                         llm_history=[{"role": "user", "content": "Hallo"}], debug_messages=["x"])
        session_storage.update_session_title(session, "Ein Titel")
        for kind, path in (("image", tmp_path / "images" / session / "foto.jpg"),
                           ("audio", tmp_path / "audio" / session / "reply.wav"),
                           ("shot", tmp_path / "sandbox_output" / session / f"{sandbox.SCREENSHOT_PREFIX}1.png"),
                           ("plot", tmp_path / "sandbox_output" / session / "plot-1.png")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
            files[f"{kind}:{session}"] = path
    session_storage.update_session_config(MINE, active_agent="sokrates")
    session_events._channels.pop(MINE, None)
    yield files
    session_storage._session_meta_cache.clear()
    session_events._channels.pop(MINE, None)


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    return test_client


def test_the_chat_and_its_files_go_the_session_and_its_deliverables_stay(world: dict[str, Path], client: TestClient) -> None:
    assert client.post(f"/sessions/{MINE}/clear").status_code == 204
    session = session_storage.load_session(MINE)
    assert session is not None
    data = session["data"]
    assert (data["chat_history"], data["llm_history"], data["debug_messages"], data["title"]) == ([], [], [], "")
    assert session_storage.get_session_config(MINE)["active_agent"] == "sokrates"
    assert [world[f"{kind}:{MINE}"].exists() for kind in ("image", "audio", "shot", "plot")] == [False, False, False, True]
    assert all(world[f"{kind}:{THEIRS}"].exists() for kind in ("image", "audio", "shot", "plot"))   # nobody else's
    assert [event.kind for event in session_events.replay_after(MINE, 0)][-2:] == ["stop", "chat_cleared"]


def test_the_running_turn_and_the_reading_end_first(world: dict[str, Path], client: TestClient,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    ended: list[str] = []

    def cancel(session_id: str) -> bool:
        ended.append(f"turn {session_id}")
        return True

    monkeypatch.setattr(pipeline_registry, "cancel_pipeline", cancel)
    monkeypatch.setattr(reply_speech, "discard", lambda session_id: ended.append(f"reading {session_id}"))
    client.post(f"/sessions/{MINE}/clear")
    assert ended == [f"turn {MINE}", f"reading {MINE}"]


def test_only_the_users_own_session(world: dict[str, Path], client: TestClient) -> None:
    assert client.post(f"/sessions/{THEIRS}/clear").status_code == 404
    session = session_storage.load_session(THEIRS)
    assert session is not None and session["data"]["chat_history"]


def test_the_old_endpoint_clears_the_same_way(world: dict[str, Path], client: TestClient) -> None:
    assert client.post("/chat/clear", json={"session_id": MINE}).json()["success"] is True
    assert not world[f"image:{MINE}"].exists() and world[f"plot:{MINE}"].exists()
