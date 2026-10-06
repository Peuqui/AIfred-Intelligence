"""Announce API: token guard, room list, 404 / 413 / 422, one text or several paragraphs."""

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
    recorded: list[tuple] = []

    async def fake_announce(channel, room, text, *, session_id, metadata):
        delivered.append((channel, room, text, session_id, metadata))
        return True

    # Die Session-Dokumentation nicht in echte Sessions schreiben
    monkeypatch.setattr(announce, "announce_to_channel", fake_announce)
    monkeypatch.setattr(announce, "record_autonomous_turn", lambda *args, **kwargs: recorded.append(args))
    test_client = TestClient(api_app)
    test_client.delivered = delivered  # type: ignore[attr-defined]
    test_client.recorded = recorded  # type: ignore[attr-defined]
    return test_client


def _post(client: TestClient, **body):
    return client.post("/audio/announce", headers=AUTH, json={"room": "wohnzimmer", **body})


def test_rooms_need_the_token(client: TestClient) -> None:
    assert client.get("/audio/announce/rooms").status_code == 403
    assert client.get("/audio/announce/rooms", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/audio/announce/rooms", headers=AUTH).json() == {"rooms": ["wohnzimmer"]}


def test_missing_server_token_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANNOUNCE_API_TOKEN")
    assert client.get("/audio/announce/rooms", headers=AUTH).status_code == 503


def test_announce_delivers_with_both_tones(client: TestClient) -> None:
    response = _post(client, text="Hallo")
    assert response.status_code == 200
    assert response.json() == {"success": True, "rooms": ["wohnzimmer"]}
    channel, room, text, session_id, metadata = client.delivered[0]  # type: ignore[attr-defined]
    assert (channel, room, text, session_id) == ("freeecho2", "wohnzimmer", "Hallo", None)
    assert metadata == {
        "audio_type": "notification", "proactive": True, "start_tone": True, "end_tone": True,
        "paragraphs": ["Hallo"], "pause_ms": announce.ANNOUNCE_PAUSE_MS,
    }


def test_several_paragraphs_are_one_announcement(client: TestClient) -> None:
    response = _post(client, texts=["Erster Absatz.", "Zweiter Absatz."], pause_ms=700)
    assert response.status_code == 200
    assert len(client.delivered) == 1  # type: ignore[attr-defined]
    _, _, text, _, metadata = client.delivered[0]  # type: ignore[attr-defined]
    assert text == "Erster Absatz. Zweiter Absatz."
    # one stream: start tone before the first and end tone after the last paragraph, set by rule
    assert metadata["paragraphs"] == ["Erster Absatz.", "Zweiter Absatz."]
    assert metadata["pause_ms"] == 700
    assert (metadata["start_tone"], metadata["end_tone"]) == (True, True)


def test_the_announcement_is_documented_in_the_rooms_session(client: TestClient) -> None:
    _post(client, texts=["Erster Absatz.", "Zweiter."], speaker="Whisper")
    assert client.recorded == [  # type: ignore[attr-defined]
        ("freeecho2", "wohnzimmer", "Whisper", "Whisper: Erster Absatz. Zweiter."),
    ]


def test_without_speaker_the_session_says_announcement(client: TestClient) -> None:
    _post(client, text="Hallo")
    assert client.recorded[0][2] == "Ansage"  # type: ignore[attr-defined]


@pytest.mark.parametrize("body", [{}, {"text": "a", "texts": ["b"]}, {"texts": []}, {"texts": [""]}])
def test_exactly_one_of_text_and_texts(client: TestClient, body) -> None:
    assert _post(client, **body).status_code == 422
    assert client.delivered == []  # type: ignore[attr-defined]


def test_pause_is_limited(client: TestClient) -> None:
    assert _post(client, texts=["a"], pause_ms=announce.ANNOUNCE_MAX_PAUSE_MS + 1).status_code == 422
    assert _post(client, texts=["a"], pause_ms=-1).status_code == 422


def test_unknown_room_is_404(client: TestClient) -> None:
    response = client.post("/audio/announce", headers=AUTH, json={"room": "keller", "text": "Hallo"})
    assert response.status_code == 404
    assert client.delivered == []  # type: ignore[attr-defined]


def test_too_long_entry_is_413_not_cut(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(announce, "ANNOUNCE_MAX_CHARS", 10)
    assert _post(client, text="x" * 11).status_code == 413
    assert _post(client, texts=["ok", "x" * 11]).status_code == 413
    assert client.delivered == []  # type: ignore[attr-defined]


def test_too_long_total_is_413(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(announce, "ANNOUNCE_MAX_TOTAL_CHARS", 20)
    assert _post(client, texts=["x" * 8, "y" * 8, "z" * 8]).status_code == 413
    assert _post(client, texts=["x" * 8, "y" * 8]).status_code == 200
