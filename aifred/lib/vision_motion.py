"""Motion-Pfad des Vigilantia-Watchers (Kameras ohne Edge-AI).

Der MOG2-Detektor hat Bewegung gemeldet: Frame durch die Ausgabekette,
``motion``-Ereignis schreiben, dann Gesichtserkennung und YOLO-Personen-
erkennung — beide schreiben unabhängig ihr Ereignis (YOLO „Körper im Bild",
InsightFace die Identität; ein formatfüllendes Gesicht ohne ganzen Körper
bliebe sonst unerkannt).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from . import vision_alerts
from .face_crop_store import get_default_store
from .vision_detect import DetectionService
from .vision_faces import FacePipeline
from .vision_filters import MotionResult
from .vision_output import OutputChain
from .vision_recorder import EventRecorder
from .vision_store import VisionStore
from .vision_watch_types import WatchConfig

if TYPE_CHECKING:
    from .frame_sources import Frame

logger = logging.getLogger(__name__)


class MotionEventHandler:
    """Verarbeitet ein Bewegungs-Ereignis (und die kontinuierliche Gesichts-
    erkennung) auf einem Frame."""

    def __init__(
        self,
        store: VisionStore,
        output: OutputChain,
        detect: DetectionService,
        recorder: EventRecorder,
        faces: FacePipeline,
    ) -> None:
        self._store = store
        self._output = output
        self._detect = detect
        self._recorder = recorder
        self._faces = faces

    async def handle(
        self, frame: Frame, motion_result: MotionResult, config: WatchConfig,
    ) -> None:
        # SSoT-Ausgabekette; die Detektionen unten bekommen das UNgestempelte
        # detect-Frame (kein Overlay in Crops/Embeddings), Speichern und
        # Cluster laufen auf dem gestempelten Output.
        finalized = await self._output.finalize(
            frame, save=config.save_event_frames,
        )
        motion_event_id = self._recorder.record_motion(
            finalized.output,
            frame_path=finalized.path,
            area_ratio=motion_result.area_ratio,
            bbox=motion_result.bbox,
            watch_fps=config.fps,
            cluster_id=self._recorder.cluster_id_for(finalized.output),
        )
        # Für die ALERTS gilt: Gesicht zuerst; wird eins erkannt, ist das die
        # informativere Meldung und der generische Personen-Alert wird
        # unterdrückt (sonst zwei Telegram-Nachrichten mit demselben Bild).
        face_found = False
        if config.run_face_detect_on_motion and not config.face_recognition_continuous:
            face_found = await self._run_stage(
                "face", finalized.detect.source_id,
                self._faces.run(
                    finalized.detect, trigger="motion",
                    frame_path=finalized.path, motion_event_id=motion_event_id,
                ),
            )
        if config.run_person_detect_on_motion:
            await self._run_stage(
                "person", finalized.detect.source_id,
                self.run_person_detection(
                    finalized.detect, motion_event_id=motion_event_id,
                    frame_path=finalized.path, emit_alert=not face_found,
                ),
            )

    async def handle_continuous_face(
        self, frame: Frame, config: WatchConfig,
    ) -> None:
        """Kontinuierliche Gesichtserkennung (motion-unabhängig): nur
        Schwärzung, kein Stempel und kein Speichern — Detektions-Input."""
        detect_frame = (await self._output.finalize(
            frame, save=False, stamp=False,
        )).detect
        await self._faces.run(detect_frame, trigger="continuous")

    @staticmethod
    async def _run_stage(stage: str, source_id: str, work: Any) -> bool:
        """Eine Detektions-Stufe laufen lassen; ein Fehler wird mit Traceback
        gemeldet und stoppt die andere Stufe nicht."""
        try:
            return bool(await work)
        except Exception:
            logger.exception("%s detection failed for %s", stage, source_id)
            return False

    async def run_person_detection(
        self,
        frame: Frame,
        *,
        motion_event_id: int | None,
        frame_path: str,
        emit_alert: bool,
    ) -> None:
        """YOLO-Körper-Detektion. Schreibt bei Treffer ein ``person``-Event;
        der proaktive Alert (armed-gated) nur bei ``emit_alert=True`` — der
        Aufrufer unterdrückt ihn, wenn parallel ein Gesicht erkannt wurde."""
        persons = await self._detect.detect_persons(frame)
        if not persons:
            return
        cluster_id = self._recorder.cluster_id_for(frame)
        self._recorder.record_persons(
            persons,
            source_id=frame.source_id,
            timestamp=frame.timestamp,
            frame_path=frame_path,
            trigger="motion",
            cluster_id=cluster_id,
            parent_event_id=motion_event_id,
        )
        if not emit_alert:
            return
        crop_store = get_default_store()
        crop_urls = [
            url for index, person in enumerate(persons)
            if (url := crop_store.save_person_crop(
                frame_bytes=frame.image_bytes,
                bbox=person.bbox,
                source_id=frame.source_id,
                index=index + 1,
            ))
        ]
        await vision_alerts.emit_person_alert(
            source_id=frame.source_id,
            frame_path=frame_path,
            cluster_id=cluster_id,
            count=len(persons),
            timestamp=frame.timestamp,
            store=self._store,
            extra_crop_urls=crop_urls,
        )
