"""Tests for session ordering.

Login auto-load returns to the most recently active session. Activity is
``last_seen``: written by save_session() when content is stored and by
touch_session() when the user switches to a session. Channel sessions
(scheduler/vision/email) take part on purpose — after a restart the user wants
to see what came in.

The chat picker orders by ``last_message_at`` instead: only a new message
moves a session up, merely opening it does not.
"""

import pytest

import aifred.lib.session_storage as session_storage
from aifred.lib.session_storage import (
    create_empty_session,
    list_sessions,
    load_session,
    most_recently_seen_session_id,
    save_session,
    touch_session,
    update_chat_data,
)

OWNER = "tester"

# Session ids must be exactly 32 lowercase hex chars (see _sanitize_session_id).
SESSION_A = "a" * 32
SESSION_B = "b" * 32
SESSION_C = "c" * 32
SESSION_D = "d" * 32
SESSION_E = "e" * 32
SESSION_MISSING = "f" * 32
SESSION_FOREIGN = "0" * 32


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    """Redirect SESSION_DIR to a temp dir and start with an empty meta cache."""
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    yield tmp_path
    session_storage._session_meta_cache.clear()


def _ids(owner: str = OWNER) -> list:
    return [s["session_id"] for s in list_sessions(owner=owner)]


def _message(content: str) -> dict:
    return {"role": "user", "content": content}


def test_opening_keeps_picker_order_but_wins_login_auto_load(session_dir):
    """Switching to an older session does not reorder the chat picker."""
    create_empty_session(SESSION_A, owner=OWNER)
    create_empty_session(SESSION_B, owner=OWNER)
    assert _ids() == [SESSION_B, SESSION_A]
    assert most_recently_seen_session_id(OWNER) == SESSION_B

    assert touch_session(SESSION_A) is True
    assert _ids() == [SESSION_B, SESSION_A]
    assert most_recently_seen_session_id(OWNER) == SESSION_A


def test_new_message_moves_older_session_up(session_dir):
    """A message arriving in an older session puts it at the top."""
    create_empty_session(SESSION_A, owner=OWNER)
    create_empty_session(SESSION_B, owner=OWNER)
    assert _ids() == [SESSION_B, SESSION_A]

    assert update_chat_data(SESSION_A, [_message("neu")]) is True
    assert _ids() == [SESSION_A, SESSION_B]


def test_rewrite_without_new_message_keeps_order(session_dir):
    """Debug entries, a regenerated answer or a deleted message keep the place."""
    update_chat_data(SESSION_A, [_message("a1"), _message("a2")], owner=OWNER)
    update_chat_data(SESSION_B, [_message("b1")], owner=OWNER)
    assert _ids() == [SESSION_B, SESSION_A]
    stamped = load_session(SESSION_A)["last_message_at"]

    update_chat_data(SESSION_A, [_message("a1"), _message("a2")], debug_messages=["x"])
    update_chat_data(SESSION_A, [_message("a1"), _message("a2 regenerated")])
    update_chat_data(SESSION_A, [_message("a1")])
    assert _ids() == [SESSION_B, SESSION_A]
    assert load_session(SESSION_A)["last_message_at"] == stamped


def test_new_session_is_stamped_on_every_creation_path(session_dir):
    """Each path that creates a session file sets last_message_at."""
    create_empty_session(SESSION_A, owner=OWNER)
    update_chat_data(SESSION_B, [], owner=OWNER)
    save_session(SESSION_C, {"data": {}, "owner": OWNER})
    assert session_storage.set_pending_message(SESSION_D, "hallo") is True

    for session_id in (SESSION_A, SESSION_B, SESSION_C, SESSION_D):
        session = load_session(session_id)
        assert session is not None
        assert session["last_message_at"] == session["created_at"]
    assert _ids() == [SESSION_C, SESSION_B, SESSION_A]


def test_login_auto_load_without_sessions(session_dir):
    assert most_recently_seen_session_id(OWNER) is None


def test_channel_session_takes_part_in_ranking(session_dir):
    """A fresh scheduler/vision session outranks an older interactive one."""
    create_empty_session(SESSION_C, owner=OWNER)
    create_empty_session(SESSION_D, owner=OWNER, channel="scheduler")

    ranked = list_sessions(owner=OWNER)
    assert ranked[0]["session_id"] == SESSION_D
    assert ranked[0]["channel"] == "scheduler"


def test_touch_preserves_content(session_dir):
    """touch_session() only bumps last_seen — payload stays untouched."""
    save_session(
        SESSION_E,
        {
            "data": {"chat_history": [{"role": "user", "content": "hi"}], "title": "T"},
            "owner": OWNER,
            "channel": "vision",
        },
    )
    before = load_session(SESSION_E)
    assert before is not None

    assert touch_session(SESSION_E) is True
    after = load_session(SESSION_E)
    assert after is not None

    assert after["data"] == before["data"]
    assert after["owner"] == OWNER
    assert after["channel"] == "vision"
    assert after["created_at"] == before["created_at"]
    assert after["last_message_at"] == before["last_message_at"]
    assert after["last_seen"] > before["last_seen"]


def test_touch_unknown_session_returns_false(session_dir):
    assert touch_session(SESSION_MISSING) is False


def test_other_owners_sessions_are_not_ranked(session_dir):
    """The auto-load must never reach into another account's sessions."""
    create_empty_session(SESSION_A, owner=OWNER)
    create_empty_session(SESSION_FOREIGN, owner="someone_else")

    assert _ids() == [SESSION_A]
