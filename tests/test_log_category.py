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
