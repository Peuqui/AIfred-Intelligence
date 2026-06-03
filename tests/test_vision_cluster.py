"""Tests for the clustering core (IncrementalClusterer + cluster_events backfill)."""

from __future__ import annotations

from datetime import datetime, timedelta

from aifred.lib.vision_cluster import IncrementalClusterer, cluster_events

_T0 = datetime(2026, 6, 3, 9, 0, 0)
_BASE = 0xF0F0F0F0F0F0F0F0


class TestIncrementalClusterer:
    def test_continuous_motion_same_cluster(self):
        # Events within the gap → one ongoing occurrence.
        c = IncrementalClusterer(gap_seconds=10, max_seconds=300)
        a = c.assign("cam/x", _T0, _BASE)
        b = c.assign("cam/x", _T0 + timedelta(seconds=8), _BASE)
        assert a and a == b

    def test_gap_exceeded_new_cluster(self):
        # A pause longer than the gap → the next event is a new occurrence.
        c = IncrementalClusterer(gap_seconds=10, max_seconds=300)
        a = c.assign("cam/x", _T0, _BASE)
        b = c.assign("cam/x", _T0 + timedelta(seconds=15), _BASE)
        assert a != b

    def test_phash_change_within_gap_stays_same_cluster(self):
        # A person moving close to the cam changes the whole-frame pHash a lot
        # — within the gap it must NOT split (gap-based, no pHash split).
        c = IncrementalClusterer(gap_seconds=10, max_seconds=300)
        a = c.assign("cam/x", _T0, _BASE)
        b = c.assign("cam/x", _T0 + timedelta(seconds=5), _BASE ^ 0xFFFF)  # very different
        assert a == b

    def test_max_duration_cap_forces_new_cluster(self):
        # Continuous motion (always within the gap) but past the hard cap →
        # a new cluster opens so an endless cluster can't form.
        c = IncrementalClusterer(gap_seconds=10, max_seconds=300)
        a = c.assign("cam/x", _T0, _BASE)
        last = a
        for i in range(1, 31):  # +10s steps up to +300s → still same
            last = c.assign("cam/x", _T0 + timedelta(seconds=10 * i), _BASE)
        assert last == a
        b = c.assign("cam/x", _T0 + timedelta(seconds=310), _BASE)  # age > 300
        assert b != a

    def test_phash_zero_is_solo(self):
        c = IncrementalClusterer()
        assert c.assign("cam/x", _T0, 0) == ""

    def test_sources_are_independent(self):
        c = IncrementalClusterer(gap_seconds=10, max_seconds=300)
        a = c.assign("cam/a", _T0, _BASE)
        b = c.assign("cam/b", _T0, _BASE)
        assert a != b  # same hash+time, different source → different cluster id

    def test_deterministic_id_scheme(self):
        c = IncrementalClusterer()
        cid = c.assign("cam/v4l2_0", _T0, _BASE)
        # {slug}-{start-ts}-{hash-prefix}; slug replaces '/'
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
