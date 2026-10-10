"""TTS API: token guard, the list in AIfred's order, start/stop per entry —
locally on AIfred's card, on a host over SSH, refused without a fitting card."""

import pytest
from starlette.testclient import TestClient

import aifred.lib.settings as settings_module
from aifred.lib import tts_escalation
from aifred.lib.api.app import api_app
from aifred.lib.tts_engines import TTS_ENGINES
from aifred.lib.tts_escalation import GPUPlacement, PlacementRefused

TOKEN = "test-tts-control-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HOSTS = [{"name": "Box", "address": "box.lan", "enabled": True, "ssh": "mp@box.lan:22"}]
LIST = [
    {"engine": "qwen3local", "host": None, "enabled": True},
    {"engine": "qwen3local", "host": "Box", "enabled": True},
    {"engine": "edge", "host": None, "enabled": True},
]


@pytest.fixture
def aifred(monkeypatch: pytest.MonkeyPatch):
    """AIfred with a three-entry list; records card starts, SSH commands and stops."""
    monkeypatch.setenv("TTS_CONTROL_API_TOKEN", TOKEN)
    monkeypatch.setattr(settings_module, "persisted_settings", lambda: {"tts_hosts": HOSTS, "tts_escalation": LIST})
    qwen = TTS_ENGINES["qwen3local"]
    calls: list[tuple] = []
    monkeypatch.setattr(qwen, "is_installed", lambda: True)
    monkeypatch.setattr(qwen, "is_running", lambda: False)
    monkeypatch.setattr(qwen, "stop", lambda: (calls.append(("stop", "local")), (True, "stopped"))[1])

    async def place(engine):
        placed = engine.on_gpu("GPU-card-0")
        monkeypatch.setattr(placed, "start", lambda: (calls.append(("start", placed.gpu_uuid)), (True, "up"))[1])
        return GPUPlacement(placed, 0, 6536, 9231)

    monkeypatch.setattr(tts_escalation, "place_local_gpu_engine", place)
    monkeypatch.setattr(tts_escalation, "host_control", lambda host, *command: calls.append((host.name, *command)) or "")
    return calls


def _post(path: str, engine: str = "qwen3local", host: str | None = None, headers=AUTH):
    return TestClient(api_app).post(path, headers=headers, json={"engine": engine, "host": host, "caller": "Steuerseite"})


def test_every_endpoint_needs_the_token(aifred) -> None:
    client = TestClient(api_app)
    assert client.get("/tts/entries", params={"lang": "de"}).status_code == 403
    assert _post("/tts/start", headers={}).status_code == 403
    assert _post("/tts/stop", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert aifred == []


def test_missing_server_token_is_503(aifred, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TTS_CONTROL_API_TOKEN")
    assert _post("/tts/start").status_code == 503


def test_the_list_comes_in_aifreds_order_with_status(aifred) -> None:
    rows = TestClient(api_app).get("/tts/entries", params={"lang": "de"}, headers=AUTH).json()
    assert [(row["engine"], row["host"], row["controllable"]) for row in rows] == [
        ("qwen3local", None, True), ("qwen3local", "Box", True), ("edge", None, False),
    ]
    assert rows[0]["label"] == "Qwen3-TTS · lokal"
    assert rows[0]["status"] == "sleeping" and rows[0]["status_text"] == "schläft — startet bei Bedarf"


def test_a_local_entry_starts_on_the_card_aifred_picks(aifred) -> None:
    response = _post("/tts/start")
    assert response.status_code == 200
    assert response.json()["message"].startswith("Qwen3-TTS started on GPU 0")
    assert aifred == [("start", "GPU-card-0")]


def test_a_host_entry_starts_and_stops_over_ssh(aifred) -> None:
    assert _post("/tts/start", host="Box").status_code == 200
    assert _post("/tts/stop", host="Box").status_code == 200
    assert aifred == [("Box", "start", "qwen3-tts"), ("Box", "stop", "qwen3-tts")]


def test_no_fitting_card_refuses_and_starts_nothing(aifred, monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(engine):
        raise PlacementRefused("needs 6.536 MiB, most free 461 MiB on GPU 3")

    monkeypatch.setattr(tts_escalation, "place_local_gpu_engine", refuse)
    response = _post("/tts/start")
    assert response.status_code == 409
    assert "most free 461 MiB" in response.json()["detail"]
    assert aifred == []


def test_a_local_entry_stops_through_compose(aifred) -> None:
    assert _post("/tts/stop").status_code == 200
    assert aifred == [("stop", "local")]


@pytest.mark.parametrize(("engine", "host", "status"), [
    ("xtts", None, 404),          # not in the list
    ("qwen3local", "Nope", 404),  # unknown host
    ("edge", None, 409),          # nothing AIfred could start
])
def test_only_controllable_list_entries(aifred, engine: str, host: str | None, status: int) -> None:
    assert _post("/tts/start", engine=engine, host=host).status_code == status
    assert aifred == []
