"""Ereignis-Aufzeichnung des Vigilantia-Watchers.

SSOT dafür, wie ein erkanntes Gesicht, eine Person oder eine Edge-AI-Klasse
im ``VisionStore`` landet: Gesichts-Crop speichern, Event schreiben, Zähler
des Watch-Status hochzählen, Live-Event auf den Bus geben. Dazu der
inkrementelle Cluster-Zuordner (``cluster_id`` je Frame).
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from .face_crop_store import CropResult, get_default_store
from .vision_cluster import IncrementalClusterer
from .vision_event_bus import publish_vlm_event
from .vision_filters.face_detect import FaceDetection
from .vision_filters.face_recognize import FaceMatch
from .vision_filters.person_detect import PersonDetection
from .vision_phash import phash_bytes
from .vision_store import VisionStore
from .vision_watch_types import WatchStatus

if TYPE_CHECKING:
    from .frame_sources import Frame

logger = logging.getLogger(__name__)

# Band des Recognizers → Ereignisart. Ein unbekanntes Band ist ein Fehler.
FACE_EVENT_TYPES = {
    "known": "face_known",
    "unsure": "face_unsure",
    "unknown": "face_unknown",
}


def face_event_type(band: str) -> str:
    return FACE_EVENT_TYPES[band]


class EventRecorder:
    """Schreibt Watch-Ereignisse in den Store und auf den Live-Bus."""

    def __init__(
        self, store: VisionStore, statuses: dict[str, WatchStatus],
    ) -> None:
        self._store = store
        self._statuses = statuses
        # Ein inkrementeller Clusterer (per-Source-State) weist Ereignissen
        # beim Erkennen eine cluster_id zu — pHash aus dem In-Memory-Frame,
        # kein Disk-Reread. So haben Events ihre cluster_id sofort
        # (Alert-Dedup, Query-Dedup).
        self._clusterer = IncrementalClusterer()

    def cluster_id_for(self, frame: "Frame") -> str:
        """Live-cluster_id für das (gestempelte) Frame eines Ereignisses —
        pHash aus dem Speicher, gefüttert in den Clusterer. Der Batch-Pfad
        liest dasselbe gespeicherte Frame von Platte, pHash und damit Cluster
        sind konsistent."""
        return self._clusterer.assign(
            frame.source_id, frame.timestamp, phash_bytes(frame.image_bytes),
        )

    def note_trigger(self, source_id: str, timestamp: datetime) -> None:
        """Einen Trigger (Bewegung bzw. Edge-AI-Meldung) im Watch-Status
        verbuchen — Grundlage der Drosselung ``min_event_interval_sec``."""
        status = self._statuses[source_id]
        status.motion_events += 1
        status.last_event_at = timestamp

    def is_throttled(self, source_id: str, min_interval_sec: float) -> bool:
        """Liegt der letzte Trigger der Quelle noch keine ``min_interval_sec``
        zurück? (Drosselung ``min_event_interval_sec``.)"""
        last = self._statuses[source_id].last_event_at
        return (
            last is not None
            and (datetime.now() - last).total_seconds() < min_interval_sec
        )

    def record_motion(
        self,
        frame: "Frame",
        *,
        frame_path: str,
        area_ratio: float,
        bbox: tuple[int, int, int, int] | None,
        watch_fps: float,
        cluster_id: str,
    ) -> int:
        """``motion``-Ereignis des MOG2-Detektors. Returnt die Event-ID,
        an der Gesichts- und Personen-Ereignisse desselben Frames hängen."""
        event_id = self._store.add_event(
            source_id=frame.source_id,
            event_type="motion",
            timestamp=frame.timestamp,
            frame_path=frame_path,
            classification={
                "area_ratio": area_ratio,
                "bbox": list(bbox) if bbox else None,
            },
            confidence=float(area_ratio),
            metadata={"watch_fps": watch_fps},
            cluster_id=cluster_id,
        )
        self.note_trigger(frame.source_id, frame.timestamp)
        return event_id

    def save_face_crop(
        self,
        frame_bytes: bytes,
        source_id: str,
        det: FaceDetection,
        match: FaceMatch,
    ) -> CropResult | None:
        """Gesichts-Crop des Treffers ablegen. Zuerst speichern, damit die
        ``crop_url`` mit ins Event geht — das Personarium greift später auf
        den letzten Crop pro Identität zu."""
        return get_default_store().save(
            frame_bytes=frame_bytes,
            bbox=tuple(int(v) for v in det.bbox),  # type: ignore[arg-type]
            source_id=source_id,
            event_type=face_event_type(match.confidence_band),
            face_id=match.face_id if match.face_id > 0 else None,
            name=match.name or "",
            embedding=det.embedding,
        )

    def record_face(
        self,
        *,
        source_id: str,
        frame_timestamp: datetime,
        det: FaceDetection,
        match: FaceMatch,
        crop: CropResult | None,
        frame_path: str,
        zoom_frame_path: str,
        detect_frame_path: str,
        trigger: str,
        cluster_id: str,
        parent_event_id: int | None = None,
    ) -> int:
        """Gesichts-Ereignis schreiben, Status-Zähler erhöhen und das Live-
        Event für den Teleprompter/Personarium publizieren. Returnt die
        Event-ID. ``detect_frame_path`` ist das Bild, in dem die ``bbox``
        gilt — das Nachlernen im Personarium rechnet auf genau diesem Bild
        nach."""
        event_type = face_event_type(match.confidence_band)
        crop_url = crop.url if crop else ""
        session_id = crop.session_id if crop else ""
        face_id = match.face_id if match.face_id > 0 else None
        event_id = self._store.add_event(
            source_id=source_id,
            event_type=event_type,
            timestamp=frame_timestamp,
            frame_path=frame_path,
            face_id=face_id,
            confidence=float(match.similarity),
            classification={
                "matched_name": match.name,
                "confidence_band": match.confidence_band,
                "detection_score": det.detection_score,
                "bbox": list(det.bbox),
                "crop_url": crop_url,
                "zoom_frame_path": zoom_frame_path,
                "detect_frame_path": detect_frame_path,
            },
            metadata={
                "parent_event_id": parent_event_id,
                "trigger": trigger,
                "session_id": session_id,
            },
            cluster_id=cluster_id,
        )
        self._statuses[source_id].face_events += 1
        publish_vlm_event(source_id, {
            "id": event_id,
            "type": event_type,
            "source_id": source_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "frame_timestamp": frame_timestamp.isoformat(timespec="seconds"),
            "face_id": face_id or 0,
            "identity_key": crop.identity_key if crop else "",
            "session_id": session_id,
            "name": match.name or "",
            "similarity": round(float(match.similarity), 3),
            "confidence_band": match.confidence_band,
            "crop_url": crop_url,
            "embedding_b64": base64.b64encode(det.embedding.tobytes()).decode("ascii"),
        })
        return event_id

    def record_persons(
        self,
        persons: list[PersonDetection],
        *,
        source_id: str,
        timestamp: datetime,
        frame_path: str,
        trigger: str,
        cluster_id: str,
        zoom_frame_path: str | None = None,
        parent_event_id: int | None = None,
    ) -> float:
        """``person``-Ereignis aus YOLO-Körper-Detektionen (Motion-Pfad und
        Burst-Tick ohne Gesicht). Returnt die höchste Konfidenz."""
        max_score = max(p.score for p in persons)
        classification: dict[str, object] = {
            "count": len(persons),
            "boxes": [list(p.bbox) for p in persons],
            "max_score": max_score,
        }
        if zoom_frame_path is not None:
            classification["zoom_frame_path"] = zoom_frame_path
        metadata: dict[str, object] = {"trigger": trigger}
        if parent_event_id is not None:
            metadata["parent_event_id"] = parent_event_id
        self._store.add_event(
            source_id=source_id,
            event_type="person",
            timestamp=timestamp,
            frame_path=frame_path,
            confidence=float(max_score),
            classification=classification,
            metadata=metadata,
            cluster_id=cluster_id,
        )
        return float(max_score)

    def record_edge_class(
        self,
        event_type: str,
        *,
        source_id: str,
        timestamp: datetime,
        frame_path: str,
        zoom_frame_path: str,
        count: int,
        detector: str,
        cluster_id: str,
    ) -> None:
        """Ereignis einer Edge-AI-Klasse (person/vehicle/animal), die die
        Kamera gemeldet hat; ``detector`` sagt, wer sie bestätigt hat
        (``yolo`` oder ``edge_ai``)."""
        self._store.add_event(
            source_id=source_id,
            event_type=event_type,
            timestamp=timestamp,
            frame_path=frame_path,
            confidence=1.0,
            classification={
                "detector": detector,
                "count": count,
                "zoom_frame_path": zoom_frame_path,
            },
            metadata={"trigger": "edge_ai"},
            cluster_id=cluster_id,
        )
