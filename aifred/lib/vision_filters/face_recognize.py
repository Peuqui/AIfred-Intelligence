"""Face-Recognition — Cosine-Match registrierter Embeddings aus ``vision_store``.

InsightFace liefert L2-normalisierte Embeddings → Cosine-Similarity ist
dann einfach das Dot-Product, bulk-vektorisiert mit numpy. Pro Person
können mehrere Embeddings registriert sein (verschiedene Winkel,
Beleuchtung) — Max-Pooling: die höchste Similarity *irgendeines*
Embeddings dieser Person zählt.

Konservative Defaults:

* ``threshold_known = 0.5`` — sicheres Match
* ``threshold_unsure = 0.4`` — ambiguous Band; Caller entscheidet
  (Türsteher-Default: Unsicherheit → Pfad „unbekannt", siehe alte
  Diskussions-Session vom 2026-05-24)

Cache: Embeddings werden im Speicher gehalten, ``reload()`` muss nach
Enrollment-Operationen aufgerufen werden (oder ``invalidate()`` setzt
das Dirty-Bit für lazy reload).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Literal

import numpy as np

from ..vision_store import VisionStore

logger = logging.getLogger(__name__)

ConfidenceBand = Literal["known", "unsure", "unknown"]


@dataclass(frozen=True)
class FaceMatch:
    """Ergebnis eines Match-Lookups.

    Bei ``unknown`` sind ``face_id``/``name`` leer; bei ``unsure`` ist
    der beste Kandidat angegeben, aber die Confidence ist zu niedrig
    für eine endgültige Aussage.
    """

    face_id: int
    name: str
    similarity: float          # 0.0 - 1.0 (Cosine, L2-normalized)
    confidence_band: ConfidenceBand


_UNKNOWN = FaceMatch(face_id=0, name="", similarity=0.0, confidence_band="unknown")

# ── Prozessweite Enrollment-Epoche ────────────────────────────────
# ``FaceRecognizer(store).invalidate()`` auf einer frisch gebauten Instanz
# invalidiert nur deren (leeren) Cache — die lang lebenden Recognizer
# (z.B. im VisionWatcher) sehen davon nichts. Stattdessen: Enroll-Pfade
# rufen ``bump_enrollment_epoch()``; jede Instanz vergleicht ihre Epoche
# beim nächsten ``match()``/``size()`` und lädt bei Abweichung neu.
_epoch_lock = Lock()
_enrollment_epoch = 0


def bump_enrollment_epoch() -> None:
    """Signalisiert allen Recognizer-Instanzen im Prozess einen geänderten
    Enrollment-Bestand (lazy reload beim nächsten Match)."""
    global _enrollment_epoch
    with _epoch_lock:
        _enrollment_epoch += 1


class FaceRecognizer:
    """Match-Pipeline gegen die in ``vision_store`` registrierten Personen.

    Eine Instanz pro Process reicht. Bei mehreren Workern muss jeder
    seinen eigenen Recognizer mit eigenem Cache halten — die DB ist
    Single-Writer ohnehin.
    """

    def __init__(
        self,
        store: VisionStore,
        *,
        threshold_known: float = 0.6,
        threshold_unsure: float = 0.5,
    ) -> None:
        if threshold_unsure > threshold_known:
            raise ValueError(
                f"threshold_unsure ({threshold_unsure}) must be <= threshold_known "
                f"({threshold_known})"
            )
        self._store = store
        self._t_known = float(threshold_known)
        self._t_unsure = float(threshold_unsure)
        self._lock = Lock()
        self._dirty = True
        self._epoch = -1  # erzwingt Erst-Load; siehe bump_enrollment_epoch()
        # Bulk-Matrix: face_ids[i], names[i], embeddings[i] (shape (N, 512))
        self._face_ids: list[int] = []
        self._names: list[str] = []
        self._embeddings: np.ndarray = np.zeros((0, 0), dtype=np.float32)

    # ── Cache-Management ──────────────────────────────────────────

    def invalidate(self) -> None:
        """Markiere Cache als dirty — nächster ``match()`` lädt neu."""
        with self._lock:
            self._dirty = True

    def reload(self) -> None:
        """Force-reload aus dem Store. Sollte nach Enrollment-Ops aufgerufen
        werden (alternativ ``invalidate()`` für lazy reload)."""
        with self._lock:
            self._reload_locked()

    def _needs_reload_locked(self) -> bool:
        return self._dirty or self._epoch != _enrollment_epoch

    def _reload_locked(self) -> None:
        self._epoch = _enrollment_epoch
        rows = self._store.all_embeddings_with_face()
        if not rows:
            self._face_ids = []
            self._names = []
            self._embeddings = np.zeros((0, 0), dtype=np.float32)
            self._dirty = False
            return
        face_ids: list[int] = []
        names: list[str] = []
        embs: list[np.ndarray] = []
        for face_id, name, emb in rows:
            face_ids.append(face_id)
            names.append(name)
            embs.append(emb.astype(np.float32, copy=False).reshape(-1))
        stacked = np.vstack(embs)
        # Re-normalize defensively (InsightFace gives normed embeddings, but
        # storage roundtrip might add float drift)
        norms = np.linalg.norm(stacked, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        stacked = stacked / norms
        self._face_ids = face_ids
        self._names = names
        self._embeddings = stacked.astype(np.float32, copy=False)
        self._dirty = False

    # ── Match ─────────────────────────────────────────────────────

    def match(self, embedding: np.ndarray) -> FaceMatch:
        """Match a single 512-dim embedding against all registered persons.

        Bei leerer Embedding-DB: returns ``_UNKNOWN`` (Cold-Start verhält
        sich konsistent mit „niemand kennt das Gesicht"). Sonst:
        Max-Similarity-Pooling pro Person, dann Argmax über Personen.
        """
        with self._lock:
            if self._needs_reload_locked():
                self._reload_locked()
            if self._embeddings.shape[0] == 0:
                return _UNKNOWN

            # Normalize query embedding (defensive)
            q_raw = embedding.astype(np.float32, copy=False).reshape(-1)
            norm = float(np.linalg.norm(q_raw))
            if norm == 0.0:
                return _UNKNOWN
            q = (q_raw / norm).astype(np.float32, copy=False).reshape(-1)

            # Cosine = dot product on L2-normalized vectors
            sims = self._embeddings @ q  # shape (N,)

            # Max-pooling per face_id: take the *best* embedding for each person
            best_by_face: dict[int, tuple[float, str]] = {}
            for fid, name, sim in zip(self._face_ids, self._names, sims.tolist()):
                cur = best_by_face.get(fid)
                if cur is None or sim > cur[0]:
                    best_by_face[fid] = (float(sim), name)

            top_fid, (top_sim, top_name) = max(
                best_by_face.items(), key=lambda kv: kv[1][0]
            )

        if top_sim >= self._t_known:
            band: ConfidenceBand = "known"
        elif top_sim >= self._t_unsure:
            band = "unsure"
        else:
            return _UNKNOWN
        return FaceMatch(
            face_id=top_fid, name=top_name, similarity=top_sim, confidence_band=band
        )

    # ── Diagnostik ────────────────────────────────────────────────

    def size(self) -> int:
        """Anzahl Embeddings im aktuellen Cache (nach evtl. Lazy-Reload)."""
        with self._lock:
            if self._needs_reload_locked():
                self._reload_locked()
            return int(self._embeddings.shape[0])
