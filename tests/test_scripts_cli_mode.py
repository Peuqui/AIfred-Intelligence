"""Helper scripts must not reset the running service's debug log.

Importing ``aifred`` without CLI mode loads the whole Reflex app, and the
first log line re-initialises data/logs/aifred_debug.log (14.09.2026: test
runs and helper scripts kept wiping the live log)."""

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
IMPORT = re.compile(r"^\s*(from aifred|import aifred)", re.M)
CLI = re.compile(r"AIFRED_CLI_MODE")


def test_every_script_importing_aifred_sets_cli_mode_first() -> None:
    for path in sorted(SCRIPTS.iterdir()):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        first_import = IMPORT.search(text)
        if first_import is None:
            continue
        cli = CLI.search(text)
        assert cli is not None and cli.start() < first_import.start(), path.name
