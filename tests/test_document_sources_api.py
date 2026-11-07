"""Further document sources over the API (Vue documents page, lib/document_sources.py): the list of sources,
a folder and a search, downloading a file whose id holds slashes (Nextcloud's id is the path), uploading into a
writable source without indexing, indexing files and folders as a server job (lib/document_indexing.py) and taking
them out again. The source and the search index are stand-ins."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import document_indexing, document_sources, document_store, file_manager
from aifred.lib.document_sources import DocumentSourceError, SourceDownload, SourceEntry, source_index_name

FILES = {
    "Bericht.md": "Ein Bericht in vier Worten",
    "Projekt/Plan.txt": "Erst denken",
    "Projekt/Unten/Tief.txt": "ganz unten",
    "Projekt/leer.txt": "",
    "Projekt/Bild.png": "kein Text",
}


def _entry(path: str, is_folder: bool = False) -> SourceEntry:
    return SourceEntry(id=path, name=path.rsplit("/", 1)[-1], is_folder=is_folder,
                       size_bytes=None if is_folder else len(FILES[path]), modified="2026-11-07T18:00:00", mime="")


class _Source:
    """Stand-in for a plugin's source: ids are paths, as Nextcloud's."""

    key = "stub"

    def __init__(self, writable: bool = True) -> None:
        self.writable = writable
        self.uploaded: list[tuple[str, str]] = []

    def label(self, lang: str) -> str:
        return "Stellvertreter"

    async def list_folder(self, folder_id: str) -> list[SourceEntry]:
        if folder_id == "kaputt":
            raise DocumentSourceError("Stub: listing the folder failed (HTTP 500)")
        prefix = f"{folder_id}/" if folder_id else ""
        below = [path[len(prefix):] for path in FILES if path.startswith(prefix)]
        folders = sorted({f"{prefix}{rest.split('/')[0]}" for rest in below if "/" in rest})
        files = [f"{prefix}{rest}" for rest in below if "/" not in rest]
        return [_entry(path, is_folder=True) for path in folders] + [_entry(path) for path in files]

    async def search(self, query: str) -> list[SourceEntry]:
        return [_entry(path) for path in FILES if query.lower() in path.lower()]

    async def download(self, file_id: str) -> SourceDownload:
        async def chunks() -> AsyncIterator[bytes]:
            yield FILES[file_id].encode()
        return SourceDownload(name=file_id.rsplit("/", 1)[-1], mime="text/plain", chunks=chunks())

    async def upload(self, folder_id: str, name: str, data: bytes, mime: str) -> SourceEntry:
        self.uploaded.append((folder_id, name))
        return SourceEntry(id=f"{folder_id}/{name}", name=name, is_folder=False, size_bytes=len(data), modified="", mime=mime)


class _Index:
    """Stand-in for the document store: sections per indexed name."""

    def __init__(self) -> None:
        self.chunks: dict[str, int] = {}

    async def index_document(self, file_path: Path, filename: str, source: str = "") -> int:
        text = file_path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"Document is empty: {filename}")
        self.chunks[filename] = len(text.split())
        return self.chunks[filename]

    async def delete_document(self, path: str, delete_file: bool = False) -> int:
        return self.chunks.pop(path, 0)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_Source, _Index]]:
    source, index = _Source(), _Index()
    monkeypatch.setattr(document_sources, "document_sources", lambda: {"stub": source})
    monkeypatch.setattr(document_store, "get_document_store", lambda: index)
    monkeypatch.setattr(file_manager, "chunks_per_document", lambda: dict(index.chunks))
    monkeypatch.setattr(document_indexing, "_progress", document_indexing.IndexProgress())
    monkeypatch.setattr(document_indexing, "_waiting", [])
    monkeypatch.setattr(document_indexing, "_current", None)
    monkeypatch.setattr(document_indexing, "_worker", None)
    yield source, index


@pytest.fixture
def client(world: tuple[_Source, _Index]) -> Iterator[TestClient]:
    """As a context: its event loop runs between the requests, as the indexing job needs."""
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def _run_ended(client: TestClient) -> dict[str, object]:
    for _ in range(500):
        progress: dict[str, object] = client.get("/documents/index").json()
        if not progress["running"]:
            return progress
        time.sleep(0.01)
    raise AssertionError("the indexing run does not end")


def _rows(listing: dict[str, object]) -> list[tuple[str, str, bool]]:
    items = listing["items"]
    assert isinstance(items, list)
    return [(item["name"], item["type"], item["indexable"]) for item in items]


def test_the_sources_list_the_local_folder_first(client: TestClient) -> None:
    sources = client.get("/documents/sources").json()
    assert [(source["key"], source["writable"]) for source in sources] == [("local", True), ("stub", True)]
    assert sources[1]["label"] == "Stellvertreter"


def test_a_folder_a_search_and_a_download_by_a_path_id(client: TestClient, world: tuple[_Source, _Index]) -> None:
    _source, index = world
    index.chunks[source_index_name("stub", "Bericht.md")] = 5
    root = client.get("/documents/source/stub").json()
    assert _rows(root) == [("Projekt", "folder", False), ("Bericht.md", "file", True)]
    report = root["items"][1]
    assert (report["indexed"], report["chunks"]) == (True, 5)
    inner = client.get("/documents/source/stub", params={"folder_id": "Projekt"}).json()
    assert ("Bild.png", "file", False) in _rows(inner)                       # no parser for it
    plan = next(item for item in inner["items"] if item["name"] == "Plan.txt")
    assert plan["download_url"] == "/api/documents/source/stub/file/Projekt/Plan.txt"
    download = client.get(plan["download_url"].removeprefix("/api"))
    assert (download.status_code, download.text) == (200, "Erst denken")      # the id's slash does not break it
    hits = client.get("/documents/source/stub", params={"query": "tief"}).json()
    assert [item["id"] for item in hits["items"]] == ["Projekt/Unten/Tief.txt"] and hits["query"] == "tief"


def test_a_missing_source_is_404_and_a_failing_one_502(client: TestClient) -> None:
    assert client.get("/documents/source/weg").status_code == 404
    failed = client.get("/documents/source/stub", params={"folder_id": "kaputt"})
    assert failed.status_code == 502 and "HTTP 500" in failed.json()["detail"]


def test_an_upload_goes_to_the_source_and_is_not_indexed(client: TestClient, world: tuple[_Source, _Index]) -> None:
    source, index = world
    answer = client.post("/documents/source/stub/upload", data={"folder_id": "Projekt"}, files=[
        ("files", ("Neu.md", b"Neu hier", "text/markdown")),
        ("files", ("programm.exe", b"MZ", "application/octet-stream")),
    ]).json()
    assert answer["saved"] == ["Neu.md"] and [rejected["name"] for rejected in answer["rejected"]] == ["programm.exe"]
    assert source.uploaded == [("Projekt", "Neu.md")]
    assert index.chunks == {} and client.get("/documents/index").json()["total"] == 0   # the user decides


def test_a_source_without_uploads_refuses_them(client: TestClient, world: tuple[_Source, _Index]) -> None:
    source, _index = world
    source.writable = False
    assert client.post("/documents/source/stub/upload", files=[("files", ("a.md", b"a", "text/markdown"))]).status_code == 422
    assert source.uploaded == []


def test_files_and_folders_of_a_source_are_indexed_past_a_failure_and_come_out_again(
    client: TestClient, world: tuple[_Source, _Index],
) -> None:
    _source, index = world
    client.post("/documents/source/stub/index", json={"files": [{"id": "Bericht.md", "name": "Bericht.md"}], "folders": ["Projekt"]})
    progress = _run_ended(client)
    assert (progress["total"], progress["indexed"]) == (4, 3)                 # the picture has no parser: not queued
    assert progress["failed"] == [{"path": "stub: leer.txt", "reason": "leer.txt: Document is empty: stub:/Projekt/leer.txt"}]
    assert set(index.chunks) == {source_index_name("stub", path) for path in ("Bericht.md", "Projekt/Plan.txt", "Projekt/Unten/Tief.txt")}
    removed = client.post("/documents/source/stub/deindex", json={"file_ids": ["Bericht.md", "Projekt/Plan.txt"]}).json()
    assert removed["chunks_removed"] == 5 + 2 and set(index.chunks) == {source_index_name("stub", "Projekt/Unten/Tief.txt")}
