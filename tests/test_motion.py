"""Tests für aifred.lib.vision_filters.motion — Motion-Detection."""

from __future__ import annotations

from datetime import datetime

import cv2
import numpy as np

from aifred.lib.frame_sources import Frame
from aifred.lib.vision_filters import MotionDetector


def _make_frame_from_array(img: np.ndarray, source_id: str = "cam/test") -> Frame:
    """Encode a numpy image as JPEG and wrap in a Frame."""
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok, "imencode failed in test helper"
    h, w = img.shape[:2]
    return Frame(
        source_id=source_id,
        timestamp=datetime.now(),
        image_bytes=bytes(buf),
        format="jpeg",
        width=w,
        height=h,
    )


def _blank(w: int = 320, h: int = 240, color: int = 0) -> np.ndarray:
    """Solid-color BGR image."""
    return np.full((h, w, 3), color, dtype=np.uint8)


def _with_rect(
    base: np.ndarray, x: int, y: int, w: int, h: int, color: int = 255
) -> np.ndarray:
    """Draw a filled BGR rectangle on a copy of ``base``."""
    img = base.copy()
    cv2.rectangle(img, (x, y), (x + w, y + h), (color, color, color), thickness=-1)
    return img


class TestMotionDetector:
    def test_warmup_suppresses_motion(self):
        det = MotionDetector(warmup_frames=3, min_area_ratio=0.001)
        # Even with very different frames during warmup, no motion
        bgr_black = _blank()
        bgr_white = _blank(color=255)
        for img in (bgr_black, bgr_white, bgr_black):
            r = det.process(_make_frame_from_array(img))
            assert r.motion is False, "no motion during warmup expected"

    def test_static_scene_no_motion(self):
        det = MotionDetector(warmup_frames=2, min_area_ratio=0.001)
        bg = _blank()
        # warmup
        det.process(_make_frame_from_array(bg))
        det.process(_make_frame_from_array(bg))
        # static
        r = det.process(_make_frame_from_array(bg))
        assert r.motion is False
        assert r.bbox is None
        assert r.area_ratio < 0.01

    def test_clear_motion_detected(self):
        det = MotionDetector(warmup_frames=2, min_area_ratio=0.01)
        bg = _blank()
        # Establish background
        det.process(_make_frame_from_array(bg))
        det.process(_make_frame_from_array(bg))
        det.process(_make_frame_from_array(bg))
        # Sudden big bright rectangle ~ 40% of image
        moving = _with_rect(bg, 50, 50, 200, 150, color=255)
        r = det.process(_make_frame_from_array(moving))
        assert r.motion is True
        assert r.area_ratio > 0.01
        assert r.bbox is not None
        x, y, w, h = r.bbox
        # Bounding box should roughly cover the rectangle area
        assert w > 50 and h > 50

    def test_small_change_below_threshold_ignored(self):
        det = MotionDetector(warmup_frames=2, min_area_ratio=0.10)
        bg = _blank()
        det.process(_make_frame_from_array(bg))
        det.process(_make_frame_from_array(bg))
        det.process(_make_frame_from_array(bg))
        # Tiny 5×5 rectangle in a 320×240 image is ~0.03% — far below 10%
        tiny = _with_rect(bg, 10, 10, 5, 5, color=255)
        r = det.process(_make_frame_from_array(tiny))
        assert r.motion is False

    def test_empty_bytes_returns_no_motion(self):
        det = MotionDetector(warmup_frames=0)
        f = Frame(
            source_id="cam/test", timestamp=datetime.now(), image_bytes=b""
        )
        r = det.process(f)
        assert r.motion is False
        assert r.area_ratio == 0.0
        assert r.bbox is None

    def test_return_mask_includes_foreground(self):
        det = MotionDetector(warmup_frames=2, return_mask=True)
        bg = _blank()
        det.process(_make_frame_from_array(bg))
        det.process(_make_frame_from_array(bg))
        det.process(_make_frame_from_array(bg))
        moving = _with_rect(bg, 50, 50, 100, 100, color=255)
        r = det.process(_make_frame_from_array(moving))
        assert r.foreground_mask is not None
        assert r.foreground_mask.shape == bg.shape[:2]

    def test_ptz_pan_is_ego_motion_not_trigger(self):
        """PTZ-Schwenk (global kohärent verschobenes Bild) darf NICHT
        triggern — das Ego-Motion-Gate erkennt ihn (Shift + response +
        Fläche) und setzt das Hintergrundmodell frisch auf; nach dem
        Warmup ist die neue Ansicht sofort der Hintergrund."""
        rng = np.random.default_rng(42)
        tex = rng.integers(0, 255, (240, 320), dtype=np.uint8)
        tex = cv2.GaussianBlur(tex, (9, 9), 0)
        tex3 = cv2.cvtColor(tex, cv2.COLOR_GRAY2BGR)
        det = MotionDetector(warmup_frames=3, min_area_ratio=0.02)
        for _ in range(10):
            det.process(_make_frame_from_array(tex3))
        panned = np.roll(tex3, 30, axis=1)
        r = det.process(_make_frame_from_array(panned))
        assert r.motion is False  # Schwenk, kein Alarm
        # Neue Ansicht wird eingelernt: nach dem Warmup herrscht Ruhe …
        for _ in range(8):
            r = det.process(_make_frame_from_array(panned))
        assert r.motion is False
        assert r.area_ratio < 0.01
        # … und ein LOKALES Objekt triggert weiterhin normal (kein
        # fälschlicher Ego-Reset bei Szenen-Bewegung).
        person = panned.copy()
        person[60:200, 80:220] = rng.integers(
            0, 255, (140, 140, 3), dtype=np.uint8
        )
        r = det.process(_make_frame_from_array(person))
        assert r.motion is True
