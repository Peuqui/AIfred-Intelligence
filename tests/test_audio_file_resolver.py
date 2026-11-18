"""The audio endpoints resolve keys through the settings SSOT (audio_player_settings), not through a private copy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import audio_player_settings, plugin_registry


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    monkeypatch.setattr(plugin_registry, "is_plugin_enabled", lambda name: True)
    test_client = TestClient(api_app, follow_redirects=False)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    return test_client


def test_audio_file_resolves_a_stream_from_the_ssot_settings(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"sources": {"Radio": {"type": "http_stream", "url": "http://radio.example/stream"}}}), encoding="utf-8")
    monkeypatch.setattr(audio_player_settings, "AUDIO_PLAYER_SETTINGS_PATH", settings)

    response = client.get("/audio/file", params={"key": "Radio"})

    assert response.status_code == 302
    assert response.headers["location"] == "http://radio.example/stream"


def test_a_corrupt_settings_file_is_an_error_not_an_empty_resolver(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(audio_player_settings, "AUDIO_PLAYER_SETTINGS_PATH", settings)

    # Not a 404 "no such audio": JSONDecodeError is a ValueError, which the endpoints must not mistake for an unknown key.
    with pytest.raises(json.JSONDecodeError):
        client.get("/audio/file", params={"key": "Radio"})
    with pytest.raises(json.JSONDecodeError):
        client.post("/audio/position", json={"state_key": "Radio", "pos_sec": 1.0})


def test_a_settings_file_that_is_no_object_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(audio_player_settings, "AUDIO_PLAYER_SETTINGS_PATH", settings)

    with pytest.raises(TypeError, match="not a JSON object"):
        audio_player_settings.load_audio_player_settings()
