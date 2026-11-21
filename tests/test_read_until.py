"""The read mark of a session (lib/session_storage.py ``mark_read_until``, POST /api/sessions/{id}/read)."""

import asyncio
import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import session_events, session_storage, session_trash
from aifred.lib.api.app import api_app
from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

MINE = "a" * 32
THEIRS = "b" * 32
T1, T2, T3, T4 = (f"2026-11-21T10:00:0{n}.123456" for n in range(1, 5))


def _history() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "frage", "timestamp": T1},
        {"role": "assistant", "content": "a1", "timestamp": T2},
        {"role": "user", "content": "frage2", "timestamp": T3},
        {"role": "assistant", "content": "a2", "timestamp": T4},
    ]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_trash, "TRASH_DIR", tmp_path / "trash")
    session_storage._session_meta_cache.clear()
    session_storage.create_empty_session(MINE, owner="ki")
    session_storage.create_empty_session(THEIRS, owner="lord")
    session_storage.update_chat_data(MINE, _history())
    session_storage.update_chat_data(THEIRS, _history())
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    yield test_client
    session_storage._session_meta_cache.clear()
    for session in (MINE, THEIRS):
        session_events._channels.pop(session, None)


def _stored(session_id: str = MINE) -> dict[str, Any]:
    session = session_storage.load_session(session_id)
    assert session is not None
    return session


def test_a_new_session_has_nothing_read(client: TestClient) -> None:
    created = client.post("/sessions").json()["session_id"]
    assert _stored(created)["read_until"] == ""


def test_marking_sets_exactly_the_bubble_timestamp(client: TestClient) -> None:
    assert client.post(f"/sessions/{MINE}/read", json={"timestamp": T2}).status_code == 204
    assert _stored()["read_until"] == T2
    assert client.get(f"/sessions/{MINE}").json()["read_until"] == T2


def test_an_older_timestamp_changes_nothing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client.post(f"/sessions/{MINE}/read", json={"timestamp": T4})
    published: list[str] = []
    writes: list[str] = []

    def record_publish(session_id: str, kind: str, data: dict[str, Any], **_: Any) -> None:
        published.append(kind)

    def record_write(path: Path, session: dict[str, Any]) -> bool:
        writes.append(path.name)
        return True

    monkeypatch.setattr(session_events, "publish", record_publish)
    monkeypatch.setattr(session_storage, "_write_session_file", record_write)
    for older in (T2, T4):   # older and equal
        assert client.post(f"/sessions/{MINE}/read", json={"timestamp": older}).status_code == 204
    assert (_stored()["read_until"], published, writes) == (T4, [], [])


@pytest.mark.parametrize("timestamp", ["", "2026-11-21T10:00:09.000000", "unsinn"])
def test_a_timestamp_that_is_no_bubble_is_rejected(client: TestClient, timestamp: str) -> None:
    response = client.post(f"/sessions/{MINE}/read", json={"timestamp": timestamp})
    assert response.status_code == 422
    assert _stored()["read_until"] == ""


def test_a_session_of_someone_else_does_not_exist(client: TestClient) -> None:
    assert client.post(f"/sessions/{THEIRS}/read", json={"timestamp": T2}).status_code == 404
    assert _stored(THEIRS)["read_until"] == ""


def test_unread_counts_only_replies_after_the_mark(client: TestClient) -> None:
    def unread() -> int:
        return int(next(entry["unread"] for entry in client.get("/sessions").json() if entry["session_id"] == MINE))

    assert unread() == 2
    client.post(f"/sessions/{MINE}/read", json={"timestamp": T3})   # a user bubble: only the later reply is unread
    assert unread() == 1
    client.post(f"/sessions/{MINE}/read", json={"timestamp": T4})
    assert unread() == 0
    assert client.get("/sessions").json()[0]["read_until"] == T4


def test_reading_keeps_the_order_stamps(client: TestClient) -> None:
    before = _stored()
    client.post(f"/sessions/{MINE}/read", json={"timestamp": T4})
    after = _stored()
    assert (after["last_message_at"], after["last_seen"]) == (before["last_message_at"], before["last_seen"])


def test_the_open_tabs_hear_the_mark_and_the_list_is_announced(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    announced: list[str] = []
    monkeypatch.setattr(session_storage, "notify_changed", announced.append)

    async def scenario() -> tuple[str, dict[str, Any]]:
        session_events.bind_home_loop(asyncio.get_running_loop())
        async with session_events.subscribe(MINE) as (_, queue):
            await asyncio.to_thread(client.post, f"/sessions/{MINE}/read", json={"timestamp": T2})
            event = await asyncio.wait_for(queue.get(), 1)
            return event.kind, event.data

    try:
        assert asyncio.run(scenario()) == ("read_until", {"timestamp": T2})
    finally:
        session_events._home_loop = None
    assert announced == ["sessions"]


def test_a_tab_that_reconnects_gets_only_the_newest_mark(client: TestClient) -> None:
    """The mark is a state: the replay buffer keeps the newest one, not every step on the way."""
    client.post(f"/sessions/{MINE}/read", json={"timestamp": T2})
    client.post(f"/sessions/{MINE}/read", json={"timestamp": T4})
    marks = [event.data for event in session_events._channel(MINE).replay if event.kind == "read_until"]
    assert marks == [{"timestamp": T4}]


def test_migration_sets_missing_fields_and_leaves_present_ones(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("migrate_read_until", Path(__file__).parents[1] / "scripts" / "migrate_read_until.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def write(name: str, session: dict[str, Any]) -> None:
        (tmp_path / f"{name}.json").write_text(json.dumps(session), encoding="utf-8")

    write("missing", {"data": {"chat_history": _history()}})
    write("present", {"read_until": T1, "data": {"chat_history": _history()}})
    write("empty", {"data": {}})
    assert module.migrate_directory(tmp_path) == 2
    read = {name: json.loads((tmp_path / f"{name}.json").read_text())["read_until"] for name in ("missing", "present", "empty")}
    assert read == {"missing": T4, "present": T1, "empty": ""}
