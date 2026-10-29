"""Erkennungsdienst des Vigilantia-Watchers: Gesichts-, Personen- und
Kategorie-Detektor sowie der Gesichts-Recognizer.

Die Detektoren sind schwergewichtige Singletons und werden erst beim ersten
Gebrauch erzeugt. Der Recognizer ist EINE Instanz pro Dienst — Änderungen am
Enrollment-Bestand erreichen ihn über ``bump_enrollment_epoch`` (lazy
Reload beim nächsten Match).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .config import (
    EDGE_AI_COCO_MAP,
    VISION_FACE_THRESHOLD_KNOWN,
    VISION_FACE_THRESHOLD_UNSURE,
)
from .vision_filters.face_detect import (
    FaceDetection,
    FaceDetector,
    get_default_detector,
)
from .vision_filters.face_recognize import FaceRecognizer
from .vision_filters.person_detect import (
    PersonDetection,
    PersonDetector,
)
from .vision_filters.person_detect import (
    get_default_detector as get_default_person_detector,
)
from .vision_settings import load_vision_settings
from .vision_store import VisionStore

if TYPE_CHECKING:
    from .frame_sources import Frame


class DetectionService:
    """Lazy gehaltene Detektoren + Recognizer für die Watch-Pipelines."""

    def __init__(
        self,
        store: VisionStore,
        *,
        face_detector: FaceDetector | None = None,
        face_recognizer: FaceRecognizer | None = None,
        person_detector: PersonDetector | None = None,
    ) -> None:
        self._store = store
        self._face_detector = face_detector
        self._face_recognizer = face_recognizer
        self._person_detector = person_detector

    @property
    def face_detector(self) -> FaceDetector:
        if self._face_detector is None:
            self._face_detector = get_default_detector()
        return self._face_detector

    @property
    def person_detector(self) -> PersonDetector:
        if self._person_detector is None:
            self._person_detector = get_default_person_detector()
        return self._person_detector

    @property
    def face_recognizer(self) -> FaceRecognizer:
        """Recognizer mit den Schwellen aus den Plugin-Einstellungen
        (``face_recognition``); fehlende Schlüssel nehmen die Defaults aus
        ``config.py``, eine unlesbare Einstellungsdatei ist ein Fehler."""
        if self._face_recognizer is None:
            section = load_vision_settings().get("face_recognition") or {}
            self._face_recognizer = FaceRecognizer(
                self._store,
                threshold_known=float(
                    section.get("threshold_known", VISION_FACE_THRESHOLD_KNOWN)
                ),
                threshold_unsure=float(
                    section.get("threshold_unsure", VISION_FACE_THRESHOLD_UNSURE)
                ),
            )
        return self._face_recognizer

    async def detect_faces(self, frame: Frame) -> list[FaceDetection]:
        return await asyncio.to_thread(self.face_detector.detect, frame)

    async def detect_faces_in_regions(
        self, frame: Frame, boxes: list[tuple[int, int, int, int]],
    ) -> list[FaceDetection]:
        """Gesichtssuche im Ausschnitt jeder Box (z. B. der Personenboxen)."""
        return await asyncio.to_thread(
            self.face_detector.detect_in_regions, frame, boxes,
        )

    async def detect_persons(self, frame: Frame) -> list[PersonDetection]:
        return await asyncio.to_thread(self.person_detector.detect, frame)

    async def count_categories(
        self, frame: Frame, wanted: set[str],
    ) -> dict[str, int]:
        """YOLO-Zählung der Edge-AI-Klassen in EINER Inferenz: pro Kategorie
        die Anzahl (0 = nicht bestätigt). Dient als Gate (count > 0 =
        bestätigt) und liefert die Stückzahl für den Alert. Ein Fehler des
        Detektors wird nicht verschluckt, sondern an den Aufrufer gereicht."""
        if not wanted:
            return {}
        return await asyncio.to_thread(
            self.person_detector.detect_category_counts,
            frame, EDGE_AI_COCO_MAP, wanted,
        )
