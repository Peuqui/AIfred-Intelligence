"""Face-Hunt-Burst nach einem Edge-AI-Personen-Trigger.

Der erste Snap nach dem Trigger ist systematisch der schlechteste Moment (PTZ
schwenkt/zoomt noch). Der Burst zieht im Takt von ``burst_interval_sec``
frische Dual-Snaps und wertet JEDEN aus, solange die Kamera die Person weiter
meldet (+ ``burst_linger_sec`` Nachlauf, unbegrenzte Gesamtdauer). Jeder Tick,
der etwas zeigt, wird als Ereignis im SELBEN Cluster gespeichert:

* Gesicht gefunden → ``face_*``-Event + Crop (Personarium),
* kein Gesicht, aber YOLO sieht eine Person → ``person``-Event,
* weder noch (Beet-Frame) → verworfen, nichts auf Disk.

So entsteht pro Vorbeigang eine durchblätterbare Bildserie („Film"), die der
Casus über den Cluster zu EINER Zeile gruppiert. Gemeldet wird über den
``BurstReport``: eine Erstmeldung nach ``burst_report_deadline_sec`` (kurze
Beschreibung, wer da ist), danach nur kurze Hinweise bei echten Neuigkeiten
(neuer Name, Band-Upgrade, mehr Personen) und zum Burst-Ende die Zusammenfassung
mit je einem Film pro Linse (``vision_film``) — den Verlauf zeigen die Bilder.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .config import VISION_BURST_MIN_DEADLINE_SEC, VISION_BURST_MIN_INTERVAL_SEC
from .frame_sources.base import Frame
from .logging_utils import log_message
from .reolink_ai import ReolinkAIError
from .vision_alerts import BurstReport
from .vision_detect import DetectionService
from .vision_edge_source import EdgeAiEventSource
from .vision_output import OutputChain
from .vision_recorder import EventRecorder, face_event_type
from .vision_snap import snap_pair
from .vision_store import VisionStore
from .vision_watch_types import WatchConfig

if TYPE_CHECKING:
    from .face_crop_store import CropResult
    from .vision_filters.face_detect import FaceDetection
    from .vision_filters.face_recognize import FaceMatch

logger = logging.getLogger(__name__)

TRIGGER = "edge_ai_burst"

# Rang des Bands für die Wahl des besten Gesichts eines Ticks.
_BAND_RANK = {"known": 2, "unsure": 1, "unknown": 0}


def _mean_max(values: list[float]) -> str:
    if not values:
        return "-"
    return f"{sum(values) / len(values):.2f}/{max(values):.2f}"


class BurstRunner:
    """Hält pro Quelle höchstens einen laufenden Burst samt Bilanz-Report."""

    def __init__(
        self,
        store: VisionStore,
        output: OutputChain,
        detect: DetectionService,
        recorder: EventRecorder,
    ) -> None:
        self._store = store
        self._output = output
        self._detect = detect
        self._recorder = recorder
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._reports: dict[str, BurstReport] = {}

    # ── Zustand ───────────────────────────────────────────────────

    def running_report(self, source_id: str) -> BurstReport | None:
        """Report des laufenden Bursts dieser Quelle (sonst ``None``). Ein
        neuer Trigger füttert ihn weiter — gleiches Vorkommnis."""
        task = self._tasks.get(source_id)
        if task is None or task.done():
            return None
        return self._reports.get(source_id)

    def start(
        self,
        source_id: str,
        config: WatchConfig,
        client: Any,
        events: EdgeAiEventSource,
        cluster_id: str,
        report: BurstReport,
        is_current: Callable[[], bool],
    ) -> bool:
        """Burst starten, falls noch keiner läuft. ``is_current`` sagt, ob die
        Watch-Session noch dieselbe ist (sonst wird der Burst verworfen)."""
        if self.running_report(source_id) is not None:
            return False
        self._reports[source_id] = report
        self._tasks[source_id] = asyncio.create_task(
            self._run(source_id, config, client, events, cluster_id, report, is_current),
            name=f"vision-burst:{source_id}",
        )
        return True

    def cancel(self, source_id: str) -> None:
        task = self._tasks.pop(source_id, None)
        if task is not None and not task.done():
            task.cancel()
        self._reports.pop(source_id, None)

    # ── Schleife ──────────────────────────────────────────────────

    @staticmethod
    def _log_send_result(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("burst report send failed: %s", task.exception())

    async def _run(
        self,
        source_id: str,
        config: WatchConfig,
        client: Any,
        events: EdgeAiEventSource,
        cluster_id: str,
        report: BurstReport,
        is_current: Callable[[], bool],
    ) -> None:
        edge = config.edge_ai
        if edge is None:
            raise ValueError("Burst ohne WatchConfig.edge_ai gestartet")
        interval = max(VISION_BURST_MIN_INTERVAL_SEC, float(config.burst_interval_sec))
        linger = max(0.0, float(config.burst_linger_sec))
        deadline = max(VISION_BURST_MIN_DEADLINE_SEC, float(config.burst_report_deadline_sec))

        started = datetime.now()
        last_active = started
        ticks = n_face = n_person = 0
        # Messung je Tick (Sekunden): Snap, Auswertung, Abstand zum vorigen Tick.
        snap_times: list[float] = []
        eval_times: list[float] = []
        periods: list[float] = []
        previous_tick_start: float | None = None
        # Laufende Bilanz-Versände: harte Referenz halten, sonst kann der
        # GC einen Task mitten in der VLM-Beschreibung einsammeln.
        pending_sends: set[asyncio.Task[None]] = set()
        try:
            while True:
                if not is_current():
                    return  # Watch neu gestartet — Burst verwerfen
                if (datetime.now() - last_active).total_seconds() >= linger:
                    break
                # Leeres Basis-Frame als Marker: schlägt der Wide-Snap fehl,
                # kommt genau dieses Objekt zurück → Tick überspringen.
                base = Frame(
                    source_id=source_id, timestamp=datetime.now(), image_bytes=b"",
                )
                tick_start = time.monotonic()
                if previous_tick_start is not None:
                    periods.append(tick_start - previous_tick_start)
                previous_tick_start = tick_start
                wide_frame, face_frame = await snap_pair(
                    client, base,
                    wide_channel=edge.channel, face_channel=edge.face_channel,
                )
                snap_done = time.monotonic()
                snap_times.append(snap_done - tick_start)
                if wide_frame is not base:
                    ticks += 1
                    try:
                        kind = await self._persist_tick(
                            source_id, config, cluster_id,
                            wide_frame, face_frame, report,
                        )
                        n_face += kind == "face"
                        n_person += kind == "person"
                    except Exception:
                        logger.exception("burst tick failed for %s", source_id)
                    eval_times.append(time.monotonic() - snap_done)
                # Erstmeldung fällig (Deadline erreicht) oder Neuigkeiten seit der
                # letzten Meldung → melden, Burst läuft weiter.
                #
                # NICHT awaiten: die Erstmeldung beschreibt die bisherigen Bilder per
                # VLM und dauert (gemessen 17,4 s bei 8 Keyframes / 15 s Deadline)
                # mehrere Burst-Ticks. Awaiten
                # hieße, den Burst genau währenddessen blind zu stellen — es
                # gingen die Frames verloren, die das Ereignis dokumentieren.
                # Überlappung verhindert der Report selbst (``is_sending``).
                if report.due(deadline) or report.has_news():
                    send_task = asyncio.create_task(report.send())
                    send_task.add_done_callback(self._log_send_result)
                    pending_sends.add(send_task)
                    send_task.add_done_callback(pending_sends.discard)
                # Kamera-Flag prüfen: solange die Person gemeldet wird, läuft
                # der Burst weiter (last_active wandert mit).
                try:
                    person_active = await events.is_active("person")
                except ReolinkAIError as e:
                    logger.warning("burst ai-state poll failed for %s: %s", source_id, e)
                else:
                    if person_active:
                        last_active = datetime.now()
                # Fester Takt: der Abstand zählt von Tick-Start zu Tick-Start,
                # nicht von Tick-Ende (sonst käme Snap + Auswertung obendrauf).
                await asyncio.sleep(max(0.0, tick_start + interval - time.monotonic()))
        except asyncio.CancelledError:
            for task in pending_sends:
                task.cancel()
            return
        finally:
            self._tasks.pop(source_id, None)
            self._reports.pop(source_id, None)
        # Laufende Zwischen-Bilanz erst zu Ende bringen: sie hält die
        # Historie, an die die Abschluss-Bilanz anknüpft — und
        # ``send_final`` kann erst danach beurteilen, was noch offen ist.
        if pending_sends:
            await asyncio.gather(*pending_sends, return_exceptions=True)
        # Zusammenfassung (nur normales Burst-Ende): Beschreibung des ganzen
        # Vorkommnisses plus je Linse ein Film aus allen Burst-Bildern.
        try:
            await report.send_final()
        except Exception:
            logger.exception("burst final report failed for %s", source_id)
        # Bilanz ins Debug-Log — im Journal ging die info-Zeile unter, und
        # genau diese Zahlen beantworten "warum nur N Bilder?".
        duration = (datetime.now() - started).total_seconds()
        log_message(
            f"🎬 burst {source_id}: {ticks} ticks → {n_face} face, "
            f"{n_person} person, {ticks - n_face - n_person} empty "
            f"({duration:.0f}s)"
        )
        log_message(
            f"⏱️ burst {source_id}: interval {interval:.2f}s, "
            f"snap {_mean_max(snap_times)}, eval {_mean_max(eval_times)}, "
            f"period {_mean_max(periods)} (mean/max s)"
        )

    # ── Ein Tick ──────────────────────────────────────────────────

    async def _persist_tick(
        self,
        source_id: str,
        config: WatchConfig,
        cluster_id: str,
        wide_frame: Frame,
        face_frame: Frame,
        report: BurstReport,
    ) -> str:
        """Einen Burst-Tick auswerten und — wenn er etwas zeigt — persistieren
        (Bildpaar über die Ausgabekette, Event im Burst-Cluster, bei Gesicht
        zusätzlich Crop + Live-Publish). Beobachtungen fließen in den
        ``report``. Returnt die Art des Ticks: ``face``, ``person`` oder ``""``
        (verworfen)."""
        detections = await self._detect.detect_faces(face_frame)

        # Personen zählen — in JEDEM Tick, auch wenn Gesichter gefunden
        # wurden. Ein Begleiter, dessen Gesicht nie gematcht wird (Hand davor,
        # abgewandt, verdeckt), taucht sonst weder im Titel noch im VLM-Prompt
        # auf; die Bilanz meldete nur den Erkannten.
        person_frame = wide_frame
        persons = await self._detect.detect_persons(wide_frame)
        if not persons and face_frame is not wide_frame:
            # Nacht-/Fern-Fall: auf dem Weitwinkel ist die Person oft zu klein
            # für den Nano-YOLO — das Tele zeigt sie größer.
            persons = await self._detect.detect_persons(face_frame)
            if persons:
                person_frame = face_frame
        report.observe_headcount(len(persons))
        # Boxen + Bild für die Galerie-Ausschnitte anbieten: die Bilanz behält
        # den Tick mit den meisten Personen und schneidet erst beim Versand
        # zu. So bekommt auch der Begleiter ohne erkanntes Gesicht ein Bild.
        report.observe_person_boxes(
            person_frame.image_bytes, [p.bbox for p in persons],
        )

        # Zweiter Blick, bevor der Tick als „nur eine Person" abgelegt wird:
        # im Vollbild ist ein Gesicht auf Distanz schnell zu klein für den
        # Detektor, im Ausschnitt der Personenbox kommt es groß genug an. Ohne
        # diesen Durchgang blieb jede Person, die dem Weitwinkel zu fern stand,
        # dauerhaft gesichtslos: kein Event, kein Crop, und damit auch nichts,
        # was das Personarium je zum Zuordnen anbieten konnte. Läuft nur, wenn
        # der erste Durchgang leer ausging (eine Inferenz pro Personenbox).
        #
        # Gesucht wird in dem Bild, aus dem die Personenboxen stammen. Bei
        # einer Dual-Lens-Kamera ist das in aller Regel das Weitwinkel,
        # während die Gesichtssuche auf dem Tele lief: das Tele zeigt nur die
        # Bildmitte, wer daneben steht (Treppe, Bildrand) taucht dort gar
        # nicht auf.
        face_source = face_frame
        if not detections and persons:
            detections = await self._detect.detect_faces_in_regions(
                person_frame, [p.bbox for p in persons],
            )
            if detections:
                # Ab hier gilt: Crop, gespeicherte Bbox und das Bild, auf dem
                # das Personarium später nachrechnet, kommen aus GENAU diesem
                # Bild — sonst findet das Nachlernen die Box nicht wieder.
                face_source = person_frame

        if not detections:
            # Kein Gesicht — zeigt der Tick wenigstens eine Person (auch
            # abgewandt)? Dann als "Film-Frame" sichern, sonst verwerfen.
            if not persons:
                return ""
            pair = await self._output.finalize_pair(
                wide_frame, face_frame, save=config.save_event_frames,
            )
            max_score = self._recorder.record_persons(
                persons,
                source_id=source_id,
                timestamp=wide_frame.timestamp,
                frame_path=pair.wide.path,
                zoom_frame_path=pair.zoom_path,
                trigger=TRIGGER,
                cluster_id=cluster_id,
            )
            report.observe_person(
                len(persons), max_score, pair.wide.path, pair.zoom_path,
            )
            return "person"

        # Alle Gesichter dieses Ticks matchen — nicht nur das beste. Sind zwei
        # Leute im Bild, braucht die Bilanz von BEIDEN den Kopfausschnitt; das
        # Event selbst hängt weiterhin am besten Gesicht (Band vor Konfidenz),
        # damit der Casus eine Zeile pro Tick behält.
        recognizer = self._detect.face_recognizer
        scored: list[tuple[FaceDetection, FaceMatch, CropResult | None]] = []
        best_key = (-1, -1.0)
        best_index = 0
        for index, candidate in enumerate(detections):
            match = recognizer.match(candidate.embedding)
            crop = self._recorder.save_face_crop(
                face_source.image_bytes, source_id, candidate, match,
            )
            scored.append((candidate, match, crop))
            rank = _BAND_RANK[match.confidence_band]
            score = float(match.similarity) if rank > 0 else float(candidate.detection_score)
            if (rank, score) > best_key:
                best_key = (rank, score)
                best_index = index
        det, match, crop = scored[best_index]
        # Bildpaar über die Ausgabekette; der Crop kommt aus dem ungestempelten
        # Frame (sauberes Gesicht).
        pair = await self._output.finalize_pair(
            wide_frame, face_frame, save=config.save_event_frames,
        )
        # Das Bild, in dem die ``bbox`` gilt — der Tele-Snap nur, wenn dort
        # auch detektiert wurde. Das Nachlernen im Personarium rechnet auf
        # GENAU diesem Bild nach; raten darf es nicht, seit ein Fund auch aus
        # dem Weitwinkel stammen kann (Ausschnitts-Durchgang oben).
        detect_frame_path = (
            pair.zoom_path if (face_source is face_frame and pair.zoom_path)
            else pair.wide.path
        )
        self._recorder.record_face(
            source_id=source_id,
            frame_timestamp=face_frame.timestamp,
            det=det,
            match=match,
            crop=crop,
            frame_path=pair.wide.path,
            zoom_frame_path=pair.zoom_path,
            detect_frame_path=detect_frame_path,
            trigger=TRIGGER,
            cluster_id=cluster_id,
        )
        # Beobachtungen in die Bilanz — JEDES Gesicht des Ticks, damit die
        # Meldung alle Namen und je Person den besten Kopfausschnitt kennt.
        for candidate, candidate_match, candidate_crop in scored:
            report.observe_face(
                face_event_type(candidate_match.confidence_band),
                candidate_match.name or "",
                float(candidate_match.similarity),
                float(candidate.detection_score),
                pair.wide.path, pair.zoom_path,
                candidate_crop.url if candidate_crop else "",
                faces_in_tick=len(detections),
                identity_key=candidate_crop.identity_key if candidate_crop else "",
            )
        return "face"
