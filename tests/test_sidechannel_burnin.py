"""Joint VLM + TTS burn-in: the TTS engine is started on the side-channel card, and a failed TTS
run gives no verdict (the peak would show the VLM alone)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aifred.lib import (
    nvidia_smi,
    sidechannel_burnin,
    sidechannel_vram_cache,
    tts_stress_burnin,
    vision_prewarm,
)


@pytest.fixture
def card(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    stored: list[tuple[Any, ...]] = []
    monkeypatch.setattr(sidechannel_burnin, "_resolve_side_channel_gpu_index", lambda: 4)
    monkeypatch.setattr(sidechannel_burnin, "_query_gpu_used_mb", lambda gpu: 9000)
    monkeypatch.setattr(nvidia_smi, "query", lambda fields, gpu_index=None: [{"memory.total": "32768"}])
    monkeypatch.setattr(vision_prewarm, "get_active_vlm_model", lambda: "")
    monkeypatch.setattr(sidechannel_vram_cache, "put", lambda *args: stored.append(args))
    return stored


def _tts_run(monkeypatch: pytest.MonkeyPatch, peak: int | None) -> list[str | None]:
    cards: list[str | None] = []

    async def fake(engine_key: str, *, gpu: str | None = None, debug: Any = None, iterations: Any = None) -> int | None:
        cards.append(gpu)
        return peak

    monkeypatch.setattr(tts_stress_burnin, "stress_burnin_tts", fake)
    return cards


def test_the_tts_engine_runs_on_the_side_channel_card(monkeypatch: pytest.MonkeyPatch, card: list[Any]) -> None:
    cards = _tts_run(monkeypatch, 5000)
    result = asyncio.run(sidechannel_burnin.burnin_sidechannel("xtts"))
    assert cards == ["4"]
    assert result is not None and result["gpu_index"] == 4 and result["joint_peak_mb"] == 9000 and result["fits"]
    assert card == [("", "xtts", 9000, 4, 32768, True)]


def test_a_failed_tts_run_gives_no_verdict_and_stores_nothing(monkeypatch: pytest.MonkeyPatch, card: list[Any]) -> None:
    _tts_run(monkeypatch, None)
    assert asyncio.run(sidechannel_burnin.burnin_sidechannel("xtts")) is None
    assert card == []
