"""While somebody holds the cards (GPU-Orc ``/hold``), AIfred sends no request to a model on them: not a load and not a
request to a model already running, which would swap out or distort the holder's work (19.11.2026: vllm-research's
model chain ran under a hold, and a single message to AIfred would have made llama-swap swap its model out). The turn
waits visibly and goes on when the hold ends. GPU-Orc off asks nothing; a GPU-Orc that does not answer is logged and
the request goes on."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fake_gpu_orc import FakeGpuOrc, install_fake_gpu_orc

from aifred.backends import base
from aifred.backends.vllm import vLLMBackend
from aifred.lib import gpu_hold, gpu_orc_client, remote_describers, service_status

MODEL = "Big-Model"
HOLD = {"since": "2026-11-19T15:55:00+01:00", "reason": "vllm-research: v13"}


@pytest.fixture
def orc(monkeypatch: pytest.MonkeyPatch) -> FakeGpuOrc:
    fake = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, fake)
    monkeypatch.setattr(remote_describers, "remote_hosts", list)   # the llama-swap of this machine
    return fake


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """What the wait did: the phases it reported, its log lines, the sleeps between two looks."""
    seen: dict[str, list[Any]] = {"phases": [], "log": [], "sleeps": []}
    monkeypatch.setattr(gpu_hold, "report_phase", lambda phase, **_kw: seen["phases"].append(phase))
    monkeypatch.setattr(gpu_hold, "log_message", lambda message, *_a, **_k: seen["log"].append(message))
    return seen


def _release_after(orc: FakeGpuOrc, seen: dict[str, list[Any]], looks: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hold ends after ``looks`` sleeps."""
    async def sleep(seconds: float) -> None:
        seen["sleeps"].append(seconds)
        if len(seen["sleeps"]) >= looks:
            orc.held = None

    monkeypatch.setattr(base.asyncio, "sleep", sleep)


def _backend() -> vLLMBackend:
    return vLLMBackend(base_url="http://127.0.0.1:9/v1")


def test_a_request_waits_while_the_cards_are_held_and_goes_on_when_they_are_free(
    orc: FakeGpuOrc, seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    orc.held = dict(HOLD)
    _release_after(orc, seen, 2, monkeypatch)
    asyncio.run(_backend()._wait_while_held(MODEL))
    assert len(seen["sleeps"]) == 2                                   # it looked again until the hold ended
    assert seen["phases"] == ["gpu_hold", ""]                         # shown once, ended once
    assert any("vllm-research: v13" in line and "waits" in line for line in seen["log"])
    assert any("has ended" in line for line in seen["log"])


def test_without_a_hold_nothing_waits(orc: FakeGpuOrc, seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    _release_after(orc, seen, 1, monkeypatch)
    asyncio.run(_backend()._wait_while_held(MODEL))
    assert seen["sleeps"] == [] and seen["phases"] == [] and "GET /loading" in orc.requests


def test_a_model_already_running_waits_too(orc: FakeGpuOrc, seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    # The gap of 19.11.: a running model skipped GPU-Orc entirely, so a request went to it under the hold.
    orc.held = dict(HOLD)
    _release_after(orc, seen, 1, monkeypatch)
    monkeypatch.setattr(service_status, "running_models", lambda _root: [type("M", (), {"name": MODEL})()])
    assert asyncio.run(_backend()._load_model(MODEL)) == 0.0
    assert len(seen["sleeps"]) == 1                                   # waited once, then it was free
    assert seen["phases"] == ["gpu_hold", ""]
    assert orc.frees == []                                            # running: nothing to clear afterwards


def test_gpu_orc_off_asks_nothing(orc: FakeGpuOrc, seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    orc.held = dict(HOLD)
    monkeypatch.setattr(gpu_orc_client, "GPU_ORC_URL", "")
    asyncio.run(_backend()._wait_while_held(MODEL))
    assert orc.requests == [] and seen["phases"] == []


def test_a_gpu_orc_that_does_not_answer_lets_the_request_go_on_and_says_so(
    seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gpu_orc_client, "GPU_ORC_URL", "http://127.0.0.1:9")   # nobody listens there
    monkeypatch.setattr(gpu_orc_client, "_default_client", None)
    monkeypatch.setattr(remote_describers, "remote_hosts", list)
    asyncio.run(_backend()._wait_while_held(MODEL))
    assert seen["phases"] == []
    assert any("did not say whether the cards are held" in line for line in seen["log"])


def test_a_requested_hold_a_person_granted_is_withdrawn_and_the_request_goes_on(
    orc: FakeGpuOrc, seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Granted, GPU-Orc would clear the cards; AIfred goes first and asks for the end, which withdraws a requested hold.
    orc.hold_requested = {**HOLD, "owner": "vllm-research", "granted_by": "Peuqui"}

    async def sleep(seconds: float) -> None:
        raise AssertionError("the request waited for a hold that was withdrawn")

    monkeypatch.setattr(base.asyncio, "sleep", sleep)
    asyncio.run(_backend()._wait_while_held(MODEL))
    assert orc.end_requests == [{"owner": "vllm-research", "by": f"AIfred: '{MODEL}'"}]
    assert orc.hold_requested is None and seen["phases"] == []
    assert any("withdrawn" in line and "vllm-research" in line for line in seen["log"])


def test_an_active_hold_on_this_machine_is_asked_to_end_once_and_the_request_waits_for_it(
    orc: FakeGpuOrc, seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AIfred goes first (Peuqui 22.11.2026): whatever started the turn, it asks the holder to let go and waits until
    it has; the request stays on the hold, so it is made once."""
    orc.held = {**HOLD, "owner": "vllm-research"}
    _release_after(orc, seen, 3, monkeypatch)
    asyncio.run(_backend()._wait_while_held(MODEL))
    assert orc.end_requests == [{"owner": "vllm-research", "by": f"AIfred: '{MODEL}'"}]
    assert len(seen["sleeps"]) == 3                                   # it waited until the owner let go
    assert orc.released_owners == []                                  # AIfred never ends the hold itself


def test_a_hold_of_another_machine_is_only_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    # On Aragon the hold is mostly Peuqui's own (a game): AIfred does not ask for its end.
    aragon = FakeGpuOrc()
    aragon.held = {**HOLD, "owner": "somebody"}
    client = gpu_orc_client.GpuOrcClient(aragon.serve(), machine="Aragon")

    async def sleep(_seconds: float) -> None:
        aragon.held = None

    monkeypatch.setattr(gpu_hold.asyncio, "sleep", sleep)
    monkeypatch.setattr(gpu_hold, "report_phase", lambda *_a, **_k: None)
    asyncio.run(gpu_hold.wait_until_free(client, "a test"))
    assert aragon.end_requests == []


def test_a_requested_hold_nobody_granted_holds_nothing_back(
    orc: FakeGpuOrc, seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A main model is still loaded and stays: the turns go on.
    orc.hold_requested = {**HOLD, "owner": "vllm-research"}

    async def sleep(seconds: float) -> None:
        raise AssertionError("the request waited under a hold nobody granted")

    monkeypatch.setattr(base.asyncio, "sleep", sleep)
    asyncio.run(_backend()._wait_while_held(MODEL))
    assert seen["phases"] == []


# ── The phase carries what the page shows ────────────────────────────────


def _phases_with_detail(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    reported: list[tuple[str, Any]] = []
    monkeypatch.setattr(gpu_hold, "report_phase", lambda phase, **kw: reported.append((phase, kw.get("detail"))))
    monkeypatch.setattr(gpu_hold, "machine_name", lambda: "Mini")
    return reported


def test_the_phase_names_the_machine_the_holder_and_who_asked_for_the_end(
    orc: FakeGpuOrc, seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported = _phases_with_detail(monkeypatch)
    orc.held = {**HOLD, "owner": "vllm-research"}
    _release_after(orc, seen, 2, monkeypatch)
    asyncio.run(_backend()._wait_while_held(MODEL))
    assert reported == [
        ("gpu_hold", {"machine": "Mini", "owner": "vllm-research", "reason": HOLD["reason"], "since": HOLD["since"],
                      "local": True, "state": "active", "end_requested_by": f"AIfred: '{MODEL}'"}),
        ("", None),                                                   # one report: nothing changed in between
    ]


def test_a_hold_on_another_machine_is_named_by_that_machine_and_not_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported = _phases_with_detail(monkeypatch)
    aragon = FakeGpuOrc()
    aragon.held = {**HOLD, "owner": "somebody"}
    client = gpu_orc_client.GpuOrcClient(aragon.serve(), machine="Aragon")

    async def sleep(_seconds: float) -> None:
        aragon.held = None

    monkeypatch.setattr(gpu_hold.asyncio, "sleep", sleep)
    asyncio.run(gpu_hold.wait_until_free(client, "a test"))
    detail = reported[0][1]
    assert detail["machine"] == "Aragon" and detail["local"] is False and detail["state"] == "active"


def test_the_phase_is_reported_again_when_an_end_is_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    """The waiting turn shows who asked the holder to end its hold: the phase comes again when the request appears
    (on another machine, where AIfred only waits, a person asks there)."""
    reported = _phases_with_detail(monkeypatch)
    aragon = FakeGpuOrc()
    aragon.held = {**HOLD, "owner": "vllm-research"}
    client = gpu_orc_client.GpuOrcClient(aragon.serve(), machine="Aragon")
    polls = 0

    async def sleep(_seconds: float) -> None:
        nonlocal polls
        polls += 1
        if polls == 1:
            aragon.held = {**HOLD, "owner": "vllm-research", "end_requested_by": "ki"}  # the second look sees the request
        elif polls == 2:
            pass  # a third look, nothing new: no new report
        else:
            aragon.held = None

    monkeypatch.setattr(gpu_hold.asyncio, "sleep", sleep)
    asyncio.run(gpu_hold.wait_until_free(client, "a test"))
    assert [(phase, detail and detail["end_requested_by"]) for phase, detail in reported] == [
        ("gpu_hold", None), ("gpu_hold", "ki"), ("", None),
    ]
