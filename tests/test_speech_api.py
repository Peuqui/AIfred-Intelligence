"""The speech page's endpoints (lib/api/speech.py over lib/tts_settings.py): the list in speaking order, moved,
switched and its units changed as one transaction each, shown from the settings while the service does not
answer (and not changed then); each chat agent's switch, language and voice per engine; the narrator's voice.
settings.json is a temporary one, the TTS service an httpx.MockTransport that only reads."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from aifred.lib import settings, tts_service
from aifred.lib.tts_client import TTSClient


def _row(engine: str, host: str | None = None, **fields: Any) -> dict[str, Any]:
    return {
        "engine": engine, "host": host, "label": {"de": f"{engine} lang", "en": f"{engine} long"},
        "label_short": {"de": engine.upper(), "en": engine.upper()}, "status": "stopped", "unit_default": "sentence",
        "needs_gpu": True, "cloud": False, "browser": False, "installed": True, "start_hint": None, "vram_mib": {},
        "idle_remaining_s": None, "default_vram_mib": None, "needed_mib": None, "default_max_parallel": 1,
        "default_max_queue": 4, **fields,
    }


CATALOG = [_row("xtts"), _row("qwen3", "Aragon", status="running", needs_gpu=False), _row("browser", needs_gpu=False, browser=True)]
VOICES = {"xtts": ["Bettina", "★ Butler", "Anton"], "qwen3": ["Vivian"]}


class _Service:
    def __init__(self) -> None:
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if request.method != "GET":
            raise AssertionError(f"AIfred only reads the service, got {request.method} {request.url.path}")
        if request.url.path == "/engines":
            return httpx.Response(200, json=CATALOG)
        if request.url.path == "/voices":
            names = VOICES.get(request.url.params["engine"], [])
            return httpx.Response(200, json=[{"name": name, "label": name} for name in names])
        raise AssertionError(f"unexpected {request.url.path}")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, _Service, Path]]:
    from aifred.lib import tts_llm_coordination
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    file = tmp_path / "settings.json"
    file.write_text(json.dumps({
        "ui_language": "de",
        "tts_escalation": [{"engine": "xtts", "host": None, "enabled": True}, {"engine": "qwen3", "host": "Aragon", "enabled": True}],
        "tts_agent_voices_per_engine": {"xtts": {"aifred": {"voice": "Bettina", "speed": "1.1x", "pitch": "1.0"}}},
    }))
    monkeypatch.setattr(settings, "SETTINGS_FILE", file)
    service = _Service()
    client = TTSClient("http://tts.test", transport=httpx.MockTransport(service.handler))
    monkeypatch.setattr(tts_service, "get_tts_client", lambda: client)
    monkeypatch.setattr(tts_llm_coordination, "planned_tts_engine", lambda _model: "xtts")
    tts_service.reset_cache()
    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client, service, file
    tts_service.reset_cache()


def _saved(file: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(file.read_text())
    return data


def test_the_list_comes_in_speaking_order_with_what_the_catalog_adds_switched_off(world: tuple[TestClient, _Service, Path]) -> None:
    client, _service, _file = world
    listed = client.get("/speech/list").json()
    assert listed["service_error"] == ""
    assert [(row["engine"], row["label"], row["enabled"]) for row in listed["rows"]] == [
        ("xtts", "XTTS · lokal", True), ("qwen3", "QWEN3 · Aragon", True), ("browser", "BROWSER · Gerät", False),
    ]
    assert listed["rows"][0]["reserved"] and not listed["rows"][1]["reserved"]


def test_moving_switching_and_the_unit_save_the_whole_list(world: tuple[TestClient, _Service, Path]) -> None:
    client, _service, file = world
    moved = client.post("/speech/list/move", json={"index": 1, "delta": -1}).json()
    assert [row["engine"] for row in moved["rows"]] == ["qwen3", "xtts", "browser"]
    client.post("/speech/list/switch", json={"index": 2, "enabled": True})
    client.put("/speech/units/xtts", json={"unit": "whole"})
    saved = _saved(file)
    assert saved["tts_escalation"] == [
        {"engine": "qwen3", "host": "Aragon", "enabled": True}, {"engine": "xtts", "host": None, "enabled": True},
        {"engine": "browser", "host": None, "enabled": True},
    ]
    assert saved["tts_toggles_per_engine"] == {"xtts": {"unit": "whole"}} and saved["ui_language"] == "de"   # the rest stays
    assert client.put("/speech/units/xtts", json={"unit": "word"}).status_code == 422
    assert client.post("/speech/list/move", json={"index": 7, "delta": 1}).status_code == 422


def test_without_the_service_the_list_shows_from_the_settings_and_does_not_change(world: tuple[TestClient, _Service, Path]) -> None:
    client, service, file = world
    before = file.read_text()
    service.down = True
    listed = client.post("/speech/list/refresh").json()           # a fresh read: the cached catalog is no answer
    assert listed["service_error"] and [row["engine"] for row in listed["rows"]] == ["xtts", "qwen3"]
    assert client.post("/speech/list/move", json={"index": 1, "delta": -1}).status_code == 503
    assert file.read_text() == before
    assert client.get("/speech/choices").json()["engines"] == []


def test_each_chat_agent_has_its_voice_per_engine_its_switch_and_language(world: tuple[TestClient, _Service, Path]) -> None:
    client, _service, file = world
    agents = {agent["agent"]: agent for agent in client.get("/speech/agents", params={"engine": "xtts"}).json()}
    assert (agents["aifred"]["voice"], agents["aifred"]["speed"]) == ("Bettina", "1.1x")        # saved
    assert agents["aifred"]["voices"][0] == "★ Butler"                                            # starred first
    changed = client.put("/speech/agents/sokrates/voices/xtts", json={"voice": "Anton", "speed": "0.9x", "pitch": "0.95"}).json()
    assert (changed["voice"], changed["speed"], changed["pitch"]) == ("Anton", "0.9x", "0.95")
    assert _saved(file)["tts_agent_voices_per_engine"]["xtts"]["sokrates"] == {"voice": "Anton", "speed": "0.9x", "pitch": "0.95"}
    switched = client.patch("/speech/agents/sokrates", params={"engine": "xtts"}, json={"enabled": False, "language": "en"}).json()
    assert (switched["enabled"], switched["language"]) == (False, "en")
    assert _saved(file)["tts_agents"]["sokrates"] == {"enabled": False, "language": "en"}
    assert client.put("/speech/agents/sokrates/voices/xtts", json={"voice": "Anton", "speed": "3x", "pitch": "1.0"}).status_code == 422
    assert client.patch("/speech/agents/sokrates", params={"engine": "xtts"}, json={"language": "tlh"}).status_code == 422
    assert client.get("/speech/agents", params={"engine": "xtts"}).status_code == 200
    assert client.patch("/speech/agents/niemand", params={"engine": "xtts"}, json={"enabled": True}).status_code == 404


def test_the_narrator_reads_with_a_voice_its_engine_offers(world: tuple[TestClient, _Service, Path]) -> None:
    client, _service, file = world
    assert client.get("/speech/narrator/xtts").json()["voice"] == "Bettina"        # none saved: the first
    assert client.put("/speech/narrator/xtts", json={"voice": "Nemo"}).status_code == 422
    assert client.put("/speech/narrator/xtts", json={"voice": "Anton"}).json()["voice"] == "Anton"
    assert _saved(file)["narrator_voices"] == {"xtts": "Anton"}
