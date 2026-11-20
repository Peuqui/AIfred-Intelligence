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


def test_a_requested_hold_a_person_granted_makes_the_request_wait(
    orc: FakeGpuOrc, seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Granted: GPU-Orc clears the cards as soon as no work of others runs, a model loaded now would be cleared again.
    orc.hold_requested = {**HOLD, "owner": "vllm-research", "granted_by": "Peuqui"}

    async def sleep(seconds: float) -> None:
        seen["sleeps"].append(seconds)
        orc.hold_requested = None

    monkeypatch.setattr(base.asyncio, "sleep", sleep)
    asyncio.run(_backend()._wait_while_held(MODEL))
    assert len(seen["sleeps"]) == 1 and seen["phases"] == ["gpu_hold", ""]
    assert any("vllm-research" in line and "granted by Peuqui" in line for line in seen["log"])


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


def test_the_phase_names_the_machine_the_holder_and_the_state_and_follows_a_change(
    orc: FakeGpuOrc, seen: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported = _phases_with_detail(monkeypatch)
    orc.hold_requested = {**HOLD, "owner": "vllm-research", "granted_by": "Peuqui"}

    async def sleep(_seconds: float) -> None:
        seen["sleeps"].append(_seconds)
        if len(seen["sleeps"]) == 1:          # GPU-Orc cleared: the granted hold became active
            orc.hold_requested, orc.held = None, {**HOLD, "owner": "vllm-research"}
        else:
            orc.held = None

    monkeypatch.setattr(base.asyncio, "sleep", sleep)
    asyncio.run(_backend()._wait_while_held(MODEL))
    common = {"machine": "Mini", "owner": "vllm-research", "reason": HOLD["reason"], "since": HOLD["since"], "local": True}
    assert reported == [
        ("gpu_hold", {**common, "state": "granted"}),
        ("gpu_hold", {**common, "state": "active"}),
        ("", None),
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
