"""GPU release API (GET /gpu/holders, POST /gpu/release): the token, the form GPU-Orc reads, the status codes."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib.api.app import api_app
from aifred.lib.gpu_release import GpuReleaseResult
from aifred.lib.vision_filters import face_detect

TOKEN = "test-gpu-release-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HOLDER = {"item": "face-worker", "gpu_uuids": ["GPU-1"], "memory_mib": {"GPU-1": 892}, "busy": False}


class _Worker:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.asked: list[tuple[bool, str]] = []

    def holders(self) -> list[dict[str, Any]]:
        return [HOLDER]

    def release(self, *, force: bool, reason: str) -> GpuReleaseResult:
        self.asked.append((force, reason))
        return GpuReleaseResult("face-worker", self.outcome, "detail")


class _Detector:
    def __init__(self, worker: _Worker) -> None:
        self.worker = worker


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GPU_RELEASE_API_TOKEN", TOKEN)
    monkeypatch.setattr(face_detect, "_default_detector", None)
    return TestClient(api_app)


def _with_worker(monkeypatch: pytest.MonkeyPatch, outcome: str) -> _Worker:
    worker = _Worker(outcome)
    monkeypatch.setattr(face_detect, "running_detector", lambda: _Detector(worker))
    return worker


def test_without_the_token_nothing_is_said_or_released(client: TestClient) -> None:
    assert client.get("/gpu/holders").status_code == 403
    assert client.post("/gpu/release", json={"items": "all", "reason": "x"}).status_code == 403
    wrong = {"Authorization": "Bearer nope"}
    assert client.get("/gpu/holders", headers=wrong).status_code == 403


def test_without_a_configured_token_it_is_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GPU_RELEASE_API_TOKEN")
    assert client.get("/gpu/holders", headers=AUTH).status_code == 503


def test_holders_without_a_detector_is_empty_and_builds_none(client: TestClient) -> None:
    assert client.get("/gpu/holders", headers=AUTH).json() == {"holders": []}
    assert face_detect._default_detector is None


def test_holders_name_the_face_worker(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_worker(monkeypatch, "released")
    assert client.get("/gpu/holders", headers=AUTH).json() == {"holders": [HOLDER]}


@pytest.mark.parametrize(("outcome", "status"), [
    ("released", 200), ("not_running", 200), ("busy", 409), ("failed", 500),
])
def test_release_answers_one_result_with_the_status_gpu_orc_reads(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, outcome: str, status: int,
) -> None:
    worker = _with_worker(monkeypatch, outcome)
    answer = client.post("/gpu/release", headers=AUTH, json={"items": ["face-worker"], "force": True, "reason": "GLM lädt"})
    assert answer.status_code == status
    assert answer.json() == {"results": [{"item": "face-worker", "outcome": outcome, "detail": "detail"}]}
    assert worker.asked == [(True, "GLM lädt")]


def test_release_without_a_detector_is_not_running(client: TestClient) -> None:
    answer = client.post("/gpu/release", headers=AUTH, json={"items": "all", "reason": "GLM lädt"})
    assert answer.status_code == 200
    assert answer.json()["results"] == [{"item": "face-worker", "outcome": "not_running", "detail": "nothing runs"}]


@pytest.mark.parametrize("body", [
    {"items": ["tts"], "reason": "x"},
    {"items": ["face-worker", "face-worker"], "reason": "x"},
    {"items": [], "reason": "x"},
    {"items": "all", "reason": "   "},
    {"items": "all"},
    {"items": "all", "reason": "x", "force": "yes"},
    {"items": "all", "reason": "x", "extra": 1},
])
def test_a_body_gpu_orc_would_not_send_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> None:
    worker = _with_worker(monkeypatch, "released")
    assert client.post("/gpu/release", headers=AUTH, json=body).status_code == 422
    assert worker.asked == []
