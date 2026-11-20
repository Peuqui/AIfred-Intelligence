"""The status line's row for the hold of this machine's own cards: every state of ``local_hold_status`` and the two
buttons behind it (grant a requested hold, end an active one). The GPU-Orc is the fake of the tests."""

from __future__ import annotations

import socket

import pytest
from fake_gpu_orc import FakeGpuOrc, install_fake_gpu_orc
from starlette.testclient import TestClient

from aifred.lib import gpu_orc_client
from aifred.lib.api.app import api_app
from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username
from aifred.lib.gpu_hold import local_hold_status

HOLD = {"since": "2026-11-20T09:30:00+01:00", "reason": "vllm-research: v13", "owner": "vllm-research"}


@pytest.fixture
def orc(monkeypatch: pytest.MonkeyPatch) -> FakeGpuOrc:
    fake = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    return test_client


def test_off_when_gpu_orc_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_orc_client, "GPU_ORC_URL", "")
    status = local_hold_status()
    assert (status.state, status.machine) == ("off", socket.gethostname())


def test_unreachable_names_the_error() -> None:
    status = local_hold_status()   # the autouse fixture points GPU-Orc at a dead address
    assert status.state == "unreachable" and status.error


def test_free_without_a_hold(orc: FakeGpuOrc) -> None:
    status = local_hold_status()
    assert (status.state, status.owner, status.reason, status.since, status.granted_by) == ("free", None, None, None, None)


def test_no_hold_still_says_how_full_the_cards_are(orc: FakeGpuOrc) -> None:
    """No hold is not empty cards (a model may run): the state carries the memory in use over every card."""
    orc.cards = [
        {"index": 0, "uuid": "GPU-a", "name": "A", "memory_total_mib": 49152, "memory_used_mib": 40000, "holders": [], "unattributed_mib": 0},
        {"index": 1, "uuid": "GPU-b", "name": "B", "memory_total_mib": 32768, "memory_used_mib": 1000, "holders": [], "unattributed_mib": 0},
    ]
    status = local_hold_status()
    assert (status.state, status.used_mib, status.total_mib) == ("free", 41000, 81920)


def test_requested_carries_owner_reason_and_time(orc: FakeGpuOrc) -> None:
    orc.hold_requested = dict(HOLD)
    status = local_hold_status()
    assert (status.state, status.owner, status.reason, status.since, status.granted_by) == (
        "requested", "vllm-research", "vllm-research: v13", HOLD["since"], None,
    )


def test_granted_is_a_requested_hold_somebody_granted(orc: FakeGpuOrc) -> None:
    orc.hold_requested = {**HOLD, "granted_by": "ki"}
    status = local_hold_status()
    assert (status.state, status.granted_by) == ("granted", "ki")


def test_active_beats_a_request(orc: FakeGpuOrc) -> None:
    orc.held = dict(HOLD)
    status = local_hold_status()
    assert (status.state, status.owner) == ("active", "vllm-research")


def test_the_endpoint_answers_the_status(orc: FakeGpuOrc, client: TestClient) -> None:
    orc.hold_requested = dict(HOLD)
    answer = client.get("/service-status/local-hold").json()
    assert (answer["state"], answer["owner"], answer["machine"]) == ("requested", "vllm-research", socket.gethostname())


def test_grant_sends_owner_and_the_logged_in_user_and_answers_the_new_state(orc: FakeGpuOrc, client: TestClient) -> None:
    orc.hold_requested = dict(HOLD)
    answer = client.post("/service-status/grant", json={"owner": "vllm-research"})
    assert answer.status_code == 200
    assert orc.grants == [{"owner": "vllm-research", "by": "ki"}]
    assert (answer.json()["state"], answer.json()["granted_by"]) == ("granted", "ki")


def test_grant_of_another_owners_hold_is_a_conflict(orc: FakeGpuOrc, client: TestClient) -> None:
    orc.hold_requested = dict(HOLD)
    answer = client.post("/service-status/grant", json={"owner": "somebody-else"})
    assert answer.status_code == 409


def test_end_hold_forces_the_release_of_the_owner(orc: FakeGpuOrc, client: TestClient) -> None:
    orc.held = dict(HOLD)
    answer = client.post("/service-status/end-hold", json={"owner": "vllm-research"})
    assert answer.status_code == 200
    assert orc.forced_releases == ["vllm-research"]
    assert answer.json()["state"] == "free"


def test_end_hold_with_gpu_orc_off_is_a_conflict(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_orc_client, "GPU_ORC_URL", "")
    assert client.post("/service-status/end-hold", json={"owner": "x"}).status_code == 409
