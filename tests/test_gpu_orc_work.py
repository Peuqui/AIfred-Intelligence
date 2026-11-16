"""The work report to GPU-Orc: a lease while turns run, ended when the last is over, kept honest on errors."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fake_gpu_orc import FakeGpuOrc, install_fake_gpu_orc

from aifred.lib import gpu_orc_work
from aifred.lib.gpu_orc_client import GpuOrcClient, get_gpu_orc_client
from aifred.lib.gpu_orc_work import WorkReporter


@pytest.fixture
def fake(monkeypatch) -> FakeGpuOrc:
    service = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, service)
    return service


class Turns:
    def __init__(self) -> None:
        self.running = 0

    def __call__(self) -> int:
        return self.running


def reporter(turns: Turns) -> WorkReporter:
    return WorkReporter(get_gpu_orc_client(), turns, owner="aifred-test", poll_s=0.01, ttl_s=60)


def test_nothing_is_reported_while_no_turn_runs(fake) -> None:
    reporter(Turns()).report_once()
    assert fake.work_calls == []


def test_a_running_turn_reports_work_with_the_lease_time(fake) -> None:
    turns = Turns()
    turns.running = 2
    reporter(turns).report_once()
    assert fake.work == {"aifred-test": {"owner": "aifred-test", "reason": "AIfred: 2 running turn(s)", "ttl_s": 60}}


def test_the_lease_is_renewed_while_turns_run_and_ended_after_the_last(fake) -> None:
    turns = Turns()
    work = reporter(turns)
    turns.running = 1
    work.report_once()
    work.report_once()
    turns.running = 0
    work.report_once()
    work.report_once()   # nothing left to end
    assert fake.work_calls == ["begin aifred-test", "begin aifred-test", "end aifred-test"] and fake.work == {}


def test_an_unreachable_gpu_orc_is_logged_once_and_the_end_is_retried(monkeypatch, fake) -> None:
    logged: list[str] = []
    monkeypatch.setattr(gpu_orc_work, "log_message", lambda text, category="info": logged.append(text))
    turns = Turns()
    work = reporter(turns)
    turns.running = 1
    work.report_once()                       # the lease stands
    down = GpuOrcClient("http://gpu-orc.test", transport=httpx.MockTransport(
        lambda request: httpx.Response(500, json={"error": "down"})))
    work._client = down
    work.report_once()
    work.report_once()
    assert len(logged) == 1
    work._client = get_gpu_orc_client()
    turns.running = 0
    work.report_once()                       # the end that was missed is sent now
    assert fake.work_calls[-1] == "end aifred-test" and fake.work == {}


def test_stopping_the_reporter_ends_the_lease(fake) -> None:
    turns = Turns()
    turns.running = 1

    async def scenario() -> None:
        task = asyncio.create_task(reporter(turns).run())
        await asyncio.sleep(0.1)
        assert fake.work
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert fake.work == {}
