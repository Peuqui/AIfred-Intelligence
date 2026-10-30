"""GPU-Orc client: the two forms of /free AIfred sends, what it reads from /status, and the caller that
clears every card but the models (calibration)."""

from __future__ import annotations

import pytest
from fake_gpu_orc import FakeGpuOrc, card, install_fake_gpu_orc

from aifred.lib.config import GPU_ORC_MODEL_SERVER
from aifred.lib.gpu_orc_client import (
    CardHolder,
    CardStatus,
    FreeAction,
    FreeResult,
    GpuOrcError,
    describe,
    get_gpu_orc_client,
)


@pytest.fixture
def fake(monkeypatch) -> FakeGpuOrc:
    service = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, service)
    return service


def test_the_model_form_names_the_model_server_and_the_reason(fake) -> None:
    fake.actions = [{"consumer": "stt-suite", "item": "gpu-worker", "outcome": "released_forced", "detail": "waited"}]
    result = get_gpu_orc_client().free_for_model("m", reason="AIfred: loading 'm'")
    assert fake.frees == [{"model": "m", "model_server": GPU_ORC_MODEL_SERVER, "reason": "AIfred: loading 'm'"}]
    assert result == FreeResult(False, True, (FreeAction("stt-suite", "gpu-worker", "released_forced", "waited"),))


def test_clearing_all_cards_keeps_the_model_servers_models(fake) -> None:
    get_gpu_orc_client().free_all_but_models(reason="AIfred: calibration")
    assert fake.frees == [{"gpus": "all", "keep": [GPU_ORC_MODEL_SERVER], "reason": "AIfred: calibration"}]


def test_a_refused_request_is_an_error_with_gpu_orcs_reason(fake) -> None:
    fake.refusal = (400, "model 'x' is not in the config of llama-swap")
    with pytest.raises(GpuOrcError, match=r"HTTP 400.*not in the config"):
        get_gpu_orc_client().free_for_model("x", reason="r")


def test_a_gpu_orc_that_does_not_answer_is_an_error_naming_the_address() -> None:
    # conftest points the client at a dead address
    with pytest.raises(GpuOrcError, match=r"not reachable at http://127\.0\.0\.1:9 \(/health\)"):
        get_gpu_orc_client().free_for_model("m", reason="r")


def test_the_status_is_read_per_card(fake) -> None:
    fake.cards = [card("GPU-a", ("stt-suite", "gpu-worker", 3100), ("llama-swap", "big", None)), card("GPU-b")]
    assert get_gpu_orc_client().card_status() == [
        CardStatus("GPU-a", (CardHolder("stt-suite", "gpu-worker", 3100), CardHolder("llama-swap", "big", None))),
        CardStatus("GPU-b", ()),
    ]


def test_describe_says_what_was_released_what_stayed_and_whether_the_cards_are_free() -> None:
    result = FreeResult(False, False, (
        FreeAction("stt-suite", "gpu-worker", "released", ""),
        FreeAction("llama-swap", "embed", "kept", ""),
        FreeAction("tts-suite", "xtts", "not_freed", "service: container still running"),
        FreeAction("tts-suite", "", "error", "/gpu/holders answered HTTP 500"),
    ))
    assert describe(result) == [
        ("🧹 GPU-Orc: stt-suite gpu-worker released", "info"),
        ("⚠️ GPU-Orc: tts-suite xtts not_freed (service: container still running)", "warning"),
        ("⚠️ GPU-Orc: tts-suite error (/gpu/holders answered HTTP 500)", "warning"),
        ("⚠️ GPU-Orc: the cards are not free", "warning"),
    ]


def test_an_already_running_model_says_nothing() -> None:
    assert describe(FreeResult(True, True, ())) == []


# ---- Calibration: the cards must be free ----------------------------------------------------------


def test_the_calibration_clears_every_card_but_the_models_and_says_what_came_of_it(fake) -> None:
    from aifred.state._calibration_mixin import _free_cards_for_calibration

    fake.actions = [{"consumer": "tts-suite", "item": "xtts", "outcome": "released"}]
    said: list[str] = []
    _free_cards_for_calibration(said.append)
    assert fake.frees == [{"gpus": "all", "keep": [GPU_ORC_MODEL_SERVER], "reason": "AIfred: calibration"}]
    assert said == ["   🧹 GPU-Orc: tts-suite xtts released"]


def test_a_gpu_orc_that_does_not_answer_is_said_and_the_calibration_goes_on() -> None:
    from aifred.state._calibration_mixin import _free_cards_for_calibration

    said: list[str] = []
    _free_cards_for_calibration(said.append)  # conftest points the client at a dead address
    assert len(said) == 1 and "GPU-Orc could not clear the cards for the calibration" in said[0]
