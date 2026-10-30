"""Describer vor TTS und STT: was GPU-Orc auf der Karte des Profils freiräumen kann, zählt als frei; vor dem
Laden lässt AIfred GPU-Orc die Karten des Profils freiräumen (dieselbe Modell-Form wie vor einem Chat-Modell)."""

import pytest
from fake_gpu_orc import FakeGpuOrc, card, install_fake_gpu_orc

from aifred.lib import (
    gpu_orc_client,
    nvidia_smi,
    vision_routing,
    vision_vram_check,
    vlm_vram_cache,
)
from aifred.lib.calibration import llamaswap_io
from aifred.lib.config import GPU_ORC_MODEL_SERVER

PROFILE = "VL-4B-visiond-gpu0"
CARD, OTHER = "GPU-a", "GPU-b"


@pytest.fixture
def fake(monkeypatch) -> FakeGpuOrc:
    """The profile needs 7396 MiB + reserve on CARD; 5665 MiB are free there."""
    monkeypatch.setattr(llamaswap_io, "parse_llamaswap_config", lambda _p: {
        PROFILE: {"current_context": 24576, "env": {"CUDA_VISIBLE_DEVICES": CARD}},
    })
    monkeypatch.setattr(vlm_vram_cache, "get", lambda _m, _c: 7396)
    monkeypatch.setattr(nvidia_smi, "query", lambda _f: [
        {"index": "0", "uuid": CARD, "memory.free": "5665"},
    ])
    monkeypatch.setattr(vision_routing, "loaded_llamaswap_profiles", list)
    service = FakeGpuOrc()
    service.cards = [card(CARD), card(OTHER)]
    install_fake_gpu_orc(monkeypatch, service)
    return service


def fits() -> bool:
    return vision_vram_check.check_visiond_fits(PROFILE).fits


def test_stt_and_tts_vram_on_the_card_counts_as_free(fake):
    assert not fits()
    fake.cards = [card(CARD, ("stt-suite", "gpu-worker", 3652)), card(OTHER)]
    assert fits()
    fake.cards = [card(CARD, ("tts-suite", "xtts", 3680)), card(OTHER)]
    assert fits()


def test_vram_on_another_card_does_not_count(fake):
    fake.cards = [card(CARD), card(OTHER, ("stt-suite", "gpu-worker", 3652))]
    assert not fits()


def test_the_model_servers_models_stay_and_give_no_room(fake):
    fake.cards = [card(CARD, (GPU_ORC_MODEL_SERVER, "bge-embed", 3652)), card(OTHER)]
    assert not fits()


def test_a_holder_without_a_measured_memory_gives_no_room(fake):
    fake.cards = [card(CARD, ("stt-suite", "diarize-worker", None)), card(OTHER)]
    assert not fits()


def test_without_gpu_orc_the_room_is_unknown_and_said(fake, monkeypatch):
    """The page asks this on every load: a dead GPU-Orc means 'room unknown' with the reason, not an error."""
    monkeypatch.setattr(gpu_orc_client, "_default_client", gpu_orc_client.GpuOrcClient("http://127.0.0.1:9"))
    result = vision_vram_check.check_visiond_fits(PROFILE)
    assert not result.fits and result.gpu_index == -1
    assert "Belegung der Karten unbekannt" in result.message


def test_the_describer_has_gpu_orc_clear_its_cards(fake):
    vision_routing.release_side_channels_for_describer(PROFILE)
    assert fake.frees == [
        {"model": PROFILE, "model_server": GPU_ORC_MODEL_SERVER, "reason": f"AIfred: describer {PROFILE}"},
    ]


def test_a_gpu_orc_that_does_not_answer_does_not_stop_the_describer_load(monkeypatch):
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(gpu_orc_client, "log_message", lambda message, category="info": said.append((category, message)))
    vision_routing.release_side_channels_for_describer(PROFILE)  # conftest: dead address, no exception
    assert len(said) == 1 and said[0][0] == "warning" and PROFILE in said[0][1]
