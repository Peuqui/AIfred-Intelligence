"""Further document sources: discovery among the enabled plugins, download route, the document index."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from aifred.lib import document_sources as ds
from aifred.lib import file_manager as fm
from aifred.lib.api.app import api_app
from aifred.lib.document_sources import (
    DocumentSource,
    DocumentSourceError,
    SourceDownload,
    SourceEntry,
)


class FakeSource:
    key = "fake_drive"
    writable = True

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


# ── a source's files in the document index ─────────────────────────

class FakeStore:
    def __init__(self) -> None:
        self.indexed: list[tuple[Path, bytes, str, str]] = []
        self.documents: list[dict] = []
        self.failure: Exception | None = None

    async def index_document(self, file_path: Path, filename: str, source: str = "") -> int:
        if self.failure:
            raise self.failure
        self.indexed.append((file_path, file_path.read_bytes(), filename, source))
        return 3

    def list_documents(self) -> list[dict]:
        if self.failure:
            raise self.failure
        return self.documents


@pytest.fixture
def store(plugins, monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    plugins.append(_plugin(FakeSource()))
    fake = FakeStore()
    monkeypatch.setattr("aifred.lib.document_store.get_document_store", lambda: fake)
    return fake


def test_a_source_file_is_indexed_from_a_temporary_copy(store: FakeStore) -> None:
    result = asyncio.run(fm.index_source_file("fake_drive", "ordner/bericht.txt", "Heizöl Bericht.txt"))
    assert (result.success, result.metadata["chunks"]) == (True, 3)
    copy, data, filename, source = store.indexed[0]
    assert (data, filename, source) == ("Heizöl".encode(), "fake_drive:/ordner/bericht.txt", "fake_drive")
    assert copy.suffix == ".txt"  # the parser goes by it
    assert not copy.exists()


def test_a_type_without_parser_is_refused_before_any_download(store: FakeStore) -> None:
    result = asyncio.run(fm.index_source_file("fake_drive", "broken", "bild.png"))
    assert (result.success, result.detail) == (False, "Unsupported file type: bild.png")


@pytest.mark.parametrize(("source_key", "file_id"), [("fake_drive", "broken"), ("nope", "abc")])
def test_a_file_that_cannot_be_fetched_is_reported(store: FakeStore, source_key: str, file_id: str) -> None:
    assert not asyncio.run(fm.index_source_file(source_key, file_id, "a.txt")).success
    assert store.indexed == []


def test_a_bad_file_is_reported_and_does_not_end_a_run(store: FakeStore) -> None:
    store.failure = ValueError("Document is empty: a.txt")
    result = asyncio.run(fm.index_source_file("fake_drive", "abc", "a.txt"))
    assert (result.success, result.detail) == (False, "a.txt: Document is empty: a.txt")


def test_files_below_walks_the_subfolders() -> None:
    tree = {
        "": [SourceEntry("sub", "sub", True, None, "", "folder"), SourceEntry("a.txt", "a.txt", False, 1, "", "text/plain")],
        "sub": [SourceEntry("sub/b.txt", "b.txt", False, 1, "", "text/plain")],
    }

    class Tree(FakeSource):
        async def list_folder(self, folder_id: str) -> list[SourceEntry]:
            return tree[folder_id]

    assert [entry.id for entry in asyncio.run(ds.files_below(Tree(), ""))] == ["sub/b.txt", "a.txt"]


def test_a_source_document_is_no_orphan(store: FakeStore) -> None:
    store.documents = [
        {"filename": "fake_drive:/a.txt", "total_chunks": 1, "upload_date": "", "source": "fake_drive"},
        {"filename": "gibt-es-nicht.txt", "total_chunks": 1, "upload_date": "", "source": ""},
    ]
    assert [doc["filename"] for doc in fm.list_orphaned().metadata["orphans"]] == ["gibt-es-nicht.txt"]


def test_a_listing_works_without_the_index(store: FakeStore) -> None:
    store.documents = [{"filename": "a.txt", "total_chunks": 7, "upload_date": "", "source": ""}]
    assert fm.chunks_per_document() == {"a.txt": 7}
    store.failure = ConnectionError("ChromaDB is down")
    assert fm.chunks_per_document() == {}
