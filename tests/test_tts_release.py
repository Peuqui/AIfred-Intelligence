"""Freigabe statt Container-Stoppen: AIfred BITTET den TTS-Dienst (POST /release), Karten freizugeben; der
Dienst entscheidet. Adapter (release_gpu_engines), GPU-Wächter vor dem Modellstart, Abstimmung LLM/TTS."""

from __future__ import annotations

import pytest
from fake_tts_service import FakeTTSService, catalog_row, install_fake_service

import aifred.lib.calibration as calibration
import aifred.lib.logging_utils as logging_utils
import aifred.lib.tts_llm_coordination as coordination
import aifred.lib.tts_service as tts_service
from aifred.backends.base import OpenAICompatibleBackend
from aifred.lib.calibration import gpu
from aifred.lib.tts_client import TTSSuiteUnreachable

CARD_A, CARD_B, CARD_C = "GPU-a", "GPU-b", "GPU-c"
UUIDS = [CARD_A, CARD_B, CARD_C]
PCI = {"CUDA_DEVICE_ORDER": "PCI_BUS_ID"}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """xtts on card A, fish on card B (both running), qwen on card C (stopped), piper (CPU, running),
    xtts on the host Aragon (running, remote card), edge (cloud)."""
    service = FakeTTSService([
        catalog_row("xtts", vram_mib={CARD_A: 3000}),
        catalog_row("fish", vram_mib={CARD_B: 5000}),
        catalog_row("qwen", status="stopped", vram_mib={}),
        catalog_row("piper", needs_gpu=False),
        catalog_row("xtts", "Aragon", vram_mib={"GPU-remote": 3000}),
        catalog_row("edge", cloud=True, needs_gpu=False),
    ])
    install_fake_service(monkeypatch, tmp_path, service)
    return service


@pytest.fixture
def logged(monkeypatch) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []

    def record(message: str, category: str = "info") -> None:
        lines.append((category, message))

    monkeypatch.setattr(tts_service, "log_message", record)
    monkeypatch.setattr(logging_utils, "log_message", record)
    return lines


# ---- Adapter ----------------------------------------------------------------------------------


def test_without_cards_every_running_local_gpu_engine_is_asked_to_go(fake) -> None:
    results = tts_service.release_gpu_engines(reason="test")
    assert fake.releases == [[{"engine": "xtts", "host": None}, {"engine": "fish", "host": None}]]
    assert [(r.engine, r.host, r.outcome) for r in results] == [("xtts", None, "stopped"), ("fish", None, "stopped")]


def test_with_cards_only_the_engines_on_those_cards_are_asked(fake) -> None:
    tts_service.release_gpu_engines({CARD_B, CARD_C}, reason="test")
    assert fake.releases == [[{"engine": "fish", "host": None}]]


def test_kept_engines_stay(fake) -> None:
    tts_service.release_gpu_engines(None, reason="test", keep=["xtts"])
    assert fake.releases == [[{"engine": "fish", "host": None}]]


def test_nothing_in_the_way_sends_no_request(fake) -> None:
    assert tts_service.release_gpu_engines({CARD_C}, reason="test") == []
    assert fake.releases == []


def test_an_engine_whose_vram_is_unmeasured_cannot_be_judged_against_cards(monkeypatch, tmp_path) -> None:
    service = FakeTTSService([catalog_row("xtts", vram_mib=None)])
    install_fake_service(monkeypatch, tmp_path, service)
    with pytest.raises(tts_service.TTSVramUnknown):
        tts_service.release_gpu_engines({CARD_A}, reason="test")
    assert service.releases == []


def test_the_catalog_is_not_stale_after_a_release(fake) -> None:
    tts_service.catalog()
    fake.catalog[0]["status"] = "stopped"          # what the service did meanwhile
    tts_service.release_gpu_engines(reason="test")
    assert next(i for i in tts_service.catalog() if i.engine == "xtts" and i.host is None).status == "stopped"


def test_a_refused_release_is_logged_visibly_and_returned(fake, logged) -> None:
    fake.release_outcome = "in_use"
    results = tts_service.release_gpu_engines(reason="loading 'm'")
    assert {r.outcome for r in results} == {"in_use"}
    refusals = [m for c, m in logged if c == "warning" and "TTS in use, release of xtts refused" in m]
    assert len(refusals) == 1 and "loading 'm'" in refusals[0]


@pytest.mark.parametrize("outcome", ["unreachable", "failed"])
def test_a_failed_release_is_logged_with_its_outcome(fake, logged, outcome: str) -> None:
    fake.release_outcome = outcome
    tts_service.release_gpu_engines(reason="test")
    assert any(c == "warning" and f"({outcome})" in m for c, m in logged)


def test_a_service_that_is_not_there_is_a_clear_error(monkeypatch) -> None:
    # conftest points the client at a dead address
    with pytest.raises(TTSSuiteUnreachable):
        tts_service.release_gpu_engines(reason="test")


def test_the_adapter_still_has_no_start_or_stop() -> None:
    for name in ("start_engine", "stop_engine", "start_entry", "stop_entry"):
        assert not hasattr(tts_service, name), name


# ---- GPU guard before a model load ---------------------------------------------------------------


@pytest.fixture
def config(monkeypatch):
    models = {
        "on-b": {"env": {**PCI, "CUDA_VISIBLE_DEVICES": "1"}},
        "on-c": {"env": {**PCI, "CUDA_VISIBLE_DEVICES": "2"}},
        "on-a-tts-xtts": {"env": {**PCI, "CUDA_VISIBLE_DEVICES": "0"}},
    }
    monkeypatch.setattr(calibration, "parse_llamaswap_config", lambda _path: models)
    monkeypatch.setattr(gpu, "gpu_uuids_by_index", lambda: UUIDS)


def test_the_guard_asks_for_the_engines_on_the_cards_of_the_model(fake, config) -> None:
    OpenAICompatibleBackend._release_tts_for_load("on-b")
    assert fake.releases == [[{"engine": "fish", "host": None}]]


def test_the_guard_asks_for_nothing_when_no_engine_sits_on_the_cards(fake, config) -> None:
    OpenAICompatibleBackend._release_tts_for_load("on-c")
    assert fake.releases == []


def test_the_guard_keeps_the_engine_the_tts_profile_reserves_room_for(fake, config) -> None:
    OpenAICompatibleBackend._release_tts_for_load("on-a-tts-xtts")
    assert fake.releases == []


def test_the_guard_with_unknown_cards_asks_for_every_running_gpu_engine(fake, config) -> None:
    OpenAICompatibleBackend._release_tts_for_load("not-in-the-config")
    assert fake.releases == [[{"engine": "xtts", "host": None}, {"engine": "fish", "host": None}]]


def test_a_refused_release_is_shown_and_not_repeated(fake, config, logged) -> None:
    fake.release_outcome = "in_use"
    OpenAICompatibleBackend._release_tts_for_load("on-b")
    assert len(fake.releases) == 1                      # no retry, no waiting: the load goes on
    assert any("TTS in use, release of fish refused" in m for _, m in logged)


def test_the_guard_does_not_fail_the_load_when_the_service_is_down(config, logged) -> None:
    OpenAICompatibleBackend._release_tts_for_load("on-b")
    assert any(c == "warning" and "TTS release before loading 'on-b' failed: TTSSuiteUnreachable" in m for c, m in logged)


# ---- Abstimmung LLM / TTS -------------------------------------------------------------------------


@pytest.fixture
def llm_steps(monkeypatch) -> list[str]:
    steps: list[str] = []
    monkeypatch.setattr(coordination, "_is_llm_loaded", lambda backend: False)
    monkeypatch.setattr(coordination, "_llm_already_on_tts_variant", lambda engine, backend: False)

    def restart(backend: str = "llamacpp") -> bool:
        steps.append("restart")
        return True

    monkeypatch.setattr(coordination, "restart_llm_backend", restart)
    monkeypatch.setattr(coordination, "get_effective_model_info", lambda backend="llamacpp": "model")
    return steps


def run_plan(wanted: str) -> list[str]:
    return list(coordination.ensure_tts_state(wanted_tts=wanted, backend_type="llamacpp"))


def test_the_plan_asks_for_the_other_gpu_engines_and_keeps_the_planned_one(fake, llm_steps) -> None:
    run_plan("xtts")
    assert fake.releases == [[{"engine": "fish", "host": None}]]
    assert llm_steps == ["restart"]


def test_a_refused_release_in_the_plan_is_reported_and_the_switch_goes_on(fake, llm_steps) -> None:
    fake.release_outcome = "in_use"
    messages = run_plan("xtts")
    assert any("FISH" in m and "TTS in use, release refused" in m for m in messages)
    assert llm_steps == ["restart"]


def test_an_unreachable_service_in_the_plan_is_reported_and_the_switch_goes_on(llm_steps, monkeypatch) -> None:
    monkeypatch.setattr(coordination, "gpu_engine_keys", lambda: {"xtts"})
    messages = run_plan("xtts")
    assert any(m.startswith("⚠️ TTS release failed") for m in messages)
    assert llm_steps == ["restart"]
