"""The queue of a session's waiting messages (session_storage): a message written, dictated or injected while the
session's turn still runs waits there, and the turn service starts the oldest when the turn ends (the order of two
waiting turns is tested in test_turn_service.py). A queue, not a slot: a second message must not replace the first.
"""

from pathlib import Path

import pytest

from aifred.lib import session_storage
from aifred.lib.session_storage import PendingMessage, load_session, pop_pending_message, queue_pending_message

SESSION = "c" * 32


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    session_storage.create_empty_session(SESSION, owner="t")
    yield tmp_path
    session_storage._session_meta_cache.clear()


def test_messages_come_out_in_the_order_they_went_in(session_dir: Path) -> None:
    assert queue_pending_message(SESSION, "erstes Diktat")
    assert queue_pending_message(SESSION, "zweites Diktat")
    assert pop_pending_message(SESSION) == PendingMessage("erstes Diktat")
    assert pop_pending_message(SESSION) == PendingMessage("zweites Diktat")
    assert pop_pending_message(SESSION) is None


def test_queueing_keeps_the_rest_of_the_session(session_dir: Path) -> None:
    session_storage.save_session(SESSION, {"data": {"chat_history": [{"role": "user", "content": "hallo"}]}}, owner="t")
    assert queue_pending_message(SESSION, "Diktat")
    session = load_session(SESSION)
    assert session is not None
    assert session["data"]["chat_history"] == [{"role": "user", "content": "hallo"}]
    assert session["data"]["pending_messages"] == [{"text": "Diktat", "images": []}]


def test_a_waiting_message_keeps_its_images(session_dir: Path) -> None:
    url = f"/_upload/images/{SESSION}/foto_1.jpg"
    assert queue_pending_message(SESSION, "Was ist das?", [url])
    assert pop_pending_message(SESSION) == PendingMessage("Was ist das?", (url,))


def test_an_unreadable_session_file_is_never_overwritten(session_dir: Path) -> None:
    path = session_dir / f"{SESSION}.json"
    path.write_text("{ kaputt", encoding="utf-8")
    assert not queue_pending_message(SESSION, "Diktat")
    assert path.read_text(encoding="utf-8") == "{ kaputt"


def test_a_session_that_is_gone_gets_no_file_of_its_own(session_dir: Path) -> None:
    # Until 08.11.2026 queueing created a missing session (the Reflex inject path relied on it): a message for a
    # session deleted meanwhile came back as a session holding nothing but that message.
    other = "d" * 32
    assert not queue_pending_message(other, "Diktat")
    assert not (session_dir / f"{other}.json").exists()
    assert pop_pending_message(other) is None
