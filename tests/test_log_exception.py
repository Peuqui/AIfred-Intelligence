"""log_exception: a broad catch at a boundary keeps the origin of the failure in the log."""

import pytest

from aifred.lib import logging_utils
from aifred.lib.logging_utils import log_exception


def _fails_deep_down() -> None:
    raise KeyError("missing_field")


def test_the_log_names_message_error_and_where_it_was_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(logging_utils, "log_message", lambda message, category="info": logged.append((message, category)))
    try:
        _fails_deep_down()
    except KeyError as error:
        log_exception("Agent import failed", error)

    [(message, category)] = logged
    assert category == "error"
    assert message.startswith("Agent import failed: KeyError('missing_field')\n")
    assert "in _fails_deep_down" in message and 'raise KeyError("missing_field")' in message
