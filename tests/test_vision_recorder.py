"""Tests für aifred.lib.vision_recorder.EventRecorder gegen einen echten VisionStore."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from aifred.lib.vision_filters.person_detect import PersonDetection
from aifred.lib.vision_recorder import EventRecorder, face_event_type
from aifred.lib.vision_store import VisionStore
from aifred.lib.vision_watch_types import WatchStatus

SOURCE = "cam/test"
TS = datetime(2026, 5, 6, 7, 8, 9)


def _status(last_event_at: datetime | None = None) -> WatchStatus:
    return WatchStatus(
        source_id=SOURCE, running=True, started_at=TS, fps=5.0, frames_seen=0,
        motion_events=0, face_events=0, last_event_at=last_event_at,
    )


@pytest.fixture()
def store(tmp_path) -> VisionStore:
    return VisionStore(tmp_path / "recorder.db")


@pytest.fixture()
def statuses() -> dict[str, WatchStatus]:
    return {SOURCE: _status()}


@pytest.fixture()
def recorder(store, statuses) -> EventRecorder:
    return EventRecorder(store, statuses)


def _events(store: VisionStore, event_type: str | None = None) -> list[dict]:
    return store.query_events(source_id=SOURCE, event_type=event_type, limit=None)


class TestFaceEventType:
    @pytest.mark.parametrize(("band", "expected"), [
        ("known", "face_known"), ("unsure", "face_unsure"), ("unknown", "face_unknown"),
    ])
    def test_known_bands(self, band, expected):
        assert face_event_type(band) == expected

    def test_unknown_band_raises(self):
        with pytest.raises(KeyError):
            face_event_type("sehr_sicher")


class TestThrottle:
    def test_not_throttled_without_last_event(self, recorder):
        assert recorder.is_throttled(SOURCE, 60.0) is False

    def test_throttled_within_interval(self, statuses, recorder):
        statuses[SOURCE].last_event_at = datetime.now() - timedelta(seconds=5)
        assert recorder.is_throttled(SOURCE, 60.0) is True

    def test_not_throttled_after_interval(self, statuses, recorder):
        statuses[SOURCE].last_event_at = datetime.now() - timedelta(seconds=5)
        assert recorder.is_throttled(SOURCE, 1.0) is False

    def test_zero_interval_never_throttles(self, statuses, recorder):
        statuses[SOURCE].last_event_at = datetime.now()
        assert recorder.is_throttled(SOURCE, 0.0) is False

    def test_unknown_source_raises(self, recorder):
        with pytest.raises(KeyError):
            recorder.is_throttled("cam/unbekannt", 1.0)


class TestNoteTrigger:
    def test_counts_and_sets_last_event(self, statuses, recorder):
        recorder.note_trigger(SOURCE, TS)
        recorder.note_trigger(SOURCE, TS + timedelta(seconds=3))
        assert statuses[SOURCE].motion_events == 2
        assert statuses[SOURCE].last_event_at == TS + timedelta(seconds=3)


class TestRecordPersons:
    def _persons(self) -> list[PersonDetection]:
        return [
            PersonDetection(bbox=(1, 2, 30, 40), score=0.6),
            PersonDetection(bbox=(5, 6, 70, 80), score=0.9),
        ]

    def test_minimal_event_and_max_score(self, store, recorder):
        score = recorder.record_persons(
            self._persons(), source_id=SOURCE, timestamp=TS, frame_path="/f.jpg",
            trigger="motion", cluster_id="c1",
        )
        assert score == 0.9
        (event,) = _events(store, "person")
        assert event["confidence"] == 0.9
        assert event["frame_path"] == "/f.jpg"
        assert event["cluster_id"] == "c1"
        assert event["classification"] == {
            "count": 2, "boxes": [[1, 2, 30, 40], [5, 6, 70, 80]], "max_score": 0.9,
        }
        assert event["metadata"] == {"trigger": "motion"}

    def test_optional_fields_only_when_set(self, store, recorder):
        recorder.record_persons(
            self._persons(), source_id=SOURCE, timestamp=TS, frame_path="/f.jpg",
            trigger="burst", cluster_id="c1", zoom_frame_path="/z.jpg",
            parent_event_id=7,
        )
        (event,) = _events(store, "person")
        assert event["classification"]["zoom_frame_path"] == "/z.jpg"
        assert event["metadata"] == {"trigger": "burst", "parent_event_id": 7}

    def test_empty_zoom_path_is_still_recorded(self, store, recorder):
        # ``is not None``: ein leerer String zählt als gesetzt.
        recorder.record_persons(
            self._persons(), source_id=SOURCE, timestamp=TS, frame_path="",
            trigger="t", cluster_id="", zoom_frame_path="", parent_event_id=0,
        )
        (event,) = _events(store, "person")
        assert event["classification"]["zoom_frame_path"] == ""
        assert event["metadata"]["parent_event_id"] == 0

    def test_does_not_touch_status_counters(self, statuses, recorder):
        recorder.record_persons(
            self._persons(), source_id=SOURCE, timestamp=TS, frame_path="",
            trigger="t", cluster_id="",
        )
        assert statuses[SOURCE].motion_events == 0
        assert statuses[SOURCE].last_event_at is None

    def test_empty_list_raises(self, recorder):
        with pytest.raises(ValueError):
            recorder.record_persons(
                [], source_id=SOURCE, timestamp=TS, frame_path="", trigger="t",
                cluster_id="",
            )


class TestRecordEdgeClass:
    @pytest.mark.parametrize("event_type", ["person", "vehicle", "animal"])
    def test_event_fields(self, store, recorder, event_type):
        recorder.record_edge_class(
            event_type, source_id=SOURCE, timestamp=TS, frame_path="/f.jpg",
            zoom_frame_path="/z.jpg", count=2, detector="yolo", cluster_id="c9",
        )
        (event,) = _events(store, event_type)
        assert event["confidence"] == 1.0
        assert event["frame_path"] == "/f.jpg"
        assert event["cluster_id"] == "c9"
        assert event["classification"] == {
            "detector": "yolo", "count": 2, "zoom_frame_path": "/z.jpg",
        }
        assert event["metadata"] == {"trigger": "edge_ai"}


class TestRecordMotion:
    def test_event_and_trigger_noted(self, store, statuses, recorder):
        frame = SimpleNamespace(source_id=SOURCE, timestamp=TS)
        event_id = recorder.record_motion(
            frame, frame_path="/m.jpg", area_ratio=0.25, bbox=(1, 2, 3, 4),  # type: ignore[arg-type]
            watch_fps=5.0, cluster_id="c1",
        )
        (event,) = _events(store, "motion")
        assert event["id"] == event_id
        assert event["classification"] == {"area_ratio": 0.25, "bbox": [1, 2, 3, 4]}
        assert event["metadata"] == {"watch_fps": 5.0}
        assert statuses[SOURCE].motion_events == 1
        assert statuses[SOURCE].last_event_at == TS

    def test_bbox_none(self, store, recorder):
        frame = SimpleNamespace(source_id=SOURCE, timestamp=TS)
        recorder.record_motion(
            frame, frame_path="", area_ratio=0.1, bbox=None,  # type: ignore[arg-type]
            watch_fps=1.0, cluster_id="",
        )
        (event,) = _events(store, "motion")
        assert event["classification"]["bbox"] is None
