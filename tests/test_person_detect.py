"""Tests for the YOLO person detector — letterbox geometry and ONNX output
postprocessing (decode → filter → NMS → inverse letterbox). The ONNX session
is faked with a crafted YOLOv8/v11 output tensor, so no model file is needed."""

from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np

from aifred.lib.vision_filters.person_detect import (
    PersonDetector,
    _letterbox,
)


def _jpeg(w: int, h: int) -> bytes:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _frame(w: int = 640, h: int = 480):
    return SimpleNamespace(image_bytes=_jpeg(w, h), source_id="cam/test")


class _FakeSession:
    """Minimal onnxruntime.InferenceSession stand-in."""

    def __init__(self, output: np.ndarray) -> None:
        self._output = output

    def get_inputs(self):
        return [SimpleNamespace(name="images")]

    def run(self, _outputs, _feed):
        return [self._output]


def _detector_with(output: np.ndarray, **kw) -> PersonDetector:
    d = PersonDetector(input_size=480, confidence=0.35, **kw)
    d._session = _FakeSession(output)  # bypass lazy load
    d._input_name = "images"
    return d


def _yolo_output(boxes: list[tuple[float, float, float, float, float]]) -> np.ndarray:
    """Build a realistic (1, 84, 8400) YOLO detect tensor. The given boxes go
    into the first anchors, all remaining anchors stay zero (filtered by the
    confidence threshold). Anchors >> channels mirrors a real export, so the
    channel/anchor axis heuristic behaves as in production."""
    n_anchors = 8400
    arr = np.zeros((1, 84, n_anchors), dtype=np.float32)
    for i, (cx, cy, w, h, score) in enumerate(boxes):
        arr[0, 0, i] = cx
        arr[0, 1, i] = cy
        arr[0, 2, i] = w
        arr[0, 3, i] = h
        arr[0, 4, i] = score  # class 0 = person
    return arr


class TestLetterbox:
    def test_keeps_aspect_and_pads(self):
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        canvas, scale, pad_x, pad_y = _letterbox(img, 480)
        assert canvas.shape == (480, 480, 3)
        assert scale == 480 / 640  # width is the limiting dimension
        assert pad_x == 0.0
        assert pad_y == (480 - 360) / 2  # vertical padding


class TestDetect:
    def test_single_person_mapped_back_to_image(self):
        out = _yolo_output([(240.0, 240.0, 100.0, 300.0, 0.9)])
        det = _detector_with(out)
        results = det.detect(_frame(640, 480))
        assert len(results) == 1
        x, y, w, h = results[0].bbox
        assert results[0].score == np.float32(0.9)
        # Box stays within the original 640×480 frame.
        assert 0 <= x < 640 and 0 <= y < 480
        assert w > 0 and h > 0 and x + w <= 640 and y + h <= 480

    def test_low_score_filtered(self):
        out = _yolo_output([(240.0, 240.0, 100.0, 300.0, 0.10)])
        det = _detector_with(out)
        assert det.detect(_frame()) == []

    def test_no_boxes(self):
        out = _yolo_output([])
        # an empty anchor axis still yields a valid (1,84,0) tensor
        det = _detector_with(out)
        assert det.detect(_frame()) == []

    def test_overlapping_boxes_collapsed_by_nms(self):
        # Two near-identical high-score boxes → NMS keeps one.
        out = _yolo_output([
            (240.0, 240.0, 100.0, 300.0, 0.90),
            (242.0, 241.0, 102.0, 298.0, 0.85),
        ])
        det = _detector_with(out)
        assert len(det.detect(_frame())) == 1

    def test_undecodable_frame_returns_empty(self):
        out = _yolo_output([(240.0, 240.0, 100.0, 300.0, 0.9)])
        det = _detector_with(out)
        bad = SimpleNamespace(image_bytes=b"not-a-jpeg", source_id="cam/test")
        assert det.detect(bad) == []
