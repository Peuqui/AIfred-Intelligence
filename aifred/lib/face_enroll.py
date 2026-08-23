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


async def _best_detection_for_event(event: dict[str, Any], detector: Any) -> Any:
    """Die zur gespeicherten Event-Bbox gehörende InsightFace-Detektion auf
    dem gespeicherten Frame (Zoom-Snap bevorzugt — auf DEM Bild lief die
    Erkennung, siehe unten). Wirft ``ValueError``, wenn Bbox/Frame fehlen
    oder keine Detektion zur Box passt (IoU < 0.3)."""
    from datetime import datetime
    from pathlib import Path

    from .frame_sources import Frame

    cls = dict(event.get("classification") or {})
    bbox_raw = cls.get("bbox")
    if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
        raise ValueError(f"event {event.get('id')} has no face bbox")
    event_bbox = tuple(int(v) for v in bbox_raw)

    # Das Frame nehmen, auf dem die Erkennung tatsächlich lief: bei
    # Edge-AI-Events ist das der Zoom-Snap (classification.zoom_frame_path)
    # — die gespeicherte Bbox gehört zu DIESEM Bild, und auf dem Weitwinkel
    # wäre das Gesicht oft zu klein für eine erneute Detektion.
    frame_path = str(cls.get("zoom_frame_path") or event.get("frame_path") or "")
    if not frame_path or not Path(frame_path).exists():
        raise ValueError("frame no longer on disk (cleanup)")

    frame = Frame(
        source_id=str(event.get("source_id") or ""),
        timestamp=datetime.now(),
        image_bytes=Path(frame_path).read_bytes(),
    )
    detections = await asyncio.to_thread(detector.detect, frame)
    if not detections:
        raise ValueError("no face found in stored frame")
    # Die Detektion, die zur damals gespeicherten Box gehört — bei einem
    # Gesicht trivial, bei mehreren entscheidet die Box-Überlappung.
    best = max(detections, key=lambda d: _box_iou(tuple(d.bbox), event_bbox))  # type: ignore[arg-type]
    if _box_iou(tuple(best.bbox), event_bbox) < 0.3:  # type: ignore[arg-type]
        raise ValueError("stored bbox does not match any detected face")
    return best


def _shared_recognizer() -> Any:
    """Recognizer über den Watcher beziehen (SSOT: Schwellen aus den
    Plugin-Settings + geteilter Embedding-Cache mit Epoch-Reload)."""
    from .vision_watcher import get_default_watcher
    return get_default_watcher()._get_face_recognizer()  # noqa: SLF001


async def _match_and_assign(
    store: Any,
    events: list[dict[str, Any]],
    detector: Any,
    recognizer: Any,
    *,
    require_face_id: int | None = None,
) -> dict[str, int]:
    """Gemeinsamer Kern von Cluster-Sweep und manuellem Re-Match: Events
    nachdetektieren, gegen die Embedding-DB matchen, SICHERE Treffer
    zuordnen (nur Event → Person, KEIN neues Embedding — Lernen bleibt
    eine bewusste Nutzer-Entscheidung). ``require_face_id`` beschränkt
    auf Treffer genau dieser Person (Sweep). Returnt ``{name: count}``
    der zugeordneten Events."""
    resolved: dict[str, int] = {}
    for ev in events:
        try:
            det = await _best_detection_for_event(ev, detector)
        except ValueError:
            continue  # Frame weg / kein passendes Gesicht — überspringen
        match = recognizer.match(det.embedding)
        if match.confidence_band != "known":
            continue
        if require_face_id is not None and int(match.face_id) != require_face_id:
            continue
        store.set_event_face_id(int(ev["id"]), int(match.face_id))
        resolved[match.name or "?"] = resolved.get(match.name or "?", 0) + 1
    return resolved


async def _resolve_cluster_siblings(
    store: Any, cluster_id: str, *, exclude_id: int, face_id: int, detector: Any,
) -> int:
    """Nach einem manuellen Enroll die übrigen unzugeordneten Gesichts-
    Events DESSELBEN Vorkommnisses gegen die (frisch erweiterte)
    Embedding-DB matchen und bei sicherem Treffer derselben Person
    zuordnen. Fremde/unsichere Gesichter bleiben bewusst unzugeordnet
    (zweiter Besucher im selben Vorkommnis!). Returnt die Anzahl
    aufgelöster Geschwister-Events."""
    siblings = store.list_untagged_face_events(cluster_id, exclude_id=exclude_id)
    if not siblings:
        return 0
    resolved = await _match_and_assign(
        store, siblings, detector, _shared_recognizer(),
        require_face_id=face_id,
    )
    return sum(resolved.values())


async def rematch_untagged_faces() -> dict[str, Any]:
    """Manueller Re-Match (Personarium-Button): ALLE unzugeordneten
    face_unknown/face_unsure-Events erneut gegen die aktuelle
    Embedding-DB erkennen und sichere Treffer zuordnen — ohne neue
    Embeddings. Sinnvoll, nachdem der Nutzer die guten Crops gelernt
    hat: der Rest desselben Besuchers wird abgehakt, übrig bleibt nur
    echt Unbekanntes/Unbrauchbares.

    Returnt ``{"checked": int, "resolved": int, "by_name": {name: n}}``.
    """
    from .vision_filters.face_detect import get_default_detector
    from .vision_store import VisionStore

    store = VisionStore()
    # Nur die sichtbaren Kandidaten (ohne per ✕ Verworfene) — die Bilanz
    # in der Statuszeile muss zum Grid passen.
    events = store.list_untagged_face_events(include_dismissed=False)
    if not events:
        return {"checked": 0, "resolved": 0, "by_name": {}}
    by_name = await _match_and_assign(
        store, events, get_default_detector(), _shared_recognizer(),
    )
    resolved = sum(by_name.values())
    logger.info(
        "rematch_untagged_faces: %d/%d resolved (%s)",
        resolved, len(events), by_name,
    )
    return {"checked": len(events), "resolved": resolved, "by_name": by_name}


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

    detector = get_default_detector()
    best = await _best_detection_for_event(event, detector)

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

    # Geschwister-Events desselben Vorkommnisses gleich mit auflösen —
    # sonst taucht der Cluster im Personarium mit dem nächstbesten Crop
    # sofort wieder als „unzugeordnet" auf.
    siblings_resolved = 0
    cluster_id = str(event.get("cluster_id") or "")
    if cluster_id:
        siblings_resolved = await _resolve_cluster_siblings(
            store, cluster_id,
            exclude_id=int(event_id), face_id=int(face_id),
            detector=detector,
        )

    logger.info(
        "enrolled face from event %d → face_id=%d name=%s (is_new=%s, "
        "siblings_resolved=%d)",
        event_id, face_id, resolved_name, is_new, siblings_resolved,
    )
    return {
        "face_id": int(face_id),
        "name": resolved_name,
        "is_new": is_new,
        "quality": float(best.detection_score),
        "siblings_resolved": siblings_resolved,
    }
