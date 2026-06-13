"""calibrate_with_ai resolves provider + endpoint + key via the shared
cloud_api SSOT (CLOUD_API_PROVIDERS / get_cloud_api_key), not a hardcoded
DashScope path. The early guards must fail honestly (no silent default,
no algorithm fallback) when the calibration agent is misconfigured.
"""

from __future__ import annotations

import asyncio

import aifred.backends.cloud_api as cloud_api
import aifred.lib.agent_config as agent_config
import aifred.lib.calibration.ai_agent as ai_agent


def _drain(gen) -> list[str]:
    async def run() -> list[str]:
        return [x async for x in gen]
    return asyncio.run(run())


def _set_calibration(monkeypatch, **cfg) -> None:
    monkeypatch.setattr(
        agent_config, "load_agents_raw", lambda: {"calibration": cfg}
    )


def _run() -> list[str]:
    return _drain(ai_agent.calibrate_with_ai(
        model_id="m", full_cmd="--model x --port 1", safety_margin_mb=192,
    ))


def test_unknown_provider_errors(monkeypatch):
    _set_calibration(monkeypatch, cloud_provider="bogus", model="x")
    out = _run()
    assert out[-1].startswith("__AI_ERROR__")
    assert "Unknown cloud provider" in out[-1]


def test_missing_model_errors(monkeypatch):
    _set_calibration(monkeypatch, cloud_provider="qwen", model="")
    out = _run()
    assert out[-1].startswith("__AI_ERROR__")
    assert "No calibration model configured" in out[-1]


def test_missing_key_errors(monkeypatch):
    _set_calibration(monkeypatch, cloud_provider="qwen", model="qwen-plus")
    monkeypatch.setattr(cloud_api, "get_cloud_api_key", lambda provider: None)
    out = _run()
    assert out[-1].startswith("__AI_ERROR__")
    assert "API key missing" in out[-1]


def test_provider_drives_endpoint(monkeypatch):
    # A non-default provider (deepseek) must reach the AI client with ITS
    # base_url, proving the endpoint is no longer hardcoded to DashScope.
    _set_calibration(monkeypatch, cloud_provider="deepseek", model="deepseek-chat")
    monkeypatch.setattr(cloud_api, "get_cloud_api_key", lambda provider: "fake-key")
    monkeypatch.setattr(ai_agent, "kill_orphan_on_port",
                        lambda *a, **k: asyncio.sleep(0))
    captured: dict = {}
    import openai

    class _CaptureClient:
        def __init__(self, *a, **k):
            captured.update(k)
            raise RuntimeError("stop after client construction")
    monkeypatch.setattr(openai, "AsyncOpenAI", _CaptureClient)

    async def run() -> None:
        try:
            async for _ in ai_agent.calibrate_with_ai(
                model_id="m", full_cmd="--model x --port 1",
                safety_margin_mb=192,
            ):
                pass
        except Exception:  # noqa: BLE001 — _CaptureClient stops the loop
            pass
    asyncio.run(run())

    from aifred.lib.config import CLOUD_API_PROVIDERS
    assert captured.get("base_url") == CLOUD_API_PROVIDERS["deepseek"]["base_url"]
