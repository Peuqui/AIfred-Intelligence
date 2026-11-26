"""The server turns on faulthandler, so a crash in C code leaves the Python stack of every thread in the journal.

pytest turns faulthandler on by itself, so the check runs in a fresh interpreter.
"""

import subprocess
import sys

_PROBE = """
import faulthandler
import aifred.lib.server_shutdown as server_shutdown
server_shutdown.serve = lambda *args, **kwargs: None
from aifred.server import main
main()
print(faulthandler.is_enabled())
"""


def test_main_enables_faulthandler_for_all_threads() -> None:
    result = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=120, check=True)
    assert result.stdout.strip().splitlines()[-1] == "True"
