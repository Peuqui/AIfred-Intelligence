"""Tests for the session RMW lock (M4) — concurrent writers on one session file.

Session files are written from multiple threads (Reflex main loop, message-hub
worker thread, debug bus). Every read-modify-write must hold
``session_storage.session_rmw_lock`` for the whole load→mutate→save sequence,
otherwise the later writer overwrites the earlier one (lost update).

These tests hammer the REAL call sites (save_user_to_session, _append_response,
debug_bus._flush_to_session) from parallel threads — without the lock they
lose entries and fail.
"""

import threading
from datetime import datetime, timezone

import pytest

import aifred.lib.session_storage as session_storage
from aifred.lib.envelope import InboundMessage


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    """Redirect SESSION_DIR to a temp dir so tests never touch real sessions."""
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    return tmp_path


def _make_message(text: str) -> InboundMessage:
    return InboundMessage(
        channel="testchannel",
        channel_id="thread-1",
        sender="tester",
        text=text,
        timestamp=datetime.now(timezone.utc),
    )


def _run_threads(workers: list) -> None:
    threads = [threading.Thread(target=w) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for t in threads:
        assert not t.is_alive()


class TestConcurrentSessionWrites:
    N_THREADS = 4
    N_ITER = 25

    def test_concurrent_user_appends_no_lost_update(self, session_dir):
        """N threads append user messages in parallel — nothing may get lost."""
        from aifred.lib.message_processor import save_user_to_session

        sid = "a" * 32
        session_storage.create_empty_session(sid, owner="mp")

        def worker(worker_id: int):
            for i in range(self.N_ITER):
                save_user_to_session(sid, _make_message(f"w{worker_id}-{i}"))

        _run_threads([lambda w=w: worker(w) for w in range(self.N_THREADS)])

        session = session_storage.load_session(sid)
        expected = self.N_THREADS * self.N_ITER
        assert len(session["data"]["chat_history"]) == expected
        assert len(session["data"]["llm_history"]) == expected

    def test_chat_and_debug_writers_do_not_clobber_each_other(self, session_dir):
        """Hub response appends and debug-bus flushes hit the same file:
        both fields must survive intact (the debug flush re-writes
        chat_history from its own load, and vice versa)."""
        from aifred.lib.message_processor import _append_response
        from aifred.lib.debug_bus import _flush_to_session

        sid = "b" * 32
        session_storage.create_empty_session(sid, owner="mp")

        def chat_worker(worker_id: int):
            for i in range(self.N_ITER):
                _append_response(sid, f"response w{worker_id}-{i}")

        def debug_worker(worker_id: int):
            for i in range(self.N_ITER):
                _flush_to_session(sid, [f"debug w{worker_id}-{i}"])

        _run_threads(
            [lambda w=w: chat_worker(w) for w in range(2)]
            + [lambda w=w: debug_worker(w) for w in range(2)]
        )

        session = session_storage.load_session(sid)
        assert len(session["data"]["chat_history"]) == 2 * self.N_ITER
        assert len(session["data"]["debug_messages"]) == 2 * self.N_ITER

    def test_rmw_lock_is_reentrant(self, session_dir):
        """The multi-step writers hold the lock and nest into the locked
        session_storage helpers — a non-reentrant lock would deadlock here."""
        sid = "c" * 32
        with session_storage.session_rmw_lock:
            assert session_storage.create_empty_session(sid, owner="mp") is True
            assert session_storage.update_session_title(sid, "titel") is True
        assert session_storage.get_session_title(sid) == "titel"
