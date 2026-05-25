"""Tests für aifred.lib.vision_store — SQLite-Schema und CRUD."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from aifred.lib.vision_store import VisionStore, SCHEMA_VERSION


@pytest.fixture()
def store(tmp_path: Path) -> VisionStore:
    return VisionStore(tmp_path / "vision.db")


class TestSchema:
    def test_init_creates_db_and_sets_version(self, store: VisionStore):
        assert store.db_path.exists()
        assert store.schema_version() == SCHEMA_VERSION

    def test_idempotent_init(self, tmp_path: Path):
        VisionStore(tmp_path / "v.db")
        VisionStore(tmp_path / "v.db")  # second open must not fail


class TestSources:
    def test_upsert_and_get(self, store: VisionStore):
        store.upsert_source(
            "cam/test",
            "Test Cam",
            "webcam",
            prompt_context="Eingangsbereich",
            position="Haustür",
            auto_start=True,
            settings={"sensitivity": "high"},
        )
        s = store.get_source("cam/test")
        assert s is not None
        assert s["display_name"] == "Test Cam"
        assert s["auto_start"] is True
        assert s["prompt_context"] == "Eingangsbereich"
        assert s["settings"] == {"sensitivity": "high"}

    def test_upsert_updates_existing(self, store: VisionStore):
        store.upsert_source("cam/x", "v1", "webcam")
        store.upsert_source("cam/x", "v2", "webcam", prompt_context="zone-1")
        s = store.get_source("cam/x")
        assert s is not None
        assert s["display_name"] == "v2"
        assert s["prompt_context"] == "zone-1"

    def test_list_sources_ordered(self, store: VisionStore):
        store.upsert_source("cam/b", "B", "webcam")
        store.upsert_source("cam/a", "A", "webcam")
        ids = [s["source_id"] for s in store.list_sources()]
        assert ids == ["cam/a", "cam/b"]

    def test_delete_source(self, store: VisionStore):
        store.upsert_source("cam/x", "X", "webcam")
        assert store.delete_source("cam/x") is True
        assert store.get_source("cam/x") is None
        assert store.delete_source("cam/x") is False  # second time: no-op


class TestFaces:
    def test_add_and_get(self, store: VisionStore):
        fid = store.add_face("Maria", notes="Schwester", enrolled_by="user")
        face = store.get_face_by_id(fid)
        assert face is not None
        assert face["name"] == "Maria"
        assert face["notes"] == "Schwester"

    def test_unique_name(self, store: VisionStore):
        store.add_face("Bob")
        with pytest.raises(Exception):
            store.add_face("Bob")  # UNIQUE violation

    def test_get_by_name(self, store: VisionStore):
        store.add_face("Charlie")
        face = store.get_face_by_name("Charlie")
        assert face is not None
        assert face["name"] == "Charlie"
        assert store.get_face_by_name("Nobody") is None

    def test_list_faces_ordered_by_name(self, store: VisionStore):
        store.add_face("Bob")
        store.add_face("Alice")
        names = [f["name"] for f in store.list_faces()]
        assert names == ["Alice", "Bob"]

    def test_delete_cascades_to_embeddings(self, store: VisionStore):
        fid = store.add_face("Dora")
        store.add_embedding(fid, np.ones(512, dtype=np.float32))
        store.add_embedding(fid, np.zeros(512, dtype=np.float32))
        assert len(store.list_embeddings(fid)) == 2
        store.delete_face(fid)
        # Embeddings should be cascade-deleted
        assert len(store.list_embeddings(fid)) == 0


class TestEmbeddings:
    def test_add_and_roundtrip(self, store: VisionStore):
        fid = store.add_face("Eve")
        original = np.random.rand(512).astype(np.float32)
        eid = store.add_embedding(fid, original, quality_score=0.9)
        assert eid > 0
        rows = store.list_embeddings(fid)
        assert len(rows) == 1
        recovered = rows[0]["embedding"]
        np.testing.assert_array_almost_equal(recovered, original)
        assert rows[0]["quality_score"] == pytest.approx(0.9)

    def test_all_embeddings_with_face(self, store: VisionStore):
        f1 = store.add_face("Frank")
        f2 = store.add_face("Grace")
        store.add_embedding(f1, np.ones(512, dtype=np.float32))
        store.add_embedding(f1, np.full(512, 2.0, dtype=np.float32))
        store.add_embedding(f2, np.zeros(512, dtype=np.float32))
        all_emb = store.all_embeddings_with_face()
        assert len(all_emb) == 3
        names = {name for _, name, _ in all_emb}
        assert names == {"Frank", "Grace"}


class TestEvents:
    def test_add_and_query(self, store: VisionStore):
        eid = store.add_event(
            "cam/a",
            "motion",
            classification={"area_pct": 0.12},
            confidence=0.8,
            frame_path="/tmp/abc.jpg",
        )
        assert eid > 0
        events = store.query_events(source_id="cam/a")
        assert len(events) == 1
        assert events[0]["event_type"] == "motion"
        assert events[0]["classification"] == {"area_pct": 0.12}

    def test_filter_by_event_type(self, store: VisionStore):
        store.add_event("cam/a", "motion")
        store.add_event("cam/a", "face_known")
        store.add_event("cam/a", "face_unknown")
        only_known = store.query_events(event_type="face_known")
        assert len(only_known) == 1
        assert only_known[0]["event_type"] == "face_known"

    def test_filter_by_face_id(self, store: VisionStore):
        fid = store.add_face("Hank")
        store.add_event("cam/a", "face_known", face_id=fid)
        store.add_event("cam/a", "motion")
        only_face = store.query_events(face_id=fid)
        assert len(only_face) == 1
        assert only_face[0]["face_id"] == fid

    def test_query_time_window(self, store: VisionStore):
        now = datetime.now()
        store.add_event("cam/a", "motion", timestamp=now - timedelta(hours=2))
        store.add_event("cam/a", "motion", timestamp=now - timedelta(minutes=10))
        store.add_event("cam/a", "motion", timestamp=now)
        recent = store.query_events(since=now - timedelta(hours=1))
        assert len(recent) == 2

    def test_query_limit_and_order(self, store: VisionStore):
        now = datetime.now()
        for i in range(5):
            store.add_event(
                "cam/a", "motion", timestamp=now - timedelta(minutes=i)
            )
        latest = store.query_events(limit=2)
        # ordered DESC by timestamp → newest first
        assert len(latest) == 2
        ts0 = datetime.fromisoformat(latest[0]["timestamp"])
        ts1 = datetime.fromisoformat(latest[1]["timestamp"])
        assert ts0 > ts1

    def test_prune_events(self, store: VisionStore):
        now = datetime.now()
        store.add_event("cam/a", "motion", timestamp=now - timedelta(days=10))
        store.add_event("cam/a", "motion", timestamp=now - timedelta(days=5))
        store.add_event("cam/a", "motion", timestamp=now)
        deleted = store.prune_events(now - timedelta(days=7))
        assert deleted == 1
        remaining = store.query_events()
        assert len(remaining) == 2

    def test_face_delete_nulls_event_face_id(self, store: VisionStore):
        fid = store.add_face("Ivy")
        store.add_event("cam/a", "face_known", face_id=fid)
        store.delete_face(fid)
        events = store.query_events()
        assert len(events) == 1
        assert events[0]["face_id"] is None  # ON DELETE SET NULL
