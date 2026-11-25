"""The search index of the local documents folder over the API (Vue documents page, lib/file_manager.py):
indexing files and folders as a server job (lib/document_indexing.py), taking files out of the index while they stay
on the disk, and the care of the index (orphans, clear). Browsing, uploading, previewing and downloading are the file
API's (tests/test_files_api.py). The folder is a temporary one, the search index a stand-in."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import document_indexing, document_store, file_manager


class _Index:
    """Stand-in for the document store: what is indexed, by path."""

    def __init__(self) -> None:
        self.chunks: dict[str, int] = {"bericht.txt": 3}

    async def delete_document(self, path: str, delete_file: bool = False) -> int:
        return self.chunks.pop(path, 0)

    def list_documents(self) -> list[dict[str, object]]:
        return [{"filename": path, "total_chunks": chunks, "upload_date": "2026-11-07T21:04:00", "source": ""} for path, chunks in self.chunks.items()]

    def clear(self) -> int:
        removed = sum(self.chunks.values())
        self.chunks.clear()
        return removed

    def rename_document(self, old: str, new: str) -> int:
        self.chunks[new] = self.chunks.pop(old)
        return 1

    async def index_document(self, file_path: Path, filename: str, source: str = "") -> int:
        text = file_path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"Document is empty: {filename}")
        self.chunks[filename] = len(text.split())
        return self.chunks[filename]


@pytest.fixture
def documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, _Index]]:
    folder = tmp_path / "documents"
    (folder / "projekt").mkdir(parents=True)
    (folder / "projekt" / "plan.md").write_text("# Plan\n\nErst denken.")
    (folder / "bericht.txt").write_text("Der Bericht.")
    (folder / "daten.xyz").write_bytes(b"\x00\x01")
    index = _Index()
    monkeypatch.setattr(file_manager, "DOCUMENTS_DIR", folder)
    monkeypatch.setattr(file_manager, "chunks_per_document", lambda: dict(index.chunks))
    monkeypatch.setattr(document_store, "get_document_store", lambda: index)
    monkeypatch.setattr(document_indexing, "_progress", document_indexing.IndexProgress())
    monkeypatch.setattr(document_indexing, "_waiting", [])
    monkeypatch.setattr(document_indexing, "_current", None)
    monkeypatch.setattr(document_indexing, "_worker", None)
    yield folder, index


@pytest.fixture
def client(documents: tuple[Path, _Index]) -> Iterator[TestClient]:
    """As a context: its event loop runs between the requests, as the indexing job needs."""
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def _run_ended(client: TestClient) -> dict[str, object]:
    """The indexing run's outcome, once the server has worked through its queue."""
    for _ in range(500):
        progress: dict[str, object] = client.get("/documents/index").json()
        if not progress["running"]:
            return progress
        time.sleep(0.01)
    raise AssertionError("the indexing run does not end")


def test_deindexing_forgets_the_files_and_keeps_them_on_disk(client: TestClient, documents: tuple[Path, _Index]) -> None:
    folder, index = documents
    index.chunks["projekt/plan.md"] = 2
    answer = client.post("/documents/local/deindex", json={"files": ["bericht.txt", "projekt/plan.md", "nie-indexiert.txt"]})
    assert answer.status_code == 200 and answer.json() == {"chunks_removed": 5}
    assert index.chunks == {}
    assert (folder / "bericht.txt").is_file() and (folder / "projekt" / "plan.md").is_file()


def test_a_damaged_file_does_not_stop_the_run_and_a_folder_brings_what_is_below(
    client: TestClient, documents: tuple[Path, _Index],
) -> None:
    folder, index = documents
    (folder / "leer.txt").write_text("   ")
    (folder / "projekt" / "unten").mkdir()
    (folder / "projekt" / "unten" / "tief.txt").write_text("ganz unten")
    client.post("/documents/local/index", json={"files": ["leer.txt", "bericht.txt", "bericht.txt"], "folders": ["projekt"]})
    progress = _run_ended(client)
    assert (progress["total"], progress["done"], progress["indexed"]) == (4, 4, 3)
    assert progress["failed"] == [{"path": "leer.txt", "reason": "Document is empty: leer.txt"}]
    assert set(index.chunks) == {"bericht.txt", "projekt/plan.md", "projekt/unten/tief.txt"}
    assert client.post("/documents/local/index", json={"folders": ["../.."]}).status_code == 422


def test_the_index_care_finds_orphans_and_removes_only_those(client: TestClient, documents: tuple[Path, _Index]) -> None:
    _folder, index = documents
    index.chunks["weg.txt"] = 4                                              # its file is gone
    overview = client.get("/documents/index/overview").json()
    assert (overview["documents"], overview["chunks"]) == (2, 7)
    assert overview["orphans"] == [{"filename": "weg.txt", "chunks": 4, "indexed": "07.11.2026 21:04"}]
    after = client.post("/documents/index/orphans/remove", json={"filenames": ["weg.txt", "bericht.txt"]}).json()
    assert after["orphans"] == [] and index.chunks == {"bericht.txt": 3}     # the file on disk keeps its entry
    assert client.post("/documents/index/clear").json() == {"chunks_removed": 3} and index.chunks == {}
