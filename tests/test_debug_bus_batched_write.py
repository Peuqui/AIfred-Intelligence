"""The debug bus writes a session file once per scope, not once per line; the pages get every line at once."""

from __future__ import annotations

from pathlib import Path

import pytest

from aifred.lib import debug_bus, session_events, session_storage

SESSION = "d" * 32


def _stored_lines() -> list[str]:
    session = session_storage.load_session(SESSION)
    assert session is not None
    return list(session["data"].get("debug_messages", []))


@pytest.fixture
def session_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage.create_empty_session(SESSION, owner="mp")
    return tmp_path


def test_lines_of_a_scope_are_one_write(session_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[int] = []
    real_flush = debug_bus._flush_to_session

    def counting_flush(session_id: str, messages: list[str]) -> None:
        writes.append(len(messages))
        real_flush(session_id, messages)

    monkeypatch.setattr(debug_bus, "_flush_to_session", counting_flush)
    published: list[dict] = []
    monkeypatch.setattr(session_events, "publish", lambda sid, kind, payload, **kw: published.append(payload))

    with debug_bus.session_scope(SESSION):
        for i in range(5):
            debug_bus.debug(f"line {i}")
        assert _stored_lines() == []
        assert len(published) == 5

    assert writes == [5]
    lines = _stored_lines()
    assert [line.split(" | ", 1)[1] for line in lines] == [f"line {i}" for i in range(5)]


def test_a_page_opened_mid_turn_sees_the_waiting_lines_and_clearing_drops_them() -> None:
    debug_bus._pending["s-open"] = ["10:00:00 | one"]
    try:
        assert debug_bus.pending_lines("s-open") == ["10:00:00 | one"]
        debug_bus.drop_pending("s-open")
        assert debug_bus.pending_lines("s-open") == []
    finally:
        debug_bus._pending.pop("s-open", None)
