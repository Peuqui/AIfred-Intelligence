"""The work report to GPU-Orc: a lease while turns run, ended when the last is over, kept honest on errors."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest
from fake_gpu_orc import FakeGpuOrc, current_client, install_fake_gpu_orc

from aifred.lib import gpu_orc_work
from aifred.lib.debug_bus import session_scope
from aifred.lib.gpu_orc_client import GpuOrcClient
from aifred.lib.gpu_orc_work import WorkReporter


@pytest.fixture
def fake(monkeypatch) -> FakeGpuOrc:
    service = FakeGpuOrc()
    # AIfred asks the holder of a granted hold to end it, which withdraws it at once (gpu_hold._ask_holder_to_end). The
    # waits here are the case where that request fails: the turn then waits under the granted hold, and its waiting must
    # not count as work, or GPU-Orc never clears and the turn waits forever.
    service.end_refusal = (500, "the end cannot be asked for now")
    install_fake_gpu_orc(monkeypatch, service)
    return service


class Turns:
    """``running`` turns, one per session ``s0``, ``s1``, …"""

    def __init__(self) -> None:
        self.running = 0

    def __call__(self) -> frozenset[str]:
        return frozenset(f"s{index}" for index in range(self.running))


def reporter(turns: Turns) -> WorkReporter:
    return WorkReporter(current_client(), turns, owner="aifred-test", poll_s=0.01, ttl_s=60)


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
    work._client = GpuOrcClient("http://127.0.0.1:9")  # nobody listens there
    work.report_once()
    work.report_once()
    assert len(logged) == 1
    work._client = current_client()
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


# ── A turn that waits for the cards under a hold is no work ──────────────────
# Otherwise AIfred keeps reporting work, GPU-Orc never clears the cards for a granted hold, the hold never becomes
# active and the turn waits forever.

HELD = {"since": "2026-11-19T15:55:00+01:00", "reason": "vllm-research: v13", "owner": "vllm-research", "granted_by": "Peuqui"}


def _waiting_turn(fake, monkeypatch, work: WorkReporter, turns: Turns) -> None:
    """One turn runs and waits for the cards under a granted hold; ``work.report_once()`` is called while it waits."""
    from aifred.lib import gpu_hold

    fake.hold_requested = dict(HELD)

    async def sleep(_seconds: float) -> None:
        work.report_once()                       # the reporter looks while the turn waits
        fake.hold_requested = None               # the hold ends: the wait goes on

    monkeypatch.setattr(gpu_hold.asyncio, "sleep", sleep)
    turns.running = 1
    asyncio.run(_in_session("s0", gpu_hold.wait_until_free(current_client(), "a test")))


async def _in_session(session_id: str | None, wait: Coroutine[Any, Any, None]) -> None:
    """``wait`` as a turn of ``session_id`` would run it (``None``: outside any turn, e.g. in the background)."""
    with session_scope(session_id):
        await wait


def test_a_turn_waiting_under_a_granted_hold_does_not_count_as_work(fake, monkeypatch) -> None:
    turns = Turns()
    work = reporter(turns)
    work.report_once()                           # nothing runs yet
    turns.running = 1
    fake.work_calls.clear()
    work.report_once()                           # the turn works: lease stands
    assert fake.work_calls == ["begin aifred-test"]
    _waiting_turn(fake, monkeypatch, work, turns)
    assert fake.work_calls == ["begin aifred-test", "end aifred-test"]   # waiting: the lease ended


def test_a_second_turn_that_really_runs_keeps_the_work_up_while_one_waits(fake, monkeypatch) -> None:
    turns = Turns()
    work = reporter(turns)
    turns.running = 2

    from aifred.lib import gpu_hold
    fake.hold_requested = dict(HELD)

    async def sleep(_seconds: float) -> None:
        work.report_once()
        fake.hold_requested = None

    monkeypatch.setattr(gpu_hold.asyncio, "sleep", sleep)
    asyncio.run(_in_session("s0", gpu_hold.wait_until_free(current_client(), "a test")))
    assert fake.work_calls == ["begin aifred-test"] and "aifred-test" in fake.work


def test_a_wait_outside_any_turn_takes_no_work_away_from_a_turn_that_runs(fake, monkeypatch) -> None:
    """An embedding or a camera image waits for the cards in the background: the turn of s0 still works on them."""
    turns = Turns()
    work = reporter(turns)
    turns.running = 1

    from aifred.lib import gpu_hold
    fake.hold_requested = dict(HELD)

    async def sleep(_seconds: float) -> None:
        work.report_once()
        fake.hold_requested = None

    monkeypatch.setattr(gpu_hold.asyncio, "sleep", sleep)
    asyncio.run(_in_session(None, gpu_hold.wait_until_free(current_client(), "an embedding")))
    assert fake.work_calls == ["begin aifred-test"] and "aifred-test" in fake.work


def test_two_waits_of_one_turn_count_once(fake, monkeypatch) -> None:
    """The turn of s0 waits in two places at once; the turn of s1 really runs and keeps the work up."""
    turns = Turns()
    work = reporter(turns)
    turns.running = 2

    from aifred.lib import gpu_hold
    fake.hold_requested = dict(HELD)
    both_wait = asyncio.Event()
    looks = 0

    async def sleep(_seconds: float) -> None:
        nonlocal looks
        looks += 1
        if looks == 2:                           # the second wait of s0 has marked itself too
            work.report_once()
            fake.hold_requested = None
            both_wait.set()
        await both_wait.wait()

    monkeypatch.setattr(gpu_hold.asyncio, "sleep", sleep)

    async def scenario() -> None:
        with session_scope("s0"):
            await asyncio.gather(
                gpu_hold.wait_until_free(current_client(), "a description"),
                gpu_hold.wait_until_free(current_client(), "an embedding"),
            )

    asyncio.run(scenario())
    assert fake.work_calls == ["begin aifred-test"] and fake.work["aifred-test"]["reason"] == "AIfred: 1 running turn(s)"


def test_the_count_of_waiting_turns_sinks_again_after_a_cancel_and_after_an_error(fake, monkeypatch) -> None:
    from aifred.lib import gpu_hold

    fake.hold_requested = dict(HELD)

    async def scenario() -> None:
        task = asyncio.create_task(_in_session("s0", gpu_hold.wait_until_free(current_client(), "a test")))
        while not gpu_hold.waiting_sessions():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert gpu_hold.waiting_sessions() == frozenset()

    def broken(_client, _what, _last):
        raise RuntimeError("boom")

    monkeypatch.setattr(gpu_hold, "_still_held", broken)
    with pytest.raises(RuntimeError):
        gpu_hold.wait_until_free_blocking(current_client(), "a test")
    assert gpu_hold.waiting_sessions() == frozenset()


def test_an_embedding_of_a_turn_waits_as_that_turn(fake, monkeypatch) -> None:
    """The embedding waits blocking in a thread (embeddings._wait_while_cards_held); every way there from a turn goes
    through asyncio.to_thread (DocumentStore, AgentMemory), which carries the session: the turn counts as waiting."""
    from aifred.lib import embeddings, gpu_hold

    fake.hold_requested = dict(HELD)
    seen: list[frozenset[str]] = []

    def sleep(_seconds: float) -> None:
        seen.append(gpu_hold.waiting_sessions())
        fake.hold_requested = None

    monkeypatch.setattr(gpu_hold.time, "sleep", sleep)

    async def scenario() -> None:
        with session_scope("s0"):
            await asyncio.to_thread(embeddings._wait_while_cards_held)

    asyncio.run(scenario())
    assert seen == [frozenset({"s0"})] and gpu_hold.waiting_sessions() == frozenset()
