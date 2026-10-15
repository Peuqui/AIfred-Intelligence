"""check_visiond_fits: Burn-in-Peak + Reserve gegen den freien VRAM der Karten des -visiond-Profils."""

import pytest

from aifred.lib import (
    nvidia_smi,
    tts_service,
    vision_routing,
    vision_vram_check,
    vlm_vram_cache,
)
from aifred.lib.calibration import llamaswap_io

PROFILE = "Qwen3VL-4B-Instruct-Q8_0-visiond"
PINNED = "GPU-aaaa"
OTHER = "GPU-bbbb"


@pytest.fixture
def setup(monkeypatch):
    def configure(*, env: dict, peak: int | None, free: dict[str, int], loaded: list[str] | None = None):
        monkeypatch.setattr(
            llamaswap_io, "parse_llamaswap_config",
            lambda _path: {PROFILE: {"current_context": 24576, "env": env}},
        )
        monkeypatch.setattr(vlm_vram_cache, "get", lambda _model, _ctx: peak)
        monkeypatch.setattr(
            nvidia_smi, "query",
            lambda _fields: [
                {"index": str(i), "uuid": uuid, "memory.free": str(mb)}
                for i, (uuid, mb) in enumerate(free.items())
            ],
        )
        monkeypatch.setattr(vision_routing, "loaded_llamaswap_profiles", lambda: loaded or [])
        monkeypatch.setattr(tts_service, "running_gpu_footprint", dict)
    return configure


def test_fits_on_pinned_gpu_with_enough_free_vram(setup):
    setup(env={"CUDA_VISIBLE_DEVICES": PINNED}, peak=8988, free={OTHER: 0, PINNED: 32000})
    result = vision_vram_check.check_visiond_fits(PROFILE)
    assert result.fits
    assert result.gpu_index == 1


def test_does_not_fit_when_pinned_gpu_too_full(setup):
    setup(env={"CUDA_VISIBLE_DEVICES": PINNED}, peak=8988, free={OTHER: 48000, PINNED: 4000})
    assert not vision_vram_check.check_visiond_fits(PROFILE).fits


def test_does_not_fit_when_pinned_gpu_is_missing(setup):
    setup(env={"CUDA_VISIBLE_DEVICES": PINNED}, peak=8988, free={OTHER: 48000})
    result = vision_vram_check.check_visiond_fits(PROFILE)
    assert not result.fits
    assert PINNED in result.message


def test_unmeasured_model_does_not_fit(setup):
    setup(env={"CUDA_VISIBLE_DEVICES": PINNED}, peak=None, free={PINNED: 32000})
    assert not vision_vram_check.check_visiond_fits(PROFILE).fits


def test_a_service_that_does_not_answer_gives_no_placement_and_no_exception(setup, monkeypatch):
    """The page asks this on every load: a dead TTS service means 'room unknown' with the reason, not an error."""
    from aifred.lib.tts_client import TTSSuiteUnreachable

    setup(env={"CUDA_VISIBLE_DEVICES": PINNED}, peak=8988, free={PINNED: 32000})

    def unreachable():
        raise TTSSuiteUnreachable("TTS service not reachable")

    monkeypatch.setattr(tts_service, "running_gpu_footprint", unreachable)
    result = vision_vram_check.check_visiond_fits(PROFILE)
    assert not result.fits and result.gpu_index == -1
    assert "unbekannt" in result.message


def test_unpinned_profile_sums_all_gpus(setup):
    setup(env={}, peak=8988, free={OTHER: 5000, PINNED: 5000})
    result = vision_vram_check.check_visiond_fits(PROFILE)
    assert result.fits
    assert result.gpu_index == -1


def test_already_loaded_profile_fits(setup):
    setup(env={"CUDA_VISIBLE_DEVICES": PINNED}, peak=None, free={}, loaded=[PROFILE])
    assert vision_vram_check.check_visiond_fits(PROFILE).fits
