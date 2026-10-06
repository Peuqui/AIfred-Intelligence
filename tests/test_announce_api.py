"""Announce API: token guard, room list, 404 / 413 and delivery."""

import pytest
from starlette.testclient import TestClient

from aifred.lib.api import announce
from aifred.lib.api.app import api_app

TOKEN = "test-announce-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ANNOUNCE_API_TOKEN", TOKEN)
    monkeypatch.setattr(announce, "resolve_announce_targets",
                        lambda channel, target: ["wohnzimmer"] if target in ("*", "wohnzimmer") else [])
    delivered: list[tuple] = []

    async def fake_announce(channel, room, text, *, session_id, metadata):
        delivered.append((channel, room, text, session_id, metadata))
        return True

    monkeypatch.setattr(announce, "announce_to_channel", fake_announce)
    test_client = TestClient(api_app)
    test_client.delivered = delivered  # type: ignore[attr-defined]
    return test_client


def test_rooms_need_the_token(client: TestClient) -> None:
    assert client.get("/audio/announce/rooms").status_code == 403
    assert client.get("/audio/announce/rooms", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/audio/announce/rooms", headers=AUTH).json() == {"rooms": ["wohnzimmer"]}


def test_missing_server_token_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANNOUNCE_API_TOKEN")
    assert client.get("/audio/announce/rooms", headers=AUTH).status_code == 503


def test_announce_delivers_with_notification_chime(client: TestClient) -> None:
    response = client.post("/audio/announce", headers=AUTH, json={"room": "wohnzimmer", "text": "Hallo"})
    assert response.status_code == 200
    assert response.json() == {"success": True, "rooms": ["wohnzimmer"]}
    channel, room, text, session_id, metadata = client.delivered[0]  # type: ignore[attr-defined]
    assert (channel, room, text, session_id) == ("freeecho2", "wohnzimmer", "Hallo", None)
    assert metadata == {"audio_type": "notification", "proactive": True}


def test_unknown_room_is_404(client: TestClient) -> None:
    response = client.post("/audio/announce", headers=AUTH, json={"room": "keller", "text": "Hallo"})
    assert response.status_code == 404
    assert client.delivered == []  # type: ignore[attr-defined]


def test_too_long_text_is_413_not_cut(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(announce, "ANNOUNCE_MAX_CHARS", 10)
    response = client.post("/audio/announce", headers=AUTH, json={"room": "wohnzimmer", "text": "x" * 11})
    assert response.status_code == 413
    assert client.delivered == []  # type: ignore[attr-defined]
