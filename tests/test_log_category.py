"""A log line's category stands in the line, info stays unmarked (logging_utils.log_message). Until 08.11.2026 about
170 calls passed warning, error or stats and the line never showed it."""
from pathlib import Path

import pytest

from aifred.lib import logging_utils


def test_a_category_other_than_info_stands_in_the_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "aifred_debug.log"
    monkeypatch.setattr(logging_utils, "DEBUG_LOG_FILE", str(log))
    monkeypatch.setattr(logging_utils, "FILE_DEBUG_ENABLED", True)
    monkeypatch.setattr(logging_utils, "_debug_log_initialized", True)
    monkeypatch.delenv("AIFRED_CLI_MODE", raising=False)
    logging_utils.log_message("Modell geladen")
    logging_utils.log_message("Karte knapp", "warning")
    logging_utils.log_message("Laden fehlgeschlagen", "error")
    lines = [line.split(" | ", 1)[1] for line in log.read_text(encoding="utf-8").splitlines()]
    assert lines == ["Modell geladen", "WARNING | Karte knapp", "ERROR | Laden fehlgeschlagen"]


def test_importing_the_lib_leaves_an_existing_debug_log_untouched(tmp_path: Path) -> None:
    """Only the service start (server_startup) initializes the log; a script that imports the lib must not wipe the
    running service's live log (24.11.2026: an import of lib.scheduler emptied it)."""
    import os
    import subprocess
    import sys

    logs = tmp_path / "logs"
    logs.mkdir()
    log = logs / "aifred_debug.log"
    log.write_text("laufender Dienst\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "AIFRED_CLI_MODE"}
    env["AIFRED_DATA_DIR"] = str(tmp_path)
    repo_root = Path(__file__).resolve().parent.parent
    subprocess.run([sys.executable, "-c", "from aifred.lib.scheduler import get_job_store; from aifred.lib.logging_utils import log_message; get_job_store(); log_message(\"aus dem Skript\")"], cwd=repo_root, env=env, check=True,
                   capture_output=True, timeout=120)
    assert log.read_text(encoding="utf-8").startswith("laufender Dienst\n")
