"""Peek of an agent bundle: an unreadable bundle is the uploader's error (400), with the reason."""

import io
import json
import zipfile

import pytest
from starlette.testclient import TestClient

from aifred.lib.agent_bundle import BUNDLE_FORMAT_VERSION
from aifred.lib.api.app import api_app
from aifred.lib.auth import sign_username
from aifred.lib.browser_storage import USERNAME_COOKIE_NAME
from aifred.lib.i18n import t


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.parametrize(("upload", "reason"), [
    (b"no zip at all", "File is not a zip file"),
    (_zip({"readme.txt": b"x"}), "manifest.json"),
    (_zip({"manifest.json": b"{not json"}), "Expecting property name"),
    (_zip({"manifest.json": json.dumps({"format_version": "0"}).encode()}), t("error_bundle_version", version="0")),
    (_zip({"manifest.json": json.dumps({"format_version": BUNDLE_FORMAT_VERSION, "agent_ids": []}).encode()}),
     t("error_bundle_empty")),
])
def test_an_unreadable_bundle_is_a_400_with_its_reason(upload: bytes, reason: str) -> None:
    client = TestClient(api_app)
    client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    response = client.post("/agents/import/peek", files={"file": ("b.zip", upload)})
    assert response.status_code == 400
    assert response.json()["detail"].startswith("Bundle nicht lesbar: ")
    assert reason in response.json()["detail"]


def test_a_readable_bundle_answers_with_its_agents() -> None:
    # As export_bundle writes it: exported_at is a Unix time (a string made the answer a 500 on 07.11.2026).
    upload = _zip({
        "manifest.json": json.dumps({"format_version": BUNDLE_FORMAT_VERSION, "exported_at": 1794078740, "agent_ids": ["prueffall"]}).encode(),
        "agents/prueffall/config.json": json.dumps({"display_name": "Prüffall", "description": "aus dem Test"}).encode(),
    })
    client = TestClient(api_app)
    client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    peek = client.post("/agents/import/peek", files={"file": ("b.zip", upload)}).json()
    assert peek["exported_at"] == 1794078740
    assert peek["agents"] == [{"agent_id": "prueffall", "display_name": "Prüffall", "description": "aus dem Test", "exists_locally": False}]
