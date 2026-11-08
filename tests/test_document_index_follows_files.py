"""Löschen, Umbenennen und Verschieben halten den Dokumenten-Index mit (lib/file_manager.py, lib/document_store.py;
das Hochladen mit Indexieren prüfen test_documents_api.py und test_document_sources_api.py).

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
from typing import Any

import chromadb
import pytest

from aifred.lib import document_store as ds
from aifred.lib import file_manager as fm


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
