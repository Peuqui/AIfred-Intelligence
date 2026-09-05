"""Zweiter Detektions-Durchgang auf den Personenboxen.

InsightFace selbst wird gefakt — getestet wird die Mechanik drumherum:
Koordinaten-Rueckrechnung, Qualitaetsschwellen und das Dedupe zwischen
ueberlappenden Regionen.
"""

from __future__ import annotations

from datetime import datetime

import cv2
import numpy as np
import pytest

from aifred.lib.frame_sources import Frame
from aifred.lib.vision_filters.face_detect import FaceDetector, box_iou


def _frame() -> Frame:
    """Echtes 1920x1080-JPEG — so laeuft der Decode-Weg wie in Produktion,
    statt ihn wegzupatchen."""
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    return Frame(
        source_id="cam/door", timestamp=datetime(2026, 9, 5, 7, 51),
        image_bytes=bytes(cv2.imencode(".jpg", img)[1].tobytes()),
    )


class _RawFace:
    """Was InsightFace aus app.get() zurueckgibt (Box in x1,y1,x2,y2)."""

    def __init__(self, bbox, det_score=0.9):
        self.bbox = list(bbox)
        self.det_score = det_score
        self.normed_embedding = np.ones(512, dtype=np.float32)
        self.kps = np.zeros((5, 2), dtype=np.float32)


class _FakeApp:
    """Liefert pro Ausschnittsgroesse ein vorgegebenes Ergebnis und merkt
    sich, welche Ausschnitte es gesehen hat."""

    def __init__(self, result_for_roi):
        self._result_for_roi = result_for_roi
        self.seen_shapes: list[tuple[int, int]] = []

    def get(self, img):
        self.seen_shapes.append(img.shape[:2])
        return self._result_for_roi(img)


def _detector(app, **kwargs) -> FaceDetector:
    """Detector mit vorinitialisierter InsightFace-App (kein Modell-Laden)."""
    det = FaceDetector(min_score=0.65, min_size_px=40, **kwargs)
    det._app = app
    return det


def test_roi_boxes_come_back_in_full_frame_coordinates():
    # Gesicht sitzt 20/30 innerhalb der Personenbox bei (500, 400).
    app = _FakeApp(lambda img: [_RawFace((20, 30, 80, 100))])
    det = _detector(app)
    found = det.detect_in_regions(_frame(), [(500, 400, 200, 600)])
    assert len(found) == 1
    assert found[0].bbox == (520, 430, 60, 70)
    # Der Ausschnitt wurde 1:1 geschnitten, nicht vergroessert — sonst
    # waere die Mindestgroesse nicht mehr ehrlich messbar.
    assert app.seen_shapes == [(600, 200)]


def test_keypoints_are_shifted_too():
    app = _FakeApp(lambda img: [_RawFace((20, 30, 80, 100))])
    det = _detector(app)
    found = det.detect_in_regions(_frame(), [(500, 400, 200, 600)])
    assert found[0].keypoints is not None
    assert found[0].keypoints[0].tolist() == [500.0, 400.0]


def test_face_below_min_size_is_dropped():
    """Der Ausschnitt taeuscht keine Aufloesung vor: eine 30-px-Box bleibt
    eine 30-px-Box und faellt unter min_size_px=40 durch."""
    app = _FakeApp(lambda img: [_RawFace((10, 10, 40, 40))])
    det = _detector(app)
    assert det.detect_in_regions(_frame(), [(0, 0, 200, 600)]) == []


def test_low_score_is_dropped():
    app = _FakeApp(lambda img: [_RawFace((10, 10, 90, 90), det_score=0.4)])
    det = _detector(app)
    assert det.detect_in_regions(_frame(), [(0, 0, 200, 600)]) == []


def test_same_face_from_overlapping_regions_counts_once():
    """Zwei ueberlappende Personenboxen zeigen dasselbe Gesicht — es darf
    nur EIN Fund daraus werden (sonst zwei Events, zwei Crops)."""
    def result(img):
        # Beide Regionen zeigen dasselbe Gesicht — je nach Startpunkt der
        # Region liegt es lokal woanders, im Vollbild aber immer bei (300, 200).
        width = img.shape[1]
        local_x = 100 if width == 400 else 0     # Region 1 beginnt bei x=200,
        return [_RawFace((local_x, 100, local_x + 80, 180))]   # Region 2 bei 300

    app = _FakeApp(result)
    det = _detector(app)
    found = det.detect_in_regions(
        _frame(), [(200, 100, 400, 400), (300, 100, 300, 400)],
    )
    assert len(found) == 1
    assert found[0].bbox == (300, 200, 80, 80)


def test_regions_smaller_than_a_face_are_skipped():
    app = _FakeApp(lambda img: [_RawFace((0, 0, 100, 100))])
    det = _detector(app)
    assert det.detect_in_regions(_frame(), [(0, 0, 20, 20)]) == []
    assert app.seen_shapes == [], "zu kleine Region gar nicht erst inferieren"


def test_no_regions_no_inference():
    app = _FakeApp(lambda img: [_RawFace((0, 0, 100, 100))])
    det = _detector(app)
    assert det.detect_in_regions(_frame(), []) == []
    assert app.seen_shapes == []


def test_regions_are_clipped_to_the_image():
    """Eine Box, die ueber den Bildrand hinausragt, darf nicht crashen."""
    app = _FakeApp(lambda img: [])
    det = _detector(app)
    det.detect_in_regions(_frame(), [(1800, 1000, 400, 400)])
    assert app.seen_shapes == [(80, 120)]


@pytest.mark.parametrize("a,b,expected", [
    ((0, 0, 10, 10), (0, 0, 10, 10), 1.0),
    ((0, 0, 10, 10), (20, 20, 10, 10), 0.0),
    ((0, 0, 10, 10), (5, 0, 10, 10), 1 / 3),
])
def test_box_iou(a, b, expected):
    assert box_iou(a, b) == pytest.approx(expected)
