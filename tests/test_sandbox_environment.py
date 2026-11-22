"""The environment of the sandbox (Laboratorium).

Contract:
- The code sees only the variables the sandbox sets itself. AIfred's own environment holds the
  secrets of .env (API keys, mail passwords, tokens); bwrap would hand it on unless cleared, and a
  print(os.environ) would put them in front of the model.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from aifred.lib import sandbox

SESSION_ID = "0123456789abcdef0123456789abcdef"


def _environment_inside() -> dict[str, str]:
    result = asyncio.run(sandbox.execute_sandboxed_code(
        "import json, os\nprint(json.dumps(dict(os.environ)))\n", session_id=SESSION_ID,
    ))
    assert result.exit_code == 0, result.stderr
    environment: dict[str, str] = json.loads(result.stdout)
    return environment


def test_a_variable_of_aifred_does_not_reach_the_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_TEST_API_KEY", "streng-geheim")
    assert "SANDBOX_TEST_API_KEY" not in _environment_inside()


def test_the_code_gets_only_what_the_sandbox_sets() -> None:
    assert set(_environment_inside()) <= {
        "HOME", "MPLBACKEND", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS", "PYTHONPATH", "PATH", "PWD", "LC_CTYPE",
    }
