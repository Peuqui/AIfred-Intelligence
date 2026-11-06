"""Load a script from scripts/ as a module, for the tests of that script."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load_script(filename: str, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None, f"scripts/{filename} cannot be loaded"
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: dataclasses in the script look their module up there.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
