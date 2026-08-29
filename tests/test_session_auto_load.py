"""Tests for the login auto-load rule: "load the most recently active session".

Activity is ``last_seen``: written by save_session() when content is stored and
by touch_session() when the user switches to a session. Channel sessions
(scheduler/vision/email) take part in the ranking on purpose — after a restart
the user wants to see what came in.
"""

import pytest

import aifred.lib.session_storage as session_storage
from aifred.lib.session_storage import (
    create_empty_session,
    list_sessions,
    load_session,
    save_session,
    touch_session,
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


def test_touch_moves_session_to_top(session_dir):
    """Switching to an older session makes it the most recent one."""
    create_empty_session(SESSION_A, owner=OWNER)
    create_empty_session(SESSION_B, owner=OWNER)
    assert _ids()[0] == SESSION_B

    assert touch_session(SESSION_A) is True
    assert _ids()[0] == SESSION_A


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

    assert touch_session(SESSION_E) is True
    after = load_session(SESSION_E)

    assert after["data"] == before["data"]
    assert after["owner"] == OWNER
    assert after["channel"] == "vision"
    assert after["created_at"] == before["created_at"]
    assert after["last_seen"] > before["last_seen"]


def test_touch_unknown_session_returns_false(session_dir):
    assert touch_session(SESSION_MISSING) is False


def test_other_owners_sessions_are_not_ranked(session_dir):
    """The auto-load must never reach into another account's sessions."""
    create_empty_session(SESSION_A, owner=OWNER)
    create_empty_session(SESSION_FOREIGN, owner="someone_else")

    assert _ids() == [SESSION_A]
