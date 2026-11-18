"""The Auctor page's endpoint (api/about.py): notice and licence read from a LICENSE file, version and repo URL."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import config

LICENSE = """# Some Licence 9.9

<https://example.org/licenses/9.9>

## Notices

> Required Notice: Copyright 1999 Jane Doe (JD)
"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    license_file = tmp_path / "LICENSE"
    license_file.write_text(LICENSE, encoding="utf-8")
    monkeypatch.setattr(config, "LICENSE_FILE", license_file)
    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_about_reads_notice_and_licence_from_the_license_file(client: TestClient) -> None:
    body = client.get("/about").json()
    assert body["author_notice"] == "Required Notice: Copyright 1999 Jane Doe (JD)"
    assert body["license_name"] == "Some Licence 9.9"
    assert body["license_url"] == "https://example.org/licenses/9.9"
    assert body["repo_url"] == config.REPO_URL


def test_about_names_the_running_commit(client: TestClient) -> None:
    body = client.get("/about").json()
    assert body["version_commit"] and body["version_date"]


def test_about_needs_the_login(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/about").status_code == 403
