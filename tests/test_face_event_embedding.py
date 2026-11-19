"""Das Embedding eines Gesichts-Ereignisses: beim Erkennen gespeichert, vom Personarium zum Zuordnen und Neu-Erkennen
genutzt, mit dem Ereignis gelöscht. Die Einzelbilder eines Bursts bleiben nicht erhalten, deshalb gibt es in diesen Tests
keine einzige Bilddatei. Das Erkennungsmodell ist ein Stellvertreter."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from aifred.lib import face_enroll, personarium, vision_cleanup, vision_store
from aifred.lib.face_crop_store import CropResult
from aifred.lib.user_error import Invalid
from aifred.lib.vision_filters.face_detect import FaceDetection
from aifred.lib.vision_filters.face_recognize import FaceMatch
from aifred.lib.vision_recorder import EventRecorder
from aifred.lib.vision_store import VisionStore
from aifred.lib.vision_watch_types import WatchStatus

SOURCE = "cam/door"
START = datetime(2026, 11, 19, 9, 0, 0)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VisionStore:
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    monkeypatch.setattr(personarium, "_rematch", personarium.Rematch())
    return VisionStore()


def _embedding(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(512).astype(np.float32)


def _face_event(store: VisionStore, seed: int | None, *, minute: int = 0, crop: str = "a.jpg") -> int:
    return store.add_event(
        SOURCE, "face_unknown", timestamp=START + timedelta(minutes=minute),
        classification={"crop_url": f"/_upload/face_crops/{crop}", "detection_score": 0.8},
        embedding=None if seed is None else _embedding(seed),
    )


def test_a_recorded_face_event_keeps_the_embedding_of_its_detection(store: VisionStore) -> None:
    recorder = EventRecorder(store, {SOURCE: WatchStatus(
        source_id=SOURCE, running=True, started_at=START, fps=5.0, frames_seen=0,
        motion_events=0, face_events=0, last_event_at=None)})
    det = FaceDetection(bbox=(1, 2, 3, 4), embedding=_embedding(1), detection_score=0.9, keypoints=None)
    match = FaceMatch(face_id=0, name="", similarity=0.1, confidence_band="unknown")
    crop = CropResult(url="/_upload/face_crops/x.jpg", abs_path=Path("x.jpg"), identity_key="k", band="unknown", started_at=START)
    event_id = recorder.record_face(
        source_id=SOURCE, frame_timestamp=START, det=det, match=match, crop=crop,
        frame_path="", zoom_frame_path="", trigger="burst", cluster_id="c",
    )
    stored = store.get_event_embedding(event_id)
    assert stored is not None and np.array_equal(stored, det.embedding)
    event = store.get_event(event_id)
    assert event is not None
    assert "embedding" not in event["classification"] and "embedding" not in event["metadata"]


def test_assigning_works_without_any_picture_and_attaches_the_stored_embedding(store: VisionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    event_id = _face_event(store, 2)
    monkeypatch.setattr(face_enroll, "_shared_recognizer", lambda: SimpleNamespace(match=lambda _e: SimpleNamespace(confidence_band="unknown")))
    result = face_enroll.enroll_face_from_event(event_id, name="Berta")
    assert result["is_new"] and not result["embedding_skipped"] and result["quality"] == 0.8
    (learned,) = store.list_embeddings(result["face_id"])
    assert np.array_equal(learned["embedding"], _embedding(2))
    assert learned["crop_url"].endswith("a.jpg")
    event = store.get_event(event_id)
    assert event is not None and event["face_id"] == result["face_id"]


def test_an_event_without_embedding_is_refused_with_its_own_key(store: VisionStore) -> None:
    event_id = _face_event(store, None)
    with pytest.raises(Invalid) as refused:
        face_enroll.enroll_face_from_event(event_id, name="Berta")
    assert (refused.value.key, refused.value.values) == ("face_embedding_missing", {"event": event_id})
    assert store.get_face_by_name("Berta") is None      # nothing half-done


def test_recognising_again_assigns_an_open_event_by_its_stored_embedding(store: VisionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    anna = store.add_face("Anna")
    store.add_embedding(anna, _embedding(3))
    known = _face_event(store, 3, minute=0, crop="k.jpg")
    other = _face_event(store, 4, minute=1, crop="o.jpg")
    seen: list[bytes] = []

    class Recognizer:
        def match(self, embedding: np.ndarray) -> SimpleNamespace:
            seen.append(embedding.tobytes())
            if np.array_equal(embedding, _embedding(3)):
                return SimpleNamespace(confidence_band="known", face_id=anna, name="Anna")
            return SimpleNamespace(confidence_band="unknown", face_id=0, name="")

    monkeypatch.setattr(face_enroll, "_shared_recognizer", Recognizer)
    result = face_enroll.rematch_untagged_faces()
    assert result == {"checked": 2, "resolved": 1, "by_name": {"Anna": 1}}
    assert sorted(seen) == sorted([_embedding(3).tobytes(), _embedding(4).tobytes()])
    resolved, untouched = store.get_event(known), store.get_event(other)
    assert resolved is not None and untouched is not None
    assert resolved["face_id"] == anna and untouched["face_id"] is None


def test_every_way_to_delete_an_event_takes_its_embedding_along(store: VisionStore) -> None:
    old = START - timedelta(days=100)
    single, filtered, expired, kept = (
        store.add_event(SOURCE, kind, timestamp=when, embedding=_embedding(seed))
        for seed, (kind, when) in enumerate([
            ("face_unknown", START), ("face_known", START), ("face_unknown", old), ("face_unknown", START)])
    )
    assert store.delete_event(single)
    assert store.delete_events_filtered(event_types=["face_known"]) == 1
    assert vision_cleanup._cleanup_vision_db_events(START - timedelta(days=30)) == 1
    for gone in (single, filtered, expired):
        assert store.get_event_embedding(gone) is None
    assert store.get_event_embedding(kept) is not None
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_embeddings").fetchone()[0] == 1
