"""Hochladen und Löschen halten den Dokumenten-Index mit.

Kontrakt (state/_document_mixin.py, lib/file_manager.py):
- Was in den Dokumentenordner hochgeladen wird, wird von selbst indexiert, nach dem Speichern und im
  Hintergrund. Ein Fehler dabei lässt die Datei stehen und wird dem Benutzer gezeigt.
- Ein Upload in eine weitere Quelle bleibt draußen: Deren Dateien liegen hinter einer höheren Stufe
  als die Suche, Indexieren bleibt dort die Entscheidung des Benutzers.
- Löschen entfernt Datei und Index. Klappt das am Index nicht, bleibt die Datei und die Antwort sagt
  es: Sonst fände die Suche weiter, was es nicht mehr gibt.
- Der Index eines gleichnamigen Dokuments in einem anderen Ordner bleibt unberührt.
- Umbenennen und Verschieben nehmen den Index mit, auch alles unter einem Ordner. Die IDs ziehen um:
  An ihnen hängen die Nachbarn eines Treffers, und ein neues Dokument unter dem alten Namen schriebe
  sonst über das umbenannte.

Ohne Netz: FakeStore steht für den DocumentStore, die Dateien liegen in einem Temp-Ordner. Was an den
IDs hängt, prüft ein echter ChromaDB-Client im Arbeitsspeicher: Eine Attrappe verdeckte genau das.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from pathlib import Path
from typing import Any, Self

import chromadb
import pytest
from reflex.event import EventSpec

from aifred.lib import document_store as ds
from aifred.lib import file_manager as fm
from aifred.state import AIState


class FakeStore:
    def __init__(self) -> None:
        self.documents: dict[str, int] = {}
        self.failure: Exception | None = None

    def list_documents(self) -> list[dict[str, Any]]:
        return [{"filename": name} for name in self.documents]

    async def delete_document(self, filename: str, delete_file: bool = True) -> int:
        assert delete_file is False
        if self.failure:
            raise self.failure
        return self.documents.pop(filename, 0)

    def rename_document(self, old_filename: str, new_filename: str) -> int:
        count = self.documents.pop(old_filename, 0)
        if count:
            self.documents[new_filename] = count
        return count


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(ds, "get_document_store", lambda: fake)
    return fake


@pytest.fixture
def documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fm, "DOCUMENTS_DIR", tmp_path)
    (tmp_path / "ordner").mkdir()
    (tmp_path / "ordner" / "brief.txt").write_text("Text")
    (tmp_path / "brief.txt").write_text("Anderer Text")
    return tmp_path


def test_deleting_removes_file_and_index(store: FakeStore, documents: Path) -> None:
    store.documents["ordner/brief.txt"] = 3
    result = asyncio.run(fm.delete_file("ordner", "brief.txt"))
    assert result.success and result.metadata["chunks_removed"] == 3
    assert store.documents == {} and not (documents / "ordner" / "brief.txt").exists()


def test_when_the_index_fails_the_file_stays_and_the_answer_says_so(store: FakeStore, documents: Path) -> None:
    store.documents["ordner/brief.txt"] = 3
    store.failure = ConnectionError("Chroma weg")
    result = asyncio.run(fm.delete_file("ordner", "brief.txt"))
    assert not result.success and "Chroma weg" in result.detail
    assert (documents / "ordner" / "brief.txt").exists()


def test_without_an_index_the_file_stays(monkeypatch: pytest.MonkeyPatch, documents: Path) -> None:
    monkeypatch.setattr(ds, "get_document_store", lambda: None)
    assert not asyncio.run(fm.delete_file("ordner", "brief.txt")).success
    assert (documents / "ordner" / "brief.txt").exists()


def test_the_disk_alone_can_still_be_chosen(monkeypatch: pytest.MonkeyPatch, documents: Path) -> None:
    monkeypatch.setattr(ds, "get_document_store", lambda: None)
    assert asyncio.run(fm.delete_file("ordner", "brief.txt", from_index=False)).success
    assert not (documents / "ordner" / "brief.txt").exists()


def test_a_file_of_the_same_name_elsewhere_keeps_its_index(store: FakeStore, documents: Path) -> None:
    store.documents["brief.txt"] = 2
    asyncio.run(fm.delete_file("ordner", "brief.txt"))
    assert store.documents == {"brief.txt": 2}


def test_deleting_a_folder_empties_its_part_of_the_index(store: FakeStore, documents: Path) -> None:
    store.documents.update({"ordner/brief.txt": 3, "ordner2/anderes.txt": 1, "brief.txt": 2})
    result = asyncio.run(fm.delete_folder("", "ordner", recursive=True))
    assert result.success and result.metadata["chunks_removed"] == 3
    assert set(store.documents) == {"ordner2/anderes.txt", "brief.txt"}
    assert not (documents / "ordner").exists()


def test_when_the_index_fails_the_folder_stays(store: FakeStore, documents: Path) -> None:
    store.documents["ordner/brief.txt"] = 3
    store.failure = ConnectionError("Chroma weg")
    result = asyncio.run(fm.delete_folder("", "ordner", recursive=True))
    assert not result.success and (documents / "ordner" / "brief.txt").exists()


def test_a_folder_can_leave_the_index_and_stay_on_disk(store: FakeStore, documents: Path) -> None:
    store.documents["ordner/brief.txt"] = 3
    assert asyncio.run(fm.delete_folder("", "ordner", recursive=True, from_disk=False)).success
    assert store.documents == {} and (documents / "ordner" / "brief.txt").exists()


def test_moving_a_file_takes_its_index_along(store: FakeStore, documents: Path) -> None:
    store.documents["brief.txt"] = 2
    result = fm.move_file("brief.txt", "ordner/neu.txt")
    assert result.success and result.metadata["chunks_updated"] == 2
    assert store.documents == {"ordner/neu.txt": 2}


def test_moving_a_folder_takes_what_lies_below_along(store: FakeStore, documents: Path) -> None:
    store.documents.update({"ordner/brief.txt": 3, "ordner2/anderes.txt": 1})
    assert fm.move_file("ordner", "archiv/ordner").success
    assert store.documents == {"archiv/ordner/brief.txt": 3, "ordner2/anderes.txt": 1}


def test_renaming_a_folder_takes_what_lies_below_along(store: FakeStore, documents: Path) -> None:
    store.documents.update({"ordner/brief.txt": 3, "ordner2/anderes.txt": 1})
    result = fm.rename("", "ordner", "neu")
    assert result.success and result.metadata["chunks_updated"] == 3
    assert store.documents == {"neu/brief.txt": 3, "ordner2/anderes.txt": 1}


def test_renaming_without_sync_leaves_the_index_alone(store: FakeStore, documents: Path) -> None:
    store.documents["ordner/brief.txt"] = 3
    assert fm.rename("ordner", "brief.txt", "neu.txt", sync_index=False).success
    assert store.documents == {"ordner/brief.txt": 3}


@pytest.fixture
def real_store(monkeypatch: pytest.MonkeyPatch) -> ds.DocumentStore:
    """A DocumentStore on a real, throwaway Chroma collection. Each line of a file is one passage."""
    client = chromadb.EphemeralClient()
    store = ds.DocumentStore.__new__(ds.DocumentStore)
    store._collection = client.create_collection(f"test_{uuid.uuid4().hex}")
    store._embed_index = lambda chunks: [[float(len(text)), 1.0] for text in chunks]  # type: ignore[assignment]
    store._folder_cache = None
    store._search_index = None
    store._search_index_lock = threading.Lock()
    monkeypatch.setattr(ds, "_chunk_text", lambda text, *_: text.split("\n"))
    return store


def _index(store: ds.DocumentStore, tmp_path: Path, name: str, *passages: str) -> None:
    path = tmp_path / Path(name).name
    path.write_text("\n".join(passages), encoding="utf-8")
    asyncio.run(store.index_document(path, name))


def _passages(store: ds.DocumentStore, name: str) -> list[str]:
    data = store._collection.get(where={"filename": name})
    return [text for _, text in sorted(zip(data["ids"], data["documents"], strict=True))]


def test_a_renamed_document_keeps_text_and_embeddings_and_its_neighbours_are_found(
    real_store: ds.DocumentStore, tmp_path: Path,
) -> None:
    _index(real_store, tmp_path, "ordner/a.txt", "null", "eins", "zwei")
    assert real_store.rename_document("ordner/a.txt", "archiv/b.txt") == 3

    assert _passages(real_store, "archiv/b.txt") == ["null", "eins", "zwei"]
    assert _passages(real_store, "ordner/a.txt") == []
    neighbour = real_store._collection.get(
        ids=[ds.chunk_id("archiv/b.txt", 1)], include=["documents", "embeddings", "metadatas"],
    )
    assert neighbour["documents"] == ["eins"] and list(neighbour["embeddings"][0]) == [4.0, 1.0]
    assert neighbour["metadatas"][0]["folder"] == "archiv"


def test_a_new_document_under_the_old_name_leaves_the_renamed_one_whole(
    real_store: ds.DocumentStore, tmp_path: Path,
) -> None:
    _index(real_store, tmp_path, "a.txt", "alt null", "alt eins", "alt zwei")
    real_store.rename_document("a.txt", "a-alt.txt")
    _index(real_store, tmp_path, "a.txt", "neu null", "neu eins")
    assert _passages(real_store, "a-alt.txt") == ["alt null", "alt eins", "alt zwei"]
    assert _passages(real_store, "a.txt") == ["neu null", "neu eins"]


def test_renaming_onto_a_longer_document_leaves_nothing_of_it(
    real_store: ds.DocumentStore, tmp_path: Path,
) -> None:
    _index(real_store, tmp_path, "lang.txt", "1", "2", "3", "4")
    _index(real_store, tmp_path, "kurz.txt", "a")
    real_store.rename_document("kurz.txt", "lang.txt")
    assert _passages(real_store, "lang.txt") == ["a"]


def test_a_document_longer_than_one_write_arrives_whole(
    real_store: ds.DocumentStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ds, "DOCUMENT_EMBED_BATCH_SIZE", 2)
    _index(real_store, tmp_path, "a.txt", *"abcde")
    assert real_store.rename_document("a.txt", "b.txt") == 5
    assert sorted(_passages(real_store, "b.txt")) == list("abcde")


def test_renaming_nothing_changes_nothing(real_store: ds.DocumentStore, tmp_path: Path) -> None:
    _index(real_store, tmp_path, "b.txt", "bleibt")
    assert real_store.rename_document("gibt-es-nicht.txt", "b.txt") == 0
    assert _passages(real_store, "b.txt") == ["bleibt"]


def test_renaming_renews_the_search(real_store: ds.DocumentStore, tmp_path: Path) -> None:
    _index(real_store, tmp_path, "a.txt", "text")
    real_store._folder_cache = {"alt"}
    real_store.rename_document("a.txt", "b.txt")
    assert real_store._folder_cache is None


class _FakeState:
    """As much of the state as the handlers touch. ``async with self`` is the lock of a
    background event."""

    _index_toast = AIState._index_toast

    def __init__(self) -> None:
        self.ui_language = "de"
        self.logged_in_user = "peuqui"
        self.is_uploading_document = False
        self.document_upload_status = ""
        self.doc_current_folder = "ordner"
        self.doc_source = "local"
        self.doc_delete_batch = False
        self.doc_delete_target = "brief.txt"
        self.doc_delete_is_folder = False
        self.doc_delete_from_disk = True
        self.doc_delete_from_index = True
        self.doc_rename_target = "brief.txt"
        self.doc_rename_value = "neu.txt"
        self.db_orphans_visible = False
        self.db_orphans: list[dict[str, str]] = []
        self.debug: list[str] = []
        self.refreshed = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def add_debug(self, message: str) -> None:
        self.debug.append(message)

    def _refresh_file_list(self) -> None:
        self.refreshed += 1

    async def _refresh_open_source(self) -> None:
        self.refreshed += 1

    async def _refresh_db_documents(self) -> None:
        self.refreshed += 1

    async def _reload_db_orphans(self) -> None:
        self.refreshed += 1

    @staticmethod
    def doc_index_uploaded(rel_paths: list[str]) -> tuple[str, list[str]]:
        return ("index", rel_paths)

    def _open_remote_folder_id(self) -> str:
        return "/"


def _run(handler_name: str, state: _FakeState, *args: Any) -> list[Any]:
    async def run() -> list[Any]:
        return [event async for event in AIState.event_handlers[handler_name].fn(state, *args)]
    return asyncio.run(run())


def _toasts(events: list[Any]) -> str:
    return " ".join(str(event) for event in events if event is not None)


@pytest.fixture
def indexed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def index_file(rel_path: str) -> fm.FileOpResult:
        calls.append(rel_path)
        return fm.FileOpResult(True, "ok", {"chunks": 4})

    monkeypatch.setattr(fm, "index_file", index_file)
    return calls


def test_an_upload_is_indexed_where_it_lies(indexed: list[str]) -> None:
    state = _FakeState()
    events = _run("doc_index_uploaded", state, ["ordner/a.pdf", "b.pdf"])
    assert indexed == ["ordner/a.pdf", "b.pdf"]
    assert "4" in _toasts(events) and state.refreshed == 2 and state.document_upload_status == ""


def test_a_failed_index_is_shown_and_does_not_end_the_run(monkeypatch: pytest.MonkeyPatch, indexed: list[str]) -> None:
    async def broken(rel_path: str) -> fm.FileOpResult:
        if rel_path.endswith("a.pdf"):
            raise ValueError("Document is empty: a.pdf")
        indexed.append(rel_path)
        return fm.FileOpResult(True, "ok", {"chunks": 1})

    monkeypatch.setattr(fm, "index_file", broken)
    state = _FakeState()
    events = _run("doc_index_uploaded", state, ["a.pdf", "c.pdf"])
    assert "Document is empty" in _toasts(events) and indexed == ["c.pdf"]
    assert state.document_upload_status == ""


def test_a_failed_delete_is_reported_not_celebrated(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(*args: Any, **kwargs: Any) -> fm.FileOpResult:
        return fm.FileOpResult(False, "brief.txt stays: removing it from the index failed: Chroma weg")

    monkeypatch.setattr(fm, "delete_file", refused)
    state = _FakeState()
    events = _run("doc_confirm_delete", state)
    assert "Chroma weg" in _toasts(events) and "error" in _toasts(events)
    assert state.doc_delete_target == ""


def test_the_dialog_can_clear_a_folder_from_the_index_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def delete_folder(parent: str, name: str, **kwargs: Any) -> fm.FileOpResult:
        seen.update(kwargs)
        return fm.FileOpResult(True, "ok", {"chunks_removed": 5})

    monkeypatch.setattr(fm, "delete_folder", delete_folder)
    state = _FakeState()
    state.doc_delete_is_folder = True
    state.doc_delete_from_disk = False
    _run("doc_confirm_delete", state)
    assert seen == {"recursive": True, "from_disk": False, "from_index": True}


class _Upload:
    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self.content = content

    async def read(self) -> bytes:
        return self.content


def test_an_upload_starts_the_indexing_after_saving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fm, "DOCUMENTS_DIR", tmp_path)               # where save_upload puts it
    (tmp_path / "ordner").mkdir()                                     # the open folder
    state = _FakeState()
    events = _run("handle_document_upload", state, [_Upload("a.txt", b"Text"), _Upload("b.exe", b"MZ")])
    assert (tmp_path / "ordner" / "a.txt").read_bytes() == b"Text"
    assert events[-1] == ("index", ["ordner/a.txt"])


def test_a_rejected_upload_starts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fm, "DOCUMENTS_DIR", tmp_path)
    events = _run("handle_document_upload", _FakeState(), [_Upload("b.exe", b"MZ")])
    assert not any(isinstance(event, tuple) for event in events)


def test_an_upload_to_a_further_source_stays_out_of_the_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fm, "DOCUMENTS_DIR", tmp_path)
    uploaded: list[str] = []

    class Source:
        async def upload(self, folder_id: str, name: str, data: bytes, mime: str) -> None:
            uploaded.append(name)

    monkeypatch.setattr("aifred.lib.document_sources.get_document_source", lambda key: Source())
    state = _FakeState()
    state.doc_source = "nextcloud_files"
    events = _run("handle_document_upload", state, [_Upload("a.txt", b"Text")])
    assert uploaded == ["a.txt"] and not any(isinstance(event, tuple) for event in events)


def test_the_indexing_runs_as_a_background_event_of_the_state() -> None:
    handler = AIState.event_handlers["doc_index_uploaded"]
    assert isinstance(handler(["a.txt"]), EventSpec)
    assert handler.is_background


def test_a_rename_of_a_folder_does_not_run_on_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def rename(*args: Any, **kwargs: Any) -> fm.FileOpResult:
        seen.append(threading.get_ident())
        return fm.FileOpResult(True, "ok", {"chunks_updated": 1})

    monkeypatch.setattr(fm, "rename", rename)
    asyncio.run(AIState.event_handlers["doc_confirm_rename"].fn(_FakeState()))
    assert seen and seen[0] != loop_thread


def test_a_failed_removal_in_the_database_tab_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(*args: Any, **kwargs: Any) -> fm.FileOpResult:
        return fm.FileOpResult(False, "brief.txt stays: removing it from the index failed: Chroma weg")

    monkeypatch.setattr(fm, "delete_file", refused)
    state = _FakeState()
    assert "Chroma weg" in _toasts(_run("db_deindex_document", state, "ordner/brief.txt"))
    state.db_orphans = [{"filename": "weg.txt"}]
    assert "Chroma weg" in _toasts(_run("db_delete_all_orphans", state))
