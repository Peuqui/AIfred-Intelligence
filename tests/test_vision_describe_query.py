"""Regression tests for the describe/query path:

* ``VisionStore.get_event`` — direct primary-key lookup that must find ANY
  event, not just the most recent ones (the bug that left every event beyond
  the most-recent-1000 forever undescribed).
* ``VisionStore.list_event_ids`` — full ordered id list driving the
  cross-page Casus slideshow.
* ``query_events(limit=None)`` — no artificial cap.
* ``vision_query_events`` dedup — one row per cluster (frames collapsed),
  with an image_url, plus per-happening solo events.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aifred.lib.vision_store import VisionStore
from aifred.lib.vision_utils import VIGILANTIA_DIR

_BASE = datetime(2026, 6, 3, 9, 0, 0)


@pytest.fixture()
def store(tmp_path: Path) -> VisionStore:
    return VisionStore(tmp_path / "vision.db")


def _add(store: VisionStore, seconds: int, *, frame: str = "", cls=None) -> int:
    """Add a motion event `seconds` after the base time, return its id."""
    return store.add_event(
        "cam/test",
        "motion",
        timestamp=_BASE + timedelta(seconds=seconds),
        frame_path=frame,
        classification=cls or {},
    )


class TestGetEvent:
    def test_finds_event_by_id(self, store: VisionStore):
        eid = _add(store, 0, cls={"description": "hallo"})
        ev = store.get_event(eid)
        assert ev is not None
        assert ev["id"] == eid
        assert ev["classification"] == {"description": "hallo"}

    def test_returns_none_for_missing(self, store: VisionStore):
        assert store.get_event(999999) is None

    def test_finds_old_event_beyond_recent_window(self, store: VisionStore):
        """The regression: an event that is NOT among the most recent ones
        must still be retrievable by id. The old code scanned only the
        most-recent-1000 via query_events and failed 'event not found' for
        everything older — leaving the whole backlog undescribable."""
        oldest = _add(store, 0)
        for i in range(1, 150):
            _add(store, i)
        # The capped recent query does NOT contain the oldest event …
        recent_ids = {e["id"] for e in store.query_events(limit=100)}
        assert oldest not in recent_ids
        # … but the direct lookup finds it regardless.
        assert store.get_event(oldest) is not None


class TestListEventIds:
    def test_all_ids_newest_first(self, store: VisionStore):
        ids = [_add(store, i) for i in range(5)]
        got = store.list_event_ids(source_id="cam/test")
        assert got == list(reversed(ids))  # timestamp DESC

    def test_filter_by_event_type(self, store: VisionStore):
        store.add_event("cam/test", "motion", timestamp=_BASE)
        store.add_event("cam/test", "face_known", timestamp=_BASE + timedelta(seconds=1))
        ids = store.list_event_ids(event_types=["face_known"])
        assert len(ids) == 1

    def test_filter_by_source(self, store: VisionStore):
        store.add_event("cam/a", "motion", timestamp=_BASE)
        store.add_event("cam/b", "motion", timestamp=_BASE + timedelta(seconds=1))
        assert len(store.list_event_ids(source_id="cam/a")) == 1


class TestQueryEventsNoCap:
    def test_limit_none_returns_all(self, store: VisionStore):
        for i in range(150):
            _add(store, i)
        assert len(store.query_events(limit=None)) == 150
        assert len(store.query_events(limit=10)) == 10


class TestQueryEventsDedup:
    """The tool collapses cluster members into one happening per cluster."""

    def _run(self, store: VisionStore, monkeypatch) -> dict:
        from aifred.lib.plugin_base import PluginContext
        from aifred.lib import vision_bulk
        from aifred.plugins.tools import vision as vision_plugin

        monkeypatch.setattr(vision_plugin, "_store", lambda: store)

        # Der Tool-Pfad ruft immer run_bulk_describe (VLM) — hier stubben,
        # der Test prüft nur die Cluster-Dedup-Logik.
        async def _no_describe(**kwargs):
            return None

        monkeypatch.setattr(vision_bulk, "run_bulk_describe", _no_describe)
        ctx = PluginContext(agent_id="aifred", lang="de", session_id="t")
        tool = vision_plugin.plugin._tool_query_events(ctx)
        return json.loads(asyncio.run(tool.executor()))

    def test_cluster_collapses_to_one_row(self, store: VisionStore, monkeypatch):
        frame = str(VIGILANTIA_DIR / "motion" / "cam_test" / "2026-06-03" / "a.jpg")
        # Three near-identical frames of one happening …
        c_ids = [_add(store, i, frame=frame, cls={"description": "Garten"}) for i in range(3)]
        for eid in c_ids:
            store.set_event_cluster(eid, "cam_test-bucket-aaaa")
        # … plus a separate solo event.
        _add(store, 100, frame=frame, cls={"description": "Tür"})

        out = self._run(store, monkeypatch)
        assert out["success"] is True
        # 1 cluster + 1 solo = 2 happenings, not 4 raw frames.
        assert out["count"] == 2
        by_frames = sorted(e["frames_in_cluster"] for e in out["events"])
        assert by_frames == [1, 3]
        # Every returned happening carries a browser image_url.
        assert all(e["image_url"].startswith("/_upload/vigilantia/") for e in out["events"])

    def test_unclustered_events_stay_individual(self, store: VisionStore, monkeypatch):
        frame = str(VIGILANTIA_DIR / "motion" / "cam_test" / "2026-06-03" / "b.jpg")
        for i in range(3):
            _add(store, i, frame=frame)  # no cluster_id → solo
        out = self._run(store, monkeypatch)
        assert out["count"] == 3
        assert all(e["frames_in_cluster"] == 1 for e in out["events"])
