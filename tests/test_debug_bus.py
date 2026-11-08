"""A debug line of a turn reaches the session twice: its file (a page opened later loads it) and a ``debug`` event
(the pages open now show it). That holds for ``debug()`` and for a channel's ``channel_log`` (until 08.11.2026 the
channel's line reached only the file). Outside a session_scope a line stays in the log."""
from collections.abc import Iterator
from pathlib import Path

import pytest

from aifred.lib import debug_bus, session_events, session_storage
from aifred.plugins.channels.email_channel import EmailChannel   # a real channel; its log is BaseChannel's

SESSION = "e" * 32


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    session_storage.save_session(SESSION, {"data": {"chat_history": [], "llm_history": []}}, owner="ki")
    session_events._channels.pop(SESSION, None)
    yield
    session_storage._session_meta_cache.clear()
    session_events._channels.pop(SESSION, None)


def _in_file() -> list[str]:
    stored = session_storage.load_session(SESSION)
    assert stored is not None
    return [line.split(" | ", 1)[1] for line in stored["data"].get("debug_messages", [])]


def _sent() -> list[str]:
    return [event.data["line"].split(" | ", 1)[1] for event in session_events.replay_after(SESSION, 0) if event.kind == "debug"]


@pytest.mark.parametrize("write", [debug_bus.debug, EmailChannel().channel_log], ids=["debug", "channel_log"])
def test_a_line_of_a_turn_goes_to_the_file_and_to_the_open_pages(session: None, write: object) -> None:
    assert callable(write)
    with debug_bus.session_scope(SESSION):
        write("📨 Nachricht empfangen")
    assert _in_file() == ["📨 Nachricht empfangen"]
    assert _sent() == ["📨 Nachricht empfangen"]


def test_outside_a_session_a_line_stays_in_the_log(session: None) -> None:
    debug_bus.debug("Server startet")
    EmailChannel().channel_log("Kanal verbunden")
    assert _in_file() == []
    assert _sent() == []
