"""The watchdog of the server's event loop: a blocking call on the loop leaves its line and its duration in the log."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from aifred.lib import loop_watchdog
from aifred.lib.loop_watchdog import LoopWatchdog


def blocking_call_on_the_loop() -> None:
    time.sleep(0.6)


async def run_with_watchdog(block: bool) -> None:
    watchdog = LoopWatchdog(asyncio.get_running_loop(), threading.get_ident(), interval_s=0.05, stall_s=0.2)
    watchdog.start()
    try:
        await asyncio.sleep(0.3)
        if block:
            blocking_call_on_the_loop()
        await asyncio.sleep(0.3)
    finally:
        watchdog.stop()


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(loop_watchdog, "log_message", lambda message, category="info": lines.append(message))
    return lines


def test_a_blocking_call_is_named_with_its_duration(logged: list[str]) -> None:
    asyncio.run(run_with_watchdog(block=True))
    assert len(logged) == 2
    assert "blocking_call_on_the_loop" in logged[0]
    assert "time.sleep" in logged[0]
    assert "answers again after" in logged[1]


def test_a_loop_that_answers_logs_nothing(logged: list[str]) -> None:
    asyncio.run(run_with_watchdog(block=False))
    assert logged == []
