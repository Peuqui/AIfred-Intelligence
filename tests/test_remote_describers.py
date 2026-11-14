"""Describers on other machines (lib/remote_describers): configuration, the choice of Rule A and B, the
second attempt when the machine goes away mid-request, and where the image request is sent."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from fake_gpu_orc import FakeGpuOrc, card
from fake_vlm_stream import streaming_client

from aifred.lib import describer_list, remote_describers, vision_routing, vision_utils, vlm_vram_cache
from aifred.lib.describer_list import LOCAL, MAIN
from aifred.lib.frame_sources import Frame
from aifred.lib.gpu_orc_client import GpuOrcClient, GpuOrcError
from aifred.lib.remote_describers import RemoteHost, RemoteModel
from aifred.lib.vision_routing import Describer, describe_via, describe_with_second_attempt
from aifred.lib.vision_vram_check import VRAMCheckResult

HOST = {"name": "Box", "llamaswap_url": "http://box.test:11435", "gpu_orc_url": "http://box.test:5070",
        "gpu_orc_token_file": "/nonexistent/token"}
BIG = "Big-VL-30B-visiond"
SMALL = "Small-VL-4B-visiond"


@pytest.fixture
def box(monkeypatch, tmp_path):
    """One configured machine; its reachability, its models and its GPU-Orc as set."""
    monkeypatch.setattr(remote_describers, "VISION_REMOTE_HOSTS", json.dumps([HOST]))
    monkeypatch.setattr(vlm_vram_cache, "_store", vlm_vram_cache.JsonPeakCache(tmp_path / "vlm.json", "test"))
    state: dict[str, Any] = {"reachable": True, "models": [], "gpu_orc": FakeGpuOrc(), "token_ok": True}

    def models(host: RemoteHost) -> list[RemoteModel]:
        return [RemoteModel(host, name, 24576, loaded) for name, loaded in state["models"]]

    def gpu_orc(host: RemoteHost) -> GpuOrcClient:
        if not state["token_ok"]:
            raise GpuOrcError("token file missing")
        return GpuOrcClient(host.gpu_orc_url, transport=httpx.MockTransport(state["gpu_orc"].handler), token="t")

    monkeypatch.setattr(remote_describers, "reachable", lambda host: state["reachable"])
    monkeypatch.setattr(remote_describers, "seeing_models", models)
    monkeypatch.setattr(remote_describers, "gpu_orc", gpu_orc)
    return state


def _card(total: int, used: int, *holders: tuple[str, str, int | None]) -> dict[str, Any]:
    c = card("GPU-box", *holders)
    c.update(memory_total_mib=total, memory_used_mib=used)
    return c


# ── configuration ────────────────────────────────────────────────────

def test_the_machines_come_from_the_environment(monkeypatch):
    monkeypatch.setattr(remote_describers, "VISION_REMOTE_HOSTS", json.dumps([{**HOST, "llamaswap_url": "http://b/v1/"}]))
    [host] = remote_describers.remote_hosts()
    assert host == RemoteHost("Box", "http://b", "http://box.test:5070", "/nonexistent/token")


@pytest.mark.parametrize("raw", ['{"name": "Box"}', "[{}]", "not json", json.dumps([HOST, HOST]),
                                 json.dumps([{**HOST, "name": "a/b"}])])
def test_a_malformed_list_of_machines_is_an_error(monkeypatch, raw):
    monkeypatch.setattr(remote_describers, "VISION_REMOTE_HOSTS", raw)
    with pytest.raises(ValueError, match="VISION_REMOTE_HOSTS"):
        remote_describers.remote_hosts()


def test_a_qualified_id_leads_to_the_machine_and_the_model_there(box):
    assert remote_describers.endpoint(f"Box/{BIG}") == ("http://box.test:11435/v1", BIG)
    assert remote_describers.endpoint(BIG) is None
    assert remote_describers.endpoint(f"Other/{BIG}") is None  # an Ollama namespace, not a machine


def test_only_models_that_see_are_listed(monkeypatch):
    host = RemoteHost("Box", "http://box.test:11435", "http://box.test:5070", "/x")
    answer = {"data": [
        {"id": BIG, "context_length": 24576, "capabilities": {"vision": True}, "status": {"value": "ready"}},
        {"id": "Text-only", "context_length": 8192, "capabilities": {}, "status": {"value": "unloaded"}},
        # never loaded there: llama-swap knows its capabilities from the config, not yet its context
        {"id": SMALL, "capabilities": {"vision": True}, "status": {"value": "unloaded"}},
    ]}
    monkeypatch.setattr(httpx, "get", lambda url, timeout: httpx.Response(200, json=answer, request=httpx.Request("GET", url)))
    assert remote_describers.seeing_models(host) == [RemoteModel(host, BIG, 24576, True), RemoteModel(host, SMALL, None, False)]


def test_a_model_never_loaded_there_does_not_fit(box):
    [host] = remote_describers.remote_hosts()
    fit = remote_describers.check_fits(RemoteModel(host, BIG, None, False))
    assert fit.fits is False and "noch nie geladen" in fit.message


def test_a_machine_that_does_not_answer_is_not_reachable(monkeypatch):
    host = RemoteHost("Box", "http://box.test:11435", "http://box.test:5070", "/x")

    def refuse(url, timeout):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "get", refuse)
    assert remote_describers.reachable(host) is False


# ── the choice ───────────────────────────────────────────────────────

def test_a_machine_that_is_off_is_passed_over(box):
    box.update(reachable=False, models=[(BIG, True)])
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is False


def test_the_model_must_be_one_that_sees_there(box):
    box["models"] = [(SMALL, True)]
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is False


def test_a_loaded_model_fits(box):
    box["models"] = [(BIG, True)]
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is True


def test_a_model_never_measured_does_not_fit(box):
    box["models"] = [(BIG, False)]
    box["gpu_orc"].cards = [_card(24564, 1599)]
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is False


def test_a_measured_model_fits_when_its_need_is_free(box):
    box["models"] = [(BIG, False)]
    vlm_vram_cache.put(f"Box/{BIG}", 24576, 20769)
    box["gpu_orc"].cards = [_card(24564, 1599)]  # 22965 free ≥ 20769 + 500
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is True


def test_a_measured_model_that_does_not_fit_is_passed_over(box):
    box["models"] = [(BIG, False)]
    vlm_vram_cache.put(f"Box/{BIG}", 24576, 20769)
    box["gpu_orc"].cards = [_card(24564, 8614)]  # the TTS containers hold 7015 MiB
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is False


def test_what_the_machines_gpu_orc_clears_counts_as_free(box):
    box["models"] = [(BIG, False)]
    vlm_vram_cache.put(f"Box/{BIG}", 24576, 20769)
    box["gpu_orc"].cards = [_card(24564, 8614, ("tts-suite", "xtts", 7015))]
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is True


def test_the_measurement_belongs_to_the_context(box):
    box["models"] = [(BIG, False)]
    vlm_vram_cache.put(f"Box/{BIG}", 8192, 15000)  # measured at another context
    box["gpu_orc"].cards = [_card(24564, 1599)]
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is False


def test_nothing_new_while_a_big_model_loads_there(box):
    box["models"] = [(BIG, False)]
    vlm_vram_cache.put(f"Box/{BIG}", 24576, 7235)
    box["gpu_orc"].loading = ["Something-Big"]
    box["gpu_orc"].cards = [_card(24564, 1599)]
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is False


def test_nothing_new_while_somebody_holds_the_cards_there(box):
    box["models"] = [(BIG, False)]
    vlm_vram_cache.put(f"Box/{BIG}", 24576, 7235)
    box["gpu_orc"].held = {"since": "2026-11-05T09:00:00+01:00", "reason": "gaming"}
    box["gpu_orc"].cards = [_card(24564, 1599)]
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is False


def test_without_its_gpu_orc_the_room_is_unknown(box):
    box.update(models=[(BIG, False)], token_ok=False)
    vlm_vram_cache.put(f"Box/{BIG}", 24576, 7235)
    assert remote_describers.check_remote(f"Box/{BIG}")[0] is False


# ── Rule A and B ─────────────────────────────────────────────────────

@pytest.fixture
def local(monkeypatch):
    """This machine: a blind main model, a describer that fits, nothing loaded that sees; the describer
    list as each test sets it (``order``: sources, all switched on unless named in ``off``)."""
    fits = VRAMCheckResult(fits=True, needed_mb=7735, free_mb=20000, gpu_index=4)
    monkeypatch.setattr(vision_routing, "fitting_visiond", lambda name: (f"{name}-visiond", fits))
    monkeypatch.setattr(vision_routing, "loaded_seeing_profile", lambda: None)
    monkeypatch.setattr(vision_utils, "is_vision_model_sync", lambda m: m == "Seeing-Main")
    monkeypatch.setattr(vision_routing, "camera_vlm", lambda: "Local-VL")

    def order(*sources: str, off: tuple[str, ...] = ()) -> None:
        entries = [{"source": s, "enabled": s not in off} for s in sources]
        monkeypatch.setattr(describer_list, "_settings", lambda: {describer_list.SETTINGS_KEY: entries})
    return order


def test_camera_images_go_to_the_first_entry_that_can(box, local):
    local(f"Box/{BIG}", LOCAL, MAIN)
    box["models"] = [(BIG, True)]
    assert vision_routing.camera_describer("Local-VL", explicit=False) == Describer(f"Box/{BIG}", False)


def test_a_machine_that_is_off_hands_on_to_the_next_entry(box, local):
    local(f"Box/{BIG}", LOCAL, MAIN)
    box.update(reachable=False, models=[(BIG, True)])
    assert vision_routing.camera_describer("Local-VL", explicit=False) == Describer("Local-VL-visiond", False)


def test_a_second_model_of_the_same_machine_takes_over_when_the_first_does_not_fit(box, local):
    local(f"Box/{BIG}", f"Box/{SMALL}", LOCAL)
    box["models"] = [(BIG, False), (SMALL, True)]  # the big one never measured, the small one loaded
    assert vision_routing.camera_describer("Local-VL", explicit=False) == Describer(f"Box/{SMALL}", False)


def test_a_switched_off_entry_is_passed_over(box, local):
    local(f"Box/{BIG}", LOCAL, off=(f"Box/{BIG}",))
    box["models"] = [(BIG, True)]
    assert vision_routing.camera_describer("Local-VL", explicit=False) == Describer("Local-VL-visiond", False)


def test_the_main_model_entry_takes_camera_images_when_it_sees(box, local, monkeypatch):
    local(MAIN, LOCAL)
    monkeypatch.setattr(vision_routing, "loaded_seeing_profile", lambda: "Big-Seeing-Main")
    assert vision_routing.camera_describer("Local-VL", explicit=False) == Describer("Big-Seeing-Main", False)


def test_nothing_on_the_list_can_and_no_explicit_request_means_no_description(box, local):
    local(f"Box/{BIG}", LOCAL, off=(LOCAL,))
    box["reachable"] = False
    with pytest.raises(vision_routing.NoVisionModelError, match="does not answer"):
        vision_routing.camera_describer("Local-VL", explicit=False)


def test_a_main_model_that_sees_keeps_chat_images(box, local):
    local(f"Box/{BIG}", LOCAL, MAIN)
    box["models"] = [(BIG, True)]
    assert vision_routing.chat_describer("Seeing-Main") == Describer("Seeing-Main", False)


def test_a_blind_main_model_hands_chat_images_to_the_list(box, local):
    local(MAIN, f"Box/{BIG}", LOCAL)  # the main model entry does not count for chat images
    box["models"] = [(BIG, True)]
    assert vision_routing.chat_describer("Blind-Main") == Describer(f"Box/{BIG}", False)


# ── the list as the settings show it ─────────────────────────────────

def _overview(models: list[str], *, up: bool = True) -> list[remote_describers.HostOverview]:
    [host] = remote_describers.remote_hosts()
    return [remote_describers.HostOverview(host, up, [RemoteModel(host, m, 24576, False) for m in models] if up else [], "")]


def test_without_a_stored_list_this_machine_then_the_main_model_and_new_models_off_at_the_end(box):
    entries = describer_list.matched(describer_list.stored(), _overview([BIG, SMALL]))
    assert [(e.source, e.enabled) for e in entries] == [
        (LOCAL, True), (MAIN, True), (f"Box/{BIG}", False), (f"Box/{SMALL}", False)]


def test_a_model_the_answering_machine_no_longer_has_drops_out(box):
    stored = [describer_list.Entry(f"Box/{BIG}", True), describer_list.Entry(LOCAL, True), describer_list.Entry(MAIN, False)]
    entries = describer_list.matched(stored, _overview([SMALL]))
    assert [(e.source, e.enabled) for e in entries] == [(LOCAL, True), (MAIN, False), (f"Box/{SMALL}", False)]


def test_the_entries_of_a_machine_that_is_off_stay(box):
    stored = [describer_list.Entry(f"Box/{BIG}", True), describer_list.Entry(LOCAL, True), describer_list.Entry(MAIN, True)]
    entries = describer_list.matched(stored, _overview([], up=False))
    assert [(e.source, e.enabled) for e in entries] == [(f"Box/{BIG}", True), (LOCAL, True), (MAIN, True)]


def test_a_list_naming_a_source_twice_is_an_error(monkeypatch):
    twice = [{"source": LOCAL, "enabled": True}, {"source": LOCAL, "enabled": False}]
    monkeypatch.setattr(describer_list, "_settings", lambda: {describer_list.SETTINGS_KEY: twice})
    with pytest.raises(ValueError, match="twice"):
        describer_list.stored()


# ── second attempt ───────────────────────────────────────────────────

def _attempts(fail_on: set[str]) -> tuple[list[str], Any]:
    seen: list[str] = []

    async def run(describer: Describer) -> str:
        seen.append(describer.model)
        if describer.model in fail_on:
            raise RuntimeError("VLM call failed: connection reset")
        return f"described by {describer.model}"
    return seen, run


def test_a_machine_that_went_away_gets_one_second_attempt_here(box, monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr("aifred.lib.vision_routing.debug", logged.append)
    seen, run = _attempts({f"Box/{BIG}"})
    box["reachable"] = False  # answers no more after the failure
    result = asyncio.run(describe_with_second_attempt(
        Describer(f"Box/{BIG}", False), lambda: Describer("Local-VL-visiond", False), run,
    ))
    assert result == "described by Local-VL-visiond"
    assert seen == [f"Box/{BIG}", "Local-VL-visiond"]
    assert any("Box went away" in line for line in logged)
    assert any("failed after" in line and f"Box/{BIG}" in line for line in logged)
    assert any("described with Local-VL-visiond in" in line for line in logged)


def test_a_machine_that_still_answers_gets_no_second_attempt(box):
    seen, run = _attempts({f"Box/{BIG}"})
    with pytest.raises(RuntimeError, match="connection reset"):
        asyncio.run(describe_with_second_attempt(
            Describer(f"Box/{BIG}", False), lambda: Describer("Local-VL-visiond", False), run,
        ))
    assert seen == [f"Box/{BIG}"]


def test_a_failure_on_this_machine_gets_no_second_attempt(box):
    seen, run = _attempts({"Local-VL-visiond"})
    with pytest.raises(RuntimeError):
        asyncio.run(describe_with_second_attempt(
            Describer("Local-VL-visiond", False), lambda: Describer("Local-VL-visiond", False), run,
        ))
    assert seen == ["Local-VL-visiond"]


def test_describe_via_announces_a_describer_that_evicts_the_chat_model(box, monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr("aifred.lib.vision_routing.debug", logged.append)
    seen, run = _attempts(set())
    result = asyncio.run(describe_via(lambda: Describer("Vision-LLM", True), run))
    assert result == "described by Vision-LLM" and seen == ["Vision-LLM"]
    assert any("evicts the chat model" in line and "Vision-LLM" in line for line in logged)
    logged.clear()
    asyncio.run(describe_via(lambda: Describer("Local-VL-visiond", False), run))
    assert not any("evicts" in line for line in logged)  # nothing is evicted: nothing to announce
    assert any("described with Local-VL-visiond in" in line for line in logged)


def test_describe_via_picks_again_for_the_second_attempt(box):
    picked: list[str] = []
    models = iter([f"Box/{BIG}", "Local-VL-visiond"])

    def pick() -> Describer:
        picked.append(next(models))
        return Describer(picked[-1], False)

    seen, run = _attempts({f"Box/{BIG}"})
    box["reachable"] = False  # answers no more after the failure
    assert asyncio.run(describe_via(pick, run)) == "described by Local-VL-visiond"
    assert picked == [f"Box/{BIG}", "Local-VL-visiond"] and seen == picked


# ── the image request ────────────────────────────────────────────────

def test_the_image_request_goes_to_the_machine_with_the_id_there(box, monkeypatch):
    from aifred.lib.vision_analyzer import analyze_sequence
    sent: list[dict[str, Any]] = []
    released: list[str] = []
    monkeypatch.setattr(vision_routing, "release_side_channels_for_describer", released.append)
    monkeypatch.setattr(httpx, "AsyncClient", streaming_client(sent, ["Eine Person am Briefkasten."]))
    frame = Frame(source_id="cam/test", timestamp=datetime.now(), image_bytes=b"FAKEJPEG", format="jpeg",
                  width=320, height=240)
    result = asyncio.run(analyze_sequence([frame], "Was ist zu sehen?", model=f"Box/{BIG}"))
    assert sent[0]["url"] == "http://box.test:11435/v1/chat/completions"
    assert sent[0]["payload"]["model"] == BIG
    assert result.model == f"Box/{BIG}" and result.text == "Eine Person am Briefkasten."
    assert released == [f"Box/{BIG}"]  # the machine's GPU-Orc clears its cards


# ── status bar ───────────────────────────────────────────────────────

def test_the_status_bar_shows_load_holders_and_hold(box):
    box["gpu_orc"].cards = [_card(24564, 8834, ("llama-swap", SMALL, 7235))]
    [row] = remote_describers.host_status()
    assert (row.reachable, row.held, row.used_mib, row.total_mib, row.holders) == (
        True, None, 8834, 24564, [remote_describers.HostHolder("llama-swap", SMALL)])
    box["gpu_orc"].held = {"since": "2026-11-05T09:00:00+01:00", "reason": "gaming"}
    assert remote_describers.host_status()[0].held == "gaming"


def test_a_machine_that_is_off_shows_as_unreachable(box):
    box["reachable"] = False
    [row] = remote_describers.host_status()
    assert row.reachable is False and "does not answer" in row.error


def test_holding_and_releasing_goes_to_the_machines_gpu_orc(box):
    remote_describers.set_hold("Box", True, "held from AIfred's status bar")
    assert box["gpu_orc"].holds == ["held from AIfred's status bar"] and box["gpu_orc"].held is not None
    remote_describers.set_hold("Box", False, "")
    assert box["gpu_orc"].held is None
    with pytest.raises(remote_describers.RemoteHostError, match="no machine"):
        remote_describers.set_hold("Nowhere", True, "x")


def test_the_main_model_entry_is_only_aifreds_own_model(monkeypatch):
    """14.11.2026: at night Vigilantia described camera images with another session's loaded test model
    (vllm-research's GLM). "Main model" is AIfred's configured model, in any variant, loaded and seeing."""
    from aifred.lib import config, vision_utils, vlm_naming

    monkeypatch.setattr(config, "get_effective_model_from_settings", lambda _agent="aifred": "Qwen3.6-35B-A3B")
    monkeypatch.setattr(vision_utils, "has_native_vision", lambda _profile: True)
    monkeypatch.setattr(vlm_naming, "is_visiond_profile", lambda _profile: False)
    monkeypatch.setattr(vision_routing, "loaded_llamaswap_profiles", lambda: ["GLM-5.3-Flash-test-vllm"])
    assert vision_routing.loaded_seeing_profile() is None
    monkeypatch.setattr(vision_routing, "loaded_llamaswap_profiles", lambda: ["GLM-5.3-Flash-test-vllm", "Qwen3.6-35B-A3B"])
    assert vision_routing.loaded_seeing_profile() == "Qwen3.6-35B-A3B"
