"""Tests für aifred.lib.vision_filters.face_recognize — Cosine-Match.

InsightFace selbst wird nicht geladen; wir testen mit synthetischen
512-dim Embeddings, deren Cosine-Similarity wir gezielt steuern können.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from aifred.lib.vision_filters.face_recognize import FaceRecognizer
from aifred.lib.vision_store import VisionStore


@pytest.fixture()
def store(tmp_path: Path) -> VisionStore:
    return VisionStore(tmp_path / "vision_faces.db")


def _unit_vector(seed: int, dim: int = 512) -> np.ndarray:
    """Reproducible unit vector for tests."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _vector_with_similarity(
    reference: np.ndarray, target_sim: float, seed: int
) -> np.ndarray:
    """Unit vector with controlled cosine-similarity to ``reference``.

    Construction: ``v = sim * ref + sqrt(1 - sim²) * perp`` where ``perp``
    is a random unit vector perpendicular to ``ref``. By construction
    ``cos(v, ref) == target_sim`` (up to float precision).
    """
    rng = np.random.default_rng(seed)
    dim = reference.shape[0]
    perp = rng.standard_normal(dim).astype(np.float32)
    perp -= float(np.dot(perp, reference)) * reference
    perp /= np.linalg.norm(perp)
    v = target_sim * reference + np.sqrt(max(0.0, 1.0 - target_sim**2)) * perp
    return v / np.linalg.norm(v)


class TestEmptyStore:
    def test_unknown_when_no_faces(self, store: VisionStore):
        rec = FaceRecognizer(store)
        result = rec.match(_unit_vector(0))
        assert result.confidence_band == "unknown"
        assert result.face_id == 0
        assert rec.size() == 0


class TestThresholdValidation:
    def test_unsure_above_known_raises(self, store: VisionStore):
        with pytest.raises(ValueError):
            FaceRecognizer(store, threshold_known=0.3, threshold_unsure=0.5)


class TestSinglePersonSingleEmbedding:
    def test_exact_match_is_known(self, store: VisionStore):
        emb = _unit_vector(1)
        fid = store.add_face("Alice")
        store.add_embedding(fid, emb)
        rec = FaceRecognizer(store)

        result = rec.match(emb)
        assert result.confidence_band == "known"
        assert result.name == "Alice"
        assert result.face_id == fid
        assert result.similarity > 0.999

    def test_close_match_above_unsure_threshold(self, store: VisionStore):
        registered = _unit_vector(1)
        query = _vector_with_similarity(registered, target_sim=0.7, seed=11)
        fid = store.add_face("Alice")
        store.add_embedding(fid, registered)
        rec = FaceRecognizer(store, threshold_known=0.5, threshold_unsure=0.4)
        result = rec.match(query)
        assert result.confidence_band == "known"
        assert result.name == "Alice"
        assert result.similarity == pytest.approx(0.7, abs=0.01)

    def test_orthogonal_query_is_unknown(self, store: VisionStore):
        registered = _unit_vector(1)
        query = _unit_vector(2)  # nearly orthogonal in high-dim
        fid = store.add_face("Alice")
        store.add_embedding(fid, registered)
        rec = FaceRecognizer(store, threshold_known=0.5, threshold_unsure=0.4)
        result = rec.match(query)
        assert result.confidence_band == "unknown"

    def test_borderline_match_is_unsure(self, store: VisionStore):
        registered = _unit_vector(1)
        # Target similarity 0.45 — in the unsure band [0.4, 0.5)
        query = _vector_with_similarity(registered, target_sim=0.45, seed=22)
        store.add_embedding(store.add_face("Alice"), registered)
        rec = FaceRecognizer(store, threshold_known=0.5, threshold_unsure=0.4)
        result = rec.match(query)
        assert result.confidence_band == "unsure"
        assert 0.4 <= result.similarity < 0.5


class TestMultiPersonMaxPooling:
    def test_chooses_best_matching_person(self, store: VisionStore):
        alice_emb = _unit_vector(1)
        bob_emb = _unit_vector(2)
        fid_a = store.add_face("Alice")
        fid_b = store.add_face("Bob")
        store.add_embedding(fid_a, alice_emb)
        store.add_embedding(fid_b, bob_emb)
        rec = FaceRecognizer(store)

        # Query identical to Alice
        result = rec.match(alice_emb)
        assert result.name == "Alice"
        assert result.face_id == fid_a

    def test_multi_embeddings_per_person_uses_best(self, store: VisionStore):
        alice_emb_1 = _unit_vector(1)
        alice_emb_2 = _unit_vector(99)  # very different angle (different seed)
        fid = store.add_face("Alice")
        store.add_embedding(fid, alice_emb_1)
        store.add_embedding(fid, alice_emb_2)
        rec = FaceRecognizer(store)

        # Query identical to embedding #2 — max-pooling should still match Alice
        result = rec.match(alice_emb_2)
        assert result.confidence_band == "known"
        assert result.name == "Alice"


class TestCacheReload:
    def test_invalidate_picks_up_new_face(self, store: VisionStore):
        rec = FaceRecognizer(store)
        emb = _unit_vector(1)
        # Initially: no faces
        assert rec.match(emb).confidence_band == "unknown"

        # Enroll without telling the recognizer
        store.add_embedding(store.add_face("Late"), emb)

        # Stale cache — still unknown
        # (size() triggers lazy reload, so we test match() directly first
        # against the still-stale state)
        # Force reload
        rec.invalidate()
        result = rec.match(emb)
        assert result.confidence_band == "known"
        assert result.name == "Late"

    def test_size_reflects_store(self, store: VisionStore):
        fid = store.add_face("X")
        store.add_embedding(fid, _unit_vector(1))
        store.add_embedding(fid, _unit_vector(2))
        rec = FaceRecognizer(store)
        assert rec.size() == 2

    def test_zero_query_is_unknown(self, store: VisionStore):
        store.add_embedding(store.add_face("Alice"), _unit_vector(1))
        rec = FaceRecognizer(store)
        zero = np.zeros(512, dtype=np.float32)
        result = rec.match(zero)
        assert result.confidence_band == "unknown"
