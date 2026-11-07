"""The session API of the Vue app (lib/api/sessions.py, lib/api/login.py).

Every endpoint works on the logged-in user's own sessions. Until 07.11.2026 GET /api/sessions
listed the sessions of every account (list_sessions() without an owner returned all of them).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import session_events, session_storage, turn_service
from aifred.lib.api.app import api_app
from aifred.lib.auth import sign_username
from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

MINE = "a" * 32
THEIRS = "b" * 32


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    session_storage.create_empty_session(MINE, owner="ki")
    session_storage.create_empty_session(THEIRS, owner="lord")
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    yield test_client
    session_storage._session_meta_cache.clear()
    for session in (MINE, THEIRS):
        session_events._channels.pop(session, None)


def test_the_list_holds_only_the_users_sessions(client: TestClient) -> None:
    response = client.get("/sessions")
    assert response.status_code == 200
    assert [entry["session_id"] for entry in response.json()] == [MINE]


@pytest.mark.parametrize("session_id", [THEIRS, "c" * 32, "../../etc/passwd", "kurz"])
def test_a_session_that_is_not_the_users_does_not_exist(client: TestClient, session_id: str) -> None:
    assert client.get(f"/sessions/{session_id}").status_code == 404
    assert client.post(f"/sessions/{session_id}/messages", json={"text": "hallo"}).status_code == 404
    assert client.delete(f"/sessions/{session_id}").status_code == 404


def test_opening_a_session_gives_history_config_and_the_event_position(client: TestClient) -> None:
    session_events.publish(MINE, "debug", {"line": "x"})
    body = client.get(f"/sessions/{MINE}").json()
    assert (body["chat_history"], body["running"], body["config"]["active_agent"]) == ([], False, "aifred")
    assert body["last_event_id"] == session_events.last_event_id(MINE) > 0


def test_a_new_session_belongs_to_the_user(client: TestClient) -> None:
    created = client.post("/sessions").json()["session_id"]
    session = session_storage.load_session(created)
    assert session is not None and session["owner"] == "ki"


def test_the_config_accepts_known_agents_and_modes_only(client: TestClient) -> None:
    assert client.patch(f"/sessions/{MINE}/config", json={"active_agent": "gibt-es-nicht"}).status_code == 422
    assert client.patch(f"/sessions/{MINE}/config", json={"multi_agent_mode": "devils_advocate"}).status_code == 422
    response = client.patch(f"/sessions/{MINE}/config", json={"active_agent": "sokrates"})
    assert response.status_code == 200 and response.json()["active_agent"] == "sokrates"
    assert session_storage.get_session_config(MINE)["active_agent"] == "sokrates"
    assert [event.kind for event in session_events.replay_after(MINE, 0)] == ["session_config"]


def test_the_config_model_names_every_field_of_a_session_config() -> None:
    from aifred.lib.api.sessions import SessionConfig

    assert set(SessionConfig.model_fields) == set(session_storage.DEFAULT_SESSION_CONFIG)


def test_a_session_turns_incognito_and_back(client: TestClient) -> None:
    assert client.get(f"/sessions/{MINE}").json()["config"]["incognito"] is False
    response = client.patch(f"/sessions/{MINE}/config", json={"incognito": True})
    assert response.status_code == 200 and response.json()["incognito"] is True
    assert session_storage.get_session_config(MINE)["incognito"] is True
    assert client.patch(f"/sessions/{MINE}/config", json={"incognito": "vielleicht"}).status_code == 422


def test_a_message_goes_to_the_turn_service(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    def submit(request: Any) -> bool:
        sent.append(request)
        return True

    monkeypatch.setattr(turn_service, "submit_turn", submit)
    response = client.post(f"/sessions/{MINE}/messages", json={"text": "Hallo"})
    assert (response.status_code, response.json()) == (202, {"queued": True})
    assert [(request.session_id, request.text) for request in sent] == [(MINE, "Hallo")]
    assert client.post(f"/sessions/{MINE}/messages", json={"text": "  "}).status_code == 422


def test_stopping_an_idle_session_cancels_nothing(client: TestClient) -> None:
    assert client.post(f"/sessions/{MINE}/stop").json() == {"cancelled": False}


def test_login_sets_the_cookie_of_the_reflex_login(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(session_storage, "account_exists", lambda user: user == "ki")
    monkeypatch.setattr(session_storage, "verify_account", lambda user, password: password == "richtig")
    anonymous = TestClient(api_app)
    assert anonymous.get("/sessions").status_code == 403
    assert anonymous.get("/auth/me").json() == {"user": None}
    assert anonymous.post("/auth/login", json={"username": "ki", "password": "falsch"}).status_code == 401
    assert anonymous.post("/auth/login", json={"username": "niemand", "password": "richtig"}).status_code == 401
    response = anonymous.post("/auth/login", json={"username": " KI ", "password": "richtig"})
    assert response.status_code == 200 and response.json() == {"user": "ki"}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{USERNAME_COOKIE_NAME}=") and "HttpOnly" not in cookie and "SameSite=lax" in cookie
    assert anonymous.get("/auth/me").json() == {"user": "ki"}


def test_the_agent_list_holds_the_agents_a_chat_can_address(client: TestClient) -> None:
    from aifred.lib.agent_config import load_agents

    listed = {entry["id"] for entry in client.get("/agents/summary").json()}
    assert listed == {agent_id for agent_id, config in load_agents().items() if config.role != "system"}
    assert "aifred" in listed


def test_the_shell_tells_the_ui_language_before_the_login() -> None:
    response = TestClient(api_app).get("/app/ui-settings")
    assert response.status_code == 200 and response.json()["ui_language"] in ("de", "en")


def test_an_entry_of_an_older_session_file_opens_with_its_fields(client: TestClient) -> None:
    session_storage.update_chat_data(MINE, chat_history=[{"role": "user", "content": "alt", "used_sources": []}])
    (entry,) = client.get(f"/sessions/{MINE}").json()["chat_history"]
    assert (entry["content"], entry["agent"], entry["used_sources"]) == ("alt", "", [])
