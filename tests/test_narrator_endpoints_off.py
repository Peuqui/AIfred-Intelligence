"""Off means off for the narrator (14.11.2026): its voice per engine (GET/PUT /speech/narrator/{engine}) answers 409 while
the plugin is off, the rest of the speech endpoints go on."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from aifred.lib import plugin_registry


@pytest.fixture
def client() -> TestClient:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    return test_client


def test_the_narrators_voice_is_refused_while_the_plugin_is_off(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_registry, "is_plugin_enabled", lambda name: name != "narrator")
    assert client.get("/speech/narrator/edge").status_code == 409
    assert client.put("/speech/narrator/edge", json={"voice": "Katja"}).status_code == 409
    assert client.get("/speech/recognition").status_code != 409


def test_with_the_plugin_on_the_voice_is_asked_for(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import tts_service
    from aifred.lib.api import speech

    monkeypatch.setattr(plugin_registry, "is_plugin_enabled", lambda name: True)
    monkeypatch.setattr(tts_service, "voice_names", lambda engine: ["Katja"])
    monkeypatch.setattr(speech, "_narrator", lambda engine: speech.NarratorVoice(engine=engine, voice="Katja", voices=["Katja"]))
    assert client.get("/speech/narrator/edge").json()["voice"] == "Katja"
