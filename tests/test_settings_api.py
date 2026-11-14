"""AIfred's general settings over the API (GET/PATCH /settings): settings.json over the defaults, a change
writes only its keys, a value the setting does not know is refused. 07.11.2026: the endpoint carried fixed
agents' models, RoPE factors and switches nobody reads (models and switches are the quick choices now);
speech and dictation had endpoints of their own."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import formatting, settings


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    file = tmp_path / "settings.json"
    file.write_text(json.dumps({"ui_language": "de", "backend_type": "llamacpp", "agent_tuning": {"aifred": {"top_k": 7}}}))
    monkeypatch.setattr(settings, "SETTINGS_FILE", file)
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_path)
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    return test_client


def test_the_settings_are_the_file_over_the_defaults(client: TestClient) -> None:
    current = client.get("/settings").json()
    assert (current["backend_type"], current["ui_language"]) == ("llamacpp", "de")
    defaults = settings.get_default_settings()
    assert current["dictation_engine"] == defaults["dictation_engine"] and current["max_debate_rounds"] == defaults["max_debate_rounds"]


def test_a_change_writes_its_keys_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    locales: list[str] = []
    monkeypatch.setattr(formatting, "set_ui_locale", locales.append)
    answer = client.patch("/settings", json={"dictation_engine": "browser", "user_name": "  Peuqui  ", "max_debate_rounds": 4, "ui_language": "en"})
    assert answer.status_code == 200 and answer.json()["user_name"] == "Peuqui"
    assert json.loads(settings.SETTINGS_FILE.read_text()) == {
        "ui_language": "en", "backend_type": "llamacpp", "agent_tuning": {"aifred": {"top_k": 7}},
        "dictation_engine": "browser", "user_name": "Peuqui", "max_debate_rounds": 4,
    }
    assert locales == ["en"]                                            # the number format follows the language


@pytest.mark.parametrize("change", [
    {"dictation_engine": "telepathie"},
    {"ui_language": "fr"},
    {"user_gender": "x"},
    {"max_debate_rounds": 11},
    {"max_debate_rounds": 0},
    {"consensus_type": "beliebig"},
    {"tts_list_max": 0},
    {"backend_type": "ollama"},                                         # the backend has its own switch
])
def test_a_value_the_setting_does_not_know_is_refused(client: TestClient, change: dict[str, object]) -> None:
    before = settings.SETTINGS_FILE.read_text()
    assert client.patch("/settings", json=change).status_code == 422
    assert settings.SETTINGS_FILE.read_text() == before


def test_the_cut_of_lists_in_the_spoken_reply_is_a_speech_setting_the_filter_reads(client: TestClient) -> None:
    """tts_list_max (07/14.11.2026: moved from the audio player's settings to the spoken output, the core)."""
    from aifred.lib.audio_processing import _get_tts_list_max_items
    from aifred.lib.config import TTS_LIST_MAX_DEFAULT

    assert client.get("/settings").json()["tts_list_max"] == TTS_LIST_MAX_DEFAULT == _get_tts_list_max_items()
    assert client.patch("/settings", json={"tts_list_max": 9}).json()["tts_list_max"] == 9
    assert json.loads(settings.SETTINGS_FILE.read_text())["tts_list_max"] == 9
    assert _get_tts_list_max_items() == 9
