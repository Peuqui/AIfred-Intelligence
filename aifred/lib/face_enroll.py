"""Nachträgliches Gesichts-Lernen aus gespeicherten Vision-Events.

Der Live-Pfad (Popup ``+ taggen``) hat das InsightFace-Embedding direkt aus
dem SSE-Event — für die Chronik gibt es das nicht: dort liegt nur das
Vollbild-Frame auf Disk plus die Bounding-Box im Event. Dieser Helper
rechnet das Embedding nach (Detektion auf dem Vollbild, Zuordnung über die
gespeicherte Box), hängt es der Person an und setzt die Event-Zuordnung.

Genutzt vom Personarium („Unzugeordnete Gesichter" → Zuordnen + Lernen).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _box_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-Union zweier ``(x, y, w, h)``-Boxen."""
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


async def enroll_face_from_event(
    event_id: int,
    *,
    face_id: int | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Gesicht eines gespeicherten Events einer Person zuordnen UND als
    Embedding lernen.

    Entweder ``face_id`` (bestehende Person) oder ``name`` (bestehende oder
    neue Person — Name-Dedup wie beim Popup-Enroll) angeben.

    Ablauf: Frame von Disk → InsightFace-Detektion (geteilter
    Default-Detector, läuft im Thread) → Detektion mit der besten
    Box-Übereinstimmung zur Event-Bbox → ``add_embedding`` +
    ``set_event_face_id`` + Recognizer-Invalidierung.

    Returnt ``{face_id, name, is_new, quality}``. Wirft ``ValueError``,
    wenn Event/Frame/Gesicht nicht (mehr) auffindbar sind.
    """
    from pathlib import Path

    from .frame_sources import Frame
    from .vision_filters.face_detect import get_default_detector
    from .vision_filters.face_recognize import bump_enrollment_epoch
    from .vision_store import VisionStore

    if (face_id is None) == (name is None):
        raise ValueError("exactly one of face_id / name required")

    store = VisionStore()
    event = store.get_event(int(event_id))
    if not event:
        raise ValueError(f"event {event_id} not found")
    cls = dict(event.get("classification") or {})
    bbox_raw = cls.get("bbox")
    if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
        raise ValueError(f"event {event_id} has no face bbox")
    event_bbox = tuple(int(v) for v in bbox_raw)

    frame_path = str(event.get("frame_path") or "")
    if not frame_path or not Path(frame_path).exists():
        raise ValueError("frame no longer on disk (cleanup)")

    from datetime import datetime
    frame = Frame(
        source_id=str(event.get("source_id") or ""),
        timestamp=datetime.now(),
        image_bytes=Path(frame_path).read_bytes(),
    )
    detector = get_default_detector()
    detections = await asyncio.to_thread(detector.detect, frame)
    if not detections:
        raise ValueError("no face found in stored frame")
    # Die Detektion, die zur damals gespeicherten Box gehört — bei einem
    # Gesicht trivial, bei mehreren entscheidet die Box-Überlappung.
    best = max(detections, key=lambda d: _box_iou(tuple(d.bbox), event_bbox))  # type: ignore[arg-type]
    if _box_iou(tuple(best.bbox), event_bbox) < 0.3:  # type: ignore[arg-type]
        raise ValueError("stored bbox does not match any detected face")

    is_new = False
    if face_id is None:
        assert name is not None
        clean = name.strip()
        if not clean:
            raise ValueError("empty name")
        is_new = store.get_face_by_name(clean) is None
        face_id = store.get_or_create_face(clean, enrolled_by="personarium")
        resolved_name = clean
    else:
        rec = store.get_face_by_id(int(face_id))
        if not rec:
            raise ValueError(f"face {face_id} not found")
        resolved_name = str(rec.get("name") or "")

    store.add_embedding(
        int(face_id), best.embedding,
        quality_score=float(best.detection_score),
        crop_url=str(cls.get("crop_url") or ""),
    )
    store.set_event_face_id(int(event_id), int(face_id))
    # Lebende Recognizer informieren — nächstes Frame erkennt die Person.
    bump_enrollment_epoch()
    logger.info(
        "enrolled face from event %d → face_id=%d name=%s (is_new=%s)",
        event_id, face_id, resolved_name, is_new,
    )
    return {
        "face_id": int(face_id),
        "name": resolved_name,
        "is_new": is_new,
        "quality": float(best.detection_score),
    }
