"""A document source as an area of the file requester (``/files/source-<key>``, lib/source_ops.py): listing with the
index state, what the source can decide the offered actions, folders, rename, move and copy, delete, upload, and the
three reading ways. The index follows rename and move (a folder: every file below it) and loses a deleted file. The
source and the search index are stand-ins."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest
from starlette.testclient import TestClient

from aifred.lib import document_sources, document_store, file_manager
from aifred.lib.document_sources import (
    DocumentSource,
    DocumentSourceError,
    EditableDocumentSource,
    SourceDownload,
    SourceEntry,
    source_index_name,
)

if TYPE_CHECKING:
    _SourceBase = DocumentSource
else:
    _SourceBase = object


def _parent(path: str) -> str:
    return path.rpartition("/")[0]


class _ReadingSource(_SourceBase):
    """What a source must be at least: browse, search, download, upload. Ids are paths."""

    key = "fake"

    def __init__(self, writable: bool = True) -> None:
        self.writable = writable
        self.files: dict[str, bytes] = {"Bericht.md": b"Ein Bericht", "Projekt/Plan.txt": b"Erst denken", "Projekt/Tief/Unten.txt": b"ganz unten"}
        self.folders: set[str] = {"Projekt", "Projekt/Tief", "Leer"}

    def label(self, lang: str) -> str:
        return "Stellvertreter"

    async def list_folder(self, folder_id: str) -> list[SourceEntry]:
        found = [self._entry(path, True) for path in sorted(self.folders) if _parent(path) == folder_id]
        return found + [self._entry(path, False) for path in sorted(self.files) if _parent(path) == folder_id]

    def _entry(self, path: str, is_folder: bool) -> SourceEntry:
        return SourceEntry(id=path, name=path.rsplit("/", 1)[-1], is_folder=is_folder,
                           size_bytes=None if is_folder else len(self.files[path]), modified="2026-11-07T18:00:00+00:00", mime="")

    async def search(self, query: str) -> list[SourceEntry]:
        return [self._entry(path, False) for path in sorted(self.files) if query.lower() in path.lower()]

    async def download(self, file_id: str) -> SourceDownload:
        if file_id not in self.files:
            raise DocumentSourceError("source_download_failed", source="Fake", name=file_id, reason="HTTP 404")

        async def chunks() -> AsyncIterator[bytes]:
            yield self.files[file_id]
        return SourceDownload(name=file_id.rsplit("/", 1)[-1], mime="text/plain", chunks=chunks())

    async def upload(self, folder_id: str, name: str, data: bytes, mime: str) -> SourceEntry:
        path = f"{folder_id}/{name}".strip("/")
        if path in self.files:
            raise DocumentSourceError("source_name_exists", source="Fake", name=name)
        self.files[path] = data
        return self._entry(path, False)


class _EditableSource(_ReadingSource):
    """The same with changes; each answers the id the item has afterwards."""

    def _prefixed(self, old: str, new: str) -> None:
        self.files = {(new + p[len(old):] if p == old or p.startswith(f"{old}/") else p): d for p, d in self.files.items()}
        self.folders = {(new + p[len(old):] if p == old or p.startswith(f"{old}/") else p) for p in self.folders}

    async def create_folder(self, folder_id: str, name: str) -> str:
        path = f"{folder_id}/{name}".strip("/")
        if path in self.folders:
            raise DocumentSourceError("source_name_exists", source="Fake", name=name)
        self.folders.add(path)
        return path

    async def rename(self, file_id: str, name: str) -> str:
        target = f"{_parent(file_id)}/{name}".strip("/")
        self._prefixed(file_id, target)
        return target

    async def delete(self, file_id: str) -> None:
        self.files = {p: d for p, d in self.files.items() if p != file_id and not p.startswith(f"{file_id}/")}
        self.folders = {p for p in self.folders if p != file_id and not p.startswith(f"{file_id}/")}

    async def move(self, file_id: str, target_folder_id: str) -> str:
        target = f"{target_folder_id}/{file_id.rsplit('/', 1)[-1]}".strip("/")
        self._prefixed(file_id, target)
        return target

    async def copy(self, file_id: str, target_folder_id: str) -> str:
        target = f"{target_folder_id}/{file_id.rsplit('/', 1)[-1]}".strip("/")
        for path in [p for p in self.files if p == file_id or p.startswith(f"{file_id}/")]:
            self.files[target + path[len(file_id):]] = self.files[path]
        if file_id in self.folders:
            self.folders |= {target + p[len(file_id):] for p in self.folders if p == file_id or p.startswith(f"{file_id}/")}
        return target


class _Index:
    """Stand-in for the document store: sections per indexed name."""

    def __init__(self) -> None:
        self.chunks: dict[str, int] = {}

    def rename_document(self, old_filename: str, new_filename: str) -> int:
        moved = self.chunks.pop(old_filename, 0)
        if moved:
            self.chunks[new_filename] = moved
        return moved

    async def delete_document(self, path: str, delete_file: bool = False) -> int:
        return self.chunks.pop(path, 0)


@pytest.fixture
def index(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Index]:
    stand_in = _Index()
    monkeypatch.setattr(document_store, "get_document_store", lambda: stand_in)
    monkeypatch.setattr(file_manager, "chunks_per_document", lambda: dict(stand_in.chunks))
    yield stand_in


def _client(monkeypatch: pytest.MonkeyPatch, source: DocumentSource) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    monkeypatch.setattr(document_sources, "document_sources", lambda: {source.key: source})
    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


@pytest.fixture
def source(index: _Index) -> Iterator[_EditableSource]:
    yield _EditableSource()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, source: _EditableSource) -> Iterator[TestClient]:
    yield from _client(monkeypatch, source)


def _names(listing: dict) -> list[str]:
    return [entry["basename"] for entry in listing["files"]]


def test_the_fake_sources_are_what_they_claim() -> None:
    assert isinstance(_EditableSource(), EditableDocumentSource)
    assert not isinstance(_ReadingSource(), EditableDocumentSource)


def test_a_source_is_an_area_with_its_name_and_the_actions_it_can_do(client: TestClient) -> None:
    info = client.get("/files/source-fake/info").json()
    assert info["root"] == "Stellvertreter"
    assert info["actions"] == sorted({"preview", "download", "search", "upload", "newfolder", "rename", "delete", "copy", "move"})
    assert client.get("/files/source-weg/info").status_code == 404


def test_a_listing_carries_the_index_state_of_each_file(client: TestClient, index: _Index) -> None:
    index.chunks[source_index_name("fake", "Bericht.md")] = 4
    root = client.get("/files/source-fake").json()
    assert root["dirname"] == "source-fake://" and root["read_only"] is False
    assert _names(root) == ["Leer", "Projekt", "Bericht.md"]
    report = root["files"][2]
    assert (report["path"], report["type"], report["file_size"], report["indexable"], report["index_chunks"]) == (
        "source-fake://Bericht.md", "file", 11, True, 4)
    assert root["files"][1]["indexable"] is False and root["files"][1]["type"] == "dir"
    assert report["last_modified"] == 1794074400
    inner = client.get("/files/source-fake", params={"path": "source-fake://Projekt"}).json()
    assert _names(inner) == ["Tief", "Plan.txt"] and inner["files"][1]["dir"] == "source-fake://Projekt"


def test_a_search_asks_the_source(client: TestClient, index: _Index) -> None:
    index.chunks[source_index_name("fake", "Projekt/Plan.txt")] = 2
    found = client.get("/files/source-fake/search", params={"filter": "plan"}).json()
    assert [(entry["path"], entry["index_chunks"]) for entry in found["files"]] == [("source-fake://Projekt/Plan.txt", 2)]


def test_view_download_and_text_read_the_source_file(client: TestClient) -> None:
    path = {"path": "source-fake://Projekt/Plan.txt"}
    viewed = client.get("/files/source-fake/preview", params=path)
    assert (viewed.status_code, viewed.content) == (200, b"Erst denken") and "inline" in viewed.headers["content-disposition"]
    downloaded = client.get("/files/source-fake/download", params=path)
    assert downloaded.content == b"Erst denken" and downloaded.headers["content-disposition"].startswith("attachment")
    assert client.get("/files/source-fake/text", params=path).json() == {"name": "Plan.txt", "text": "Erst denken"}
    assert client.get("/files/source-fake/download", params={"path": "source-fake://nichts.txt"}).status_code == 502


def test_a_folder_is_made_and_one_in_use_is_refused(client: TestClient, source: _EditableSource) -> None:
    made = client.post("/files/source-fake/create-folder", json={"path": "source-fake://Projekt", "name": "Neu"}).json()
    assert "Neu" in _names(made) and "Projekt/Neu" in source.folders
    assert client.post("/files/source-fake/create-folder", json={"path": "source-fake://Projekt", "name": "Neu"}).status_code == 502
    assert client.post("/files/source-fake/create-folder", json={"path": "source-fake://", "name": "a/b"}).status_code == 422


def test_a_rename_takes_the_index_along(client: TestClient, source: _EditableSource, index: _Index) -> None:
    index.chunks[source_index_name("fake", "Bericht.md")] = 4
    renamed = client.post("/files/source-fake/rename", json={"path": "source-fake://", "item": "source-fake://Bericht.md", "name": "Neu.md"}).json()
    assert "Neu.md" in _names(renamed) and "Neu.md" in source.files
    assert index.chunks == {source_index_name("fake", "Neu.md"): 4}


def test_moving_a_folder_takes_every_indexed_file_below_it_along(client: TestClient, source: _EditableSource, index: _Index) -> None:
    index.chunks.update({source_index_name("fake", "Projekt/Plan.txt"): 2, source_index_name("fake", "Projekt/Tief/Unten.txt"): 3,
                         source_index_name("fake", "Projekt2/Fremd.txt"): 9})   # a name that only starts like the folder
    client.post("/files/source-fake/move", json={"sources": ["source-fake://Projekt"], "destination": "source-fake://Leer"})
    assert "Leer/Projekt/Plan.txt" in source.files and "Projekt/Plan.txt" not in source.files
    assert index.chunks == {source_index_name("fake", "Leer/Projekt/Plan.txt"): 2, source_index_name("fake", "Leer/Projekt/Tief/Unten.txt"): 3,
                            source_index_name("fake", "Projekt2/Fremd.txt"): 9}


def test_a_copy_leaves_the_index_alone(client: TestClient, source: _EditableSource, index: _Index) -> None:
    index.chunks[source_index_name("fake", "Bericht.md")] = 4
    client.post("/files/source-fake/copy", json={"sources": ["source-fake://Bericht.md"], "destination": "source-fake://Leer"})
    assert source.files["Leer/Bericht.md"] == b"Ein Bericht" and "Bericht.md" in source.files
    assert index.chunks == {source_index_name("fake", "Bericht.md"): 4}


def test_a_delete_takes_the_sections_of_the_file_and_of_a_folder_away(client: TestClient, source: _EditableSource, index: _Index) -> None:
    index.chunks.update({source_index_name("fake", "Bericht.md"): 4, source_index_name("fake", "Projekt/Plan.txt"): 2,
                         source_index_name("fake", "Projekt/Tief/Unten.txt"): 3})
    result = client.post("/files/source-fake/delete", json={"path": "source-fake://", "items": [
        {"path": "source-fake://Bericht.md", "type": "file"}, {"path": "source-fake://Projekt", "type": "dir"}]}).json()
    assert sorted(entry["basename"] for entry in result["deleted"]) == ["Bericht.md", "Projekt"]
    assert source.files == {} and index.chunks == {}


def test_an_upload_goes_to_the_source_and_is_not_indexed(client: TestClient, source: _EditableSource, index: _Index) -> None:
    sent = client.post("/files/source-fake/upload", data={"path": "source-fake://Leer"}, files={"file": ("Neu.md", b"Neu hier", "text/markdown")})
    assert sent.status_code == 200 and source.files["Leer/Neu.md"] == b"Neu hier" and index.chunks == {}
    # any file, not only what the index reads: the store is the user's (an image, an archive …)
    assert client.post("/files/source-fake/upload", data={"path": "source-fake://Leer"},
                       files={"file": ("foto.jpg", b"JPEG", "image/jpeg")}).status_code == 200
    assert source.files["Leer/foto.jpg"] == b"JPEG"


def test_a_source_that_cannot_change_things_offers_and_allows_no_changes(monkeypatch: pytest.MonkeyPatch, index: _Index) -> None:
    for client in _client(monkeypatch, _ReadingSource()):
        assert client.get("/files/source-fake/info").json()["actions"] == ["download", "preview", "search", "upload"]
        assert client.post("/files/source-fake/rename", json={"path": "source-fake://", "item": "source-fake://Bericht.md", "name": "x.md"}).status_code == 403
        assert client.post("/files/source-fake/create-folder", json={"path": "source-fake://", "name": "x"}).status_code == 403
        assert client.post("/files/source-fake/delete", json={"path": "source-fake://", "items": [{"path": "source-fake://Bericht.md", "type": "file"}]}).status_code == 403
        assert client.get("/files/source-fake").status_code == 200


def test_a_source_that_is_not_writable_takes_no_upload_and_no_changes(monkeypatch: pytest.MonkeyPatch, index: _Index) -> None:
    for client in _client(monkeypatch, _EditableSource(writable=False)):
        assert client.get("/files/source-fake/info").json()["actions"] == ["download", "preview", "search"]
        assert client.post("/files/source-fake/upload", data={"path": ""}, files={"file": ("a.md", b"a", "text/markdown")}).status_code == 403
        assert client.post("/files/source-fake/rename", json={"path": "source-fake://", "item": "source-fake://Bericht.md", "name": "x.md"}).status_code == 403
        assert client.get("/files/source-fake").json()["read_only"] is True


def test_a_plugin_that_is_off_gives_no_area(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import plugin_registry
    from aifred.lib.user_error import Conflict

    def refuse(plugin: str) -> None:
        raise Conflict("plugin_disabled", plugin=plugin)

    monkeypatch.setattr(plugin_registry, "document_source_owners", lambda: {"fake": "nextcloud"})
    monkeypatch.setattr(plugin_registry, "require_plugin_enabled", refuse)
    for client in _client(monkeypatch, _ReadingSource()):
        monkeypatch.setattr(document_sources, "document_sources", dict)
        assert client.get("/files/source-fake/info").status_code == 409
