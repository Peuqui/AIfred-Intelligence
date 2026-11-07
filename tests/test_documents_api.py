"""The local documents folder over the API (Vue documents page, lib/file_manager.py): listing with the
place in the search index, new folder, rename, delete from disk and/or index (one failing name does
not stop the rest), preview through the index's parser; nothing outside the folder. The folder is a
temporary one, the search index a stand-in."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import document_store, file_manager


class _Index:
    """Stand-in for the document store: what is indexed, by path."""

    def __init__(self) -> None:
        self.chunks: dict[str, int] = {"bericht.txt": 3}

    async def delete_document(self, path: str, delete_file: bool = False) -> int:
        return self.chunks.pop(path, 0)

    def list_documents(self) -> list[dict[str, str]]:
        return [{"filename": path} for path in self.chunks]

    def rename_document(self, old: str, new: str) -> int:
        self.chunks[new] = self.chunks.pop(old)
        return 1


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
    yield folder, index


@pytest.fixture
def client(documents: tuple[Path, _Index]) -> TestClient:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    return test_client


def _names(listing: dict[str, object]) -> list[tuple[str, str]]:
    items = listing["items"]
    assert isinstance(items, list)
    return [(item["name"], item["type"]) for item in items]


def test_the_listing_shows_folders_first_and_what_is_indexed(client: TestClient) -> None:
    listing = client.get("/documents/local").json()
    assert _names(listing) == [("projekt", "folder"), ("bericht.txt", "file"), ("daten.xyz", "file")]
    report = next(item for item in listing["items"] if item["name"] == "bericht.txt")
    assert report["indexed"] and report["chunks"] == 3 and listing["download_prefix"] == "/_upload/documents/"
    inner = client.get("/documents/local", params={"folder": "projekt"}).json()
    assert _names(inner) == [("plan.md", "file")] and inner["download_prefix"] == "/_upload/documents/projekt/"
    assert client.get("/documents/local", params={"folder": "../"}).status_code == 422


def test_a_new_folder_and_a_rename_answer_with_the_listing(client: TestClient, documents: tuple[Path, _Index]) -> None:
    folder, index = documents
    assert ("archiv", "folder") in _names(client.post("/documents/local/folders", json={"name": "archiv"}).json())
    assert client.post("/documents/local/folders", json={"name": "../draußen"}).status_code == 422
    listing = client.post("/documents/local/rename", json={"old": "bericht.txt", "new": "bericht-2026.txt"}).json()
    assert ("bericht-2026.txt", "file") in _names(listing) and not (folder / "bericht.txt").exists()
    assert index.chunks == {"bericht-2026.txt": 3}                          # the index followed
    assert client.post("/documents/local/rename", json={"old": "fehlt.txt", "new": "x.txt"}).status_code == 422


def test_a_deletion_goes_on_past_a_failing_name(client: TestClient, documents: tuple[Path, _Index]) -> None:
    folder, index = documents
    answer = client.post("/documents/local/delete", json={
        "files": ["bericht.txt", "gibt-es-nicht.txt"], "folders": ["projekt"], "from_disk": True, "from_index": True,
    }).json()
    assert answer["deleted"] == 2 and answer["chunks_removed"] == 3
    assert [failure["name"] for failure in answer["failed"]] == ["gibt-es-nicht.txt"]
    assert _names(answer["listing"]) == [("daten.xyz", "file")] and not (folder / "projekt").exists()
    assert index.chunks == {}                                                  # the report left the index too


def test_only_the_index_forgets_a_file_kept_on_disk(client: TestClient, documents: tuple[Path, _Index]) -> None:
    folder, index = documents
    answer = client.post("/documents/local/delete", json={"files": ["bericht.txt"], "from_disk": False, "from_index": True}).json()
    assert answer["deleted"] == 1 and (folder / "bericht.txt").is_file() and index.chunks == {}


def test_the_preview_reads_what_the_index_reads(client: TestClient) -> None:
    preview = client.get("/documents/local/preview", params={"path": "projekt/plan.md"}).json()
    assert preview["name"] == "plan.md" and "Erst denken." in preview["text"]
    assert client.get("/documents/local/preview", params={"path": "daten.xyz"}).status_code == 422      # no parser
    assert client.get("/documents/local/preview", params={"path": "fehlt.txt"}).status_code == 404
    assert client.get("/documents/local/preview", params={"path": "../../etc/passwd"}).status_code == 422
