"""TTS start API: token guard, AIfred's card choice, refusal without a fitting card."""

import pytest
from starlette.testclient import TestClient

from aifred.lib import tts_escalation
from aifred.lib.api.app import api_app
from aifred.lib.tts_engines import TTS_ENGINES
from aifred.lib.tts_escalation import GPUPlacement, PlacementRefused

TOKEN = "test-tts-control-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def qwen(monkeypatch: pytest.MonkeyPatch):
    """Qwen3 with a built image, not running; start() records the card it got."""
    monkeypatch.setenv("TTS_CONTROL_API_TOKEN", TOKEN)
    engine = TTS_ENGINES["qwen3local"]
    started: list[str] = []
    monkeypatch.setattr(engine, "is_installed", lambda: True)
    monkeypatch.setattr(engine, "is_running", lambda: False)

    async def place(candidate):
        placed = candidate.on_gpu("GPU-card-0")
        monkeypatch.setattr(placed, "start", lambda: (started.append(placed.gpu_uuid), (True, "started"))[1])
        return GPUPlacement(placed, 0, 6536, 9231)

    monkeypatch.setattr(tts_escalation, "place_local_gpu_engine", place)
    engine.started = started  # type: ignore[attr-defined]
    return engine


def _start(engine: str = "qwen3local", headers=AUTH):
    return TestClient(api_app).post("/tts/start", headers=headers, json={"engine": engine, "caller": "Steuerseite"})


def test_the_token_is_required(qwen) -> None:
    assert _start(headers={}).status_code == 403
    assert _start(headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert qwen.started == []


def test_missing_server_token_is_503(qwen, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TTS_CONTROL_API_TOKEN")
    assert _start().status_code == 503


def test_the_engine_starts_on_the_card_aifred_picks(qwen) -> None:
    response = _start()
    assert response.status_code == 200
    assert response.json() == {
        "success": True, "engine": "qwen3local", "gpu": 0, "message": "Qwen3-TTS started on GPU 0",
    }
    assert qwen.started == ["GPU-card-0"]


def test_no_fitting_card_refuses_and_starts_nothing(qwen, monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(candidate):
        raise PlacementRefused("needs 6.536 MiB, most free 461 MiB on GPU 3")

    monkeypatch.setattr(tts_escalation, "place_local_gpu_engine", refuse)
    response = _start()
    assert response.status_code == 409
    assert "most free 461 MiB" in response.json()["detail"]
    assert qwen.started == []


def test_a_running_engine_is_left_alone(qwen, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qwen, "is_running", lambda: True)
    response = _start()
    assert response.json()["gpu"] is None
    assert qwen.started == []


@pytest.mark.parametrize("engine, status", [("nope", 404), ("edge", 422), ("piper", 422)])
def test_only_known_local_gpu_engines(qwen, engine: str, status: int) -> None:
    assert _start(engine).status_code == status


def test_an_engine_without_image_is_409(qwen, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qwen, "is_installed", lambda: False)
    assert _start().status_code == 409
