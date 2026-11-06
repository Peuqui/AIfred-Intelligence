"""The document search: every chunk, by meaning and by words.

Kontrakt (lib/document_search_index.py, DocumentStore.search):
- Jede Frage wird mit jedem Abschnitt verglichen, nichts wird aus Mengengründen vorausgewählt: Ein
  einzelnes Dokument unter Tausenden wird gefunden, und jeder Treffer ist seitenweise erreichbar.
- Wortsuche und Vektorsuche laufen zusammen. Ein Wort findet auch, was der Bedeutung nach weit weg
  liegt, ohne Rücksicht auf Groß- und Kleinschreibung, Diakritika und Wortgrenzen.
- ``without_corpora`` lässt die eigenen Bestände (Bibel, Judaica) aus, wenn kein Ordner genannt ist:
  So fragt ``search_documents``. Mit Ordnerangabe sind sie da, die Bibliothek sucht weiter überall.
- Eine Änderung am Bestand gilt bei der nächsten Suche, auch wenn sie von außen kam.

Ohne Netz: FakeCollection steht für ChromaDB, gesucht wird mit dem echten Index.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import numpy as np
import pytest

from aifred.lib.document_search_index import DocumentSearchIndex, _word_query
from aifred.lib.document_store import DocumentStore

DIMENSIONS = 16


def _unit(*weights: float) -> list[float]:
    vector = np.zeros(DIMENSIONS)
    vector[: len(weights)] = weights
    unit: list[float] = (vector / np.linalg.norm(vector)).tolist()
    return unit


class FakeCollection:
    def __init__(self) -> None:
        self.chunks: dict[str, tuple[str, dict[str, Any], list[float]]] = {}
        self.loads = 0

    def add(self, chunk_id: str, text: str, vector: list[float], folder: str = "", filename: str = "a.txt",
            chunk_index: int = 0, total_chunks: int = 1) -> None:
        meta = {"folder": folder, "filename": filename, "chunk_index": chunk_index, "total_chunks": total_chunks}
        self.chunks[chunk_id] = (text, meta, vector)

    def count(self) -> int:
        return len(self.chunks)

    def get(self, ids: list[str] | None = None, limit: int | None = None, offset: int = 0,
            include: list[str] | None = None, **_: Any) -> dict[str, Any]:
        if ids is None:
            self.loads += 1 if offset == 0 else 0
            chosen = list(self.chunks)[offset:None if limit is None else offset + limit]  # None: all, as in Chroma
        else:
            chosen = [i for i in ids if i in self.chunks]
        return {
            "ids": chosen,
            "documents": [self.chunks[i][0] for i in chosen],
            "metadatas": [self.chunks[i][1] for i in chosen],
            "embeddings": [self.chunks[i][2] for i in chosen],
        }


def _store(collection: FakeCollection, query_vector: list[float]) -> DocumentStore:
    store = DocumentStore.__new__(DocumentStore)
    store._collection = collection
    store._embed_query = lambda _input: [query_vector]  # type: ignore[assignment]
    store._folder_cache = None
    store._search_index = None
    store._search_index_lock = threading.Lock()
    return store


def _search(store: DocumentStore, query: str, **kwargs: Any) -> tuple[list[dict[str, Any]], bool, int]:
    return asyncio.run(store.search(query, neighbor_window=0, **kwargs))


def _names(hits: list[dict[str, Any]]) -> list[str]:
    return [hit["content"] for hit in hits]


FAR = _unit(0, 0, 0, 0, 0, 0, 0, 1)


@pytest.fixture()
def crowd() -> FakeCollection:
    """A large corpus, all close together: what buries a single document."""
    collection = FakeCollection()
    rng = np.random.default_rng(1)
    for i in range(3000):
        vector = _unit(1, *(rng.normal(size=6) * 0.05))
        collection.add(f"crowd{i}", f"Textstelle Nummer {i}", vector, folder="archiv/alt", filename=f"alt{i // 50}.txt")
    return collection


class TestEverythingIsSearched:
    def test_a_single_document_among_thousands_is_found(self, crowd: FakeCollection) -> None:
        crowd.add("lonely", "Mein einzelner Brief", _unit(0, 0, 0, 1))
        hits, _, _ = _search(_store(crowd, _unit(0, 0, 0, 1)), "ganz andere frage")
        assert _names(hits)[0] == "Mein einzelner Brief"

    def test_every_hit_is_reachable_page_by_page(self, crowd: FakeCollection) -> None:
        store = _store(crowd, _unit(1))
        seen: list[str] = []
        page = 1
        while True:
            hits, has_more, total = _search(store, "unbekannt", n_results=500, page=page)
            seen += _names(hits)
            if not has_more:
                break
            page += 1
        assert total == 3000
        assert len(seen) == 3000 and len(set(seen)) == 3000

    def test_a_page_behind_the_end_is_empty(self, crowd: FakeCollection) -> None:
        hits, has_more, total = _search(_store(crowd, _unit(1)), "unbekannt", n_results=500, page=99)
        assert hits == [] and has_more is False and total == 3000

    def test_order_is_stable_between_pages(self, crowd: FakeCollection) -> None:
        store = _store(crowd, _unit(1))
        whole = _names(_search(store, "unbekannt", n_results=20)[0])
        pages = [_names(_search(store, "unbekannt", n_results=5, page=p)[0]) for p in (1, 2, 3, 4)]
        assert [name for page in pages for name in page] == whole

    def test_off_topic_chunks_without_the_word_stay_out(self, crowd: FakeCollection) -> None:
        crowd.add("other", "Völlig fremder Inhalt", FAR)
        hits, _, total = _search(_store(crowd, _unit(1)), "unbekannt", n_results=3001)
        assert "Völlig fremder Inhalt" not in _names(hits) and total == 3000


class TestWords:
    @pytest.fixture()
    def store(self) -> DocumentStore:
        collection = FakeCollection()
        collection.add("near", "Etwas Allgemeines über Öl", _unit(1))
        collection.add("bill", "Die Heizölrechnung Nr. 4711 vom Lieferanten", FAR)
        collection.add("talmud", "R. A͑qiba sprach zu ihnen", FAR)
        collection.add("named", "Inhalt ohne das Stichwort", FAR, filename="steuerbescheid-2024.pdf")
        return _store(collection, _unit(1))

    def test_a_number_is_found_though_the_meaning_is_far_away(self, store: DocumentStore) -> None:
        assert "Die Heizölrechnung Nr. 4711 vom Lieferanten" in _names(_search(store, "Rechnung 4711")[0])

    def test_a_part_of_a_compound_is_found(self, store: DocumentStore) -> None:
        assert "Die Heizölrechnung Nr. 4711 vom Lieferanten" in _names(_search(store, "Heizöl")[0])

    def test_case_does_not_matter(self, store: DocumentStore) -> None:
        assert "Die Heizölrechnung Nr. 4711 vom Lieferanten" in _names(_search(store, "HEIZÖLRECHNUNG")[0])

    def test_marks_do_not_matter(self, store: DocumentStore) -> None:
        assert "R. A͑qiba sprach zu ihnen" in _names(_search(store, "Aqiba")[0])
        assert "R. A͑qiba sprach zu ihnen" in _names(_search(store, "A͑qiba")[0])

    def test_hebrew_is_found_with_and_without_points(self) -> None:
        collection = FakeCollection()
        collection.add("pointed", "בְּרֵאשִׁית בָּרָא אֱלֹהִים", FAR)
        collection.add("plain", "ספר תורה", FAR)
        store = _store(collection, _unit(1))
        assert _names(_search(store, "בראשית")[0]) == ["בְּרֵאשִׁית בָּרָא אֱלֹהִים"]
        assert _names(_search(store, "תּוֹרָה")[0]) == ["ספר תורה"]

    def test_an_umlaut_meets_itself(self, store: DocumentStore) -> None:
        assert "Die Heizölrechnung Nr. 4711 vom Lieferanten" in _names(_search(store, "heizöl")[0])

    def test_the_file_name_counts(self, store: DocumentStore) -> None:
        assert "Inhalt ohne das Stichwort" in _names(_search(store, "Steuerbescheid")[0])

    @pytest.mark.parametrize("query", ['Nr" OR "x', "4711*", "NOT AND OR", "a", "", '"', "(((", "col:umn"])
    def test_a_query_is_never_fts_syntax(self, store: DocumentStore, query: str) -> None:
        _search(store, query)

    def test_too_short_words_are_left_to_the_meaning(self) -> None:
        assert _word_query("in KI") == ""
        assert _word_query("Die KI") == '"Die"'

    def test_what_both_rankings_like_comes_first(self) -> None:
        collection = FakeCollection()
        collection.add("meaning", "Passt nur dem Sinn nach", _unit(1, 0.01))
        collection.add("both", "Passt mit Barmherzigkeit auch im Wort", _unit(1, 0.02))
        collection.add("word", "Barmherzigkeit steht nur hier", _unit(1, 5))
        hits, _, _ = _search(_store(collection, _unit(1)), "Barmherzigkeit", n_results=1, diversify=False)
        assert _names(hits) == ["Passt mit Barmherzigkeit auch im Wort"]


class TestScope:
    @pytest.fixture()
    def store(self) -> DocumentStore:
        collection = FakeCollection()
        collection.add("mine", "Mein Dokument", _unit(1), folder="")
        collection.add("mail", "Anhang einer Mail", _unit(1), folder="anhaenge/2026")
        collection.add("verse", "Am Anfang schuf Gott", _unit(1), folder="bibel/Schlachter")
        collection.add("tractate", "Mishna Berakhot", _unit(1), folder="judaica/talmud")
        return _store(collection, _unit(1))

    def test_the_tool_leaves_the_separate_corpora_out(self, store: DocumentStore) -> None:
        hits, _, _ = _search(store, "Anfang", without_corpora=True)
        assert set(_names(hits)) == {"Mein Dokument", "Anhang einer Mail"}

    def test_a_named_corpus_is_searched_with_its_sub_folders(self, store: DocumentStore) -> None:
        for name, content in (("bibel", "Am Anfang schuf Gott"), ("judaica", "Mishna Berakhot")):
            assert _names(_search(store, "Anfang", folder=name, without_corpora=True)[0]) == [content]

    def test_the_library_still_searches_the_whole_store(self, store: DocumentStore) -> None:
        assert _search(store, "Anfang")[2] == 4

    def test_a_named_folder_covers_only_itself(self, store: DocumentStore) -> None:
        assert _names(_search(store, "Anfang", folder="anhaenge")[0]) == ["Anhang einer Mail"]

    def test_a_folder_that_does_not_exist_finds_nothing(self, store: DocumentStore) -> None:
        assert _search(store, "Anfang", folder="gibtsnicht") == ([], False, 0)


class TestStaysCurrent:
    def test_loaded_once(self, crowd: FakeCollection) -> None:
        store = _store(crowd, _unit(1))
        _search(store, "eins")
        _search(store, "zwei")
        assert crowd.loads == 1

    def test_a_change_counts_at_the_next_search(self) -> None:
        collection = FakeCollection()
        collection.add("first", "Erster Stand", _unit(1))
        store = _store(collection, _unit(1))
        _search(store, "Stand")
        collection.add("second", "Zweiter Stand", _unit(1))
        store._invalidate_caches()
        assert "Zweiter Stand" in _names(_search(store, "Stand")[0])

    def test_a_write_from_outside_shows_in_the_count(self) -> None:
        collection = FakeCollection()
        collection.add("first", "Erster Stand", _unit(1))
        store = _store(collection, _unit(1))
        _search(store, "Stand")
        collection.add("second", "Von außen geschrieben", _unit(1))
        assert "Von außen geschrieben" in _names(_search(store, "außen")[0])

    def test_empty_collection(self) -> None:
        assert _search(_store(FakeCollection(), _unit(1)), "egal") == ([], False, 0)


class TestHits:
    def test_a_hit_carries_its_metadata_and_a_distance(self) -> None:
        collection = FakeCollection()
        collection.add("c", "Inhalt", _unit(1), folder="f", filename="x.txt", chunk_index=2, total_chunks=9)
        hit = _search(_store(collection, _unit(1)), "Inhalt")[0][0]
        assert hit["filename"] == "x.txt" and hit["folder"] == "f"
        assert hit["chunk_index"] == 2 and hit["total_chunks"] == 9
        assert hit["distance"] == pytest.approx(0.0, abs=1e-5)

    def test_distances_are_on_chromas_scale(self) -> None:
        index = DocumentSearchIndex([{
            "ids": ["a"], "documents": ["text"], "metadatas": [{"folder": "", "filename": "a"}],
            "embeddings": [_unit(1)],
        }])
        ranking = index.rank("xyz", _unit(0, 1), np.array([True]))
        assert ranking.distances[0] == pytest.approx(2.0)

    def test_neighbours_come_along(self) -> None:
        collection = FakeCollection()
        collection.add("a.txt__chunk_0", "Davor", FAR, chunk_index=0, total_chunks=3)
        collection.add("a.txt__chunk_1", "Treffer", _unit(1), chunk_index=1, total_chunks=3)
        collection.add("a.txt__chunk_2", "Danach", FAR, chunk_index=2, total_chunks=3)
        hits, _, total = asyncio.run(_store(collection, _unit(1)).search("zzz", n_results=1, neighbor_window=1))
        assert _names(hits) == ["Davor", "Treffer", "Danach"]
        assert [hit["_neighbor"] for hit in hits] == [True, False, True]
        assert total == 1
