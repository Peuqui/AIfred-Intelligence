"""Before a load AIfred asks GPU-Orc to clear the model's cards; the load goes on whatever it answers."""

import asyncio
from typing import Any, ClassVar, Self

import httpx
import pytest
from fake_gpu_orc import FakeGpuOrc, install_fake_gpu_orc

from aifred.backends.base import BackendModelStartError
from aifred.backends.vllm import vLLMBackend
from aifred.lib import gpu_orc_client, service_status
from aifred.lib.config import GPU_ORC_MODEL_SERVER
from aifred.lib.service_status import LoadedModel


class _Response:
    def __init__(self, payload: dict[str, Any], status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    """Stands in for httpx.AsyncClient: records the loads through /upstream/<model>/health. What
    /running lists is ``running`` (see ``_run``)."""

    running: ClassVar[list[str]] = []
    loaded: ClassVar[list[str]] = []
    load_answer = _Response({})

    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str) -> _Response:
        self.loaded.append(url.split("/upstream/", 1)[1].removesuffix("/health"))
        return self.load_answer


@pytest.fixture
def logged(monkeypatch) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    monkeypatch.setattr(gpu_orc_client, "log_message", lambda message, category="info": lines.append((category, message)))
    return lines


def _backend(monkeypatch: pytest.MonkeyPatch, running: list[str]) -> vLLMBackend:
    _Client.running, _Client.loaded = running, []
    _Client.load_answer = _Response({})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        service_status, "running_models", lambda base_url: [LoadedModel(name, "ready", 900) for name in _Client.running],
    )
    backend = vLLMBackend.__new__(vLLMBackend)
    backend.base_url = "http://swap/v1"
    return backend


def _load(monkeypatch: pytest.MonkeyPatch, models: list[str], running: list[str]) -> None:
    backend = _backend(monkeypatch, running)
    for model in models:
        asyncio.run(backend._pre_request_check(model))


def _free_for(model: str) -> dict[str, str]:
    return {"model": model, "model_server": GPU_ORC_MODEL_SERVER, "reason": f"AIfred: loading '{model}'"}


def test_the_guard_asks_gpu_orc_for_the_model_before_every_load(monkeypatch) -> None:
    # Same model twice while it is not running = a re-load after its ttl expired: a worker may have taken
    # the card meanwhile, so both loads ask.
    fake = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, fake)
    _load(monkeypatch, ["pp4-vllm", "pp4-vllm"], [])
    assert fake.frees == [_free_for("pp4-vllm"), _free_for("pp4-vllm")]
    assert _Client.loaded == ["pp4-vllm", "pp4-vllm"]


def test_the_guard_waits_as_long_as_gpu_orc_says(monkeypatch) -> None:
    fake = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, fake)
    _load(monkeypatch, ["pp4-vllm"], [])
    assert fake.free_timeouts == [690]


def test_no_load_no_question(monkeypatch) -> None:
    fake = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, fake)
    _load(monkeypatch, ["pp4-vllm"], ["pp4-vllm"])
    assert fake.frees == []
    assert _Client.loaded == []


def test_what_gpu_orc_released_is_logged(monkeypatch, logged) -> None:
    fake = FakeGpuOrc()
    fake.actions = [
        {"consumer": "stt-suite", "item": "gpu-worker", "outcome": "released", "waited_s": 0.4},
        {"consumer": "llama-swap", "item": "vl4b-visiond", "outcome": "kept"},
    ]
    install_fake_gpu_orc(monkeypatch, fake)
    _load(monkeypatch, ["pp4-vllm"], [])
    assert logged == [("info", "🧹 GPU-Orc: stt-suite gpu-worker released — AIfred: loading 'pp4-vllm'")]


def test_cards_that_stay_held_are_logged_loudly_and_the_load_goes_on(monkeypatch, logged) -> None:
    fake = FakeGpuOrc()
    fake.free = False
    fake.actions = [{"consumer": "tts-suite", "item": "xtts", "outcome": "refused_busy", "detail": "still busy"}]
    install_fake_gpu_orc(monkeypatch, fake)
    _load(monkeypatch, ["pp4-vllm"], [])
    assert _Client.loaded == ["pp4-vllm"]
    assert logged == [
        ("warning", "⚠️ GPU-Orc: tts-suite xtts refused_busy (still busy) — AIfred: loading 'pp4-vllm'"),
        ("warning", "⚠️ GPU-Orc: the cards are not free — AIfred: loading 'pp4-vllm'"),
    ]


def test_a_gpu_orc_that_does_not_answer_is_logged_loudly_and_the_load_goes_on(monkeypatch, logged) -> None:
    # conftest points the client at a dead address
    _load(monkeypatch, ["pp4-vllm"], [])
    assert _Client.loaded == ["pp4-vllm"]
    assert len(logged) == 1 and logged[0][0] == "warning"
    assert "GPU-Orc could not clear the cards for 'pp4-vllm'" in logged[0][1]


def test_a_refused_request_names_gpu_orcs_reason(monkeypatch, logged) -> None:
    fake = FakeGpuOrc()
    fake.refusal = (400, "model 'nope' is not in the config of llama-swap")
    install_fake_gpu_orc(monkeypatch, fake)
    _load(monkeypatch, ["nope"], [])
    assert _Client.loaded == ["nope"]
    assert "HTTP 400" in logged[0][1] and "not in the config of llama-swap" in logged[0][1]


def test_load_time_is_measured_only_for_a_load(monkeypatch) -> None:
    install_fake_gpu_orc(monkeypatch, FakeGpuOrc())
    backend = _backend(monkeypatch, ["pp4-vllm"])
    assert asyncio.run(backend._pre_request_check("pp4-vllm")) == 0.0
    _Client.running = []
    assert asyncio.run(backend._pre_request_check("pp4-vllm")) > 0.0


def test_a_failed_load_names_the_upstream_exit(monkeypatch) -> None:
    install_fake_gpu_orc(monkeypatch, FakeGpuOrc())
    backend = _backend(monkeypatch, [])
    _Client.load_answer = _Response({}, 502, "upstream command exited prematurely")

    async def cause() -> str:
        return "ValueError: no GPU"

    monkeypatch.setattr(backend, "_upstream_exit_cause", cause)
    with pytest.raises(BackendModelStartError, match="ValueError: no GPU"):
        asyncio.run(backend._pre_request_check("pp4-vllm"))
