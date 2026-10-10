"""Further document sources: discovery among the enabled plugins, download route."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from aifred.lib import document_sources as ds
from aifred.lib.api.app import api_app
from aifred.lib.document_sources import DocumentSource, DocumentSourceError, SourceDownload, SourceEntry


class FakeSource:
    key = "fake_drive"

    def label(self, lang: str) -> str:
        return "Fake Drive"

    async def list_folder(self, folder_id: str) -> list[SourceEntry]:
        return [SourceEntry("f1", "Ordner", True, None, "", "folder")]

    async def search(self, query: str) -> list[SourceEntry]:
        return []

    async def download(self, file_id: str) -> SourceDownload:
        if file_id == "broken":
            raise DocumentSourceError("Fake Drive: downloading failed (HTTP 404)")

        async def chunks():
            yield b"Heiz"
            yield "öl".encode()

        return SourceDownload(name="Heizöl Bericht.txt", mime="text/plain", chunks=chunks())

    async def upload(self, folder_id: str, name: str, data: bytes, mime: str) -> SourceEntry:
        return SourceEntry("new", name, False, len(data), "", mime)


def _plugin(source, available=True):
    return SimpleNamespace(document_source=source, is_available=lambda: available)


@pytest.fixture
def plugins(monkeypatch: pytest.MonkeyPatch):
    found: list = []
    monkeypatch.setattr("aifred.lib.plugin_registry.discover_tools", lambda: list(found))
    return found


def test_the_fake_satisfies_the_protocol() -> None:
    assert isinstance(FakeSource(), DocumentSource)


def test_only_enabled_available_plugins_offer_a_source(plugins) -> None:
    plugins.extend([
        _plugin(FakeSource()),
        _plugin(None),                                         # e.g. Drive service switched off
        SimpleNamespace(is_available=lambda: True),            # a plugin without the attribute
    ])
    assert list(ds.document_sources()) == ["fake_drive"]
    plugins[0] = _plugin(FakeSource(), available=False)       # not connected
    assert ds.document_sources() == {}
    with pytest.raises(LookupError, match="fake_drive"):
        ds.get_document_source("fake_drive")


@pytest.fixture
def client(plugins, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    plugins.append(_plugin(FakeSource()))
    monkeypatch.setattr("aifred.lib.auth.verify_signed_username", lambda cookie: "mp" if cookie else None)
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, "signed")
    return test_client


def test_a_file_streams_with_its_name(client: TestClient) -> None:
    response = client.get("/documents/source/fake_drive/file/abc")
    assert response.status_code == 200
    assert response.content == "Heizöl".encode()
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''Heiz%C3%B6l%20Bericht.txt"


@pytest.mark.parametrize(("path", "status"), [
    ("/documents/source/nope/file/abc", 404),
    ("/documents/source/fake_drive/file/broken", 502),
])
def test_unknown_source_and_failed_download(client: TestClient, path: str, status: int) -> None:
    assert client.get(path).status_code == status


def test_the_login_cookie_is_required(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/documents/source/fake_drive/file/abc").status_code == 403
