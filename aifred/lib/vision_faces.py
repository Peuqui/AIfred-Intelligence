"""Gesichtserkennung eines Frames mit Ereignis-Aufzeichnung und Meldung.

Gemeinsamer Kern für den Motion-Pfad, die kontinuierliche Gesichtserkennung
und den Edge-AI-Pfad: detektieren, gegen die Embedding-DB matchen, pro Gesicht
ein Ereignis schreiben und die Treffer je Band (bekannt / unsicher / unbekannt)
zu EINER Meldung bündeln.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import vision_alerts
from .vision_detect import DetectionService
from .vision_recorder import EventRecorder, face_event_type
from .vision_store import VisionStore

if TYPE_CHECKING:
    from .frame_sources import Frame

# Reihenfolge der Meldungen je Band.
_BANDS = ("face_known", "face_unsure", "face_unknown")


class FacePipeline:
    """Detektion + Recognition + Aufzeichnung + Meldung für ein Frame."""

    def __init__(
        self, store: VisionStore, detect: DetectionService, recorder: EventRecorder,
    ) -> None:
        self._store = store
        self._detect = detect
        self._recorder = recorder

    async def run(
        self,
        frame: Frame,
        *,
        trigger: str,
        frame_path: str = "",
        zoom_frame_path: str = "",
        motion_event_id: int | None = None,
        collect: vision_alerts.BurstReport | None = None,
    ) -> bool:
        """Returnt ``True``, wenn mindestens ein Gesicht erkannt wurde (der
        Aufrufer unterdrückt dann den generischen Personen-Alert).
        ``collect`` (BurstReport): Beobachtungen dorthin statt Sofort-Alerts —
        die Burst-Bilanz meldet gebündelt. Detektor-Fehler werden nicht
        verschluckt, sondern an den Aufrufer gereicht."""
        source_id = frame.source_id
        detections = await self._detect.detect_faces(frame)
        if not detections:
            return False

        recognizer = self._detect.face_recognizer
        cluster_id = self._recorder.cluster_id_for(frame)
        # Pro Band aggregieren: ALLE Gesichter eines Vorkommnisses werden zu
        # EINER Meldung je Band zusammengefasst (nicht pro Gesicht einzeln,
        # sonst dedupliziert der Dispatcher gleichartige weg → nur ein Name).
        # DB-Events bleiben pro Gesicht.
        # ``known_named`` / ``unsure_named``: Name → beste Similarity des
        # Vorkommnisses (Insertion-Order = Erkennungsfolge), Grundlage der
        # Pro-Name-Konfidenz im Alert-Titel ("Peuqui (91 %), Anna (87 %)").
        # ``band_sims``: alle Similarity-Werte je Band — Konfidenz-Spanne für
        # Titel OHNE Namen ("2 unsichere Erkennungen (55–60 %)").
        known_named: dict[str, float] = {}
        unsure_named: dict[str, float] = {}
        band_count = {band: 0 for band in _BANDS}
        band_crop = {band: "" for band in _BANDS}
        band_sims: dict[str, list[float]] = {band: [] for band in _BANDS}
        for det in detections:
            match = recognizer.match(det.embedding)
            event_type = face_event_type(match.confidence_band)
            crop = self._recorder.save_face_crop(
                frame.image_bytes, source_id, det, match,
            )
            crop_url = crop.url if crop else ""
            band_count[event_type] += 1
            band_sims[event_type].append(float(match.similarity))
            if not band_crop[event_type]:
                band_crop[event_type] = crop_url
            if match.name:
                if event_type == "face_known":
                    named = known_named
                elif event_type == "face_unsure":
                    named = unsure_named
                else:
                    named = None
                if named is not None:
                    named[match.name] = max(
                        named.get(match.name, 0.0), float(match.similarity),
                    )
            self._recorder.record_face(
                source_id=source_id,
                frame_timestamp=frame.timestamp,
                det=det,
                match=match,
                crop=crop,
                frame_path=frame_path,
                zoom_frame_path=zoom_frame_path,
                # Bild, in dem die ``bbox`` gilt: das Frame, das der Aufrufer
                # in die Erkennung gegeben hat — bei Dual-Lens der Tele-Snap,
                # sonst das Vollbild.
                detect_frame_path=zoom_frame_path or frame_path,
                trigger=trigger,
                cluster_id=cluster_id,
                parent_event_id=motion_event_id,
            )
            if collect is not None:
                collect.observe_face(
                    event_type, match.name or "", float(match.similarity),
                    float(det.detection_score), frame_path, zoom_frame_path,
                    crop_url, faces_in_tick=len(detections),
                    identity_key=crop.identity_key if crop else "",
                )

        # Burst-Pfad: Beobachtungen sind im Report gelandet — die Bilanz
        # meldet gebündelt, hier keine Sofort-Alerts.
        if collect is not None:
            return True

        # Eine Meldung pro Band, das im Vorkommnis vorkam. So werden ALLE
        # bekannten Namen genannt (offen, ungedeckelt) und Unbekannte/
        # Unsichere gezählt, statt dass die Dedup gleichartige Gesichter auf
        # eine Meldung mit einem Namen zusammenstreicht.
        for band, named in (
            ("face_known", known_named),
            ("face_unsure", unsure_named),
            ("face_unknown", {}),
        ):
            if band_count[band] <= 0:
                continue
            # Benannt: names + korrespondierende Pro-Name-Similarities
            # (gleiche Reihenfolge). Namenlos: alle Band-Werte → Spanne.
            await vision_alerts.emit_face_alert(
                source_id=source_id,
                event_type=band,
                frame_path=frame_path,
                zoom_frame_path=zoom_frame_path,
                crop_url=band_crop[band],
                cluster_id=cluster_id,
                names=list(named),
                count=band_count[band],
                similarities=list(named.values()) if named else band_sims[band],
                timestamp=frame.timestamp,
                store=self._store,
            )
        return True
