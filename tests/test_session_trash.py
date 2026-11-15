"""The trash of the chats (lib/session_trash.py, /session-trash): a deleted chat goes in, comes back whole, leaves for
good by hand or after the retention, and only its owner sees it."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import session_events, session_storage, session_trash
from aifred.lib.api.app import api_app
from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

MINE = "a" * 32
OLD = "c" * 32
THEIRS = "b" * 32


@pytest.fixture
def removed_files(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The sessions whose chat files were removed (images, audio, screenshots)."""
    removed: list[str] = []
    monkeypatch.setattr(session_trash, "_remove_chat_files", removed.append)
    return removed


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, removed_files: list[str]) -> Iterator[TestClient]:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_trash, "TRASH_DIR", tmp_path / "trash")
    session_storage._session_meta_cache.clear()
    for session_id, owner in ((MINE, "ki"), (OLD, "ki"), (THEIRS, "lord")):
        session_storage.create_empty_session(session_id, owner=owner)
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    yield test_client
    session_storage._session_meta_cache.clear()
    for session_id in (MINE, OLD, THEIRS):
        session_events._channels.pop(session_id, None)


def ids(client: TestClient, path: str) -> list[str]:
    return sorted(entry["session_id"] for entry in client.get(path).json())


def test_deleting_a_session_moves_it_to_the_trash(client: TestClient, removed_files: list[str]) -> None:
    assert client.delete(f"/sessions/{MINE}").status_code == 204
    assert ids(client, "/sessions") == [OLD]
    assert ids(client, "/session-trash") == [MINE]
    assert "deleted_at" in session_trash.TRASH_DIR.joinpath(f"{MINE}.json").read_text()
    assert removed_files == []   # the files of the chat stay while it can come back


def test_a_session_comes_back_whole(client: TestClient) -> None:
    session = session_storage.load_session(MINE)
    assert session is not None
    session["data"]["chat_history"] = [{"role": "user", "content": "Hallo"}]
    session_storage.save_session(MINE, session)
    client.delete(f"/sessions/{MINE}")
    assert client.post(f"/session-trash/{MINE}/restore").status_code == 204
    assert ids(client, "/session-trash") == []
    restored = session_storage.load_session(MINE)
    assert restored is not None and "deleted_at" not in restored
    assert restored["data"]["chat_history"] == [{"role": "user", "content": "Hallo"}]


def test_deleting_for_good_takes_the_files_of_the_chat_along(client: TestClient, removed_files: list[str]) -> None:
    client.delete(f"/sessions/{MINE}")
    assert client.delete(f"/session-trash/{MINE}").status_code == 204
    assert ids(client, "/session-trash") == []
    assert removed_files == [MINE]


def test_emptying_the_trash_removes_every_chat_of_the_user_in_it(client: TestClient, removed_files: list[str]) -> None:
    client.post("/sessions/delete", json={"session_ids": [MINE, OLD]})
    session_trash.trash(THEIRS, "lord")
    assert client.delete("/session-trash").status_code == 204
    assert sorted(removed_files) == [MINE, OLD]
    assert session_trash.TRASH_DIR.joinpath(f"{THEIRS}.json").exists()   # the other user's stays


def test_the_trash_of_another_user_does_not_exist_for_the_user(client: TestClient) -> None:
    session_trash.trash(THEIRS, "lord")
    assert ids(client, "/session-trash") == []
    assert client.post(f"/session-trash/{THEIRS}/restore").status_code == 404
    assert client.delete(f"/session-trash/{THEIRS}").status_code == 404


def test_a_session_that_is_not_in_the_trash_is_not_found(client: TestClient) -> None:
    assert client.post(f"/session-trash/{MINE}/restore").status_code == 404
    assert client.delete(f"/session-trash/{MINE}").status_code == 404


def test_a_session_whose_id_is_taken_again_is_not_restored_over_it(client: TestClient) -> None:
    client.delete(f"/sessions/{MINE}")
    session_storage.create_empty_session(MINE, owner="ki")
    assert client.post(f"/session-trash/{MINE}/restore").status_code == 409


def test_the_cleanup_removes_only_what_lay_there_longer_than_the_retention(client: TestClient, removed_files: list[str]) -> None:
    client.post("/sessions/delete", json={"session_ids": [MINE, OLD]})
    now = datetime.now()
    # OLD went in eight days ago, MINE a day ago
    old_path = session_trash.TRASH_DIR / f"{OLD}.json"
    old_path.write_text(old_path.read_text().replace(
        session_trash.TRASH_DIR.joinpath(f"{OLD}.json").read_text().split('"deleted_at": "')[1].split('"')[0],
        (now - timedelta(days=8)).isoformat(timespec="seconds"),
    ))
    assert session_trash.purge_expired(now) == 1
    assert ids(client, "/session-trash") == [MINE]
    assert removed_files == [OLD]
