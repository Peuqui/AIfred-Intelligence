"""The settings page's housekeeping (api/housekeeping.py): the local files nothing deletes by itself and the audit
log, over their own folders and database."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import security, storage_manager


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    exports, sandbox, showcase = tmp_path / "html_preview", tmp_path / "sandbox_output", tmp_path / "examples"
    exports.mkdir()
    sandbox.mkdir()
    (exports / "chat.html").write_text("<html>Chat</html>")
    (sandbox / "plot.png").write_bytes(b"png")
    monkeypatch.setattr(storage_manager, "_HTML_PREVIEW_DIR", exports)
    monkeypatch.setattr(storage_manager, "_SANDBOX_OUTPUT_DIR", sandbox)
    monkeypatch.setattr(storage_manager, "_SHOWCASE_DIR", showcase)
    monkeypatch.setattr(security, "_audit_db_path", tmp_path / "audit.db")
    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_files_are_listed_copied_into_the_showcase_and_deleted(client: TestClient, tmp_path: Path) -> None:
    files = {entry["kind"]: entry for entry in client.get("/storage/files").json()}
    assert set(files) == {"export", "sandbox"} and files["export"]["is_html"] and not files["sandbox"]["is_html"]
    assert client.post("/storage/showcase", json={"id": files["export"]["id"]}).json() == {"name": "chat.html"}
    assert (tmp_path / "examples" / "chat.html").exists()
    assert client.post("/storage/showcase", json={"id": files["sandbox"]["id"]}).status_code == 500   # HTML only
    left = client.post("/storage/files/delete", json={"ids": [files["sandbox"]["id"], "export/../../outside.txt"]}).json()
    assert [entry["kind"] for entry in left] == ["export"] and not (tmp_path / "sandbox_output" / "plot.png").exists()


def test_deleting_the_last_file_of_a_session_folder_removes_the_folder(client: TestClient, tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox_output"
    (sandbox / "session1").mkdir()
    (sandbox / "session1" / "a.png").write_bytes(b"png")
    (sandbox / "session1" / "b.png").write_bytes(b"png")
    client.post("/storage/files/delete", json={"ids": ["sandbox/session1/a.png"]})
    assert (sandbox / "session1" / "b.png").exists()                  # not empty yet: the folder stays
    client.post("/storage/files/delete", json={"ids": ["sandbox/session1/b.png"]})
    assert not (sandbox / "session1").exists() and sandbox.is_dir()   # empty: gone, the root stays


def test_the_audit_log_shows_the_newest_runs_first(client: TestClient) -> None:
    security.audit_log(session_id="a" * 32, agent_id="aifred", source="browser", tool_name="calculator", tool_tier=0,
                       tool_args_preview="1+1", duration_ms=12.0)
    security.audit_log(session_id="b" * 32, agent_id="sokrates", source="email", tool_name="send_email", tool_tier=1,
                       success=False)
    entries = client.get("/audit").json()
    assert [(entry["tool_name"], entry["success"]) for entry in entries] == [("send_email", False), ("calculator", True)]
    assert (entries[1]["session_short"], entries[1]["args"]) == ("aaaaaaaa", "1+1")
