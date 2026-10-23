"""Shared fixture for EPIM integration tests: copies of an entry-free EPIM
database (full schema with EPIM's triggers, plus its lookup tables).

The fixture is built from a real EPIM file and stays local (data/ is not
versioned)::

    python -m aifred.plugins.tools.epim.maintenance make-fixture <file.epim> data/epim/test-fixture.epim
"""

import shutil
from pathlib import Path

import pytest

from aifred.lib.config import PROJECT_ROOT

FIXTURE = PROJECT_ROOT / "data" / "epim" / "test-fixture.epim"

requires_fixture = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason=f"EPIM test fixture missing ({FIXTURE}); build it with maintenance make-fixture",
)


def copy_fixture(directory: Path, name: str = "test.epim") -> Path:
    target = directory / name
    shutil.copyfile(FIXTURE, target)
    return target
