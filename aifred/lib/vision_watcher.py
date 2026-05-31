"""Watch-Mode-Supervisor — kontinuierliche Pipeline pro Source.

Eine ``VisionWatcher``-Instanz hält pro aktiver Source eine eigene
asyncio-Task am Laufen: die Source streamt Frames, der Watcher
verarbeitet sie durch Motion-Filter, optional Face-Detect+Recognize,
und schreibt Events ins ``VisionStore``. Frames werden parallel auf
den ``FrameBus`` publisht, damit andere Konsumenten (Browser-Live-
Preview, VLM-Analyzer) sie ebenfalls erhalten.

Public API: ``start(source_id, **opts)``, ``stop(source_id)``,
``list_active()``, ``is_running(source_id)``, ``shutdown()``.

Lifecycle: pro Prozess eine ``VisionWatcher``-Instanz, am besten
über ``get_default_watcher()`` geholt. Tests können eigene
Instanzen mit Test-Store/Bus erzeugen.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from .config import DATA_DIR
from .frame_bus import FrameBus, get_default_bus
from .frame_sources import get as get_source
from .vision_filters import MotionDetector
from .vision_filters.face_detect import FaceDetector, get_default_detector
from .vision_filters.face_recognize import FaceRecognizer
from .vision_store import VisionStore

if TYPE_CHECKING:
    from .frame_sources import Frame, FrameSource

logger = logging.getLogger(__name__)

_DEFAULT_FRAMES_DIR = DATA_DIR / "vision" / "frames"

# Erster Call nach Watch-Start: noch keine History, also keine
# „unverändert"-Option im Prompt — die VLM muss die Szene voll
# beschreiben statt zu vermuten was sich „nicht" geändert haben
# könnte.
_FIRST_CONTINUOUS_PROMPT = (
    "Beschreibe in einem Satz, was du gerade siehst."
)

# Folge-Calls: „unverändert" als Kurz-Antwort erlaubt, damit der
# Teleprompter als Delta-Narration liest und sich nicht wiederholt.
_DELTA_CONTINUOUS_PROMPT = (
    "Beschreibe in einem Satz, was du gerade siehst. Wenn sich "
    "gegenüber dem letzten Frame nichts wesentlich geändert hat, "
    "sage knapp 'unverändert'."
)


@dataclass
class WatchConfig:
    """Per-Source Konfiguration für eine Watch-Session.

    Werte werden vom Tool-Plugin aus ``settings.json`` (Sektion
    ``watch``) befüllt und beim ``start()`` überschrieben — Caller können
    pro Aufruf abweichen (z.B. höhere fps, Face-Detect on/off).
    """

    fps: float = 1.0
    motion_min_area_ratio: float = 0.02
    motion_history_frames: int = 500
    motion_var_threshold: float = 16.0
    motion_warmup_frames: int = 10
    min_event_interval_sec: float = 5.0
    save_event_frames: bool = True
    run_face_detect_on_motion: bool = True
    # ── VLM-Analyse pro Motion-Event ─────────────────────────────────
    # Wenn aktiviert, wird das Motion-Frame zusätzlich an die VLM gegeben
    # und der beschreibende Text als ``vlm_analysis``-Event ins Store
    # geschrieben. Das ist der Mechanismus, der das Teleprompter-Feld
    # im Live-Preview-Popup mit Inhalt füllt.
    run_vlm_on_motion: bool = False
    # If True the watcher calls the VLM on EVERY tick (subject to
    # vlm_cooldown_sec), not just on motion events. Used by the
    # live-preview teleprompter — gives a continuous narration of
    # what the cam sees. Combined with run_vlm_on_motion: motion path
    # still emits "motion" events and triggers face-recog, the
    # continuous path emits "vlm_analysis" events independently.
    run_vlm_continuous: bool = False
    vlm_prompt: str = ""           # Override für default_prompt aus settings.json
    vlm_cooldown_sec: float = 10.0  # Mindestabstand zwischen VLM-Calls
    # NB: Kein eigenes vlm_model-Feld mehr — das Modell wird einheitlich
    # aus plugins/tools/vision/settings.json gelesen. Der Live-Vorschau-
    # Popup ändert dieses Setting direkt (SSOT mit dem Settings-Modal).
    # Wenn True läuft die Face-Detection auf JEDEM Frame (gedrosselt
    # durch ``min_event_interval_sec``), nicht nur bei motion. Sinnvoll
    # für Schreibtisch-Setups wo eine Person ruhig vor der Cam sitzt
    # und vom Motion-Detector nicht mehr getriggert wird.
    face_recognition_continuous: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class WatchStatus:
    """Read-only Snapshot über eine laufende oder beendete Watch-Session."""

    source_id: str
    running: bool
    started_at: datetime
    fps: float
    frames_seen: int
    motion_events: int
    face_events: int
    last_event_at: datetime | None
    last_error: str | None


class VisionWatcher:
    """Manages background watch tasks per source."""

    def __init__(
        self,
        store: VisionStore,
        *,
        bus: FrameBus | None = None,
        face_detector: FaceDetector | None = None,
        face_recognizer: FaceRecognizer | None = None,
        frames_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._bus = bus or get_default_bus()
        # Lazy injection: detector and recognizer are only initialised
        # when the first frame triggers face_detect.
        self._explicit_face_detector = face_detector
        self._explicit_face_recognizer = face_recognizer
        self._frames_dir = frames_dir or _DEFAULT_FRAMES_DIR
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._configs: dict[str, WatchConfig] = {}
        self._statuses: dict[str, WatchStatus] = {}
        self._lock = Lock()
        # Per-source generation counter — wird bei jedem ``start()``
        # hochgezählt. Fire-and-forget VLM-Tasks (``_maybe_run_continuous_vlm``)
        # capturen den Wert beim Eintritt; passt der beim Abschluss
        # nicht zur aktuellen Generation, wird ihr Ergebnis verworfen
        # statt in den frisch geleerten History-Ring zu kippen.
        self._watch_generation: dict[str, int] = {}
        # In-Flight-Sentinel: solange ein VLM-Call für eine Source
        # läuft, lassen wir keinen zweiten parallel ran. Sonst rauschen
        # bei hoher Stream-fps mehrere fire-and-forget Tasks
        # gleichzeitig durch den Cooldown-Check, BEVOR der erste
        # ``_last_vlm_at`` gesetzt hat — alle sehen ``last=None``,
        # alle starten den VLM, der Anfang füllt sich mit
        # duplizierten Volltext-Beschreibungen.
        self._vlm_in_flight: set[str] = set()
        # Per-source Zonen-Maske (oder None). Im blackout-Modus werden die
        # Pixel der Zone vor Speichern/Face/VLM geschwärzt (DSGVO).
        self._zone_masks: dict[str, Any] = {}
        # Per-source MotionDetector-Instanz — für Live-Reload der Maske
        # (Editor-Save) ohne die Watch-Schleife neu zu starten.
        self._detectors: dict[str, Any] = {}
        # Per-source timestamp of the last VLM call; used to throttle
        # so we don't fire vlm_cooldown_sec → see _maybe_run_vlm.
        self._last_vlm_at: dict[str, datetime] = {}
        # Per-source ring buffer of the last N (timestamp, description)
        # tuples — fed back into the next continuous-VLM prompt so the
        # model can write a delta-update instead of starting fresh.
        from collections import deque
        from .config import VISION_VLM_CONTINUOUS_HISTORY
        self._vlm_history: dict[str, deque[tuple[datetime, str]]] = {}
        self._vlm_history_size = int(VISION_VLM_CONTINUOUS_HISTORY)

    # ── Public API ────────────────────────────────────────────────

    async def start(self, source_id: str, config: WatchConfig) -> WatchStatus:
        """Start watching a registered source. Idempotent — if already
        running, returns the existing status without restarting.

        Eine neue Session startet immer mit frischem VLM-Kontext: die
        Ring-Buffer-History UND die Cooldown-Marke werden geleert,
        damit der erste Call die Szene voll beschreibt statt
        gegen alte „Bisherige Beobachtungen" zu vergleichen.
        """
        with self._lock:
            existing = self._tasks.get(source_id)
            if existing is not None and not existing.done():
                return self._statuses[source_id]
            source = get_source(source_id)
            if source is None:
                raise ValueError(f"unknown source: {source_id}")
            # NOTE: deliberately NOT calling source.is_available() —
            # that opens a cv2.VideoCapture probe which races against
            # any running live-preview stream for the V4L2 device.
            # The watch loop's stream() call has retry-open logic and
            # will fail cleanly there if the cam truly can't be opened.
            self._configs[source_id] = config
            # Frischer Start: weder VLM-History noch Cooldown-Marke
            # aus einer vorigen Session sollen die neue beeinflussen.
            # Generation-Bump invalidiert noch laufende VLM-Tasks der
            # alten Session, damit ihr Resultat den frischen Ring nicht
            # nachträglich wieder mit altem Kontext füllt.
            self._watch_generation[source_id] = (
                self._watch_generation.get(source_id, 0) + 1
            )
            self._vlm_history.pop(source_id, None)
            self._last_vlm_at.pop(source_id, None)
            self._statuses[source_id] = WatchStatus(
                source_id=source_id,
                running=True,
                started_at=datetime.now(),
                fps=config.fps,
                frames_seen=0,
                motion_events=0,
                face_events=0,
                last_event_at=None,
                last_error=None,
            )
            task = asyncio.create_task(
                self._watch_loop(source, config), name=f"vision-watch:{source_id}"
            )
            self._tasks[source_id] = task
        return self._statuses[source_id]

    async def stop(self, source_id: str) -> bool:
        """Cancel a running watch. Returns False if nothing was running."""
        with self._lock:
            task = self._tasks.get(source_id)
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        with self._lock:
            self._tasks.pop(source_id, None)
            self._detectors.pop(source_id, None)
            self._zone_masks.pop(source_id, None)
            if source_id in self._statuses:
                self._statuses[source_id].running = False  # type: ignore[misc]
        return True

    def reload_zone_mask(self, source_id: str) -> None:
        """Zonen-Maske einer Quelle live aus den Settings nachladen und in
        Detektor + Blackout-Pfad übernehmen — ohne die Watch-Schleife neu
        zu starten. Greift sofort beim nächsten Frame. No-op wenn die
        Quelle nicht läuft (beim nächsten Start wird sie ohnehin frisch
        geladen)."""
        from .vision_filters.zone_mask import load_zone_mask
        zm = load_zone_mask(source_id)
        self._zone_masks[source_id] = zm
        det = self._detectors.get(source_id)
        if det is not None:
            det.set_zone_mask(zm)

    def reload_motion_min(self, source_id: str, ratio: float) -> None:
        """Bewegungs-Schwellwert einer laufenden Quelle live setzen (Slider
        in den Vision-Settings), ohne Re-Arm. No-op wenn die Quelle nicht
        läuft (beim nächsten Start wird der Wert ohnehin frisch geladen)."""
        det = self._detectors.get(source_id)
        if det is not None:
            det.set_min_area_ratio(ratio)

    def is_running(self, source_id: str) -> bool:
        task = self._tasks.get(source_id)
        return task is not None and not task.done()

    def list_active(self) -> list[WatchStatus]:
        """Snapshot of all currently-running watch sessions."""
        return [s for sid, s in self._statuses.items() if self.is_running(sid)]

    def get_status(self, source_id: str) -> WatchStatus | None:
        return self._statuses.get(source_id)

    async def shutdown(self) -> None:
        """Stop all running watches. Call before process exit."""
        with self._lock:
            tasks = list(self._tasks.items())
        for sid, _ in tasks:
            await self.stop(sid)

    # ── Internals ─────────────────────────────────────────────────

    def _get_face_detector(self) -> FaceDetector:
        if self._explicit_face_detector is not None:
            return self._explicit_face_detector
        return get_default_detector()

    def _get_face_recognizer(self) -> FaceRecognizer:
        if self._explicit_face_recognizer is not None:
            return self._explicit_face_recognizer
        # Thresholds aus dem Plugin-Setting lesen — Default-Constructor
        # nimmt sonst nur die Code-Defaults, ungeachtet was im
        # settings.json steht.
        try:
            import json
            from pathlib import Path
            cfg_path = (
                Path(__file__).parent.parent
                / "plugins/tools/vision/settings.json"
            )
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f) or {}
            fr = cfg.get("face_recognition") or {}
            t_known = float(fr.get("threshold_known", 0.6))
            t_unsure = float(fr.get("threshold_unsure", 0.5))
        except Exception:  # noqa: BLE001
            t_known, t_unsure = 0.6, 0.5
        return FaceRecognizer(
            self._store,
            threshold_known=t_known,
            threshold_unsure=t_unsure,
        )

    async def _watch_loop(self, source: "FrameSource", config: WatchConfig) -> None:
        """Per-source watch loop with split frame-consumer / VLM-cycle.

        Frühere Variante hatte fire-and-forget VLM-Tasks aus dem
        Frame-Stream → Backlog möglich, weil Frames in der Queue
        warteten während der VLM-Call läuft. Jetzt:

        * **frame_consumer**: subscribt am FrameHub, hält ``_latest_frame``
          synchron aktuell, fährt Motion-Detection auf jedem Frame.
        * **vlm_cycle**: pull-basiert — wenn die VLM fertig ist, holt
          sich der Cycle den AKTUELL frischesten Frame aus
          ``_latest_frame`` und analysiert. Damit ist die Pipeline
          immer am Live-Bild, ohne Frame-Backlog.

        ``vlm_cooldown_sec`` wirkt jetzt als reine Untergrenze
        (Mindest-Abstand zwischen Calls), nicht mehr als Bremse.
        """
        from .frame_hub import get_default_hub
        from .vision_filters.zone_mask import load_zone_mask
        source_id = source.source_id
        zone_mask = load_zone_mask(source_id)
        self._zone_masks[source_id] = zone_mask
        motion = MotionDetector(
            history=config.motion_history_frames,
            var_threshold=config.motion_var_threshold,
            min_area_ratio=config.motion_min_area_ratio,
            warmup_frames=config.motion_warmup_frames,
            zone_mask=zone_mask,
        )
        self._detectors[source_id] = motion
        hub = get_default_hub()

        # Shared state zwischen consumer + vlm-cycle. asyncio ist
        # single-threaded — Dict-Writes sind atomar.
        latest: dict[str, Any] = {"frame": None, "seq": 0}
        new_frame_evt = asyncio.Event()

        async def frame_consumer() -> None:
            seq = 0
            async for frame in hub.subscribe(
                source, name="watcher", fps=config.fps,
            ):
                seq += 1
                latest["frame"] = frame
                latest["seq"] = seq
                new_frame_evt.set()
                self._statuses[source_id].frames_seen += 1  # type: ignore[misc]

                motion_result = motion.process(frame)
                if not motion_result.motion:
                    continue
                last_evt = self._statuses[source_id].last_event_at
                if last_evt is not None:
                    delta = (datetime.now() - last_evt).total_seconds()
                    if delta < config.min_event_interval_sec:
                        continue
                await self._handle_motion_event(frame, motion_result, config)

        async def vlm_cycle() -> None:
            if not config.run_vlm_continuous:
                return
            last_seq_processed = -1
            while True:
                # Auf neuen Frame warten — nicht polling, sondern via
                # Event vom Consumer.
                if latest["seq"] == last_seq_processed:
                    new_frame_evt.clear()
                    await new_frame_evt.wait()
                # Mindestens vlm_cooldown_sec zwischen Calls einhalten
                # (Schutz vor GPU-Überlast, wenn die VLM ungewöhnlich
                # schnell antwortet).
                last_call = self._last_vlm_at.get(source_id)
                if last_call is not None:
                    delta = (datetime.now() - last_call).total_seconds()
                    if delta < config.vlm_cooldown_sec:
                        await asyncio.sleep(config.vlm_cooldown_sec - delta)
                # Frischestes Frame holen — kann zwischen wait() und
                # hier wieder neuer geworden sein, deshalb erneut lesen.
                frame = latest["frame"]
                last_seq_processed = latest["seq"]
                if frame is None:
                    continue
                frame = self._blackout_frame(frame)
                my_gen = self._watch_generation.get(source_id, 0)
                self._last_vlm_at[source_id] = datetime.now()
                try:
                    await self._run_continuous_vlm_inner(
                        frame, config, source_id, my_gen
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "continuous-VLM cycle error for %s: %s", source_id, e
                    )

        async def face_cycle() -> None:
            """Continuous Face-Recognition (motion-unabhängig). Wird nur
            gestartet wenn ``face_recognition_continuous=True``. Läuft
            analog zum vlm_cycle: wartet auf neuen Frame, drosselt
            durch ``min_event_interval_sec``, ruft die face-Detection
            direkt — kein Motion-Detector dazwischen."""
            if not config.face_recognition_continuous:
                return
            if not config.run_face_detect_on_motion:
                # Wenn Face-Recognition komplett aus ist, gibt's auch
                # keinen continuous-Modus.
                return
            last_face_at: datetime | None = None
            last_seq_processed = -1
            while True:
                if latest["seq"] == last_seq_processed:
                    new_frame_evt.clear()
                    await new_frame_evt.wait()
                # Throttle gegen GPU-Überlast.
                if last_face_at is not None:
                    delta = (datetime.now() - last_face_at).total_seconds()
                    if delta < config.min_event_interval_sec:
                        await asyncio.sleep(
                            config.min_event_interval_sec - delta
                        )
                frame = latest["frame"]
                last_seq_processed = latest["seq"]
                if frame is None:
                    continue
                frame = self._blackout_frame(frame)
                last_face_at = datetime.now()
                try:
                    await self._run_face_detection(frame, config, motion_event_id=None)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "continuous face cycle error for %s: %s", source_id, e
                    )

        try:
            await asyncio.gather(frame_consumer(), vlm_cycle(), face_cycle())
        except asyncio.CancelledError:
            logger.info("Watch loop cancelled for %s", source_id)
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("Watch loop crashed for %s", source_id)
            self._statuses[source_id].last_error = str(e)  # type: ignore[misc]
            self._statuses[source_id].running = False  # type: ignore[misc]

    def _blackout_frame(self, frame: "Frame") -> "Frame":
        """DSGVO-Schwärzung: hat die Quelle eine ``blackout``-Maske, werden
        die Pixel der Zone geschwärzt und ein Frame mit neuen Bytes zurück-
        gegeben — Speichern/Face/VLM sehen dann nie den öffentlichen Raum.
        Ohne blackout-Maske bleibt der Frame unverändert (kein Overhead)."""
        zm = self._zone_masks.get(frame.source_id)
        if zm is None or not zm.blacks_out:
            return frame
        import cv2
        import numpy as np
        from dataclasses import replace
        arr = cv2.imdecode(
            np.frombuffer(frame.image_bytes, np.uint8), cv2.IMREAD_COLOR
        )
        if arr is None:
            return frame
        ok, buf = cv2.imencode(
            ".jpg", zm.blackout(arr), [cv2.IMWRITE_JPEG_QUALITY, 90]
        )
        if not ok:
            return frame
        return replace(frame, image_bytes=buf.tobytes())

    async def _handle_motion_event(
        self,
        frame: "Frame",
        motion_result: Any,
        config: WatchConfig,
    ) -> None:
        source_id = frame.source_id
        frame = self._blackout_frame(frame)
        frame_path = ""
        if config.save_event_frames:
            frame_path = await asyncio.to_thread(self._save_frame, frame)
        motion_event_id = self._store.add_event(
            source_id=source_id,
            event_type="motion",
            timestamp=frame.timestamp,
            frame_path=frame_path,
            classification={
                "area_ratio": motion_result.area_ratio,
                "bbox": list(motion_result.bbox) if motion_result.bbox else None,
            },
            confidence=float(motion_result.area_ratio),
            metadata={"watch_fps": config.fps},
        )
        self._statuses[source_id].motion_events += 1  # type: ignore[misc]
        self._statuses[source_id].last_event_at = frame.timestamp  # type: ignore[misc]

        if not config.run_face_detect_on_motion:
            return
        # Im motion-getriggerten Pfad: continuous-Modus läuft separat
        # via face_cycle — hier nicht doppelt feuern.
        if config.face_recognition_continuous:
            return
        await self._run_face_detection(frame, config, motion_event_id=motion_event_id, frame_path=frame_path)

    async def _run_face_detection(
        self,
        frame: "Frame",
        config: WatchConfig,
        *,
        motion_event_id: int | None = None,
        frame_path: str = "",
    ) -> None:
        """Eigentliche Face-Detection + Recognition + Event-Publishing.
        Wird sowohl aus dem motion-getriggerten Pfad als auch aus dem
        Continuous-face_cycle gerufen. ``motion_event_id`` ist nur
        gesetzt wenn der Aufruf aus einem motion-Event kam — sonst
        ``None`` (continuous-Detection ohne Motion-Bezug)."""
        source_id = frame.source_id
        try:
            detector = self._get_face_detector()
            detections = await asyncio.to_thread(detector.detect, frame)
        except Exception as e:  # noqa: BLE001
            logger.warning("face_detect failed for %s: %s", source_id, e)
            return
        if not detections:
            return

        recognizer = self._get_face_recognizer()
        recognizer.invalidate()  # pick up any newly-enrolled faces
        from .face_crop_store import get_default_store
        from .vision_event_bus import publish_vlm_event
        import base64 as _base64
        crop_store = get_default_store()
        for det in detections:
            match = recognizer.match(det.embedding)
            event_type = (
                "face_known"
                if match.confidence_band == "known"
                else "face_unknown"
                if match.confidence_band == "unknown"
                else "face_unsure"
            )
            # Crop ZUERST speichern, damit ``crop_url`` mit ins DB-Event
            # geht — das Personarium-Modal greift später per
            # ``SELECT … classification->>'crop_url' FROM events`` auf
            # den letzten Crop pro Identity zu.
            crop_result = crop_store.save(
                frame_bytes=frame.image_bytes,
                bbox=tuple(int(v) for v in det.bbox),  # type: ignore[arg-type]
                source_id=source_id,
                event_type=event_type,
                face_id=match.face_id if match.face_id > 0 else None,
                name=match.name or "",
                embedding=det.embedding,
            )
            crop_url = crop_result.url if crop_result else ""
            identity_key = crop_result.identity_key if crop_result else ""
            session_id = crop_result.session_id if crop_result else ""
            event_id = self._store.add_event(
                source_id=source_id,
                event_type=event_type,
                timestamp=frame.timestamp,
                frame_path=frame_path,
                face_id=match.face_id if match.face_id > 0 else None,
                confidence=float(match.similarity),
                classification={
                    "matched_name": match.name,
                    "confidence_band": match.confidence_band,
                    "detection_score": det.detection_score,
                    "bbox": list(det.bbox),
                    "crop_url": crop_url,
                },
                metadata={
                    "parent_event_id": motion_event_id,
                    "trigger": "motion" if motion_event_id else "continuous",
                    "session_id": session_id,
                },
            )
            self._statuses[source_id].face_events += 1  # type: ignore[misc]
            try:
                emb_b64 = _base64.b64encode(det.embedding.tobytes()).decode("ascii")
            except Exception:  # noqa: BLE001
                emb_b64 = ""
            publish_vlm_event(source_id, {
                "id": event_id,
                "type": event_type,
                "source_id": source_id,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "frame_timestamp": frame.timestamp.isoformat(timespec="seconds"),
                "face_id": match.face_id if match.face_id > 0 else 0,
                "identity_key": identity_key,
                "session_id": session_id,
                "name": match.name or "",
                "similarity": round(float(match.similarity), 3),
                "confidence_band": match.confidence_band,
                "crop_url": crop_url,
                "embedding_b64": emb_b64,
            })

    async def _maybe_run_continuous_vlm(
        self, frame: "Frame", config: WatchConfig
    ) -> None:
        """Continuous-VLM path. Skips if we ran a VLM call within the
        cooldown window; otherwise calls the VLM, stores the event,
        and broadcasts on the event bus so SSE consumers see it live.

        Uses the per-camera prompt_context from vision_store as the
        briefing, falls back to the explicit vlm_prompt in the config,
        and lastly to settings.json default_prompt.
        """
        source_id = frame.source_id
        # In-Flight-Schutz: max ein gleichzeitiger VLM-Call pro Source.
        # Check + add ist race-frei (kein await dazwischen). Sonst
        # rauschen bei hoher Stream-fps mehrere fire-and-forget Tasks
        # gleichzeitig durch den Cooldown-Check, BEVOR der erste
        # ``_last_vlm_at`` gesetzt hat — alle sehen ``last=None``,
        # alle senden den First-Prompt, mehrere Volltext-Beschreibungen
        # landen am Anfang im Teleprompter.
        if source_id in self._vlm_in_flight:
            return
        # Snapshot der aktuellen Generation: wird beim Abschluss
        # geprüft, um Resultate aus einer alten (gestoppten) Session
        # zu verwerfen — sonst kippt der eintreffende alte VLM-Output
        # in den frisch geleerten Ring der neuen Session.
        my_gen = self._watch_generation.get(source_id, 0)
        # Cooldown check (separate from motion-event throttle)
        last = self._last_vlm_at.get(source_id)
        if last is not None:
            delta = (datetime.now() - last).total_seconds()
            if delta < config.vlm_cooldown_sec:
                return
        self._vlm_in_flight.add(source_id)
        try:
            await self._run_continuous_vlm_inner(
                frame, config, source_id, my_gen
            )
        finally:
            self._vlm_in_flight.discard(source_id)

    async def _run_continuous_vlm_inner(
        self,
        frame: "Frame",
        config: WatchConfig,
        source_id: str,
        my_gen: int,
    ) -> None:
        """Eigentlicher VLM-Call. Vom Wrapper aufgerufen, der den
        In-Flight-Sentinel hält."""

        # Build the prompt: per-cam briefing + explicit override
        from .vision_analyzer import analyze_sequence, DEFAULT_MODEL, DEFAULT_NUM_CTX
        from .vision_prewarm import get_vision_mode
        # Briefing: prefer vision_store.sources.prompt_context (the
        # per-camera context the user typed in the popup), fall back
        # to whatever the config / settings say.
        briefing = ""
        stored = self._store.get_source(source_id)
        if stored:
            briefing = str(stored.get("prompt_context") or "").strip()

        # ── KONTEXT-INJECTION DEAKTIVIERT ───────────────────────────
        # Die History-Block-Injection und der „letzter Frame"-Hinweis
        # produzierten zu viele „unverändert"-Antworten / Pattern-Lock.
        # Ohne Kontext bekommt die VLM jeden Frame als „first frame"
        # → komplette Neubeschreibung jedes Mal. Auskommentiert
        # statt gelöscht, damit später reaktivierbar.
        #
        # history = self._vlm_history.get(source_id)
        # if config.vlm_prompt.strip():
        #     base_instruction = config.vlm_prompt.strip()
        # elif history and len(history) > 0:
        #     base_instruction = _DELTA_CONTINUOUS_PROMPT
        # else:
        #     base_instruction = _FIRST_CONTINUOUS_PROMPT
        # history_block = ""
        # if history and len(history) > 0:
        #     entries = [
        #         f"- {ts.strftime('%H:%M:%S')} — {text}"
        #         for (ts, text) in list(history)
        #     ]
        #     history_block = (
        #         "Bisherige Beobachtungen (chronologisch, älteste zuerst):\n"
        #         + "\n".join(entries)
        #         + "\n\n"
        #     )

        # User-Override gewinnt, sonst Plain-Beschreibungs-Prompt.
        if config.vlm_prompt.strip():
            base_instruction = config.vlm_prompt.strip()
        else:
            base_instruction = _FIRST_CONTINUOUS_PROMPT

        prompt_parts = []
        if briefing:
            prompt_parts.append(briefing)
        # if history_block:
        #     prompt_parts.append(history_block.rstrip())
        prompt_parts.append(base_instruction)
        prompt = "\n\n".join(prompt_parts)

        # Load VLM settings (model, num_ctx, host) from plugin config
        try:
            import json
            from pathlib import Path
            cfg_path = (
                Path(__file__).parent.parent
                / "plugins/tools/vision/settings.json"
            )
            with open(cfg_path, encoding="utf-8") as f:
                _cfg = json.load(f) or {}
            vlm_cfg = _cfg.get("vlm", {}) or {}
        except Exception:  # noqa: BLE001
            vlm_cfg = {}

        keep_alive: Any = -1 if get_vision_mode() == "live" else str(
            vlm_cfg.get("keep_alive", "30m")
        )
        # Mark cooldown BEFORE the call so multiple in-flight frames
        # don't all kick off VLM calls when one is already underway.
        # ``inference_start`` ist gleichzeitig das, was wir im Live-
        # Event als Hauptzeitstempel ausgeben — Differenz zu
        # frame.timestamp = Frame-Lag (wie alt war das Bild als die
        # VLM es bekam).
        inference_start = datetime.now()
        self._last_vlm_at[source_id] = inference_start
        try:
            result = await analyze_sequence(
                [frame],
                prompt,
                model=str(vlm_cfg.get("model", DEFAULT_MODEL)),
                num_ctx=int(vlm_cfg.get("num_ctx", DEFAULT_NUM_CTX)),
                keep_alive=keep_alive,
                host=vlm_cfg.get("host"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("continuous-VLM failed for %s: %s", source_id, e)
            return

        # Stale-Resultat verwerfen: zwischen Call-Start und -Ende
        # hat ``start()`` die Generation hochgezählt (Watcher wurde
        # aus- und wieder eingeschaltet). Der frische Ring soll
        # leer bleiben, Bus und Store nichts aus der alten Session
        # mehr erhalten.
        if self._watch_generation.get(source_id, 0) != my_gen:
            logger.debug(
                "VLM result discarded (stale generation): %s", source_id
            )
            return

        # Debug-Log: zeigt was die VLM tatsächlich liefert, gekürzt.
        # Ohne das ist im Log nur ``desc_len`` sichtbar — bei Pattern-
        # Verdacht (z.B. „immer 'unverändert'") brauchen wir den Text.
        text_full = result.text.strip()
        from .logging_utils import log_message
        log_message(
            f"👁️ VLM text src={source_id} t={frame.timestamp.strftime('%H:%M:%S')} "
            f"len={len(text_full)} → {text_full[:80]!r}"
        )

        # Ring sammelt alle Antworten 1:1 wie vom Modell geliefert.
        # Der frühere „unverändert"-Filter ist raus (User-Wunsch).
        # Der Ring wird zur Zeit eh nicht mehr in den Prompt injiziert
        # (siehe Kontext-Deaktivierung oben), bleibt aber gepflegt
        # für die Reaktivierung.
        from collections import deque
        ring = self._vlm_history.get(source_id)
        if ring is None:
            ring = deque(maxlen=max(1, self._vlm_history_size))
            self._vlm_history[source_id] = ring
        ring.append((frame.timestamp, text_full))

        # Persist + broadcast — Original-Text vom Modell, 1:1.
        event_id = -1
        try:
            event_id = self._store.add_event(
                source_id=source_id,
                event_type="vlm_analysis",
                timestamp=frame.timestamp,
                classification={"description": result.text, "model": result.model},
                metadata={"prompt": prompt, "n_frames": 1, "continuous": True},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("continuous-VLM store-event failed: %s", e)

        # Push onto the in-memory event bus for SSE consumers.
        # Drei Zeitstempel im Event:
        #   * ``frame_timestamp`` — wann das Bild aufgenommen wurde
        #   * ``timestamp`` (=Inferenz-Start) — wann die VLM den
        #     Call bekam (Capture→Start = Frame-Lag)
        #   * ``inference_end`` — wann die Antwort kam
        #     (Start→Ende = VLM-Dauer)
        inference_end = datetime.now()
        from .vision_event_bus import publish_vlm_event
        publish_vlm_event(source_id, {
            "id": event_id,
            "type": "vlm_analysis",
            "source_id": source_id,
            "timestamp": inference_start.isoformat(timespec="seconds"),
            "inference_end": inference_end.isoformat(timespec="seconds"),
            "frame_timestamp": frame.timestamp.isoformat(timespec="seconds"),
            "description": result.text,
            "model": result.model,
            "duration_ms": round(result.duration_ms, 1),
        })

    def _save_frame(self, frame: "Frame") -> str:
        """Persist a JPEG-encoded frame to ``data/vision/frames/<source>/<date>/``.

        Returns the file path as string (empty on failure — caller logs).
        """
        try:
            day = frame.timestamp.strftime("%Y-%m-%d")
            # source_id can contain `/` (e.g. "cam/v4l2_0") — slug-ify by
            # replacing path separators
            slug = frame.source_id.replace("/", "_")
            outdir = self._frames_dir / slug / day
            outdir.mkdir(parents=True, exist_ok=True)
            ts_part = frame.timestamp.strftime("%H%M%S_%f")
            outfile = outdir / f"{ts_part}_{uuid.uuid4().hex[:6]}.jpg"
            outfile.write_bytes(frame.image_bytes)
            return str(outfile)
        except Exception as e:  # noqa: BLE001
            logger.warning("save_frame failed for %s: %s", frame.source_id, e)
            return ""


# ── Module-level default watcher ──────────────────────────────────────
# One singleton per process is enough; tests construct their own.

_default_watcher: VisionWatcher | None = None
_default_lock = Lock()


def get_default_watcher(store: VisionStore | None = None) -> VisionWatcher:
    """Singleton-Watcher mit Default-Store. ``store`` darf nur beim
    Erstaufruf gesetzt werden; spätere Calls ignorieren ihn."""
    global _default_watcher
    if _default_watcher is not None:
        return _default_watcher
    with _default_lock:
        if _default_watcher is None:
            _default_watcher = VisionWatcher(store or VisionStore())
        return _default_watcher
