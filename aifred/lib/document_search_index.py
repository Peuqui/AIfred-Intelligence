"""Exhaustive search over every indexed chunk, by meaning and by words.

ChromaDB stays the one truth for the chunks. Its graph index only approximates: it
missed chunks that were clearly the best match, above all a small document among
tens of thousands of others. This index compares a query with every chunk instead
(46.739 chunks: 10 ms, sorted). It lives in memory and is read from ChromaDB, so no
second store can drift apart from it.

Two rankings meet here. The vector distance finds what a text means, also in other
words; the word index finds what a text says, names and numbers above all, and also
inside compounds ("Heizöl" in "Heizölrechnung"), which is why it works on trigrams.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Self

import numpy as np

from .config import DOCUMENT_SEARCH_DISTANCE_MAX, DOCUMENT_SEARCH_RANK_CONSTANT
from .logging_utils import log_message
from .text_search import MIN_INDEXED_WORD_LENGTH, fts_phrase, query_words, without_marks

_LOAD_PAGE_SIZE = 2000


def _word_query(query: str) -> str:
    """The FTS5 expression for ``query``: any of its words. Empty when none is long enough."""
    return " OR ".join(fts_phrase(word) for word in query_words(query) if len(word) >= MIN_INDEXED_WORD_LENGTH)


@dataclass(frozen=True)
class Ranking:
    """Every hit of one query. ``rows``, best first, and ``scores`` run side by side;
    ``distances`` holds the distance of every chunk, by row."""

    rows: np.ndarray
    scores: np.ndarray
    distances: np.ndarray


class DocumentSearchIndex:
    """All chunks at the time of loading. Immutable: a change means a new load."""

    def __init__(self, pages: Iterable[dict[str, Any]]) -> None:
        """``pages`` as ChromaDB returns them, one after the other: the texts only pass
        through into the word index, they never lie in memory all at once."""
        self.ids: list[str] = []
        folders: list[str] = []
        batches: list[np.ndarray] = []
        # Reads come from worker threads; nothing writes after this constructor.
        self._words = sqlite3.connect(":memory:", check_same_thread=False)
        self._words.execute(
            "CREATE VIRTUAL TABLE chunks USING fts5(filename, content, content='', tokenize='trigram')"
        )
        with self._words:
            for page in pages:
                first_row = len(self.ids) + 1
                self.ids += page["ids"]
                folders += [str(meta.get("folder", "")) for meta in page["metadatas"]]
                batches.append(np.asarray(page["embeddings"], dtype=np.float32))
                self._words.executemany(
                    "INSERT INTO chunks(rowid, filename, content) VALUES (?, ?, ?)",
                    (
                        (row, without_marks(str(meta.get("filename", ""))), without_marks(text))
                        for row, (meta, text) in enumerate(
                            zip(page["metadatas"], page["documents"], strict=True), start=first_row
                        )
                    ),
                )
        self._folders = np.asarray(folders, dtype=str)
        self._vectors = np.concatenate(batches)
        self._squared_norms = (self._vectors * self._vectors).sum(axis=1)

    @classmethod
    def load(cls, collection: Any) -> Self:
        """Read every chunk from the ChromaDB ``collection``."""
        total = collection.count()
        index = cls(
            collection.get(limit=_LOAD_PAGE_SIZE, offset=offset, include=["embeddings", "documents", "metadatas"])
            for offset in range(0, total, _LOAD_PAGE_SIZE)
        )
        log_message(f"📄 Document search index loaded: {len(index)} chunks")
        return index

    def __len__(self) -> int:
        return len(self.ids)

    def folder_mask(self, folders: Iterable[str]) -> np.ndarray:
        """Which chunks lie in one of ``folders``."""
        chosen: np.ndarray = np.isin(self._folders, np.asarray(list(folders), dtype=str))
        return chosen

    def vectors(self, rows: np.ndarray) -> np.ndarray:
        chosen: np.ndarray = self._vectors[rows]
        return chosen

    def _word_rows(self, query: str) -> np.ndarray:
        """Rows of the chunks holding a word of ``query``, best BM25 match first."""
        expression = _word_query(query)
        if not expression:
            return np.empty(0, dtype=np.int64)
        found = self._words.execute(
            "SELECT rowid FROM chunks WHERE chunks MATCH ? ORDER BY rank", (expression,)
        ).fetchall()
        return np.fromiter((rowid - 1 for rowid, in found), dtype=np.int64, count=len(found))

    def rank(self, query: str, query_embedding: Any, in_scope: np.ndarray) -> Ranking:
        """Every chunk in scope that is close in meaning or holds a word of ``query``.

        The two rankings are merged by rank (reciprocal rank fusion): a distance and
        a BM25 score are not on one scale, a place in a list is. What both lists rate
        highly comes first, and a best match in only one of them stays near the top.
        """
        embedding = np.asarray(query_embedding, dtype=np.float32)
        # Squared L2 as ChromaDB reports it, so the configured thresholds keep their scale.
        distances = self._squared_norms + float(embedding @ embedding) - 2.0 * (self._vectors @ embedding)
        close = np.flatnonzero(in_scope & (distances <= DOCUMENT_SEARCH_DISTANCE_MAX))
        by_distance = close[np.argsort(distances[close], kind="stable")]
        word_rows = self._word_rows(query)
        by_words = word_rows[in_scope[word_rows]]

        fused = np.zeros(len(self.ids))
        for ranked in (by_distance, by_words):
            fused[ranked] += 1.0 / (DOCUMENT_SEARCH_RANK_CONSTANT + np.arange(1, len(ranked) + 1))
        hits = np.flatnonzero(fused)
        rows = hits[np.argsort(-fused[hits], kind="stable")]
        return Ranking(rows=rows, scores=fused[rows], distances=distances)
