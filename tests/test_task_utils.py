"""cancel_and_wait: the task ends, its failure while winding down is logged, the caller's own cancel goes on."""

import asyncio

import pytest

from aifred.lib import task_utils
from aifred.lib.task_utils import cancel_and_wait


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    monkeypatch.setattr(task_utils, "log_message", lambda message, category="info": lines.append((message, category)))
    return lines


def test_a_running_task_is_cancelled_and_has_ended(logged: list[tuple[str, str]]) -> None:
    async def scenario() -> asyncio.Task[None]:
        task = asyncio.create_task(asyncio.sleep(60))
        await asyncio.sleep(0)
        await cancel_and_wait(task, "sleeper")
        return task

    assert asyncio.run(scenario()).cancelled()
    assert logged == []


def test_a_failure_while_winding_down_is_logged_not_raised(logged: list[tuple[str, str]]) -> None:
    async def fails_on_cancel() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise RuntimeError("cleanup broke") from None

    async def scenario() -> None:
        task = asyncio.create_task(fails_on_cancel())
        await asyncio.sleep(0)
        await cancel_and_wait(task, "pump")

    asyncio.run(scenario())
    assert logged == [("⚠️ pump: failed while stopping: RuntimeError('cleanup broke')", "error")]


def test_a_task_that_outlives_the_timeout_is_logged(logged: list[tuple[str, str]]) -> None:
    release = asyncio.Event()

    async def ignores_the_first_cancel() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await release.wait()

    async def scenario() -> None:
        task = asyncio.create_task(ignores_the_first_cancel())
        await asyncio.sleep(0)
        await cancel_and_wait(task, "reader", timeout=0.05)
        release.set()

    asyncio.run(scenario())
    assert logged == [("⚠️ reader: still running 0.05 s after the cancel", "warning")]


def test_the_cancel_of_the_caller_is_passed_on(logged: list[tuple[str, str]]) -> None:
    async def slow_to_end() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)
            raise

    async def caller(inner: asyncio.Task[None]) -> None:
        await cancel_and_wait(inner, "inner")

    async def scenario() -> bool:
        inner = asyncio.create_task(slow_to_end())
        await asyncio.sleep(0)
        outer = asyncio.create_task(caller(inner))
        await asyncio.sleep(0.05)
        outer.cancel()
        try:
            await outer
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(scenario()), "the caller's cancel was swallowed"


def test_nothing_to_do_for_no_task_or_an_ended_one(logged: list[tuple[str, str]]) -> None:
    async def scenario() -> None:
        await cancel_and_wait(None, "none")
        done = asyncio.create_task(asyncio.sleep(0))
        await done
        await cancel_and_wait(done, "done")
        assert not done.cancelled()

    asyncio.run(scenario())
    assert logged == []
