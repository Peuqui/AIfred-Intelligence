"""Switching the backend and the cloud provider, restarting the backend's service (lib/backend_switch.py,
api/backend.py, api/system.py): saved models the new backend lacks are forgotten (AIfred's cleared, the others run on
his), the vision model falls back to the first one, leaving a backend frees what it holds, llama-swap starts only on a
switch, a switch is refused for a backend the cards cannot run, during another switch and during a calibration.
settings.json, the model caches and the llama-swap file are files of their own; the catalogs, Ollama, systemctl and the
GPUs are stand-ins."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import backend_switch, calibration_gate, model_vram_cache, settings
from aifred.lib.agent_tuning import default_agent_tuning
from aifred.lib.backend_switch import choose_cloud_main_model, choose_vision_model, drop_unavailable_models
from aifred.lib.gpu_detection import GPUInfo
from aifred.lib.runtime_settings import RuntimeSettings
from aifred.lib.user_error import Invalid, NotDone

BIG, SMALL, EYES = "Big-30B", "Small-4B", "Eyes-4B-VL"


def _view(backend_type: str = "llamacpp") -> RuntimeSettings:
    tuning = default_agent_tuning()
    tuning["aifred"].model_id = BIG
    tuning["sokrates"].model_id = SMALL
    tuning["vision"].model_id = EYES
    return RuntimeSettings(
        backend_type=backend_type, backend_url="http://llama-swap", ui_language="de", enable_tts=False,
        automatik_model_id=SMALL, agent_tuning=tuning, tts_autoplay=False, tts_agents={}, max_debate_rounds=3,
        consensus_type="majority",
    )


# ── The rules ────────────────────────────────────────────────────────────


def test_models_the_backend_lacks_are_forgotten() -> None:
    view = _view()
    notes = drop_unavailable_models(view, {BIG: "Big"})
    assert view.agent_tuning["aifred"].model_id == BIG
    assert view.agent_tuning["sokrates"].model_id == ""  # runs on AIfred's now
    assert view.automatik_model_id == ""
    assert view.agent_tuning["vision"].model_id == EYES  # the Vision agent has its own rule
    assert len(notes) == 2
    drop_unavailable_models(view, {SMALL: "Small"})
    assert view.agent_tuning["aifred"].model_id == ""  # cleared: the user picks one


def test_an_empty_catalog_forgets_nothing() -> None:
    view = _view()
    assert drop_unavailable_models(view, {}) == []
    assert (view.agent_tuning["aifred"].model_id, view.automatik_model_id) == (BIG, SMALL)


def test_the_vision_model_falls_back_to_the_first_one() -> None:
    view = _view()
    assert choose_vision_model(view, {EYES: "Eyes", "Other-VL": "Other"}) == []
    choose_vision_model(view, {"Other-VL": "Other (4 GB)", "Third-VL": "Third"})
    assert (view.agent_tuning["vision"].model_id, view.agent_tuning["vision"].model) == ("Other-VL", "Other (4 GB)")
    assert choose_vision_model(view, {}) == []  # no vision model: it keeps what it has
    assert view.agent_tuning["vision"].model_id == "Other-VL"


def test_a_new_cloud_provider_gives_aifred_its_first_model_unless_it_offers_his() -> None:
    view = _view("cloud_api")
    assert choose_cloud_main_model(view, {BIG: BIG, "z": "z"}) == []
    choose_cloud_main_model(view, {"qwen-max": "qwen-max", "qwen-plus": "qwen-plus"})
    assert view.agent_tuning["aifred"].model_id == "qwen-max"


# ── Leaving a backend, its catalog ──────────────────────────────────────


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """systemctl, Ollama and the catalogs as stand-ins; what was asked of them is in ``calls``."""
    refused: set[str] = set()  # actions systemctl fails
    world: dict[str, Any] = {"calls": [], "catalog": {"llamacpp": {BIG: "Big (30 GB)", SMALL: "Small"}, "vllm": {"Big-vllm": "Big vLLM"}, "ollama": {}},
                             "up": True, "refused": refused}

    def systemctl(action: str) -> Any:
        def run(*_args: Any) -> bool:
            world["calls"].append(action)
            if action == "start":
                world["up"] = True
            return action not in refused
        return run

    monkeypatch.setattr("aifred.lib.process_utils.stop_llama_swap", systemctl("stop"))
    monkeypatch.setattr("aifred.lib.process_utils.start_llama_swap", systemctl("start"))
    monkeypatch.setattr("aifred.lib.process_utils.restart_llama_swap", systemctl("restart"))
    monkeypatch.setattr("aifred.lib.process_utils.restart_service", lambda name, check=False: systemctl(f"restart {name}")())
    monkeypatch.setattr(backend_switch, "LLAMASWAP_START_SETTLE_S", 0)
    monkeypatch.setattr(backend_switch, "BACKEND_READY_POLL_S", 0.01)

    def discover(backend_type: str, _url: str) -> dict[str, str]:
        return dict(world["catalog"][backend_type]) if world["up"] or backend_type == "ollama" else {}

    monkeypatch.setattr("aifred.lib.model_discovery.discover_models", discover)

    class _Ollama:
        async def unload_all_models(self) -> tuple[bool, list[str]]:
            world["calls"].append("ollama unload")
            return True, ["qwen3:8b"]

        async def close(self) -> None:
            pass

    monkeypatch.setattr("aifred.backends.ollama.OllamaBackend", _Ollama)
    return world


def test_leaving_a_backend_frees_what_it_holds(services: dict[str, Any]) -> None:
    assert asyncio.run(backend_switch.leave_backend("llamacpp", "vllm")) == ["ℹ️ llama-swap keeps running (same service)"]
    assert services["calls"] == []
    asyncio.run(backend_switch.leave_backend("vllm", "ollama"))
    asyncio.run(backend_switch.leave_backend("ollama", "llamacpp"))
    assert asyncio.run(backend_switch.leave_backend("cloud_api", "llamacpp")) == []
    assert services["calls"] == ["stop", "ollama unload"]


def test_llama_swap_starts_only_when_asked(services: dict[str, Any]) -> None:
    services["up"] = False
    catalog, _notes = asyncio.run(backend_switch.model_catalog("llamacpp", "qwen"))
    assert catalog == {} and services["calls"] == []  # a look starts nothing
    catalog, notes = asyncio.run(backend_switch.model_catalog("llamacpp", "qwen", start_service=True))
    assert list(catalog) == [BIG, SMALL] and services["calls"] == ["start"]
    assert notes[-1] == "📂 Found 2 llama.cpp models"


def test_a_cloud_provider_without_key_lists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aifred.backends.cloud_api.get_cloud_api_key", lambda _provider: None)
    catalog, notes = asyncio.run(backend_switch.model_catalog("cloud_api", "qwen"))
    assert catalog == {} and "DASHSCOPE_API_KEY" in notes[0]


# ── The switch through the API ───────────────────────────────────────────


def _gpu() -> GPUInfo:
    return GPUInfo(name="V100", compute_capability=7.0, vram_mb=32768, has_tensor_cores=True, supports_fast_fp16=True,
                   recommended_backends=["ollama", "llamacpp", "vllm"], unsupported_backends=[], warnings=[],
                   total_vram_mb=32768, all_gpu_names=["V100"], all_gpu_vram_mb=[32768], all_gpu_compute_caps=[7.0])


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, services: dict[str, Any]) -> Iterator[tuple[TestClient, Path]]:
    from aifred.lib import agent_choices
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    file = tmp_path / "settings.json"
    file.write_text(json.dumps({
        "backend_type": "llamacpp", "cloud_api_provider": "qwen", "user_name": "Peter",
        "backend_models": {
            "llamacpp": {"aifred": BIG, "sokrates": SMALL, "automatik": "", "vision": EYES},
            "vllm": {"aifred": "Big-vllm", "sokrates": "Gone-vllm", "automatik": "Gone-vllm", "vision": EYES},
        },
    }))
    monkeypatch.setattr(settings, "SETTINGS_FILE", file)
    monkeypatch.setattr(model_vram_cache, "CACHE_FILE", tmp_path / "model_vram_cache.json")
    monkeypatch.setattr(model_vram_cache, "_cache", None)
    for module in ("config", "operating_points", "runtime_settings"):
        monkeypatch.setattr(f"aifred.lib.{module}.LLAMASWAP_CONFIG_PATH", tmp_path / "llama-swap.yaml")
    monkeypatch.setattr("aifred.lib.gpu_detection.server_gpu_info", _gpu)
    monkeypatch.setattr(agent_choices, "chat_agents", lambda: [("aifred", "AIfred", "🎩"), ("sokrates", "Sokrates", "🏛️")])

    async def vision_candidates(_target: object, _catalog: dict[str, str], _url: str) -> dict[str, str]:
        return {EYES: "Eyes (4 GB)"}

    monkeypatch.setattr(agent_choices, "vision_candidates", vision_candidates)
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    yield test_client, file


def test_a_switch_keeps_the_new_backends_models_it_offers(client: tuple[TestClient, Path], services: dict[str, Any]) -> None:
    test_client, file = client
    answer = test_client.put("/backend", json={"backend_type": "vllm"})
    assert answer.status_code == 200 and answer.json()["backend_type"] == "vllm"
    saved = json.loads(file.read_text())
    assert saved["backend_type"] == "vllm"
    assert saved["backend_models"]["vllm"]["aifred"] == "Big-vllm"
    assert saved["backend_models"]["vllm"]["sokrates"] == ""  # not in vLLM's catalog: runs on AIfred's
    assert saved["backend_models"]["vllm"]["automatik"] == ""
    assert saved["backend_models"]["llamacpp"]["sokrates"] == SMALL  # the old backend's choice stays for a return
    assert saved["user_name"] == "Peter"  # every other key stays
    assert services["calls"] == []  # within llama-swap's family nothing stops


def test_leaving_llama_swaps_family_stops_it(client: tuple[TestClient, Path], services: dict[str, Any]) -> None:
    test_client, file = client
    assert test_client.put("/backend", json={"backend_type": "ollama"}).status_code == 200
    assert services["calls"] == ["stop"]
    assert json.loads(file.read_text())["backend_type"] == "ollama"


def test_a_switch_is_refused_when_it_cannot_or_must_not_run(client: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, file = client
    assert test_client.put("/backend", json={"backend_type": "tpu"}).status_code == 422
    monkeypatch.setattr(calibration_gate, "_active", True)
    refused = test_client.put("/backend", json={"backend_type": "vllm"})
    assert refused.status_code == 409 and "Kalibrierung" in refused.json()["detail"]
    monkeypatch.setattr(calibration_gate, "_active", False)
    monkeypatch.setattr(backend_switch, "_switching", True)
    assert test_client.put("/backend", json={"backend_type": "vllm"}).status_code == 409
    assert json.loads(file.read_text())["backend_type"] == "llamacpp"
    assert test_client.put("/backend", json={"backend": "vllm"}).status_code == 422  # unknown field


def test_a_failed_switch_frees_the_next_one(client: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, _file = client
    monkeypatch.setattr(settings, "update_settings", lambda _changes: False)
    assert test_client.put("/backend", json={"backend_type": "vllm"}).status_code == 500
    assert backend_switch._switching is False


def test_the_cloud_provider_is_remembered_and_checked(client: tuple[TestClient, Path]) -> None:
    test_client, file = client
    assert test_client.put("/backend/cloud-provider", json={"provider": "claude"}).json()["cloud_provider"] == "claude"
    saved = json.loads(file.read_text())
    assert (saved["cloud_api_provider"], saved["backend_type"]) == ("claude", "llamacpp")  # the models stay: no cloud
    assert test_client.put("/backend/cloud-provider", json={"provider": "acme"}).status_code == 422


# ── Restarting the backend's service ─────────────────────────────────────


def test_a_restart_waits_until_the_service_answers(services: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([False, False, True])

    async def answering(_url: str) -> bool:
        return next(answers)

    monkeypatch.setattr(backend_switch, "_answers", answering)
    seconds = asyncio.run(backend_switch.restart_backend("vllm"))
    assert seconds is not None and services["calls"] == ["restart"]
    with pytest.raises(Invalid):
        asyncio.run(backend_switch.restart_backend("cloud_api"))
    services["refused"].add("restart ollama")
    with pytest.raises(NotDone):
        asyncio.run(backend_switch.restart_backend("ollama"))


def test_a_restart_that_does_not_answer_in_time_says_so(services: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def silent(_url: str) -> bool:
        return False

    monkeypatch.setattr(backend_switch, "_answers", silent)
    monkeypatch.setattr(backend_switch, "OLLAMA_READY_TIMEOUT_S", 0.05)
    assert asyncio.run(backend_switch.restart_backend("ollama")) is None
