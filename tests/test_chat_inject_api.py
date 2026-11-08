"""POST /api/chat/inject and GET /api/chat/status (lib/api/chat.py): an injected message is the user's own input.

Until 08.11.2026 inject only queued the message in the session file, and the Reflex browser picked it up from there:
without an open Reflex tab it stayed there. Now it goes to the turn service like a message written in the chat
(``submit_turn``: started now, or waiting behind the running turn; its queue is tested in test_turn_service.py).
"""

from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from starlette.testclient import TestClient

from aifred.lib import pipeline_registry, session_storage, turn_service
from aifred.lib.api.app import api_app
from aifred.lib.auth import sign_username
from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

SESSION = "e" * 32
TOKEN = "geheim"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    monkeypatch.setenv("INJECT_API_TOKEN", TOKEN)
    session_storage._session_meta_cache.clear()
    session_storage.create_empty_session(SESSION, owner="ki")
    yield TestClient(api_app)   # no login cookie: inject guards itself with its token
    session_storage._session_meta_cache.clear()


@pytest.fixture
def submitted(monkeypatch: pytest.MonkeyPatch) -> list[turn_service.TurnRequest]:
    """The turns inject hands the turn service; the first one starts, every further one waits."""
    requests: list[turn_service.TurnRequest] = []

    def submit(request: turn_service.TurnRequest) -> bool:
        requests.append(request)
        return len(requests) > 1

    monkeypatch.setattr(turn_service, "submit_turn", submit)
    return requests


# httpx2: what starlette's TestClient returns.
def _inject(client: TestClient, session_id: str = SESSION, token: str = TOKEN) -> httpx2.Response:
    return client.post("/chat/inject", json={"sender": "Claude", "session_id": session_id, "message": "Wie spät?",
                                             "token": token})


def test_the_message_becomes_a_turn_of_the_session_with_the_callers_header(
        client: TestClient, submitted: list[turn_service.TurnRequest]) -> None:
    first, second = _inject(client), _inject(client)
    assert first.status_code == second.status_code == 200
    assert (first.json()["queued"], second.json()["queued"]) == (False, True)
    assert [request.session_id for request in submitted] == [SESSION, SESSION]
    header, text = submitted[0].text.split("\n", 1)
    assert "Claude" in header and text.strip() == "Wie spät?"


@pytest.mark.parametrize("session_id", ["c" * 32, "../../etc/passwd", "kurz"])
def test_a_session_that_does_not_exist_is_404_and_no_turn(
        client: TestClient, submitted: list[turn_service.TurnRequest], session_id: str) -> None:
    assert _inject(client, session_id).status_code == 404
    assert submitted == []
    assert not (session_storage.SESSION_DIR / f"{session_id}.json").exists()


def test_a_wrong_token_is_refused_before_anything_runs(
        client: TestClient, submitted: list[turn_service.TurnRequest]) -> None:
    assert _inject(client, token="falsch").status_code == 403
    assert submitted == []


def test_the_status_follows_the_running_turn(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Until 08.11.2026 the status read a field only the Reflex state wrote: without it, never "generating".
    client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))   # unlike inject, behind the login
    monkeypatch.setattr(pipeline_registry, "is_pipeline_running", lambda session_id: session_id == SESSION)
    body = client.get("/chat/status", params={"session_id": SESSION}).json()
    assert (body["is_generating"], body["message_count"]) == (True, 0)
    monkeypatch.setattr(pipeline_registry, "is_pipeline_running", lambda session_id: False)
    assert client.get("/chat/status", params={"session_id": SESSION}).json()["is_generating"] is False
    assert client.get("/chat/status", params={"session_id": "kurz"}).status_code == 404
