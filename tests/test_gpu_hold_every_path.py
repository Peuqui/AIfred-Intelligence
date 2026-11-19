"""Every path of AIfred onto the cards respects the hold (GPU-Orc ``/hold``), not only the first request of an LLM turn
(tests/test_gpu_hold_respected.py): the later rounds of the tool loop, Ollama, every describer request, embeddings on a
card, and, outside a turn, the choice of a loaded describer, the prewarm, the calibration start, the llama-swap start and
the person detector. Work inside a turn waits; work outside a turn is refused and ends like "no describer free"."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from fake_gpu_orc import FakeGpuOrc, install_fake_gpu_orc

from aifred.backends import base
from aifred.backends.base import LLMMessage, LLMOptions
from aifred.backends.cloud_api import CloudAPIBackend
from aifred.backends.ollama import OllamaBackend
from aifred.backends.vllm import vLLMBackend
from aifred.lib import (
    calibration_gate,
    embeddings,
    gpu_hold,
    gpu_orc_client,
    process_utils,
    remote_describers,
    vision_analyzer,
    vision_burst_media,
    vision_event_analysis,
    vision_prewarm,
    vision_routing,
    vision_utils,
    vision_vram_check,
)
from aifred.lib.calibration import llamaswap_io, service
from aifred.lib.calibration.service import CalibrationRequest, CalibrationRun
from aifred.lib.frame_sources import Frame
from aifred.lib.gpu_orc_client import GpuOrcClient
from aifred.lib.user_error import Conflict, UserError
from aifred.lib.vision_routing import Describer, NoVisionModelError

MODEL = "Big-Model"
DESCRIBER = "Small-VL-4B-visiond"
HOLD = {"since": "2026-11-19T15:55:00+01:00", "reason": "vllm-research: v13"}


@pytest.fixture
def orc(monkeypatch: pytest.MonkeyPatch) -> FakeGpuOrc:
    fake = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, fake)
    monkeypatch.setattr(remote_describers, "remote_hosts", list)   # no other machine
    return fake


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    seen: dict[str, list[Any]] = {"phases": [], "log": [], "sleeps": []}
    monkeypatch.setattr(gpu_hold, "report_phase", lambda phase, **_kw: seen["phases"].append(phase))
    monkeypatch.setattr(gpu_hold, "log_message", lambda message, *_a, **_k: seen["log"].append(message))
    return seen


def _release_after(orc: FakeGpuOrc, seen: dict[str, list[Any]], looks: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hold ends after ``looks`` sleeps (async and blocking ones)."""
    def look() -> None:
        seen["sleeps"].append(1)
        if len(seen["sleeps"]) >= looks:
            orc.held = None

    async def sleep(_seconds: float) -> None:
        look()

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(time, "sleep", lambda _seconds: look())


# ── 1. the LLM, every round ───────────────────────────────────────────────────────────────────────


async def _no_chunks() -> AsyncIterator[Any]:
    return
    yield


def _round_backend(orc: FakeGpuOrc, at_request: list[Any]) -> vLLMBackend:
    backend = vLLMBackend(base_url="http://127.0.0.1:9/v1")

    async def create(**_kwargs: Any) -> AsyncIterator[Any]:
        at_request.append(orc.held)   # what the request found
        return _no_chunks()

    backend.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))  # type: ignore[assignment]
    return backend


async def _one_round(backend: base.OpenAICompatibleBackend) -> None:
    async for _ in backend.stream_round(MODEL, [{"role": "user", "content": "hi"}], LLMOptions(), None):
        pass


def test_a_hold_set_after_round_one_makes_round_two_wait(orc, seen, monkeypatch) -> None:
    at_request: list[Any] = []
    backend = _round_backend(orc, at_request)

    async def two_rounds() -> None:
        await _one_round(backend)
        orc.held = dict(HOLD)             # set during the turn
        _release_after(orc, seen, 2, monkeypatch)
        await _one_round(backend)

    asyncio.run(two_rounds())
    assert at_request == [None, None]      # round 2 went out only after the hold ended
    assert len(seen["sleeps"]) == 2 and seen["phases"] == ["gpu_hold", ""]


def test_a_cloud_api_asks_nothing(orc, seen) -> None:
    orc.held = dict(HOLD)
    backend = CloudAPIBackend(base_url="http://127.0.0.1:9/v1", api_key="k")
    at_request: list[Any] = []

    async def create(**_kwargs: Any) -> AsyncIterator[Any]:
        at_request.append("sent")
        return _no_chunks()

    backend.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))  # type: ignore[assignment]
    asyncio.run(_one_round(backend))
    assert at_request == ["sent"] and orc.requests == []


# ── 2. Ollama ─────────────────────────────────────────────────────────────────────────────────────


def _ollama(orc: FakeGpuOrc, at_request: list[Any], monkeypatch: pytest.MonkeyPatch) -> OllamaBackend:
    def handler(request: httpx.Request) -> httpx.Response:
        at_request.append(orc.held)
        line = {"message": {"role": "assistant", "content": "ok"}, "done": True, "done_reason": "stop"}
        return httpx.Response(200, content=json.dumps(line).encode())

    backend = OllamaBackend("http://ollama.test")
    backend.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def no_unload(_model: str) -> None:
        return None

    monkeypatch.setattr(backend, "_handle_hybrid_model_unload", no_unload)
    return backend


def test_ollama_chat_waits(orc, seen, monkeypatch) -> None:
    at_request: list[Any] = []
    backend = _ollama(orc, at_request, monkeypatch)
    orc.held = dict(HOLD)
    _release_after(orc, seen, 1, monkeypatch)
    asyncio.run(backend.chat("qwen3:8b", [LLMMessage(role="user", content="hi")]))
    assert at_request == [None] and seen["phases"] == ["gpu_hold", ""]


def test_ollama_stream_round_waits_every_round(orc, seen, monkeypatch) -> None:
    at_request: list[Any] = []
    backend = _ollama(orc, at_request, monkeypatch)

    async def rounds() -> None:
        async for _ in backend.stream_round("qwen3:8b", [{"role": "user", "content": "hi"}], LLMOptions(), None):
            pass
        orc.held = dict(HOLD)
        _release_after(orc, seen, 1, monkeypatch)
        async for _ in backend.stream_round("qwen3:8b", [{"role": "user", "content": "hi"}], LLMOptions(), None):
            pass

    asyncio.run(rounds())
    assert at_request == [None, None] and seen["phases"] == ["gpu_hold", ""]


def test_ollama_request_check_and_preload_wait(orc, seen, monkeypatch) -> None:
    at_request: list[Any] = []
    backend = _ollama(orc, at_request, monkeypatch)
    orc.held = dict(HOLD)
    _release_after(orc, seen, 1, monkeypatch)
    assert asyncio.run(backend._pre_request_check("qwen3:8b")) == 0.0
    assert len(seen["sleeps"]) == 1
    orc.held = dict(HOLD)
    seen["sleeps"].clear()
    _release_after(orc, seen, 1, monkeypatch)
    assert asyncio.run(backend.preload_model("qwen3:8b"))[0] is True
    assert at_request == [None] and len(seen["sleeps"]) == 1     # the preload request went out after the hold


# ── 3. every describer request ────────────────────────────────────────────────────────────────────


def _frame() -> Frame:
    return Frame(source_id="cam/test", timestamp=datetime.now(), image_bytes=b"FAKEJPEG", format="jpeg", width=320, height=240)


@pytest.fixture
def described(monkeypatch: pytest.MonkeyPatch, orc: FakeGpuOrc) -> list[Any]:
    """The requests that reached the describer, each with the hold it found."""
    at_request: list[Any] = []

    async def via_llamacpp(model: str, prompt: str, images_b64: list[str], *, n_frames: int) -> vision_analyzer.VisionAnalysis:
        at_request.append(orc.held)
        return vision_analyzer.VisionAnalysis(text="a door", model=model, prompt=prompt, n_frames=n_frames, duration_ms=1.0)

    monkeypatch.setattr(vision_analyzer, "_analyze_via_llamacpp", via_llamacpp)
    monkeypatch.setattr(vision_utils, "has_native_vision", lambda _model: True)
    monkeypatch.setattr(calibration_gate, "is_calibration_active", lambda: False)
    return at_request


def test_a_describer_request_inside_a_turn_waits(orc, seen, described, monkeypatch) -> None:
    orc.held = dict(HOLD)
    _release_after(orc, seen, 2, monkeypatch)
    result = asyncio.run(vision_analyzer.analyze_sequence([_frame()], "what?", model=DESCRIBER, while_held="wait"))
    assert result.text == "a door" and described == [None]
    assert seen["phases"] == ["gpu_hold", ""]


def test_a_describer_request_outside_a_turn_is_refused(orc, seen, described) -> None:
    orc.held = dict(HOLD)
    with pytest.raises(NoVisionModelError, match="vllm-research: v13"):
        asyncio.run(vision_analyzer.analyze_sequence([_frame()], "what?", model=DESCRIBER, while_held="refuse"))
    assert described == [] and seen["phases"] == []


def test_a_describer_request_without_a_hold_goes_out_at_once(orc, seen, described) -> None:
    for policy in ("wait", "refuse"):
        asyncio.run(vision_analyzer.analyze_sequence([_frame()], "what?", model=DESCRIBER, while_held=policy))
    assert described == [None, None] and seen["phases"] == []


def test_the_describer_of_another_machine_asks_the_orc_there(orc, seen, described, monkeypatch) -> None:
    box = FakeGpuOrc()
    host = remote_describers.RemoteHost("Box", "http://box.test:11435", "http://box.test:5070", "/x")
    monkeypatch.setattr(remote_describers, "remote_hosts", lambda: [host])
    monkeypatch.setattr(remote_describers, "gpu_orc", lambda _host: GpuOrcClient(box.serve(), token="t"))
    box.held = dict(HOLD)                 # held there, free here
    with pytest.raises(NoVisionModelError):
        asyncio.run(vision_analyzer.analyze_sequence([_frame()], "what?", model=f"Box/{DESCRIBER}", while_held="refuse"))
    assert orc.requests == [] and "GET /loading" in box.requests and described == []
    box.held = None
    orc.held = dict(HOLD)                 # held here, free there: the request goes out
    asyncio.run(vision_analyzer.analyze_sequence([_frame()], "what?", model=f"Box/{DESCRIBER}", while_held="refuse"))
    assert described == [dict(HOLD)]


def test_the_retry_after_an_empty_answer_is_checked_too(orc, seen, described, monkeypatch) -> None:
    answers: list[str] = ["", "a door"]

    async def via_llamacpp(model: str, prompt: str, images_b64: list[str], *, n_frames: int) -> vision_analyzer.VisionAnalysis:
        described.append(orc.held)
        if len(described) == 1:
            orc.held = dict(HOLD)         # set between the first request and its retry
        return vision_analyzer.VisionAnalysis(text=answers[len(described) - 1], model=model, prompt=prompt, n_frames=n_frames, duration_ms=1.0)

    monkeypatch.setattr(vision_analyzer, "_analyze_via_llamacpp", via_llamacpp)
    _release_after(orc, seen, 1, monkeypatch)
    result = asyncio.run(vision_analyzer.analyze_sequence([_frame()], "what?", model=DESCRIBER, while_held="wait"))
    assert result.text == "a door" and described == [None, None] and len(seen["sleeps"]) == 1


def test_vigilantia_under_a_hold_ends_like_no_describer_free(orc, seen, described, monkeypatch) -> None:
    """The burst's description (background, refuse): no crash, no hidden drop, the info line of "no describer free"."""
    orc.held = dict(HOLD)
    monkeypatch.setattr(vision_routing, "camera_describer", lambda _model, *, explicit: Describer(DESCRIBER, evicts_chat_model=False))
    monkeypatch.setattr("aifred.lib.vision_prewarm.get_active_vlm_model", lambda: "Small-VL-4B")

    async def cluster(_ids: Any, **_kw: Any) -> str:
        description, _ = await vision_event_analysis.analyze_frames_with_vlm(
            [_frame()], base_prompt="p", source_id="cam/test", model="Small-VL-4B", explicit=False,
        )
        return description

    monkeypatch.setattr(vision_burst_media, "analyze_cluster_with_vlm", cluster)
    monkeypatch.setattr(vision_event_analysis, "build_vlm_prompt", lambda *_a, **_k: "p")
    infos: list[str] = []
    monkeypatch.setattr(vision_burst_media.logger, "info", lambda message, *a: infos.append(message % a))
    monkeypatch.setattr("aifred.lib.prompt_loader.get_vision_event_summary_prompt", lambda: "summary")
    description = asyncio.run(vision_burst_media._describe(SimpleNamespace(), [{"id": 1, "frame_path": "a.jpg"}], 0))  # type: ignore[arg-type]
    assert description == "" and infos == ["burst: no describer free, burst without description"]
    assert described == []


# ── 4. embeddings ─────────────────────────────────────────────────────────────────────────────────


def _embed_on(profile: dict[str, Any] | None, monkeypatch: pytest.MonkeyPatch, at_request: list[Any], orc: FakeGpuOrc) -> None:
    monkeypatch.setattr(embeddings, "_llamaswap_embed_profile", lambda: profile)

    def via_llamaswap(self: Any, _input: list[str]) -> list[Any]:
        at_request.append(orc.held)
        return [np.zeros(3, dtype=np.float32)]

    monkeypatch.setattr(embeddings.OllamaEmbeddingFunction, "_embed_via_llamaswap", via_llamaswap)


def test_embeddings_on_a_card_wait(orc, seen, monkeypatch) -> None:
    at_request: list[Any] = []
    _embed_on({"ngl": 99, "env": {"CUDA_VISIBLE_DEVICES": "GPU-abc"}}, monkeypatch, at_request, orc)
    orc.held = dict(HOLD)
    _release_after(orc, seen, 2, monkeypatch)
    embeddings.OllamaEmbeddingFunction()(["text"])
    assert at_request == [None] and len(seen["sleeps"]) == 2


@pytest.mark.parametrize("profile", [
    {"ngl": 0, "env": {"CUDA_VISIBLE_DEVICES": "GPU-abc"}},   # -ngl 0: no layer on a card
    {"ngl": 99, "env": {"CUDA_VISIBLE_DEVICES": ""}},         # no card visible
])
def test_embeddings_on_the_cpu_ask_nothing(orc, seen, monkeypatch, profile) -> None:
    at_request: list[Any] = []
    _embed_on(profile, monkeypatch, at_request, orc)
    orc.held = dict(HOLD)
    embeddings.OllamaEmbeddingFunction()(["text"])
    assert at_request == [HOLD] and orc.requests == []


def test_ollama_index_embeddings_on_the_gpu_wait_and_query_embeddings_do_not(orc, seen, monkeypatch) -> None:
    import ollama

    at_request: list[Any] = []

    class FakeClient:
        def __init__(self, **_kw: Any) -> None:
            pass

        def embed(self, **_kw: Any) -> dict[str, Any]:
            at_request.append(orc.held)
            return {"embeddings": [[0.0, 0.0, 0.0]]}

    monkeypatch.setattr(ollama, "Client", FakeClient)
    monkeypatch.setattr("aifred.lib.config.EMBEDDING_USE_GPU", True)
    _embed_on(None, monkeypatch, at_request, orc)
    orc.held = dict(HOLD)
    embeddings.OllamaEmbeddingFunction(mode="query")(["text"])
    assert at_request == [HOLD] and orc.requests == []            # CPU: nothing asked
    at_request.clear()
    _release_after(orc, seen, 1, monkeypatch)
    embeddings.OllamaEmbeddingFunction(mode="index")(["text"])
    assert at_request == [None] and len(seen["sleeps"]) == 1


# ── 5. the choice of a describer ──────────────────────────────────────────────────────────────────


def test_a_loaded_describer_is_not_used_while_the_cards_are_held(orc, monkeypatch) -> None:
    monkeypatch.setattr(vision_routing, "visiond_profile_for", lambda _name: DESCRIBER)
    monkeypatch.setattr(llamaswap_io, "parse_llamaswap_config", lambda _path: {DESCRIBER: {}})
    monkeypatch.setattr(vision_routing, "loaded_llamaswap_profiles", lambda: [DESCRIBER])
    monkeypatch.setattr(vision_vram_check, "check_visiond_fits", lambda profile: SimpleNamespace(fits=True, profile=profile))
    assert vision_routing.fitting_visiond("Small-VL-4B")[0] == DESCRIBER
    orc.held = dict(HOLD)
    profile, fit = vision_routing.fitting_visiond("Small-VL-4B")
    assert profile is None and fit is not None and "vllm-research: v13" in fit.message


def test_a_loaded_model_of_another_machine_is_not_used_while_its_cards_are_held(orc, monkeypatch) -> None:
    box = FakeGpuOrc()
    host = remote_describers.RemoteHost("Box", "http://box.test:11435", "http://box.test:5070", "/x")
    monkeypatch.setattr(remote_describers, "gpu_orc", lambda _host: GpuOrcClient(box.serve(), token="t"))
    model = remote_describers.RemoteModel(host, DESCRIBER, 24576, True)
    assert remote_describers.check_fits(model).fits is True
    box.held = dict(HOLD)
    fit = remote_describers.check_fits(model)
    assert fit.fits is False and "vllm-research: v13" in fit.message


# ── 6. the prewarm ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("profile", [DESCRIBER, None], ids=["llama-swap profile", "ollama"])
def test_the_prewarm_loads_nothing_while_the_cards_are_held(orc, monkeypatch, profile) -> None:
    monkeypatch.setattr(vision_prewarm, "load_vision_settings", lambda: {"vision_mode": "live", "vlm": {"model": "Small-VL-4B"}})
    monkeypatch.setattr("aifred.lib.plugin_registry.is_plugin_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(vision_routing, "fitting_visiond", lambda _name: (profile, None))
    loads: list[str] = []

    class NoLoad:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            loads.append("client")

    monkeypatch.setattr(httpx, "AsyncClient", NoLoad)
    import ollama
    monkeypatch.setattr(ollama, "AsyncClient", NoLoad)
    orc.held = dict(HOLD)
    assert asyncio.run(vision_prewarm.prewarm_vlm()) is False
    assert loads == []


# ── 7. the calibration start ──────────────────────────────────────────────────────────────────────


def _calibration_request() -> CalibrationRequest:
    return CalibrationRequest(backend_type="llamacpp", backend_url="http://127.0.0.1:9/v1", model_id=MODEL, cells={"|": True})


def test_the_calibration_does_not_start_while_the_cards_are_held(orc) -> None:
    orc.held = dict(HOLD)
    with pytest.raises(Conflict) as refusal:
        service.start_server_run(_calibration_request())
    assert "vllm-research: v13" in refusal.value.message("en") and "vllm-research: v13" in refusal.value.message("de")
    assert refusal.value.key == "calibration_cards_held"
    assert calibration_gate.is_calibration_active() is False and service.latest_server_run() is None


# ── 8. llama-swap start and restart ───────────────────────────────────────────────────────────────


@pytest.fixture
def systemctl(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    def run(cmd: list[str], **_kw: Any) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(process_utils.subprocess, "run", run)
    return calls


def test_llama_swap_is_not_started_or_restarted_while_the_cards_are_held(orc, systemctl) -> None:
    orc.held = dict(HOLD)
    for start in (process_utils.start_llama_swap, process_utils.restart_llama_swap):
        with pytest.raises(Conflict) as refusal:
            start()
        assert refusal.value.key == "llamaswap_cards_held" and "vllm-research: v13" in refusal.value.message("de")
    assert systemctl == []


def test_llama_swap_can_be_stopped_under_a_hold_and_started_without_one(orc, systemctl) -> None:
    orc.held = dict(HOLD)
    assert process_utils.stop_llama_swap() is True
    orc.held = None
    assert process_utils.start_llama_swap() is True
    assert systemctl == [["systemctl", "stop", "llama-swap"], ["systemctl", "start", "llama-swap"]]


def test_a_calibration_that_ends_under_a_hold_leaves_llama_swap_stopped_and_says_so(orc, systemctl) -> None:
    lines: list[str] = []
    run = CalibrationRun(_calibration_request(), report=lines.append)
    orc.held = dict(HOLD)
    assert asyncio.run(run._start_llama_swap()) is False
    assert systemctl == [] and any("stays stopped" in line and "vllm-research: v13" in line for line in lines)


# ── 9. the person detector ────────────────────────────────────────────────────────────────────────


def test_the_person_detector_uses_the_cpu_while_the_cards_are_held_and_moves_when_they_are_free(orc, monkeypatch) -> None:
    from aifred.lib.vision_filters import face_detect, person_detect

    built: list[list[str]] = []

    class FakeDetector:
        def __init__(self, providers: list[str]) -> None:
            built.append(providers)

    monkeypatch.setattr(person_detect, "PersonDetector", FakeDetector)
    monkeypatch.setattr(person_detect, "_default_detector", None)
    monkeypatch.setattr(person_detect, "_default_is_stand_in", False)
    monkeypatch.setattr("aifred.lib.config.PERSON_DETECT_USE_GPU", True)
    monkeypatch.setattr(face_detect, "_preload_cuda_runtime", lambda: True)
    orc.held = dict(HOLD)
    first = person_detect.get_default_detector()
    assert person_detect.get_default_detector() is first and built == [["CPUExecutionProvider"]]
    orc.held = None
    assert person_detect.get_default_detector() is not first
    assert built == [["CPUExecutionProvider"], ["CUDAExecutionProvider"]]
    assert person_detect.get_default_detector() is person_detect.get_default_detector()   # on the card: no more asking


# ── the shared helper ─────────────────────────────────────────────────────────────────────────────


def test_hold_refusal_names_the_reason_and_is_none_without_a_hold_or_without_gpu_orc(orc, monkeypatch) -> None:
    client = gpu_orc_client.get_gpu_orc_client()
    assert gpu_hold.hold_refusal(client) is None
    orc.held = dict(HOLD)
    refusal = gpu_hold.hold_refusal(client)
    assert isinstance(refusal, UserError) and refusal.key == "vram_cards_held" and refusal.values == {"reason": HOLD["reason"]}
    assert gpu_hold.hold_refusal(None) is None
