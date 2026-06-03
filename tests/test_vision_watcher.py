"""Tests für aifred.lib.vision_watcher — Watch-Mode-Runner.

Tests laufen ohne Hardware (V4L2) und ohne InsightFace. Eine FakeSource
liefert kontrollierte Frames, FaceDetector/Recognizer werden gemockt.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import cv2
import numpy as np
import pytest

from aifred.lib.frame_sources import (
    Frame,
    SourceInfo,
    register,
    unregister_kind,
)
from aifred.lib.vision_filters.face_detect import FaceDetection
from aifred.lib.vision_filters.face_recognize import FaceRecognizer
from aifred.lib.vision_filters.person_detect import PersonDetection
from aifred.lib.vision_store import VisionStore
from aifred.lib.vision_watcher import VisionWatcher, WatchConfig


def run(coro):
    return asyncio.run(coro)


def _encode_jpeg(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return bytes(buf)


class FakeSource:
    """Frame-Source that yields a scripted sequence."""

    kind: str = "test-cam"

    def __init__(self, source_id: str, frames: list[bytes], delay_sec: float = 0.0):
        self.source_id = source_id
        self.display_name = f"Fake {source_id}"
        self._frames = frames
        self._delay = delay_sec

    def is_available(self) -> bool:
        return True

    async def snapshot(self, *, width: int = 0, height: int = 0) -> Frame:
        return self._make_frame(0)

    async def stream(
        self, fps: float = 1.0, *, width: int = 0, height: int = 0
    ) -> AsyncIterator[Frame]:
        for idx in range(len(self._frames)):
            yield self._make_frame(idx)
            if self._delay > 0:
                await asyncio.sleep(self._delay)

    def info(self) -> SourceInfo:
        return SourceInfo(
            source_id=self.source_id,
            display_name=self.display_name,
            kind=self.kind,
            width=320,
            height=240,
            fps=1.0,
            available=True,
        )

    def _make_frame(self, idx: int) -> Frame:
        return Frame(
            source_id=self.source_id,
            timestamp=datetime.now(),
            image_bytes=self._frames[idx],
            format="jpeg",
            width=320,
            height=240,
            metadata={"sequence_id": "fake-seq", "frame_idx": idx, "kind": "rgb"},
        )


class FakeFaceDetector:
    """Detector that returns a configurable list of detections regardless of input."""

    def __init__(self, detections: list[FaceDetection]):
        self._detections = detections
        self.calls = 0

    def detect(self, frame: Frame) -> list[FaceDetection]:
        self.calls += 1
        return list(self._detections)


class FakePersonDetector:
    """YOLO person detector stand-in — returns a configurable list of
    PersonDetections regardless of input."""

    def __init__(self, detections):
        self._detections = detections
        self.calls = 0

    def detect(self, frame: Frame):
        self.calls += 1
        return list(self._detections)


def _blank_jpeg(color: int = 0) -> bytes:
    img = np.full((240, 320, 3), color, dtype=np.uint8)
    return _encode_jpeg(img)


def _rect_jpeg() -> bytes:
    """Image with a clear bright rectangle — triggers Motion easily."""
    img = np.full((240, 320, 3), 0, dtype=np.uint8)
    cv2.rectangle(img, (40, 40), (260, 200), (255, 255, 255), thickness=-1)
    return _encode_jpeg(img)


@pytest.fixture(autouse=True)
def _clean_fake_sources():
    """Make sure every test starts and ends with a clean test-cam registry."""
    unregister_kind("test-cam")
    yield
    unregister_kind("test-cam")


@pytest.fixture(autouse=True)
def _no_real_alerts(monkeypatch):
    """Neutralise the alert side-effect. The real VisionWatcher fires
    emit_face_alert on face events, which reaches the live alert pipeline
    (dispatcher → record_autonomous_turn writes data/sessions/,
    announce_to_channel sends a real Telegram). These tests cover the watcher,
    not alerts (those have their own isolated test) — so stub it out."""
    import aifred.lib.vision_alerts as va

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(va, "emit_face_alert", _noop)
    monkeypatch.setattr(va, "emit_person_alert", _noop)


@pytest.fixture()
def store(tmp_path: Path) -> VisionStore:
    return VisionStore(tmp_path / "vision_watcher.db")


@pytest.fixture()
def frames_dir(tmp_path: Path) -> Path:
    return tmp_path / "frames"


class TestStartStop:
    def test_start_unknown_source_raises(self, store: VisionStore, frames_dir: Path):
        w = VisionWatcher(store, frames_dir=frames_dir)
        with pytest.raises(ValueError, match="unknown source"):
            run(w.start("cam/does-not-exist", WatchConfig()))

    def test_start_does_not_probe_availability(
        self, store: VisionStore, frames_dir: Path
    ):
        """start() deliberately does NOT call is_available() — probing
        opens a cv2.VideoCapture that races a running live-preview stream
        for the same V4L2 device. A source reporting unavailable still
        starts; a real open-failure is handled cleanly in the stream
        loop instead (see _watch_loop)."""
        class Down(FakeSource):
            def is_available(self) -> bool:
                return False

        register(Down("cam/test-down", [_blank_jpeg()] * 5, delay_sec=0.01))
        w = VisionWatcher(store, frames_dir=frames_dir)

        async def go():
            status = await w.start("cam/test-down", WatchConfig(fps=10.0))
            assert status.running is True  # no RuntimeError despite "down"
            await w.stop("cam/test-down")

        run(go())

    def test_start_then_stop_clean(self, store: VisionStore, frames_dir: Path):
        register(FakeSource("cam/test-1", [_blank_jpeg()] * 5, delay_sec=0.01))
        w = VisionWatcher(store, frames_dir=frames_dir)

        async def go():
            status = await w.start("cam/test-1", WatchConfig(fps=10.0))
            assert status.running is True
            await asyncio.sleep(0.05)
            assert w.is_running("cam/test-1")
            stopped = await w.stop("cam/test-1")
            assert stopped is True
            assert not w.is_running("cam/test-1")

        run(go())

    def test_stop_when_nothing_running(self, store: VisionStore, frames_dir: Path):
        w = VisionWatcher(store, frames_dir=frames_dir)
        assert run(w.stop("cam/whatever")) is False

    def test_start_is_idempotent_when_already_running(
        self, store: VisionStore, frames_dir: Path
    ):
        register(FakeSource("cam/test-id", [_blank_jpeg()] * 20, delay_sec=0.02))
        w = VisionWatcher(store, frames_dir=frames_dir)

        async def go():
            s1 = await w.start("cam/test-id", WatchConfig(fps=10.0))
            s2 = await w.start("cam/test-id", WatchConfig(fps=10.0))
            assert s1.started_at == s2.started_at
            await w.stop("cam/test-id")

        run(go())


class TestMotionEventLogging:
    def test_motion_triggers_event_in_store(
        self, store: VisionStore, frames_dir: Path
    ):
        # Build sequence: 3 blank frames (warmup + baseline), then a rect frame
        frames = [_blank_jpeg()] * 3 + [_rect_jpeg(), _blank_jpeg(), _rect_jpeg()]
        register(FakeSource("cam/test-motion", frames, delay_sec=0.01))
        w = VisionWatcher(store, frames_dir=frames_dir)

        async def go():
            await w.start(
                "cam/test-motion",
                WatchConfig(
                    fps=20.0,
                    motion_warmup_frames=2,
                    motion_min_area_ratio=0.01,
                    min_event_interval_sec=0.0,
                    save_event_frames=False,
                    run_face_detect_on_motion=False,
                ),
            )
            await asyncio.sleep(0.3)
            await w.stop("cam/test-motion")

        run(go())

        events = store.query_events(source_id="cam/test-motion", event_type="motion")
        assert len(events) >= 1
        assert events[0]["event_type"] == "motion"
        assert events[0]["confidence"] > 0.0

    def test_frames_saved_when_enabled(self, store: VisionStore, frames_dir: Path):
        frames = [_blank_jpeg()] * 3 + [_rect_jpeg()]
        register(FakeSource("cam/test-save", frames, delay_sec=0.01))
        w = VisionWatcher(store, frames_dir=frames_dir)

        async def go():
            await w.start(
                "cam/test-save",
                WatchConfig(
                    fps=20.0,
                    motion_warmup_frames=2,
                    motion_min_area_ratio=0.01,
                    min_event_interval_sec=0.0,
                    save_event_frames=True,
                    run_face_detect_on_motion=False,
                ),
            )
            await asyncio.sleep(0.25)
            await w.stop("cam/test-save")

        run(go())
        events = store.query_events(source_id="cam/test-save")
        assert events, "expected at least one event"
        # Saved frame path should exist on disk
        saved = [e for e in events if e["frame_path"]]
        assert saved, "expected at least one event with a saved frame"
        for ev in saved:
            assert Path(ev["frame_path"]).exists()

    def test_min_event_interval_throttles_events(
        self, store: VisionStore, frames_dir: Path
    ):
        # Many rect-frames in quick succession should produce ≤ 1 event
        # when min_event_interval is large.
        frames = [_blank_jpeg()] * 3 + [_rect_jpeg()] * 20
        register(FakeSource("cam/test-throttle", frames, delay_sec=0.005))
        w = VisionWatcher(store, frames_dir=frames_dir)

        async def go():
            await w.start(
                "cam/test-throttle",
                WatchConfig(
                    fps=50.0,
                    motion_warmup_frames=2,
                    motion_min_area_ratio=0.01,
                    min_event_interval_sec=30.0,
                    save_event_frames=False,
                    run_face_detect_on_motion=False,
                ),
            )
            await asyncio.sleep(0.3)
            await w.stop("cam/test-throttle")

        run(go())
        events = store.query_events(source_id="cam/test-throttle", event_type="motion")
        assert len(events) == 1, f"expected exactly 1 throttled event, got {len(events)}"


class TestFaceDetectIntegration:
    def test_face_unknown_event_when_no_enrollments(
        self, store: VisionStore, frames_dir: Path
    ):
        # Detector reports one face, but store has no enrolled embeddings → unknown
        fake_emb = np.random.default_rng(42).standard_normal(512).astype(np.float32)
        fake_emb /= np.linalg.norm(fake_emb)
        detector = FakeFaceDetector(
            [
                FaceDetection(
                    bbox=(10, 10, 80, 80),
                    embedding=fake_emb,
                    detection_score=0.95,
                    keypoints=None,
                )
            ]
        )

        frames = [_blank_jpeg()] * 3 + [_rect_jpeg()]
        register(FakeSource("cam/test-face-unk", frames, delay_sec=0.01))
        w = VisionWatcher(
            store,
            frames_dir=frames_dir,
            face_detector=detector,
            face_recognizer=FaceRecognizer(store),
        )

        async def go():
            await w.start(
                "cam/test-face-unk",
                WatchConfig(
                    fps=20.0,
                    motion_warmup_frames=2,
                    motion_min_area_ratio=0.01,
                    min_event_interval_sec=0.0,
                    save_event_frames=False,
                    run_face_detect_on_motion=True,
                ),
            )
            await asyncio.sleep(0.3)
            await w.stop("cam/test-face-unk")

        run(go())

        events = store.query_events(source_id="cam/test-face-unk")
        types = {e["event_type"] for e in events}
        assert "motion" in types
        assert "face_unknown" in types
        assert detector.calls >= 1

    def test_face_known_event_after_enrollment(
        self, store: VisionStore, frames_dir: Path
    ):
        # Enroll a person with known embedding, detector returns matching embedding
        emb = np.random.default_rng(7).standard_normal(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        fid = store.add_face("Alice")
        store.add_embedding(fid, emb)

        detector = FakeFaceDetector(
            [
                FaceDetection(
                    bbox=(10, 10, 80, 80),
                    embedding=emb,  # identical to enrolled
                    detection_score=0.95,
                    keypoints=None,
                )
            ]
        )

        frames = [_blank_jpeg()] * 3 + [_rect_jpeg()]
        register(FakeSource("cam/test-face-known", frames, delay_sec=0.01))
        w = VisionWatcher(
            store,
            frames_dir=frames_dir,
            face_detector=detector,
            face_recognizer=FaceRecognizer(store),
        )

        async def go():
            await w.start(
                "cam/test-face-known",
                WatchConfig(
                    fps=20.0,
                    motion_warmup_frames=2,
                    motion_min_area_ratio=0.01,
                    min_event_interval_sec=0.0,
                    save_event_frames=False,
                    run_face_detect_on_motion=True,
                ),
            )
            await asyncio.sleep(0.3)
            await w.stop("cam/test-face-known")

        run(go())

        face_events = store.query_events(
            source_id="cam/test-face-known", event_type="face_known"
        )
        assert face_events, "expected at least one face_known event"
        assert face_events[0]["face_id"] == fid
        assert face_events[0]["classification"]["matched_name"] == "Alice"


class TestPersonGate:
    """Sequence: motion → YOLO person → (only if person) face detection."""

    def _run(self, store, frames_dir, source, persons, face_dets):
        register(FakeSource(source, [_blank_jpeg()] * 3 + [_rect_jpeg()],
                            delay_sec=0.01))
        face_det = FakeFaceDetector(face_dets)
        w = VisionWatcher(
            store,
            frames_dir=frames_dir,
            face_detector=face_det,
            face_recognizer=FaceRecognizer(store),
            person_detector=FakePersonDetector(persons),
        )

        async def go():
            await w.start(source, WatchConfig(
                fps=20.0, motion_warmup_frames=2, motion_min_area_ratio=0.01,
                min_event_interval_sec=0.0, save_event_frames=False,
                run_face_detect_on_motion=True,
                run_person_detect_on_motion=True,
            ))
            await asyncio.sleep(0.3)
            await w.stop(source)

        run(go())
        return face_det

    def test_person_present_writes_event_and_runs_face(self, store, frames_dir):
        face_det = self._run(
            store, frames_dir, "cam/test-person-yes",
            persons=[PersonDetection(bbox=(10, 10, 80, 200), score=0.9)],
            face_dets=[FaceDetection(bbox=(10, 10, 80, 80),
                                     embedding=np.zeros(512, dtype=np.float32),
                                     detection_score=0.9, keypoints=None)],
        )
        person_events = store.query_events(
            source_id="cam/test-person-yes", event_type="person"
        )
        assert person_events, "expected a person event"
        assert person_events[0]["classification"]["count"] == 1
        # Person present → face detection ran.
        assert face_det.calls >= 1

    def test_no_person_still_runs_face(self, store, frames_dir):
        # YOLO finds no body (e.g. close-up face) — face detection must
        # STILL run, the two layers are independent (no gate).
        face_det = self._run(
            store, frames_dir, "cam/test-person-no",
            persons=[],  # YOLO finds nobody
            face_dets=[FaceDetection(bbox=(10, 10, 80, 80),
                                     embedding=np.zeros(512, dtype=np.float32),
                                     detection_score=0.9, keypoints=None)],
        )
        person_events = store.query_events(
            source_id="cam/test-person-no", event_type="person"
        )
        assert not person_events, "no person → no person event"
        # No YOLO person, but a face is visible → face detection still ran.
        assert face_det.calls >= 1


class TestMotionGating:
    """motion_gated=False (PTZ/tracking cams): sample every frame, no motion
    needed — only throttled by min_event_interval_sec."""

    def test_ungated_samples_without_motion(self, store, frames_dir):
        # All-blank frames → the motion detector never fires.
        register(FakeSource("cam/test-ungated", [_blank_jpeg()] * 8,
                            delay_sec=0.01))
        w = VisionWatcher(store, frames_dir=frames_dir)

        async def go():
            await w.start("cam/test-ungated", WatchConfig(
                fps=20.0, motion_warmup_frames=2, min_event_interval_sec=0.0,
                save_event_frames=False, run_face_detect_on_motion=False,
                motion_gated=False,
            ))
            await asyncio.sleep(0.3)
            await w.stop("cam/test-ungated")

        run(go())
        events = store.query_events(
            source_id="cam/test-ungated", event_type="motion"
        )
        assert events, "ungated source should sample frames without motion"


class TestStatus:
    def test_list_active_excludes_stopped(self, store: VisionStore, frames_dir: Path):
        register(FakeSource("cam/test-active", [_blank_jpeg()] * 5, delay_sec=0.02))
        w = VisionWatcher(store, frames_dir=frames_dir)

        async def go():
            await w.start("cam/test-active", WatchConfig(fps=20.0))
            await asyncio.sleep(0.02)
            active = w.list_active()
            assert any(s.source_id == "cam/test-active" for s in active)
            await w.stop("cam/test-active")
            assert all(s.source_id != "cam/test-active" for s in w.list_active())

        run(go())
