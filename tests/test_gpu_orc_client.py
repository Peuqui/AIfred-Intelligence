"""AIfred's GPU-Orc client: what it adds to the protocol (the model server's name, the status line after a
release, the typed card status, the log lines) and the caller that clears every card but the models
(calibration). The protocol itself (timeouts, errors, token, forms) is tested against the real GPU-Orc in its
own repo."""

from __future__ import annotations

import pytest
from fake_gpu_orc import FakeGpuOrc, card, current_client, install_fake_gpu_orc

from aifred.lib.config import GPU_ORC_MODEL_SERVER
from aifred.lib.gpu_orc_client import (
    CardHolder,
    CardStatus,
    FreeAction,
    FreeResult,
    TrafficLight,
    describe,
)


@pytest.fixture
def fake(monkeypatch) -> FakeGpuOrc:
    service = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, service)
    return service


def test_the_model_form_names_the_model_server_and_the_reason(fake) -> None:
    fake.actions = [{"consumer": "stt-suite", "item": "gpu-worker", "outcome": "released_forced", "detail": "waited"}]
    result = current_client().free_for_model("m", reason="AIfred: loading 'm'")
    assert fake.frees == [
        {"model": "m", "model_server": GPU_ORC_MODEL_SERVER, "reason": "AIfred: loading 'm'", "keep": []},
    ]
    assert result == FreeResult(
        free=True, already_running=False,
        actions=(FreeAction("stt-suite", "gpu-worker", "released_forced", "waited"),),
    )


@pytest.mark.parametrize(
    ("outcome", "read_again"),
    [("released", ["stt", "tts"]), ("released_forced", ["stt", "tts"]), ("refused_busy", []), ("kept", [])],
)
def test_a_release_has_the_status_line_read_stt_and_tts_again(fake, monkeypatch, outcome, read_again) -> None:
    from aifred.lib import gpu_orc_client

    asked: list[str] = []
    monkeypatch.setattr(gpu_orc_client, "notify_changed", asked.append)
    fake.actions = [{"consumer": "stt-suite", "item": "gpu-worker", "outcome": outcome}]
    current_client().free_for_model("m", reason="r")
    assert asked == read_again


def test_clearing_all_cards_keeps_the_model_servers_models(fake) -> None:
    current_client().free_all_but_models(reason="AIfred: calibration")
    assert fake.frees == [{"gpus": "all", "keep": [GPU_ORC_MODEL_SERVER], "reason": "AIfred: calibration"}]


def test_the_status_is_read_per_card(fake) -> None:
    fake.cards = [card("GPU-a", ("stt-suite", "gpu-worker", 3100), ("llama-swap", "big", None)), card("GPU-b")]
    assert current_client().card_status() == [
        CardStatus("GPU-a", (CardHolder("stt-suite", "gpu-worker", 3100), CardHolder("llama-swap", "big", None)),
                   32768, 0),
        CardStatus("GPU-b", (), 32768, 0),
    ]


def test_held_cards_make_the_light_red_and_the_hold_names_its_owner(fake) -> None:
    orc = current_client()
    assert orc.traffic_light().red is False
    hold = orc.hold("aifred-test", "gaming")
    assert fake.holds == ["gaming"] and fake.hold_owners == ["aifred-test"]
    assert hold.owner == "aifred-test" and hold.reason == "gaming"
    light = orc.traffic_light()
    assert light.red is True and light.held is not None and light.held.reason == "gaming"
    orc.release_hold("aifred-test")
    assert fake.released_owners == ["aifred-test"]
    assert orc.traffic_light() == TrafficLight((), None, None)


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
    from aifred.lib.calibration.service import _free_cards_for_calibration

    fake.actions = [{"consumer": "tts-suite", "item": "xtts", "outcome": "released"}]
    said: list[str] = []
    _free_cards_for_calibration(said.append)
    assert fake.frees == [{"gpus": "all", "keep": [GPU_ORC_MODEL_SERVER], "reason": "AIfred: calibration"}]
    assert said == ["   🧹 GPU-Orc: tts-suite xtts released"]


def test_a_gpu_orc_that_does_not_answer_is_said_and_the_calibration_goes_on() -> None:
    from aifred.lib.calibration.service import _free_cards_for_calibration

    said: list[str] = []
    _free_cards_for_calibration(said.append)  # conftest points the client at a dead address
    assert len(said) == 1 and "GPU-Orc could not clear the cards for the calibration" in said[0]


def test_work_is_reported_with_a_lease_and_ended_by_owner(fake) -> None:
    orc = current_client()
    orc.work_begin("aifred/1", "a job", 60)
    assert fake.work == {"aifred/1": {"owner": "aifred/1", "reason": "a job", "ttl_s": 60}}
    orc.work_end("aifred/1")
    assert fake.work == {} and fake.work_calls == ["begin aifred/1", "end aifred/1"]
