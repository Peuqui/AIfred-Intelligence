"""Preloading the main model takes the load path of its request (load_model), on llama.cpp and vLLM
alike; the research scraper preloads on every backend while the pages are scraped."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from aifred.backends.base import BackendModelStartError, OpenAICompatibleBackend
from aifred.backends.llamacpp import LlamaCppBackend
from aifred.backends.vllm import vLLMBackend
from aifred.lib.research import scraper_orchestrator

MODEL = "DeepSeek-vllm"


@pytest.mark.parametrize("backend_class", [vLLMBackend, LlamaCppBackend])
def test_preload_is_the_load_of_the_request(monkeypatch: pytest.MonkeyPatch, backend_class: type[OpenAICompatibleBackend]) -> None:
    assert backend_class.preload_model is OpenAICompatibleBackend.preload_model
    backend = backend_class.__new__(backend_class)
    checked: list[str] = []

    async def pre_request_check(model: str) -> float:
        checked.append(model)
        return 254.6

    monkeypatch.setattr(backend, "_pre_request_check", pre_request_check)
    assert asyncio.run(backend.preload_model(MODEL, num_ctx=500_000)) == (True, 254.6)
    assert checked == [MODEL]


def test_a_failed_preload_is_logged_and_reported_as_false(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = vLLMBackend.__new__(vLLMBackend)

    async def fail(model: str) -> float:
        raise BackendModelStartError("upstream command exited prematurely")

    logged: list[str] = []
    monkeypatch.setattr(backend, "_pre_request_check", fail)
    monkeypatch.setattr("aifred.lib.logging_utils.log_message", lambda message, category="info": logged.append(message))
    success, _ = asyncio.run(backend.preload_model(MODEL))
    assert success is False
    assert len(logged) == 1 and "exited prematurely" in logged[0]


def scrape(monkeypatch: pytest.MonkeyPatch, backend_type: str, load_time: float) -> tuple[list[str], list[str]]:
    """Run the scraper over two pages; returns the preloaded models and the debug lines."""
    preloaded: list[str] = []

    async def preload_model(model: str, num_ctx: int | None = None) -> tuple[bool, float]:
        preloaded.append(model)
        return True, load_time

    backend = SimpleNamespace(preload_model=preload_model)
    llm_client = SimpleNamespace(backend_type=backend_type, _get_backend=lambda: backend)
    monkeypatch.setattr(
        scraper_orchestrator, "scrape_webpage", lambda url: {"success": True, "word_count": 900, "url": url},
    )

    async def run() -> list[dict[str, Any]]:
        stream = scraper_orchestrator.orchestrate_scraping(["https://a.test", "https://b.test"], llm_client, MODEL, 8192)
        return [item async for item in stream]

    items = asyncio.run(run())
    return preloaded, [item["message"] for item in items if item["type"] == "debug"]


@pytest.mark.parametrize("backend_type", ["vllm", "llamacpp", "ollama"])
def test_the_scraper_preloads_on_every_backend(monkeypatch: pytest.MonkeyPatch, backend_type: str) -> None:
    preloaded, debug = scrape(monkeypatch, backend_type, load_time=254.6)
    assert preloaded == [MODEL]
    assert any(line.startswith("✅ AIfred-LLM preloaded") for line in debug)


def test_a_model_that_runs_already_is_said_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _, debug = scrape(monkeypatch, "vllm", load_time=0.0)
    assert "ℹ️ AIfred-LLM already loaded" in debug
