"""Describer vor STT: Whisper-VRAM zählt als frei und wird vor dem Laden freigegeben."""

import pytest

import aifred.lib.audio_processing as ap
from aifred.lib import nvidia_smi, vision_routing, vision_vram_check, vlm_vram_cache
from aifred.lib.calibration import llamaswap_io

PROFILE = "VL-4B-visiond-gpu0"
CARD, OTHER = "GPU-a", "GPU-b"


def test_footprint_counts_only_the_whisper_worker(monkeypatch, tmp_path):
    class Resp:
        def json(self):
            return {"gpu_worker_pid": 42}

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
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
    monkeypatch.setattr(vision_routing, "loaded_llamaswap_profiles", lambda: [])


def test_whisper_vram_counts_as_free_for_the_describer(card, monkeypatch):
    assert not vision_vram_check.check_visiond_fits(PROFILE).fits
    monkeypatch.setattr(ap, "whisper_gpu_footprint", lambda: {CARD: 3652})
    assert vision_vram_check.check_visiond_fits(PROFILE).fits


def test_whisper_on_the_target_card_is_released(card, monkeypatch):
    released = []
    monkeypatch.setattr(ap, "whisper_gpu_footprint", lambda: {CARD: 3652})
    monkeypatch.setattr(ap, "release_whisper_gpu", lambda: released.append(1) or True)
    assert vision_routing.release_stt_for_describer(PROFILE) is True
    assert released == [1]


def test_whisper_on_another_card_stays(card, monkeypatch):
    monkeypatch.setattr(ap, "whisper_gpu_footprint", lambda: {OTHER: 3652})
    monkeypatch.setattr(ap, "release_whisper_gpu", lambda: pytest.fail("must not release"))
    assert vision_routing.release_stt_for_describer(PROFILE) is False
