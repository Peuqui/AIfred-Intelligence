"""Regel A (chat_describer) und Regel B (camera_describer) aus vision_routing."""

import pytest

from aifred.lib import config, vision_routing, vision_utils, vision_vram_check
from aifred.lib.vision_routing import NoVisionModelError, camera_describer, chat_describer
from aifred.lib.vision_vram_check import VRAMCheckResult

MAIN = "Big-Text-Model"
MAIN_SEEING = "Big-Vision-Model"
VLM = "Small-VL-4B"
VISIOND = f"{VLM}-visiond"
VISION_LLM = "Chat-VL-8B"


@pytest.fixture
def world(monkeypatch):
    def configure(*, fits: bool = True, loaded: list[str] | None = None,
                  vision_llm: str = VISION_LLM, visiond_for: dict | None = None):
        seeing = {MAIN_SEEING, VISIOND, f"{VISION_LLM}-visiond", VLM}
        monkeypatch.setattr(vision_utils, "is_vision_model_sync", lambda m: m in seeing)
        monkeypatch.setattr(vision_utils, "has_native_vision", lambda m: m in seeing)
        profiles = visiond_for if visiond_for is not None else {VLM: VISIOND}
        monkeypatch.setattr(vision_routing, "visiond_profile_for", lambda m: profiles.get(m))
        monkeypatch.setattr(
            vision_vram_check, "check_visiond_fits",
            lambda _p: VRAMCheckResult(fits=fits, needed_mb=9488, free_mb=0, gpu_index=-1,
                                       message="does not fit"),
        )
        monkeypatch.setattr(vision_routing, "loaded_llamaswap_profiles", lambda: loaded or [])
        monkeypatch.setattr(vision_routing, "_settings_model",
                            lambda role: vision_llm if role == "vision" else MAIN)
        monkeypatch.setattr(config, "get_effective_model_from_settings",
                            lambda role: f"{vision_llm}-tts" if role == "vision" else MAIN)
    return configure


# ── Regel A ──────────────────────────────────────────────────────────

def test_chat_seeing_main_model_describes_itself(world):
    world()
    d = chat_describer(MAIN_SEEING)
    assert (d.model, d.evicts_chat_model) == (MAIN_SEEING, False)


def test_chat_blind_main_uses_vision_llm_in_parallel_when_it_fits(world):
    world(fits=True, visiond_for={VISION_LLM: f"{VISION_LLM}-visiond"})
    d = chat_describer(MAIN)
    assert (d.model, d.evicts_chat_model) == (f"{VISION_LLM}-visiond", False)


def test_chat_blind_main_evicts_with_effective_vision_variant_when_not_fitting(world):
    world(fits=False, visiond_for={VISION_LLM: f"{VISION_LLM}-visiond"})
    d = chat_describer(MAIN)
    assert (d.model, d.evicts_chat_model) == (f"{VISION_LLM}-tts", True)


def test_chat_blind_main_without_vision_llm_raises(world):
    world(vision_llm="")
    with pytest.raises(NoVisionModelError):
        chat_describer(MAIN)


# ── Regel B ──────────────────────────────────────────────────────────

def test_camera_vlm_fits_beside_loaded_model(world):
    world(fits=True, loaded=[MAIN_SEEING])
    d = camera_describer(VLM, explicit=False)
    assert (d.model, d.evicts_chat_model) == (VISIOND, False)


def test_camera_seeing_loaded_model_describes_when_vlm_does_not_fit(world):
    world(fits=False, loaded=[MAIN_SEEING])
    d = camera_describer(VLM, explicit=False)
    assert (d.model, d.evicts_chat_model) == (MAIN_SEEING, False)


def test_camera_automatic_never_evicts(world):
    world(fits=False, loaded=[MAIN])
    with pytest.raises(NoVisionModelError):
        camera_describer(VLM, explicit=False)


def test_camera_explicit_request_evicts_with_vision_llm(world):
    world(fits=False, loaded=[MAIN])
    d = camera_describer(VLM, explicit=True)
    assert (d.model, d.evicts_chat_model) == (f"{VISION_LLM}-tts", True)


def test_camera_visiond_describer_itself_does_not_count_as_seeing_main(world):
    world(fits=False, loaded=[VISIOND, MAIN])
    with pytest.raises(NoVisionModelError):
        camera_describer(VLM, explicit=False)


def test_camera_without_visiond_profile_keeps_model_unchanged(world):
    world(visiond_for={})
    d = camera_describer("qwen3-vl:4b", explicit=False)
    assert (d.model, d.evicts_chat_model) == ("qwen3-vl:4b", False)


def test_camera_llamaswap_model_without_visiond_never_evicts_automatically(world):
    world(visiond_for={}, loaded=[MAIN])
    with pytest.raises(NoVisionModelError):
        camera_describer(VLM, explicit=False)
