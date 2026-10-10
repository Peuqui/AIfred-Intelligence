"""Describer vor TTS und STT: ihr VRAM zählt als frei und wird vor dem Laden freigegeben."""

import pytest

import aifred.lib.audio_processing as ap
from aifred.lib import nvidia_smi, tts_engine_manager, vision_routing, vision_vram_check, vlm_vram_cache
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
    monkeypatch.setattr(tts_engine_manager, "local_tts_gpu_footprint", lambda: {})
    monkeypatch.setattr(ap, "whisper_gpu_footprint", lambda: {})


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


def test_tts_footprint_counts_only_the_container_processes(monkeypatch):
    from types import SimpleNamespace

    from aifred.lib import tts_engines
    engine = SimpleNamespace(key="qwen3local", docker_compose_path="/x/docker-compose.yml",
                             is_running=lambda: True)
    monkeypatch.setattr(tts_engines, "gpu_engines", lambda: iter([engine]))
    monkeypatch.setattr(tts_engine_manager, "_container_host_pids", lambda _f: {"2001"})
    monkeypatch.setattr(nvidia_smi, "compute_apps", lambda: [
        {"pid": "2001", "process_name": "python", "used_memory": "5100", "gpu_uuid": CARD},
        {"pid": "1003", "process_name": "llama-server", "used_memory": "39000", "gpu_uuid": CARD},
    ])
    assert tts_engine_manager.local_tts_gpu_footprint() == {"qwen3local": {CARD: 5100}}


def test_tts_vram_counts_as_free_for_the_describer(card, monkeypatch):
    assert not vision_vram_check.check_visiond_fits(PROFILE).fits
    monkeypatch.setattr(tts_engine_manager, "local_tts_gpu_footprint", lambda: {"xtts": {CARD: 3000}})
    assert vision_vram_check.check_visiond_fits(PROFILE).fits


def test_tts_on_the_target_card_is_stopped(card, monkeypatch):
    stopped: list[str] = []

    def stop(key: str) -> tuple[bool, str]:
        stopped.append(key)
        return True, ""

    monkeypatch.setattr(tts_engine_manager, "local_tts_gpu_footprint", lambda: {"xtts": {CARD: 3000}})
    monkeypatch.setattr(tts_engine_manager, "stop_engine", stop)
    assert vision_routing.release_side_channels_for_describer(PROFILE) is True
    assert stopped == ["xtts"]


def test_tts_on_another_card_keeps_running(card, monkeypatch):
    monkeypatch.setattr(tts_engine_manager, "local_tts_gpu_footprint", lambda: {"xtts": {OTHER: 3000}})
    monkeypatch.setattr(tts_engine_manager, "stop_engine", lambda key: pytest.fail("must not stop"))
    assert vision_routing.release_side_channels_for_describer(PROFILE) is False


class TestTtsBeforeModelLoad:
    """GPU guard of the backends: a model load clears TTS containers off its cards."""

    @pytest.fixture
    def guard(self, monkeypatch):
        from aifred.lib import process_utils
        from aifred.lib.calibration import gpu as calibration_gpu
        stopped: list[str] = []

        def stop(key: str) -> tuple[bool, str]:
            stopped.append(key)
            return True, ""

        monkeypatch.setattr(tts_engine_manager, "stop_engine", stop)
        monkeypatch.setattr(process_utils, "get_tts_gpu_uuid", lambda: OTHER)
        monkeypatch.setattr(calibration_gpu, "gpu_uuids_by_index", lambda: [CARD, OTHER])
        monkeypatch.setattr("aifred.lib.calibration.parse_llamaswap_config", lambda _p: {
            "Big": {"env": {"CUDA_VISIBLE_DEVICES": CARD}},
            "Big-tts-xtts": {"env": {"CUDA_VISIBLE_DEVICES": f"{CARD},{OTHER}"}},
        })
        return stopped

    def _release(self, model):
        from aifred.backends.base import OpenAICompatibleBackend
        OpenAICompatibleBackend._release_tts_for_load(model)

    def test_tts_on_a_card_the_model_needs_is_stopped(self, guard, monkeypatch):
        monkeypatch.setattr(tts_engine_manager, "local_tts_gpu_footprint", lambda: {"xtts": {CARD: 3680}})
        self._release("Big")
        assert guard == ["xtts"]

    def test_tts_on_another_card_stays(self, guard, monkeypatch):
        monkeypatch.setattr(tts_engine_manager, "local_tts_gpu_footprint", lambda: {"xtts": {OTHER: 3680}})
        self._release("Big")
        assert guard == []

    def test_the_engine_a_tts_profile_reserves_stays_on_the_side_channel_card(self, guard, monkeypatch):
        monkeypatch.setattr(tts_engine_manager, "local_tts_gpu_footprint", lambda: {"xtts": {OTHER: 3680}})
        self._release("Big-tts-xtts")
        assert guard == []
