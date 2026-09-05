"""Welches Bild das Nachlernen aufmacht.

Die gespeicherte Bbox gilt in genau EINEM Bild. Bei einer Dual-Lens-Kamera
ist das mal der Tele-Snap (Gesicht dort gefunden), mal das Weitwinkel
(Gesicht erst im Ausschnitt einer Personenbox gefunden). Das Event sagt es
ueber ``classification.detect_frame_path`` — geraten wird nicht, im falschen
Bild findet die Detektion die Box nicht wieder.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import numpy as np
import pytest

from aifred.lib.face_enroll import _best_detection_for_event
from aifred.lib.vision_filters.face_detect import FaceDetection


@pytest.fixture()
def frames(tmp_path: Path) -> dict[str, str]:
    """Zwei Bilder auf Platte — Weitwinkel und Tele desselben Ticks."""
    out = {}
    for name in ("wide", "zoom"):
        path = tmp_path / f"{name}.jpg"
        cv2.imwrite(str(path), np.zeros((480, 640, 3), dtype=np.uint8))
        out[name] = str(path)
    return out


class _RecordingDetector:
    """Merkt sich, welches Bild geoeffnet wurde, und liefert eine Detektion
    auf der erwarteten Box."""

    def __init__(self, bbox=(100, 100, 80, 80)):
        self.seen_bytes: list[bytes] = []
        self._bbox = bbox

    def detect(self, frame):
        self.seen_bytes.append(frame.image_bytes)
        return [FaceDetection(
            bbox=self._bbox,
            embedding=np.ones(512, dtype=np.float32),
            detection_score=0.9,
            keypoints=None,
        )]


def _event(**classification) -> dict:
    return {
        "id": 42,
        "source_id": "cam/door",
        "frame_path": classification.pop("frame_path", ""),
        "classification": {"bbox": [100, 100, 80, 80], **classification},
    }


def test_reads_the_frame_the_event_names(frames):
    det = _RecordingDetector()
    event = _event(
        frame_path=frames["wide"],
        zoom_frame_path=frames["zoom"],
        detect_frame_path=frames["wide"],   # im Weitwinkel gefunden
    )
    asyncio.run(_best_detection_for_event(event, det))
    assert det.seen_bytes == [Path(frames["wide"]).read_bytes()]


def test_tele_snap_is_used_when_the_event_says_so(frames):
    det = _RecordingDetector()
    event = _event(
        frame_path=frames["wide"],
        zoom_frame_path=frames["zoom"],
        detect_frame_path=frames["zoom"],
    )
    asyncio.run(_best_detection_for_event(event, det))
    assert det.seen_bytes == [Path(frames["zoom"]).read_bytes()]


def test_event_without_detect_frame_is_refused(frames):
    """Kein Rateweg mehr: ohne Angabe wird nicht gelernt."""
    event = _event(frame_path=frames["wide"], zoom_frame_path=frames["zoom"])
    with pytest.raises(ValueError, match="detect_frame_path"):
        asyncio.run(_best_detection_for_event(event, _RecordingDetector()))


def test_deleted_frame_is_reported_as_such(tmp_path):
    event = _event(detect_frame_path=str(tmp_path / "weg.jpg"))
    with pytest.raises(ValueError, match="no longer on disk"):
        asyncio.run(_best_detection_for_event(event, _RecordingDetector()))


def test_bbox_from_the_wrong_frame_is_rejected(frames):
    """Sicherheitsnetz: passt keine Detektion zur gespeicherten Box, wird
    nicht das naechstbeste Gesicht gelernt."""
    det = _RecordingDetector(bbox=(400, 300, 80, 80))   # ganz woanders
    event = _event(detect_frame_path=frames["wide"])
    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(_best_detection_for_event(event, det))


def test_event_without_bbox_is_refused(frames):
    event = {"id": 7, "source_id": "cam/door", "frame_path": frames["wide"],
             "classification": {"detect_frame_path": frames["wide"]}}
    with pytest.raises(ValueError, match="no face bbox"):
        asyncio.run(_best_detection_for_event(event, _RecordingDetector()))
