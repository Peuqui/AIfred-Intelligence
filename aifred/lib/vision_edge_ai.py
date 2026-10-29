"""Edge-AI-Pfad des Vigilantia-Watchers (Kameras mit On-Device-Erkennung).

Die Kamera erkennt Person/Fahrzeug/Tier selbst und ist nur der AUSLÖSER —
entschieden wird nach UNSEREN Detektoren. Dieses Modul enthält die Reaktion
auf einen Trigger (``EdgeAiHandler``) und den Zyklus, der auf die Flanken der
Ereignisquelle wartet (``run_edge_ai_cycle``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import vision_alerts
from .config import (
    EDGE_AI_CONFIRM,
    EDGE_AI_REACT,
    VISION_OVERLAY_SUFFIX_PREROLL,
    VISION_SAME_SCENE_MAX_HAMMING,
)
from .vision_burst import BurstRunner
from .vision_detect import DetectionService
from .vision_edge_source import EdgeAiEventSource
from .vision_faces import FacePipeline
from .vision_frames import LiveFrames
from .vision_output import OutputChain
from .vision_phash import hamming_distance, phash_bytes
from .vision_recorder import EventRecorder
from .vision_snap import snap_pair
from .vision_store import VisionStore
from .vision_watch_types import WatchConfig

if TYPE_CHECKING:
    from .frame_sources import Frame

logger = logging.getLogger(__name__)

TRIGGER = "edge_ai"


class EdgeAiHandler:
    """Verarbeitet einen Edge-AI-Trigger: Dual-Snap, Bestätigung durch unser
    YOLO, Ereignisse, Alerts, Vorlauf und der Start des Face-Hunt-Bursts."""

    def __init__(
        self,
        store: VisionStore,
        output: OutputChain,
        detect: DetectionService,
        recorder: EventRecorder,
        faces: FacePipeline,
        bursts: BurstRunner,
    ) -> None:
        self._store = store
        self._output = output
        self._detect = detect
        self._recorder = recorder
        self._faces = faces
        self._bursts = bursts
        # Zeitstempel des zuletzt abgelegten Vorlauf-Frames je Quelle — zwei
        # Trigger kurz nacheinander überlappen im Ringpuffer, doppelt
        # gespeichert wird nichts.
        self._preroll_saved_until: dict[str, datetime] = {}
        # pHash des Bildes, mit dem je Quelle zuletzt ein UNBESTÄTIGTER Alarm
        # gemeldet wurde. Gleicht ein neuer unbestätigter Alarm dem Bild, ist
        # es derselbe Fehlalarm in derselben leeren Szene.
        self._last_unconfirmed_phash: dict[str, int] = {}

    async def handle(
        self,
        frame: Frame,
        classes: list[str],
        config: WatchConfig,
        client: Any,
        events: EdgeAiEventSource,
        preroll_frames: list[Frame],
        is_current: Callable[[], bool],
    ) -> None:
        """``frame`` ist das RTSP-Frame zum Trigger (Rückfall-Bild, falls der
        Snap fehlschlägt). Schreibt pro bestätigter Klasse ein Ereignis, feuert
        die passenden Alerts und ergänzt — nur bei ``person`` — die
        Gesichts-Identität, die die Kamera nicht liefern kann."""
        edge = config.edge_ai
        if edge is None:
            raise ValueError("Edge-AI-Trigger ohne WatchConfig.edge_ai")
        source_id = frame.source_id
        # Synchronisierter Dual-Lens-Snap: Wide + Zoom FRISCH und parallel mit
        # EINEM gemeinsamen Zeitstempel. Das stale RTSP-Frame verpasst das
        # Subjekt oft (zeitversetzt), während der Zoom frisch war.
        wide_frame, face_frame = await snap_pair(
            client, frame,
            wide_channel=edge.channel, face_channel=edge.face_channel,
        )
        # Ausgabekette für beide Linsen. Die Detektionen (YOLO-Bestätigung)
        # laufen auf dem ungestempelten Bild.
        pair = await self._output.finalize_pair(
            wide_frame, face_frame, save=config.save_event_frames,
        )
        frame_path, zoom_frame_path = pair.wide.path, pair.zoom_path
        cluster_id = self._recorder.cluster_id_for(pair.wide.output)
        timestamp = pair.wide.output.timestamp
        self._recorder.note_trigger(source_id, timestamp)

        # Pro Klasse sagt EDGE_AI_CONFIRM, ob unser YOLO den Trigger bestätigen
        # muss (True, gegen IR-Halluzinationen) oder ob wir der Kamera glauben
        # (False, z.B. animal — Nano-YOLO ist da schwach). Alle zu bestätigenden
        # Klassen prüfen wir in EINER YOLO-Inferenz.
        confirm_needed = {c for c in classes if EDGE_AI_CONFIRM.get(c, True)}
        counts = await self._detect.count_categories(pair.wide.detect, confirm_needed)
        if pair.zoom_detect is not None:
            # Die Zoom-Linse sieht ein nahes, verwackeltes Subjekt oft schärfer als das Weitwinkelbild.
            zoom_counts = await self._detect.count_categories(pair.zoom_detect, confirm_needed)
            counts = {cls: max(count, zoom_counts.get(cls, 0)) for cls, count in counts.items()}

        def confirmed(cls: str) -> bool:
            # bestätigt = der Kamera vertraut (policy False) ODER eigenes YOLO
            # hat die Klasse gezählt (count > 0).
            return cls not in confirm_needed or counts.get(cls, 0) > 0

        def count_of(cls: str) -> int:
            # YOLO-Stückzahl bei bestätigten Klassen; bei vertrauten Klassen
            # (z.B. animal) liefert die Kamera keine Zahl → 0 (= "ohne Zahl").
            return counts.get(cls, 0) if cls in confirm_needed else 0

        def detector_of(cls: str) -> str:
            # Wer die Klasse bestätigt hat: unser YOLO, die vertraute Kamera, oder niemand
            # (Kamera meldet, YOLO findet nichts — die Meldung gilt trotzdem).
            if cls not in confirm_needed:
                return "edge_ai"
            return "yolo" if confirmed(cls) else "edge_ai_unconfirmed"

        def warn_unconfirmed(cls: str) -> None:
            logger.warning(
                "edge-ai %s for %s not confirmed by YOLO (wide + zoom) — reported as real",
                cls, source_id,
            )

        # Nur unbestätigte Alarme mit unverändertem Bild werden unterdrückt; ein
        # bestätigter oder vertrauter (animal) Alarm läuft immer durch.
        if await self._is_repeated_unconfirmed_alert(
            source_id, pair.wide.detect.image_bytes,
            unconfirmed=not any(confirmed(c) for c in classes),
        ):
            logger.info(
                "edge-ai %s for %s suppressed: unconfirmed and the scene is unchanged",
                classes, source_id,
            )
            for path in (pair.wide.path, pair.zoom_path):
                if path:
                    Path(path).unlink(missing_ok=True)
            return
        await self._persist_preroll(preroll_frames, config, cluster_id)

        # ── person ── Gesicht ZUERST (informativere Meldung als „person").
        # Bei Burst-Quellen laufen die Personen-/Gesichts-Meldungen als BILANZ
        # über den BurstReport (bestes Bild statt Trigger-Moment, alle Namen in
        # einer Meldung) — Sofort-Alerts nur noch dort, wo kein Burst läuft.
        # Läuft schon ein Burst, füttert der neue Trigger dessen Report weiter
        # (gleiches Vorkommnis).
        burst_capable = (
            "person" in classes
            and config.run_face_detect_on_motion
            and config.burst_interval_sec > 0
        )
        report = None
        if burst_capable:
            report = self._bursts.running_report(source_id) or vision_alerts.BurstReport(
                source_id, cluster_id, self._store,
            )
        face_found = False
        if "person" in classes and config.run_face_detect_on_motion:
            face_found = await self._faces.run(
                face_frame, trigger=TRIGGER, frame_path=frame_path,
                zoom_frame_path=zoom_frame_path, collect=report,
            )
        if "person" in classes:
            # Auch ohne YOLO-Bestätigung eine echte Meldung: die Kamera hat sie ausgelöst, und
            # dass YOLO nichts findet (Nacht, Bewegungsunschärfe, weit weg), heißt nicht, dass
            # niemand da war. Der Befund geht an die Meldung mit.
            person_unconfirmed = not confirmed("person")
            if person_unconfirmed:
                warn_unconfirmed("person")
            person_count = max(1, count_of("person"))
            self._recorder.record_edge_class(
                "person", source_id=source_id, timestamp=timestamp,
                frame_path=frame_path, zoom_frame_path=zoom_frame_path,
                count=person_count, detector=detector_of("person"),
                cluster_id=cluster_id,
            )
            if report is not None:
                report.observe_person(
                    person_count, 0.0, frame_path, zoom_frame_path, yolo_unconfirmed=person_unconfirmed,
                )
            elif not face_found:
                # Alert entfällt, wenn schon ein Gesicht erkannt wurde (sonst Doppel-Meldung).
                await vision_alerts.emit_person_alert(
                    source_id=source_id, frame_path=frame_path,
                    zoom_frame_path=zoom_frame_path, cluster_id=cluster_id,
                    count=person_count, timestamp=timestamp, store=self._store,
                    yolo_unconfirmed=person_unconfirmed,
                )

        # ── vehicle / animal ── Event + Alert; die Kamera-Meldung gilt auch dann, wenn
        # YOLO die Klasse nicht bestätigt (siehe person). Vertraute Klassen (animal) tragen
        # keinen Zusatz.
        for cls in ("vehicle", "animal"):
            if cls not in classes:
                continue
            unconfirmed = cls in confirm_needed and not confirmed(cls)
            if unconfirmed:
                warn_unconfirmed(cls)
            self._recorder.record_edge_class(
                cls, source_id=source_id, timestamp=timestamp,
                frame_path=frame_path, zoom_frame_path=zoom_frame_path,
                count=count_of(cls), detector=detector_of(cls),
                cluster_id=cluster_id,
            )
            await vision_alerts.emit_object_alert(
                source_id=source_id, object_type=cls, frame_path=frame_path,
                zoom_frame_path=zoom_frame_path, cluster_id=cluster_id,
                count=count_of(cls), timestamp=timestamp, store=self._store,
                yolo_unconfirmed=unconfirmed,
            )

        # ── Face-Hunt-Burst ──────────────────────────────────────────
        # Der Initial-Snap ist systematisch der schlechteste Moment (PTZ
        # schwenkt/zoomt noch) — der Burst jagt in schneller Folge nach dem
        # besten Gesicht, solange die Kamera die Person weiter meldet. Fire-
        # and-forget, max. ein Burst pro Quelle. Läuft bei jeder gemeldeten
        # Person (die Kamera-Meldung gilt auch ohne YOLO-Bestätigung).
        if report is not None:
            self._bursts.start(
                source_id, config, client, events, cluster_id, report, is_current,
            )

    async def _is_repeated_unconfirmed_alert(
        self, source_id: str, wide_jpeg: bytes, *, unconfirmed: bool,
    ) -> bool:
        """Ist dies ein unbestätigter Alarm, dessen Bild dem des letzten
        unbestätigten Alarms dieser Quelle gleicht? Ein bestätigter Alarm
        verwirft die Referenz (die Szene hat sich verändert)."""
        if not unconfirmed:
            self._last_unconfirmed_phash.pop(source_id, None)
            return False
        current = await asyncio.to_thread(phash_bytes, wide_jpeg)
        reference = self._last_unconfirmed_phash.get(source_id)
        if reference is not None and hamming_distance(current, reference) <= VISION_SAME_SCENE_MAX_HAMMING:
            return True
        self._last_unconfirmed_phash[source_id] = current
        return False

    async def _persist_preroll(
        self, frames: list[Frame], config: WatchConfig, cluster_id: str,
    ) -> None:
        """Vorlauf-Frames eines Triggers als Ereignisse im Cluster des Triggers
        ablegen (Ausgabekette, ohne Auswertung — die Auflösung des RTSP-
        Substreams reicht nicht für Gesichter, zeigt aber, was vor dem
        Auslösen im Bild war). Art ``motion`` mit
        ``metadata.trigger = edge_ai_preroll``."""
        for frame in frames:
            saved_until = self._preroll_saved_until.get(frame.source_id)
            if saved_until is not None and frame.timestamp <= saved_until:
                continue
            self._preroll_saved_until[frame.source_id] = frame.timestamp
            finalized = await self._output.finalize(
                frame, label_suffix=VISION_OVERLAY_SUFFIX_PREROLL,
                save=config.save_event_frames,
            )
            self._store.add_event(
                source_id=frame.source_id,
                event_type="motion",
                timestamp=finalized.output.timestamp,
                frame_path=finalized.path,
                confidence=0.0,
                classification={},
                metadata={"trigger": "edge_ai_preroll"},
                cluster_id=cluster_id,
            )


async def run_edge_ai_cycle(
    *,
    source_id: str,
    config: WatchConfig,
    events: EdgeAiEventSource,
    client: Any,
    live: LiveFrames,
    recorder: EventRecorder,
    handler: EdgeAiHandler,
    is_current: Callable[[], bool],
) -> None:
    """Wartet auf steigende Flanken der Ereignisquelle und löst die Edge-AI-
    Auswertung aus. Ersetzt MOG2/YOLO für intelligente Kameras — die Roh-
    Detektion macht die Kamera selbst."""
    edge = config.edge_ai
    if edge is None:
        raise ValueError("Edge-AI-Zyklus ohne WatchConfig.edge_ai")
    # Zeitstempel des zuletzt befeuerten Frames — gegen eingefrorene RTSP-
    # Streams: friert der Stream ein, bleibt ``live.latest`` stehen (gleicher
    # Timestamp), während die Kamera-Edge-AI nachts im IR-Bild weiter Person/
    # Tier flackert. Ohne Guard würde AIfred dasselbe Standbild dutzendfach
    # speichern + Fehlalarme feuern.
    last_fired_ts: datetime | None = None
    while True:
        # Klassen, auf die AIfred nicht reagiert (EDGE_AI_REACT), fallen vor Drossel,
        # Settle und Snap heraus — die Kamera-Meldung löst dann nichts aus.
        rising = [cls for cls in await events.wait_rising() if EDGE_AI_REACT[cls]]
        if not rising:
            continue
        if recorder.is_throttled(source_id, config.min_event_interval_sec):
            continue
        # Vorlauf VOR dem Settle einfrieren — danach ist er schon um die
        # Wartezeit gewandert.
        preroll_frames = live.preroll_snapshot()
        # Settle: dem PTZ-Kopf Zeit geben, das Subjekt zu zentrieren — dann das
        # frischeste Frame ziehen.
        if edge.settle_sec > 0:
            await asyncio.sleep(edge.settle_sec)
        frame = live.latest
        if frame is None:
            continue
        if frame.timestamp == last_fired_ts:
            # Eingefrorener Stream: derselbe Frame wie beim letzten Feuern →
            # kein echtes Ereignis, skip.
            logger.warning(
                "edge-ai: eingefrorenes Frame für %s (Stream steht?) — "
                "Trigger verworfen", source_id,
            )
            continue
        last_fired_ts = frame.timestamp
        try:
            await handler.handle(
                frame, rising, config, client, events, preroll_frames, is_current,
            )
        except Exception:
            logger.exception("edge-ai event handling failed for %s", source_id)

