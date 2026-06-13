"""calibrate_with_ai: the caller can pin the active GPU set.

Paket 1 of the AI-calibration speed support: when ``gpus`` is passed, the
loop must NOT re-enumerate (so the speed variant can lock to the fastest N
cards); when it's omitted, enumerate_gpus() is used as before.
"""

from __future__ import annotations

import asyncio

import aifred.lib.calibration.ai_agent as ai_agent
from aifred.lib.calibration.types import GPU


def _gpu(uuid: str, name: str, cc: float, total: int) -> GPU:
    return GPU(uuid=uuid, name=name, compute_cap=cc, total_mb=total,
               free_mb=total, speed_class=0, first_in_class=True)


def _drain(gen) -> list[str]:
    # The OpenAI loop is cut short on purpose (see _common_mocks) — that
    # raises AFTER the gpus/enumerate decision we want to observe, so we
    # swallow it and inspect the marker.
    async def run() -> list[str]:
        out: list[str] = []
        try:
            async for item in gen:
                out.append(item)
        except Exception:  # noqa: BLE001
            pass
        return out
    return asyncio.run(run())


def _common_mocks(monkeypatch, enum_marker: dict):
    # Provider + model + key present so we get past the early guards to the
    # GPU step. These resolve through the cloud_api SSOT now, not broker.
    import aifred.lib.agent_config as agent_config
    import aifred.backends.cloud_api as cloud_api
    monkeypatch.setattr(
        agent_config, "load_agents_raw",
        lambda: {"calibration": {"cloud_provider": "qwen", "model": "qwen-plus"}},
    )
    monkeypatch.setattr(cloud_api, "get_cloud_api_key", lambda provider: "fake-key")
    monkeypatch.setattr(ai_agent, "kill_orphan_on_port",
                        lambda *a, **k: asyncio.sleep(0))

    def fake_enum():
        enum_marker["called"] = True
        return [_gpu("GPU-enum", "RTX 8000", 7.5, 49152)]
    monkeypatch.setattr(ai_agent, "enumerate_gpus", fake_enum)

    # Kill the OpenAI loop right after GPU resolution: importing openai
    # inside the function and constructing AsyncOpenAI raises → __AI_ERROR__,
    # but only AFTER the gpus/enumerate decision we want to observe.
    import openai

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("stop after gpu resolution")
    monkeypatch.setattr(openai, "AsyncOpenAI", _Boom)


def test_passed_gpus_skip_enumerate(monkeypatch):
    marker = {"called": False}
    _common_mocks(monkeypatch, marker)
    pinned = [_gpu("GPU-pinned", "V100", 7.0, 32768)]
    _drain(ai_agent.calibrate_with_ai(
        model_id="m", full_cmd="--model x --port 1", safety_margin_mb=192,
        gpus=pinned,
    ))
    assert marker["called"] is False  # caller's list used, no re-enumerate


def test_no_gpus_enumerates(monkeypatch):
    marker = {"called": False}
    _common_mocks(monkeypatch, marker)
    _drain(ai_agent.calibrate_with_ai(
        model_id="m", full_cmd="--model x --port 1", safety_margin_mb=192,
    ))
    assert marker["called"] is True  # default path enumerates as before
