"""Buttons of the status line: what a load button loads (main model of the settings, the describer
placement that fits next to what is loaded), unloading through llama-swap and the suites, and the two
endpoints with their refusals. No service is touched: llama-swap, the suites and the backend are fakes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from starlette.testclient import TestClient

import aifred.lib.api.service_status as status_api
from aifred.lib import service_control
from aifred.lib.api.app import api_app
from aifred.lib.auth import sign_username
from aifred.lib.browser_storage import USERNAME_COOKIE_NAME
from aifred.lib.gpu_release import GpuReleaseResult
from aifred.lib.i18n import t
from aifred.lib.service_control import ServiceControlRefused
from aifred.lib.service_status import ModelStatus, ServiceSnapshot

MAIN = "DeepSeek-vllm"
VISION = "Qwen3VL-4B"
PLACEMENT = "Qwen3VL-4B-visiond-gpu0"


def settings(monkeypatch: pytest.MonkeyPatch, backend_type: str, main: str) -> None:
    monkeypatch.setattr("aifred.lib.settings.persisted_settings", lambda: {"backend_type": backend_type})
    monkeypatch.setattr("aifred.lib.config.get_effective_model_from_settings", lambda agent: main)


# ---- what a load button loads ---------------------------------------------------------


def test_the_main_model_is_the_one_a_chat_request_would_load(monkeypatch: pytest.MonkeyPatch) -> None:
    settings(monkeypatch, "vllm", MAIN)
    assert service_control.main_model_to_load() == ("vllm", MAIN)


@pytest.mark.parametrize(
    ("backend_type", "main", "key"),
    [("ollama", MAIN, "service_control_not_llamaswap"), ("llamacpp", "", "service_control_no_main_model")],
)
def test_no_main_model_to_load_says_why(monkeypatch: pytest.MonkeyPatch, backend_type: str, main: str, key: str) -> None:
    settings(monkeypatch, backend_type, main)
    with pytest.raises(ServiceControlRefused) as refused:
        service_control.main_model_to_load()
    assert refused.value.key == key


def fake_describer(monkeypatch: pytest.MonkeyPatch, vision: str, fit: tuple[str | None, Any]) -> None:
    monkeypatch.setattr("aifred.lib.vision_routing.settings_model", lambda role: vision)
    monkeypatch.setattr("aifred.lib.vision_routing.fitting_visiond", lambda name: fit)


def test_the_describer_is_the_placement_that_fits_next_to_what_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_describer(monkeypatch, VISION, (PLACEMENT, SimpleNamespace(message="fits")))
    assert service_control.describer_to_load() == ("llamacpp", PLACEMENT)


@pytest.mark.parametrize(
    ("vision", "fit", "key", "params"),
    [
        ("", (None, None), "service_control_no_describer", {}),
        (VISION, (None, None), "service_control_describer_no_profile", {"model": VISION}),
        (VISION, (None, SimpleNamespace(message="GPU 0: 900 MiB frei")), "service_control_describer_does_not_fit",
         {"reason": "GPU 0: 900 MiB frei"}),
    ],
)
def test_a_describer_that_would_push_the_main_model_out_is_not_loaded(
    monkeypatch: pytest.MonkeyPatch, vision: str, fit: Any, key: str, params: dict[str, str],
) -> None:
    fake_describer(monkeypatch, vision, fit)
    with pytest.raises(ServiceControlRefused) as refused:
        service_control.describer_to_load()
    assert (refused.value.key, refused.value.values) == (key, params)


def test_loading_goes_through_the_backend_and_closes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    done: list[str] = []

    class Backend:
        async def load_model(self, model: str) -> float:
            done.append(f"load {model}")
            return 12.5

        async def close(self) -> None:
            done.append("close")

    created: list[str] = []

    def create(backend_type: str) -> Backend:
        created.append(backend_type)
        return Backend()

    monkeypatch.setattr("aifred.backends.BackendFactory.create", create)
    assert asyncio.run(service_control.load_llamaswap_model("vllm", MAIN)) == 12.5
    assert (created, done) == (["vllm"], [f"load {MAIN}", "close"])


# ---- unloading --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, text: str = "OK") -> None:
        self.status_code, self.text, self.ok = status, text, status < 400


def test_unloading_asks_llama_swap_for_exactly_this_model(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[str] = []

    def post(url: str, timeout: float) -> FakeResponse:
        posted.append(url)
        return FakeResponse(200)

    monkeypatch.setattr(service_control.requests, "post", post)
    service_control.unload_llamaswap_model(PLACEMENT)
    assert posted == [f"{service_control.LLAMASWAP_BASE_URL}/api/models/unload/{PLACEMENT}"]


def test_a_failed_unload_names_the_model_and_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_control.requests, "post", lambda url, timeout: FakeResponse(404, "model not found"))
    with pytest.raises(ServiceControlRefused) as refused:
        service_control.unload_llamaswap_model("gone")
    assert refused.value.key == "service_control_failed" and "404 model not found" in refused.value.values["error"]

    def refuse(url: str, timeout: float) -> None:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(service_control.requests, "post", refuse)
    with pytest.raises(ServiceControlRefused, match="refused"):
        service_control.unload_llamaswap_model("x")


@pytest.mark.parametrize(
    ("outcome", "key"),
    [("released", None), ("not_running", None), ("busy", "service_control_failed"), ("failed", "service_control_failed")],
)
def test_a_click_releases_the_suites_with_force_a_running_reading_included(
    monkeypatch: pytest.MonkeyPatch, outcome: str, key: str | None,
) -> None:
    result = [GpuReleaseResult("item", outcome, "detail")]
    stt_asked: list[tuple[bool, str]] = []
    tts_asked: list[tuple[list[str], bool]] = []

    def stt_release(force: bool, reason: str) -> list[GpuReleaseResult]:
        stt_asked.append((force, reason))
        return result

    def tts_release(items: list[str], force: bool, reason: str) -> list[GpuReleaseResult]:
        tts_asked.append((items, force))
        return result

    monkeypatch.setattr("aifred.lib.stt_client.get_default_client", lambda: SimpleNamespace(gpu_release=stt_release))
    monkeypatch.setattr("aifred.lib.tts_client.get_tts_client", lambda: SimpleNamespace(gpu_release=tts_release))
    for release in (service_control.release_stt_gpu(), service_control.release_tts_engine("qwen3local")):
        if key is None:
            asyncio.run(release)
        else:
            with pytest.raises(ServiceControlRefused) as refused:
                asyncio.run(release)
            assert refused.value.key == key
    assert stt_asked == [(True, service_control.RELEASE_REASON)]
    assert tts_asked == [(["qwen3local"], True)]


# ---- the endpoints ----------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    return test_client


def snapshot_with(busy: bool) -> SimpleNamespace:
    model = ModelStatus(MAIN, "llm", "ready", busy, None, True)
    return SimpleNamespace(snapshot=lambda: ServiceSnapshot(llm_reachable=True, models=[model]))


def test_unload_of_a_busy_model_is_refused_with_the_message_in_the_pages_language(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    unloaded: list[str] = []
    monkeypatch.setattr(status_api, "get_monitor", lambda: snapshot_with(busy=True))
    monkeypatch.setattr(status_api, "unload_llamaswap_model", unloaded.append)
    answer = client.post("/service-status/unload", json={"service": "llm", "item": MAIN})
    assert answer.status_code == 409
    assert answer.json()["detail"] == f"{MAIN} arbeitet gerade und bleibt geladen."
    assert unloaded == []


def test_unload_of_an_idle_model_goes_to_llama_swap(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    unloaded: list[str] = []
    monkeypatch.setattr(status_api, "get_monitor", lambda: snapshot_with(busy=False))
    monkeypatch.setattr(status_api, "unload_llamaswap_model", unloaded.append)
    answer = client.post("/service-status/unload", json={"service": "llm", "item": MAIN})
    assert (answer.status_code, unloaded) == (200, [MAIN])


@pytest.mark.parametrize("item", ["../../unload", "Other-Model"])
def test_unload_takes_only_a_model_the_status_line_lists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, item: str,
) -> None:
    unloaded: list[str] = []
    monkeypatch.setattr(status_api, "get_monitor", lambda: snapshot_with(busy=False))
    monkeypatch.setattr(status_api, "unload_llamaswap_model", unloaded.append)
    answer = client.post("/service-status/unload", json={"service": "llm", "item": item})
    assert (answer.status_code, answer.json()["detail"], unloaded) == (409, t("error_service_control_not_loaded", name=item), [])


def test_unload_of_stt_and_tts_goes_to_the_suites(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    async def stt() -> None:
        asked.append("stt")

    async def tts(engine: str) -> None:
        asked.append(f"tts {engine}")

    monkeypatch.setattr(status_api, "release_stt_gpu", stt)
    monkeypatch.setattr(status_api, "release_tts_engine", tts)
    assert client.post("/service-status/unload", json={"service": "stt", "item": ""}).status_code == 200
    assert client.post("/service-status/unload", json={"service": "tts", "item": "xtts"}).status_code == 200
    assert asked == ["stt", "tts xtts"]


def test_load_answers_at_once_and_loads_in_the_background(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[str, str]] = []

    async def load(backend_type: str, model: str) -> float:
        started.append((backend_type, model))
        return 0.0

    monkeypatch.setattr(status_api, "describer_to_load", lambda: ("llamacpp", PLACEMENT))
    monkeypatch.setattr(status_api, "load_llamaswap_model", load)
    answer = client.post("/service-status/load", json={"service": "describer"})
    assert answer.json() == {"status": "loading", "model": PLACEMENT}
    client.close()  # ends the portal's loop; the background task has run by then
    assert started == [("llamacpp", PLACEMENT)]


def test_a_describer_that_does_not_fit_is_refused_with_the_reason(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def does_not_fit() -> tuple[str, str]:
        raise ServiceControlRefused("service_control_describer_does_not_fit", reason="GPU 0: 900 MiB frei")

    monkeypatch.setattr(status_api, "describer_to_load", does_not_fit)
    answer = client.post("/service-status/load", json={"service": "describer"})
    assert answer.status_code == 409
    assert answer.json()["detail"] == t("error_service_control_describer_does_not_fit", reason="GPU 0: 900 MiB frei")


def test_a_failed_background_load_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.backends.base import BackendModelStartError

    async def fail(backend_type: str, model: str) -> float:
        raise BackendModelStartError("upstream command exited prematurely")

    logged: list[str] = []
    monkeypatch.setattr(status_api, "load_llamaswap_model", fail)
    monkeypatch.setattr(status_api, "log_message", lambda message, category="info": logged.append(message))
    asyncio.run(status_api._load("vllm", MAIN))
    assert len(logged) == 1 and MAIN in logged[0] and "exited prematurely" in logged[0]
