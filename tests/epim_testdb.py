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
from aifred.lib.plugin_modules import OFF, READ, WRITE, ModuleAccess
from aifred.plugins.tools.epim.settings import MODULE_DEFAULTS

FIXTURE = PROJECT_ROOT / "data" / "epim" / "test-fixture.epim"

requires_fixture = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason=f"EPIM test fixture missing ({FIXTURE}); build it with maintenance make-fixture",
)


ALL_WRITE = ModuleAccess({module: WRITE for module in MODULE_DEFAULTS})
ALL_READ = ModuleAccess({module: READ for module in MODULE_DEFAULTS})
ALL_OFF = ModuleAccess({module: OFF for module in MODULE_DEFAULTS})


def modules(**levels: str) -> ModuleAccess:
    """All modules readable, the given ones at the given level."""
    return ModuleAccess({**ALL_READ.levels, **levels})


def copy_fixture(directory: Path, name: str = "test.epim") -> Path:
    target = directory / name
    shutil.copyfile(FIXTURE, target)
    return target
