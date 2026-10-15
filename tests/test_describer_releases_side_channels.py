"""Describer vor TTS und STT: der VRAM von Whisper-Worker und TTS-Containern zählt als frei; vor dem Laden
gibt AIfred den Worker frei und bittet den TTS-Dienst um die Freigabe der Container (Anfrage, kein Stoppen)."""

import pytest

import aifred.lib.audio_processing as ap
from aifred.lib import (
    nvidia_smi,
    tts_service,
    vision_routing,
    vision_vram_check,
    vlm_vram_cache,
)
from aifred.lib.calibration import llamaswap_io
from aifred.lib.tts_client import ReleaseResult, TTSSuiteUnreachable

PROFILE = "VL-4B-visiond-gpu0"
CARD, OTHER = "GPU-a", "GPU-b"


def test_footprint_counts_only_the_whisper_worker(monkeypatch, tmp_path):
    from aifred.lib.stt_client import get_default_client
    monkeypatch.setattr(get_default_client(), "status", lambda: {"gpu_worker_pid": 42})
    monkeypatch.setattr(nvidia_smi, "compute_apps", lambda: [
        {"pid": "1001", "process_name": "python3.12", "used_memory": "3652", "gpu_uuid": CARD},
        {"pid": "1002", "process_name": "python3.12", "used_memory": "2100", "gpu_uuid": OTHER},
        {"pid": "1003", "process_name": "VLLM::Worker", "used_memory": "39000", "gpu_uuid": CARD},
    ])
    nspid = {"1001": "NSpid:\t1001\t42\n", "1002": "NSpid:\t1002\t7\n", "1003": "NSpid:\t1003\n"}
    real_open = open

    def fake_open(path, *a, **k):
        pid = str(path).split("/")[2] if str(path).startswith("/proc/") else ""
        if pid in nspid:
            target = tmp_path / f"status-{pid}"
            target.write_text(nspid[pid])
            return real_open(target, *a, **k)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert ap.whisper_gpu_footprint() == {CARD: 3652}


@pytest.fixture
def card(monkeypatch):
    monkeypatch.setattr(llamaswap_io, "parse_llamaswap_config", lambda _p: {
        PROFILE: {"current_context": 24576, "env": {"CUDA_VISIBLE_DEVICES": CARD}},
    })
    monkeypatch.setattr(vlm_vram_cache, "get", lambda _m, _c: 7396)
    monkeypatch.setattr(nvidia_smi, "query", lambda _f: [
        {"index": "0", "uuid": CARD, "memory.free": "5665"},
    ])
    monkeypatch.setattr(vision_routing, "loaded_llamaswap_profiles", list)
    monkeypatch.setattr(ap, "whisper_gpu_footprint", dict)
    monkeypatch.setattr(tts_service, "running_gpu_footprint", dict)


def test_whisper_vram_counts_as_free_for_the_describer(card, monkeypatch):
    assert not vision_vram_check.check_visiond_fits(PROFILE).fits
    monkeypatch.setattr(ap, "whisper_gpu_footprint", lambda: {CARD: 3652})
    assert vision_vram_check.check_visiond_fits(PROFILE).fits


def test_whisper_on_the_target_card_is_released(card, monkeypatch):
    released: list[int] = []

    def release() -> bool:
        released.append(1)
        return True

    monkeypatch.setattr(ap, "whisper_gpu_footprint", lambda: {CARD: 3652})
    monkeypatch.setattr(ap, "release_whisper_gpu", release)
    assert vision_routing.release_side_channels_for_describer(PROFILE) is True
    assert released == [1]


def test_whisper_on_another_card_stays(card, monkeypatch):
    monkeypatch.setattr(ap, "whisper_gpu_footprint", lambda: {OTHER: 3652})
    monkeypatch.setattr(ap, "release_whisper_gpu", lambda: pytest.fail("must not release"))
    assert vision_routing.release_side_channels_for_describer(PROFILE) is False


def test_tts_vram_counts_as_free_for_the_describer(card, monkeypatch):
    assert not vision_vram_check.check_visiond_fits(PROFILE).fits
    monkeypatch.setattr(tts_service, "running_gpu_footprint", lambda: {"xtts": {CARD: 3680}})
    assert vision_vram_check.check_visiond_fits(PROFILE).fits
    monkeypatch.setattr(tts_service, "running_gpu_footprint", lambda: {"xtts": {OTHER: 3680}})
    assert not vision_vram_check.check_visiond_fits(PROFILE).fits


def test_tts_container_on_the_target_card_is_released_by_request(card, monkeypatch):
    asked: list[tuple] = []

    def release(cards, *, reason):
        asked.append((cards, reason))
        return [ReleaseResult("xtts", None, "stopped", "")]

    monkeypatch.setattr(tts_service, "release_gpu_engines", release)
    assert vision_routing.release_side_channels_for_describer(PROFILE) is True
    assert asked == [({CARD}, f"describer {PROFILE}")]


def test_a_refused_tts_release_is_not_a_release(card, monkeypatch):
    monkeypatch.setattr(
        tts_service, "release_gpu_engines", lambda cards, *, reason: [ReleaseResult("xtts", None, "in_use", "x")],
    )
    assert vision_routing.release_side_channels_for_describer(PROFILE) is False


def test_an_unreachable_tts_service_does_not_stop_the_describer_load(card, monkeypatch, caplog):
    def unreachable(cards, *, reason):
        raise TTSSuiteUnreachable("not reachable on /engines")

    monkeypatch.setattr(tts_service, "release_gpu_engines", unreachable)
    monkeypatch.setattr(ap, "whisper_gpu_footprint", lambda: {CARD: 3652})
    monkeypatch.setattr(ap, "release_whisper_gpu", lambda: True)
    with caplog.at_level("WARNING"):
        assert vision_routing.release_side_channels_for_describer(PROFILE) is True  # Whisper still released
    assert "TTS release for describer" in caplog.text
