"""The time limit of the sandbox (Laboratorium).

Contract:
- One limit, SANDBOX_TIMEOUT_SECONDS, measured on the clock from start to end. Code that
  computes with several threads may use them for the whole time; there is no CPU-time limit
  that runs out earlier and kills the run without a word to the model.
- When the time is up, the whole sandbox goes, child processes included, and the result says
  so (timed_out, a message in stderr).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import pytest

from aifred.lib import sandbox

SESSION_ID = "0123456789abcdef0123456789abcdef"
LIMIT_SECONDS = 4


@pytest.fixture(autouse=True)
def short_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "SANDBOX_TIMEOUT_SECONDS", LIMIT_SECONDS)


def _run(code: str) -> sandbox.SandboxResult:
    return asyncio.run(sandbox.execute_sandboxed_code(code, session_id=SESSION_ID))


def test_computing_with_all_math_threads_may_use_the_whole_time() -> None:
    # Four BLAS threads for half the limit use twice the limit in CPU time: a CPU-time
    # limit equal to the clock limit killed this after a quarter of the time.
    seconds = LIMIT_SECONDS / 2
    result = _run(
        "import numpy as np, time\n"
        "a = np.random.rand(2000, 2000)\n"
        "start = time.perf_counter()\n"
        f"while time.perf_counter() - start < {seconds}: a @ a\n"
        "print('done')\n"
    )
    assert (result.exit_code, result.timed_out, result.stdout.strip()) == (0, False, "done"), result.stderr


def _processes_with(marker: str) -> list[str]:
    found = []
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if marker in cmdline.read_bytes().decode(errors="replace"):
                found.append(cmdline.parent.name)
        except OSError:  # the process ended while we looked
            continue
    return found


def test_the_clock_ends_the_whole_sandbox_with_its_children() -> None:
    marker = f"aifred-sandbox-child-{uuid.uuid4().hex}"
    result = _run(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)', {marker!r}])\n"
        "time.sleep(300)\n"
    )
    assert result.timed_out and f"after {LIMIT_SECONDS}s" in result.stderr
    deadline = time.monotonic() + 5
    while _processes_with(marker) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _processes_with(marker) == []
