"""GPU-Orc off: an empty ``GPU_ORC_URL`` means no call to GPU-Orc anywhere on this machine, no warning and no
red light. Each test lets a working GPU-Orc stand ready, so a path that forgets the switch is caught by it
being asked."""

from __future__ import annotations

import asyncio

import pytest
from fake_gpu_orc import FakeGpuOrc, install_fake_gpu_orc

from aifred.lib import (
    gpu_orc_client,
    gpu_orc_work,
    nvidia_smi,
    server_startup,
    vision_routing,
    vision_vram_check,
    vlm_vram_cache,
)
from aifred.lib.calibration import llamaswap_io
from aifred.lib.calibration.service import _free_cards_for_calibration

PROFILE = "Qwen3VL-4B-Instruct-Q8_0-visiond"


@pytest.fixture
def standing_by(monkeypatch: pytest.MonkeyPatch) -> FakeGpuOrc:
    """A GPU-Orc that answers, and GPU-Orc switched off nevertheless."""
    fake = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, fake)
    monkeypatch.setattr(gpu_orc_client, "GPU_ORC_URL", "")
    return fake


def test_there_is_no_client(standing_by: FakeGpuOrc) -> None:
    assert gpu_orc_client.get_gpu_orc_client() is None


def test_clearing_the_cards_for_a_model_asks_nothing_and_says_nothing(
    standing_by: FakeGpuOrc, monkeypatch: pytest.MonkeyPatch
) -> None:
    said: list[str] = []
    monkeypatch.setattr(gpu_orc_client, "log_message", lambda message, category="info": said.append(message))
    gpu_orc_client.clear_cards_for("m", reason="r")
    vision_routing.release_side_channels_for_describer("m")
    assert standing_by.requests == [] and said == []


def test_the_calibration_says_once_that_the_cards_were_not_cleared(standing_by: FakeGpuOrc) -> None:
    said: list[str] = []
    _free_cards_for_calibration(said.append)
    assert standing_by.requests == []
    assert said == ["   ℹ️ GPU-Orc is off (GPU_ORC_URL is empty): the cards were not cleared for the calibration"]


def test_the_traffic_light_is_green(standing_by: FakeGpuOrc) -> None:
    standing_by.loading = ["Something-Big"]
    assert vision_routing.load_in_progress() is None
    assert standing_by.requests == []


def test_the_describer_room_is_the_free_vram_of_nvidia_smi(
    standing_by: FakeGpuOrc, monkeypatch: pytest.MonkeyPatch
) -> None:
    standing_by.cards = [{"uuid": "GPU-a", "holders": [{"consumer": "tts-suite", "item": "xtts", "memory_mib": 9000}]}]
    monkeypatch.setattr(
        llamaswap_io, "parse_llamaswap_config",
        lambda _path: {PROFILE: {"current_context": 24576, "env": {"CUDA_VISIBLE_DEVICES": "GPU-a"}}},
    )
    monkeypatch.setattr(vlm_vram_cache, "get", lambda _model, _ctx: 8988)
    monkeypatch.setattr(nvidia_smi, "query", lambda _fields: [{"index": "0", "uuid": "GPU-a", "memory.free": "4000"}])
    monkeypatch.setattr(vision_routing, "loaded_llamaswap_profiles", list)
    result = vision_vram_check.check_visiond_fits(PROFILE)
    assert not result.fits and result.free_mb == 4000  # the 9000 MiB of the TTS worker are not counted
    assert standing_by.requests == []


def test_no_work_is_reported(standing_by: FakeGpuOrc, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_orc_work, "running_pipeline_count", lambda: 1)  # a turn runs

    async def scenario() -> None:
        async with server_startup.gpu_orc_work_lifespan():
            await asyncio.sleep(0.1)

    assert gpu_orc_work.make_work_reporter() is None
    asyncio.run(scenario())
    assert standing_by.requests == []
