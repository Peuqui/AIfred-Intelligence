"""What the settings page reads about backend and calibration (lib/calibration/matrix.py, model_info.py,
lib/backend_overview.py, api/backend.py): the status of each matrix cell from the caches, which profile a cell
names, the context and layer split a model starts with, the backends the GPUs can run, who runs on which model.
The caches and the llama-swap file are files of their own here; the TTS service, the describers, the operating
points and the side-channel burn-ins are stand-ins."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import model_vram_cache, settings
from aifred.lib.backend_overview import available_backends
from aifred.lib.calibration import matrix as matrix_module
from aifred.lib.calibration.matrix import CalibrationMatrix, clear_failure, matrix, variant_id
from aifred.lib.calibration.model_info import LayerSplit, RopeContext, model_calibration
from aifred.lib.gpu_detection import GPUInfo
from aifred.lib.tts_client import TTSSuiteUnreachable
from aifred.state._calibration_mixin import CalibrationMixin

if TYPE_CHECKING:
    _StateBase = CalibrationMixin
else:
    _StateBase = object

Profiles = Callable[[dict[str, str]], None]

MODEL = "Big-30B"
ENGINES = {"qwen3local": "Qwen3-TTS", "xtts": "XTTS"}
DESCRIBERS = [{"key": "eyes4b", "model_id": "Eyes-4B", "label": "Eyes-4B"}]
CALIBRATED = {"gpu_model": "RTX 8000, V100", "llamacpp_calibrations": [{"max_context": 32768}]}


def _statuses(result: CalibrationMatrix | None) -> dict[str, str]:
    assert result is not None
    return {cell.key: cell.status for row in result.rows for cell in row.cells}


@pytest.fixture(autouse=True)
def stand_ins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cache file and a llama-swap file of their own, two engines and one describer."""
    monkeypatch.setattr(model_vram_cache, "CACHE_FILE", tmp_path / "model_vram_cache.json")
    monkeypatch.setattr(model_vram_cache, "_cache", None)
    monkeypatch.setattr("aifred.lib.config.LLAMASWAP_CONFIG_PATH", tmp_path / "llama-swap.yaml")
    monkeypatch.setattr("aifred.lib.operating_points.LLAMASWAP_CONFIG_PATH", tmp_path / "llama-swap.yaml")
    monkeypatch.setattr("aifred.lib.tts_service.installed_gpu_engine_keys", lambda: list(ENGINES))
    monkeypatch.setattr("aifred.lib.tts_service.engine_label_short", lambda key, _lang=None: ENGINES[key])
    monkeypatch.setattr("aifred.lib.vision_routing.vlm_calibration_choices", lambda: DESCRIBERS)


def _cache(entries: dict[str, dict[str, Any]]) -> None:
    model_vram_cache.save_cache(entries)


@pytest.fixture
def profiles(tmp_path: Path) -> Profiles:
    """Writes the llama-swap file: one entry per model with its command."""
    def write(models: dict[str, str]) -> None:
        lines = ["models:"]
        for name, cmd in models.items():
            lines += [f"  {name}:", f"    cmd: {cmd}"]
        (tmp_path / "llama-swap.yaml").write_text("\n".join(lines) + "\n")
    return write


# ── The matrix ───────────────────────────────────────────────────────────


def test_a_cell_names_its_profile() -> None:
    assert variant_id(MODEL, "|") == MODEL
    assert variant_id(MODEL, "|xtts") == f"{MODEL}-tts-xtts"
    assert variant_id(MODEL, "eyes4b|") == f"{MODEL}-vlm-eyes4b"
    assert variant_id(MODEL, "eyes4b|xtts") == f"{MODEL}-tts-xtts-vlm-eyes4b"


def test_the_llamacpp_matrix_reads_each_profile_from_the_cache() -> None:
    _cache({
        MODEL: CALIBRATED,
        # A speed profile counts for its TTS cell; a profile only discovered (no gpu_model) does not.
        f"{MODEL}-tts-qwen3local-speed": CALIBRATED,
        f"{MODEL}-tts-xtts": {"gpu_model": "", "llamacpp_calibrations": [{"max_context": 8192}]},
        f"{MODEL}-vlm-eyes4b": {"failure_status": {"reason": "oom"}},
        # A failure wins over an earlier success of the same profile.
        f"{MODEL}-tts-xtts-vlm-eyes4b": {**CALIBRATED, "failure_status": {"reason": "oom"}},
    })
    result = matrix("llamacpp", MODEL, "de")
    assert result is not None
    assert [column.label for column in result.columns] == ["Kein TTS", "Qwen3-TTS", "XTTS"]
    assert [row.label for row in result.rows] == ["Kein VLM", "Eyes-4B"]
    assert _statuses(result) == {
        "|": "done", "|qwen3local": "done", "|xtts": "open",
        "eyes4b|": "failed", "eyes4b|qwen3local": "open", "eyes4b|xtts": "failed",
    }
    assert result.tts_problem == ""


def test_without_a_model_every_cell_is_open() -> None:
    _cache({MODEL: CALIBRATED})
    assert set(_statuses(matrix("llamacpp", "", "de")).values()) == {"open"}


def test_a_silent_tts_service_leaves_the_no_tts_column_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable() -> list[str]:
        raise TTSSuiteUnreachable("connection refused")

    monkeypatch.setattr("aifred.lib.tts_service.installed_gpu_engine_keys", unreachable)
    result = matrix("llamacpp", MODEL, "en")
    assert result is not None
    assert [column.tts_key for column in result.columns] == [""]
    assert "connection refused" in result.tts_problem
    assert matrix_module.all_cell_keys() == ["|", "eyes4b|"]


def test_the_vllm_matrix_shows_the_operating_point_and_the_burn_in_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    verdicts = {("eyes4b", ""): True, ("", "xtts"): False}
    monkeypatch.setattr("aifred.lib.operating_points.is_vllm_calibrated", lambda model_id: model_id == MODEL)
    monkeypatch.setattr("aifred.lib.sidechannel_vram_cache.fits", lambda vlm, tts: verdicts.get((vlm, tts)))
    assert _statuses(matrix("vllm", MODEL, "de")) == {
        "|": "done", "|qwen3local": "open", "|xtts": "failed",
        "eyes4b|": "done", "eyes4b|qwen3local": "open", "eyes4b|xtts": "open",
    }
    assert _statuses(matrix("vllm", "Other", "de"))["|"] == "open"


def test_ollama_and_the_cloud_have_no_matrix() -> None:
    assert matrix("ollama", MODEL, "de") is None
    assert matrix("cloud_api", MODEL, "de") is None


def test_ticking_a_failed_cell_drops_only_its_failure_record() -> None:
    _cache({f"{MODEL}-vlm-eyes4b": {"gpu_model": "", "failure_status": {"reason": "oom"}}, MODEL: CALIBRATED})
    assert clear_failure(MODEL, "eyes4b|")
    assert model_vram_cache.load_cache()[f"{MODEL}-vlm-eyes4b"] == {"gpu_model": ""}
    assert not clear_failure(MODEL, "eyes4b|")  # nothing left to drop
    assert not clear_failure(MODEL, "|")  # the base has none
    assert not clear_failure(MODEL, "gone8b|")  # a describer that is no longer there
    assert not clear_failure("", "eyes4b|")


# ── A model's calibration ────────────────────────────────────────────────


def test_llamacpp_starts_with_the_profile_and_names_a_cache_that_lags_behind(profiles: Profiles) -> None:
    profiles({
        MODEL: "llama-server -m big.gguf -c 65536 --tensor-split 19,20,13,9,0",
        f"{MODEL}-speed": "llama-server -m big.gguf -c 32768 --tensor-split 30,31",
    })
    _cache({MODEL: CALIBRATED})
    info = model_calibration("llamacpp", MODEL)
    assert info is not None
    assert (info.context, info.cache_context) == (65536, 32768)
    assert info.split == LayerSplit((19, 20, 13, 9, 0), 4, 5, 65536)
    # The speed profile names only the cards it uses; the count is out of the base profile's cards.
    assert info.speed == LayerSplit((30, 31), 2, 5, 32768)


def test_llamacpp_without_a_profile_takes_the_cache_and_without_both_is_not_calibrated(profiles: Profiles) -> None:
    profiles({"Other": "llama-server -m other.gguf -c 4096"})
    _cache({MODEL: CALIBRATED})
    info = model_calibration("llamacpp", MODEL)
    assert info is not None
    assert (info.context, info.cache_context, info.split, info.speed) == (32768, 0, None, None)
    unknown = model_calibration("llamacpp", "Unknown")
    assert unknown is not None and unknown.context == 0


def test_ollama_lists_the_context_of_each_measured_rope_factor() -> None:
    _cache({MODEL: {"ollama_calibrations": [{"max_context_1.0x": 40960}, {"max_context_2.0x": 81920}]}})
    info = model_calibration("ollama", MODEL)
    assert info is not None
    assert info.rope_contexts == (RopeContext(1.0, 40960), RopeContext(2.0, 81920))


def test_vllm_takes_the_context_of_its_entry_and_a_describer_its_profile(profiles: Profiles) -> None:
    profiles({f"{MODEL}-vllm": "vllm serve /models/big --max-model-len 131072", "Eyes-4B": "llama-server -m eyes.gguf -c 8192"})
    _cache({"Eyes-4B": CALIBRATED})
    info = model_calibration("vllm", f"{MODEL}-vllm")
    assert info is not None and info.context == 131072
    # The describer is a llama.cpp profile under vLLM too.
    describer = model_calibration("vllm", "Eyes-4B")
    assert describer is not None and (describer.context, describer.cache_context) == (8192, 32768)


def test_the_cloud_and_no_model_have_no_calibration() -> None:
    assert model_calibration("cloud_api", MODEL) is None
    assert model_calibration("llamacpp", "") is None


# ── The Reflex console reads the same ───────────────────────────────────


class _Console(_StateBase):
    _show_model_calibration_info = CalibrationMixin._show_model_calibration_info

    def __init__(self, backend_type: str) -> None:
        self.backend_type = backend_type
        self.lines: list[str] = []

    def add_debug(self, message: str) -> None:
        self.lines.append(message)


def test_the_reflex_console_words_the_calibration_as_before(profiles: Profiles) -> None:
    from aifred.lib.formatting import set_ui_locale

    set_ui_locale("de")
    profiles({
        MODEL: "llama-server -m big.gguf -c 65536 --tensor-split 19,20,13,9,0",
        f"{MODEL}-speed": "llama-server -m big.gguf -c 32768 --tensor-split 30,31",
    })
    _cache({MODEL: CALIBRATED})
    console = _Console("llamacpp")
    console._show_model_calibration_info(MODEL)
    assert console.lines == [
        "   🎯 Configured: 65.536 tokens (cache shows 32.768 — re-calibrate to sync)",
        "   📊 Layer split: 19:20:13:9:0 (4/5 GPUs)",
        "   ⚡ Speed split: 30:31 (2/5 GPUs), ctx=32.768",
    ]
    _cache({"Other": {"ollama_calibrations": [{"max_context_1.0x": 40960, "max_context_1.5x": 61440}]}})
    console = _Console("ollama")
    console._show_model_calibration_info("Other")
    console._show_model_calibration_info("Unknown")
    assert console.lines == [
        "   🎯 Calibrated: Native: 40.960, RoPE 1.5x: 61.440",
        "   ⚠️ Not calibrated - please run calibration for optimal context",
    ]


# ── Backends and who runs on which model ────────────────────────────────


def _gpu(recommended: list[str]) -> GPUInfo:
    """One card, as the detector describes it."""
    return GPUInfo(name="Tesla V100", compute_capability=7.0, vram_mb=32768, has_tensor_cores=True,
                   supports_fast_fp16=True, recommended_backends=recommended, unsupported_backends=[], warnings=[],
                   total_vram_mb=32768, all_gpu_names=["Tesla V100"], all_gpu_vram_mb=[32768], all_gpu_compute_caps=[7.0])


def test_the_backends_are_the_ones_a_card_can_run_and_the_cloud() -> None:
    assert available_backends(_gpu(["ollama", "llamacpp"])) == ["ollama", "llamacpp", "cloud_api"]
    assert available_backends(None) == ["ollama", "llamacpp", "vllm", "cloud_api"]
    assert available_backends(_gpu([])) == ["ollama", "llamacpp", "vllm", "cloud_api"]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from aifred.lib import agent_choices
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    file = tmp_path / "settings.json"
    file.write_text(json.dumps({
        "backend_type": "llamacpp", "ui_language": "de",
        "backend_models": {"llamacpp": {"aifred": MODEL, "sokrates": "", "salomo": "Small-4B", "automatik": "", "vision": "Eyes-4B"}},
    }))
    monkeypatch.setattr(settings, "SETTINGS_FILE", file)
    monkeypatch.setattr(agent_choices, "chat_agents", lambda: [("aifred", "AIfred", "🎩"), ("sokrates", "Sokrates", "🏛️"), ("salomo", "Salomo", "👑")])
    monkeypatch.setattr("aifred.lib.prompt_loader.get_language", lambda: "de")
    monkeypatch.setattr("aifred.lib.gpu_detection.server_gpu_info", lambda: _gpu(["llamacpp", "vllm"]))
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    yield test_client


def test_the_calibration_overview_groups_the_agents_by_model(client: TestClient, profiles: Profiles) -> None:
    profiles({MODEL: "llama-server -m big.gguf -c 65536"})
    _cache({MODEL: CALIBRATED})
    answer = client.get("/calibration").json()
    assert (answer["backend_type"], answer["model_id"]) == ("llamacpp", MODEL)
    by_model = {entry["model_id"]: entry for entry in answer["models"]}
    assert list(by_model) == [MODEL, "Small-4B", "Eyes-4B"]
    assert by_model[MODEL]["agents"] == ["🎩 AIfred", "🏛️ Sokrates", "⚡ Automatik"]
    assert by_model["Small-4B"]["agents"] == ["👑 Salomo"]
    assert by_model["Eyes-4B"]["agents"][0].endswith("Vision")
    assert by_model[MODEL]["calibration"]["context"] == 65536
    assert answer["matrix"]["rows"][0]["cells"][0] == {"key": "|", "status": "done"}


def test_the_backend_overview_offers_what_the_cards_can_run(client: TestClient) -> None:
    answer = client.get("/backend").json()
    assert answer["backend_type"] == "llamacpp"
    assert [choice["id"] for choice in answer["backends"]] == ["llamacpp", "vllm", "cloud_api"]
    assert [card["name"] for card in answer["gpu"]["cards"]] == ["Tesla V100"]
    assert answer["cloud_provider"] == "qwen"
    assert {provider["id"] for provider in answer["cloud_providers"]} == {"claude", "qwen", "deepseek", "kimi"}
