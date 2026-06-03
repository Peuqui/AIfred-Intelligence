"""Tests for the clustering core (IncrementalClusterer + cluster_events backfill)."""

from __future__ import annotations

from datetime import datetime, timedelta

from aifred.lib.vision_cluster import IncrementalClusterer, cluster_events

_T0 = datetime(2026, 6, 3, 9, 0, 0)
_BASE = 0xF0F0F0F0F0F0F0F0


class TestIncrementalClusterer:
    def test_similar_close_frames_same_cluster(self):
        c = IncrementalClusterer(threshold=5, bucket_seconds=300)
        a = c.assign("cam/x", _T0, _BASE)
        b = c.assign("cam/x", _T0 + timedelta(seconds=10), _BASE ^ 0b1)  # dist 1
        assert a and a == b

    def test_dissimilar_frame_new_cluster(self):
        c = IncrementalClusterer(threshold=5, bucket_seconds=300)
        a = c.assign("cam/x", _T0, _BASE)
        b = c.assign("cam/x", _T0 + timedelta(seconds=10), _BASE ^ 0xFF)  # dist 8 > 5
        assert a != b

    def test_time_bucket_exceeded_new_cluster(self):
        c = IncrementalClusterer(threshold=5, bucket_seconds=300)
        a = c.assign("cam/x", _T0, _BASE)
        # identical phash but 400s later → bucket cap forces a new cluster
        b = c.assign("cam/x", _T0 + timedelta(seconds=400), _BASE)
        assert a != b

    def test_phash_zero_is_solo(self):
        c = IncrementalClusterer()
        assert c.assign("cam/x", _T0, 0) == ""

    def test_sources_are_independent(self):
        c = IncrementalClusterer(threshold=5, bucket_seconds=300)
        a = c.assign("cam/a", _T0, _BASE)
        b = c.assign("cam/b", _T0, _BASE)
        assert a != b  # same hash+time, different source → different cluster id

    def test_deterministic_id_scheme(self):
        c = IncrementalClusterer()
        cid = c.assign("cam/v4l2_0", _T0, _BASE)
        # {slug}-{bucket}-{hash-prefix}; slug replaces '/'
        assert cid.startswith("cam_v4l2_0-")
        assert cid.endswith(f"{_BASE & 0xFFFF:04x}")


class TestClusterEventsBackfill:
    def test_existing_cluster_id_preserved(self):
        # Event already live-clustered → kept verbatim, no disk read needed.
        events = [{"id": 1, "source_id": "cam/x", "timestamp": _T0.isoformat(),
                   "frame_path": "/does/not/exist.jpg", "cluster_id": "live-c1"}]
        assert cluster_events(events) == {1: "live-c1"}

    def test_missing_frame_without_cluster_is_solo(self):
        events = [{"id": 2, "source_id": "cam/x", "timestamp": _T0.isoformat(),
                   "frame_path": "/does/not/exist.jpg", "cluster_id": ""}]
        assert cluster_events(events) == {2: ""}
