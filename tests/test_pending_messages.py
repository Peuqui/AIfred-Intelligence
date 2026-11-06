"""Messages a browser session sends as a turn of its own: API injection and dictation.

The browser's 500 ms poll takes the oldest pending message and starts send_message as an event
of its own. Until 06.11.2026 a dictation sent straight to the AI ran send_message inside the
recording's POST /_upload: Reflex cancels what that request runs or chains when it ends, so a
closed tab or a locked phone cut the answer off. The queue also keeps a dictation made while
AIfred still answers (it used to be dropped with "Already generating") and a second one behind it.
"""

import ast
from pathlib import Path

import pytest

from aifred.lib import session_storage
from aifred.lib.session_storage import load_session, pop_pending_message, queue_pending_message

SESSION = "c" * 32
STATE_DIR = Path(__file__).resolve().parent.parent / "aifred" / "state"


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    yield tmp_path
    session_storage._session_meta_cache.clear()


def test_messages_come_out_in_the_order_they_went_in(session_dir: Path) -> None:
    assert queue_pending_message(SESSION, "erstes Diktat")
    assert queue_pending_message(SESSION, "zweites Diktat")
    assert pop_pending_message(SESSION) == "erstes Diktat"
    assert (session_dir / f"{SESSION}.pending").exists()   # one is still waiting
    assert pop_pending_message(SESSION) == "zweites Diktat"
    assert not (session_dir / f"{SESSION}.pending").exists()
    assert pop_pending_message(SESSION) is None


def test_queueing_keeps_the_rest_of_the_session(session_dir: Path) -> None:
    session_storage.save_session(SESSION, {"data": {"chat_history": [{"role": "user", "content": "hallo"}]}}, owner="t")
    assert queue_pending_message(SESSION, "Diktat")
    session = load_session(SESSION)
    assert session is not None
    assert session["data"]["chat_history"] == [{"role": "user", "content": "hallo"}]
    assert session["data"]["pending_messages"] == ["Diktat"]


def test_an_unreadable_session_file_is_never_overwritten(session_dir: Path) -> None:
    path = session_dir / f"{SESSION}.json"
    path.write_text("{ kaputt", encoding="utf-8")
    assert not queue_pending_message(SESSION, "Diktat")
    assert path.read_text(encoding="utf-8") == "{ kaputt"


def test_no_state_handler_runs_send_message_inside_itself() -> None:
    """A turn started inline belongs to the caller's request: inside an upload handler it dies
    with that request. Handlers start it as its own event (``yield``/``return`` of
    ``AIState.send_message``) or through the pending queue."""
    inline = []
    for path in sorted(STATE_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "send_message" and ast.unparse(node.func.value) == "self"):
                inline.append(f"{path.name}:{node.lineno}")
    assert inline == []
