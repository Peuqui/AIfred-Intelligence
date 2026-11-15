"""AuthenticatedStaticFiles: serves only with a valid login cookie."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from aifred.lib.auth import sign_username
from aifred.lib.authenticated_static import AuthenticatedStaticFiles
from aifred.lib.auth import USERNAME_COOKIE_NAME


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "secret.jpg").write_bytes(b"\xff\xd8\xff\xe0imagedata")
    app = Starlette()
    app.mount("/_upload/vigilantia", AuthenticatedStaticFiles(directory=str(tmp_path)))
    return TestClient(app)


def test_rejects_without_cookie(client: TestClient) -> None:
    resp = client.get("/_upload/vigilantia/secret.jpg")
    assert resp.status_code == 403


def test_rejects_forged_cookie(client: TestClient) -> None:
    client.cookies.set(USERNAME_COOKIE_NAME, "admin.deadbeef")  # wrong signature
    resp = client.get("/_upload/vigilantia/secret.jpg")
    assert resp.status_code == 403


def test_serves_with_valid_cookie(client: TestClient) -> None:
    client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    resp = client.get("/_upload/vigilantia/secret.jpg")
    assert resp.status_code == 200
    assert resp.content == b"\xff\xd8\xff\xe0imagedata"


def test_missing_file_still_needs_auth_first(client: TestClient) -> None:
    # No cookie → 403 even for a non-existent path (don't leak existence).
    resp = client.get("/_upload/vigilantia/nope.jpg")
    assert resp.status_code == 403


def test_every_upload_folder_needs_the_login_except_shared_previews(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import upload_files

    monkeypatch.setattr(upload_files, "DATA_DIR", tmp_path)
    app = Starlette()
    upload_files.mount_upload_folders(app)
    client = TestClient(app)
    for entry in upload_files.UPLOAD_FOLDERS:
        (tmp_path / entry.folder / "file.txt").write_text(entry.name)
        expected = 403 if entry.needs_login else 200
        assert client.get(f"{entry.path}/file.txt").status_code == expected, entry.path
    assert [entry.path for entry in upload_files.UPLOAD_FOLDERS if not entry.needs_login] == ["/_upload/html_preview"]
    client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    assert client.get("/_upload/audio/file.txt").text == "session_audio"
