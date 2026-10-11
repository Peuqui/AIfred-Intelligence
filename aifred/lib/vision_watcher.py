"""Watch-Mode-Supervisor — kontinuierliche Pipeline pro Source.

Eine ``VisionWatcher``-Instanz hält pro aktiver Source eine eigene
asyncio-Task am Laufen: der FrameHub liefert die Frames, der Watcher
verteilt sie an die Auswertungen und schreibt Events ins ``VisionStore``.
Er selbst ist nur Lebenszyklus und Verdrahtung; die Arbeit liegt in:

* ``vision_motion``      — Motion-Pfad (MOG2 → Gesicht/Person),
* ``vision_edge_ai``     — Edge-AI-Pfad (Kamera erkennt, wir bestätigen),
* ``vision_burst``       — Face-Hunt-Burst nach einem Personen-Trigger,
* ``vision_continuous_vlm`` — Dauer-VLM des Live-Teleprompters,
* ``vision_faces`` / ``vision_detect`` / ``vision_recorder`` / ``vision_output``
  — gemeinsame Bausteine.

Public API: ``start(source_id, config)``, ``stop(source_id)``,
``list_active()``, ``is_running(source_id)``, ``shutdown()``.

Lifecycle: pro Prozess eine ``VisionWatcher``-Instanz, am besten über
``get_default_watcher()`` geholt. Tests können eigene Instanzen mit
Test-Store erzeugen.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Callable

from . import vision_snap
from .config import DATA_DIR
from .frame_hub import get_default_hub
from .frame_sources import get as get_source
from .vision_burst import BurstRunner
from .vision_continuous_vlm import ContinuousVlm
from .vision_detect import DetectionService
from .vision_edge_ai import EdgeAiHandler, run_edge_ai_cycle
from .vision_edge_source import create_event_source
from .vision_faces import FacePipeline
from .vision_filters import MotionDetector, MotionResult
from .vision_filters.face_detect import FaceDetector
from .vision_filters.face_recognize import FaceRecognizer
from .vision_filters.person_detect import PersonDetector
from .vision_filters.zone_mask import load_zone_mask
from .vision_frames import LiveFrames
from .vision_motion import MotionEventHandler
from .vision_output import OutputChain
from .vision_recorder import EventRecorder
from .vision_store import VisionStore
from .vision_utils import resolve_source_resolution
from .vision_watch_types import WatchConfig, WatchStatus

if TYPE_CHECKING:
    from .frame_sources import Frame, FrameSource

logger = logging.getLogger(__name__)

_DEFAULT_FRAMES_DIR = DATA_DIR / "vigilantia" / "motion"


@dataclass
class _Session:
    """Zustand einer laufenden Watch-Session einer Quelle."""

    config: WatchConfig
    motion: MotionDetector
    task: "asyncio.Task[None] | None" = None
    # Abgekoppelter Motion-Handler: die Event-Verarbeitung (Face, YOLO, Alert)
    # läuft neben dem frame_consumer, der währenddessen lückenlos weiter
    # sampelt. Der Eintrag ist zugleich In-Flight-Schutz (max. ein Handler pro
    # Source) und die starke Referenz, die den Task vor dem GC schützt.
    motion_task: "asyncio.Task[None] | None" = None


class VisionWatcher:
    """Manages background watch tasks per source."""

    def __init__(
        self,
        store: VisionStore,
        *,
        face_detector: FaceDetector | None = None,
        face_recognizer: FaceRecognizer | None = None,
        person_detector: PersonDetector | None = None,
        frames_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._lock = Lock()
        self._sessions: dict[str, _Session] = {}
        # Status bleibt nach dem Stop lesbar (``running=False``); der Recorder
        # zählt in dieselben Objekte.
        self._statuses: dict[str, WatchStatus] = {}
        self._output = OutputChain(frames_dir or _DEFAULT_FRAMES_DIR)
        self._detect = DetectionService(
            store,
            face_detector=face_detector,
            face_recognizer=face_recognizer,
            person_detector=person_detector,
        )
        self._recorder = EventRecorder(store, self._statuses)
        faces = FacePipeline(store, self._detect, self._recorder)
        self._bursts = BurstRunner(store, self._output, self._detect, self._recorder)
        self._motion_handler = MotionEventHandler(
            store, self._output, self._detect, self._recorder, faces,
        )
        self._edge_handler = EdgeAiHandler(
            store, self._output, self._detect, self._recorder, faces, self._bursts,
        )
        self._vlm = ContinuousVlm(store, self._output)

    @property
    def detection(self) -> DetectionService:
        """Geteilter Erkennungsdienst (Detektoren + Recognizer)."""
        return self._detect

    # ── Public API ────────────────────────────────────────────────

    async def start(self, source_id: str, config: WatchConfig) -> WatchStatus:
        """Start watching a registered source. Idempotent — if already
        running, returns the existing status without restarting."""
        with self._lock:
            existing = self._sessions.get(source_id)
            if existing is not None and existing.task is not None and not existing.task.done():
                return self._statuses[source_id]
            source = get_source(source_id)
            if source is None:
                raise ValueError(f"unknown source: {source_id}")
            # NOTE: deliberately NOT calling source.is_available() — that
            # opens a cv2.VideoCapture probe which races against any running
            # live-preview stream for the V4L2 device. The watch loop's
            # stream() call has retry-open logic and fails cleanly there if
            # the cam truly can't be opened.
            zone_mask = load_zone_mask(source_id)
            self._output.set_zone_mask(source_id, zone_mask)
            session = _Session(
                config=config,
                motion=MotionDetector(
                    history=config.motion_history_frames,
                    var_threshold=config.motion_var_threshold,
                    min_area_ratio=config.motion_min_area_ratio,
                    warmup_frames=config.motion_warmup_frames,
                    zone_mask=zone_mask,
                    ego_shift_px=config.motion_ego_shift_px,
                ),
            )
            self._sessions[source_id] = session
            self._statuses[source_id] = WatchStatus(
                source_id=source_id,
                running=True,
                started_at=datetime.now(),
                fps=config.fps,
                frames_seen=0,
                motion_events=0,
                face_events=0,
                last_event_at=None,
            )
            session.task = asyncio.create_task(
                self._watch_loop(source, session), name=f"vision-watch:{source_id}",
            )
        return self._statuses[source_id]

    async def stop(self, source_id: str) -> bool:
        """Cancel a running watch. Returns False if nothing was running."""
        with self._lock:
            session = self._sessions.get(source_id)
        if session is None:
            return False
        was_running = session.task is not None and not session.task.done()
        if was_running:
            assert session.task is not None
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        self._bursts.cancel(source_id)
        with self._lock:
            self._sessions.pop(source_id, None)
            self._output.forget_source(source_id)
            if source_id in self._statuses:
                self._statuses[source_id].running = False
        return was_running

    def reload_zone_mask(self, source_id: str) -> None:
        """Zonen-Maske einer Quelle live aus den Settings nachladen und in
        Detektor + Blackout-Pfad übernehmen — ohne die Watch-Schleife neu zu
        starten. Greift sofort beim nächsten Frame. No-op wenn die Quelle
        nicht läuft (beim nächsten Start wird sie ohnehin frisch geladen)."""
        session = self._sessions.get(source_id)
        if session is None:
            return
        zone_mask = load_zone_mask(source_id)
        self._output.set_zone_mask(source_id, zone_mask)
        session.motion.set_zone_mask(zone_mask)

    def reload_motion_min(self, source_id: str, ratio: float) -> None:
        """Bewegungs-Schwellwert einer laufenden Quelle live setzen (Slider in
        den Vision-Settings), ohne Re-Arm. No-op wenn die Quelle nicht läuft
        (beim nächsten Start wird der Wert ohnehin frisch geladen)."""
        session = self._sessions.get(source_id)
        if session is not None:
            session.motion.set_min_area_ratio(ratio)

    def is_running(self, source_id: str) -> bool:
        session = self._sessions.get(source_id)
        return (
            session is not None
            and session.task is not None
            and not session.task.done()
        )

    def list_active(self) -> list[WatchStatus]:
        """Snapshot of all currently-running watch sessions."""
        return [s for sid, s in self._statuses.items() if self.is_running(sid)]

    async def shutdown(self) -> None:
        """Stop all running watches. Call before process exit."""
        with self._lock:
            source_ids = list(self._sessions)
        for source_id in source_ids:
            await self.stop(source_id)

    # ── Watch-Schleife ────────────────────────────────────────────

    async def _watch_loop(self, source: "FrameSource", session: _Session) -> None:
        """Per-source watch loop: ein ``frame_consumer`` hält ``live`` aktuell
        (und fährt bei Kameras ohne Edge-AI die Motion-Detection), die Zyklen
        pullen sich jeweils das AKTUELL frischeste Frame — kein Frame-Backlog."""
        source_id = source.source_id
        config = session.config
        # Dieselbe Session? Ergebnisse aus einer beendeten Session (Watch
        # aus- und wieder eingeschaltet) werden verworfen.
        def is_current() -> bool:
            return self._sessions.get(source_id) is session

        live = LiveFrames(config.preroll_sec)
        cycles = [self._frame_consumer(source, session, live)]
        if config.run_vlm_continuous:
            cycles.append(self._vlm_cycle(source_id, config, live, is_current))
        if config.face_recognition_continuous and config.run_face_detect_on_motion:
            cycles.append(self._face_cycle(source_id, config, live))
        if config.edge_ai is not None:
            cycles.append(self._edge_ai_cycle(source_id, config, live, is_current))
        try:
            await asyncio.gather(*cycles)
        except asyncio.CancelledError:
            logger.info("Watch loop cancelled for %s", source_id)
            raise
        except Exception:
            logger.exception("Watch loop crashed for %s", source_id)
            self._statuses[source_id].running = False
        finally:
            # Abgekoppelten Motion-Handler nicht weiterlaufen lassen, wenn die
            # Watch-Session endet — sein Event/Alert gehört zu ihr.
            pending = session.motion_task
            if pending is not None and not pending.done():
                pending.cancel()

    async def _frame_consumer(
        self, source: "FrameSource", session: _Session, live: LiveFrames,
    ) -> None:
        source_id = source.source_id
        config = session.config
        # Persistierte Per-Source-Auflösung anwenden (SSoT mit den HTTP-
        # Endpoints). Ohne das fordert der Watcher keine Auflösung an → der
        # FrameHub läuft auf Treiber-Default (z.B. 640×480 4:3), und die
        # gespeicherten Event-Frames hätten ein anderes Seitenverhältnis als
        # die im UI gewählte Auflösung. (0,0) = Treiber-Default.
        width, height = resolve_source_resolution(source_id)
        async for frame in get_default_hub().subscribe(
            source, name="watcher", fps=config.fps, width=width, height=height,
        ):
            live.push(frame)
            self._statuses[source_id].frames_seen += 1
            # Edge-AI-Kamera: der Trigger kommt aus dem Edge-AI-Zyklus (die
            # Kamera erkennt selbst). Hier nur Frames frischhalten — keine
            # lokale MOG2-Detektion.
            if config.edge_ai is not None:
                continue
            motion_result = session.motion.process(frame)
            # motion_gated (Default): nur bei Bewegung weiter. Aus (PTZ/
            # Tracking): jedes Frame samplen, nur gedrosselt.
            if config.motion_gated and not motion_result.motion:
                continue
            if self._recorder.is_throttled(source_id, config.min_event_interval_sec):
                continue
            # Läuft noch ein Handler für diese Source, wird dieses Motion-Frame
            # übersprungen (gleiche Drossel-Semantik wie min_event_interval) —
            # aber der Consumer bleibt am Stream und sieht die nächste Bewegung.
            running = session.motion_task
            if running is not None and not running.done():
                continue
            session.motion_task = asyncio.create_task(
                self._run_motion_handler(frame, motion_result, config),
                name=f"vision-motion:{source_id}",
            )

    async def _run_motion_handler(
        self, frame: "Frame", motion_result: MotionResult, config: WatchConfig,
    ) -> None:
        """Abgekoppelter Motion-Handler: fängt Fehler selbst, damit ein Crash in
        Face/YOLO/Alert nicht stumm im verworfenen Task-Objekt verschwindet."""
        try:
            await self._motion_handler.handle(frame, motion_result, config)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("motion handler crashed for %s", frame.source_id)

    async def _vlm_cycle(
        self, source_id: str, config: WatchConfig, live: LiveFrames,
        is_current: Callable[[], bool],
    ) -> None:
        async for frame in live.fresh_frames(config.vlm_cooldown_sec):
            try:
                await self._vlm.describe(frame, is_current)
            except Exception:  # noqa: BLE001
                logger.exception("continuous-VLM cycle error for %s", source_id)

    async def _face_cycle(
        self, source_id: str, config: WatchConfig, live: LiveFrames,
    ) -> None:
        """Kontinuierliche Gesichtserkennung (motion-unabhängig), gedrosselt
        durch ``min_event_interval_sec`` — kein Motion-Detector dazwischen."""
        async for frame in live.fresh_frames(config.min_event_interval_sec):
            try:
                await self._motion_handler.handle_continuous_face(frame, config)
            except Exception:  # noqa: BLE001
                logger.exception("continuous face cycle error for %s", source_id)

    async def _edge_ai_cycle(
        self, source_id: str, config: WatchConfig, live: LiveFrames,
        is_current: Callable[[], bool],
    ) -> None:
        edge = config.edge_ai
        assert edge is not None
        # EINE Session pro Kamera: den GETEILTEN Client von vision_snap holen
        # (auch Snapshot/Multipose nutzen ihn) statt einen eigenen zu erzeugen —
        # sonst zwei Logins pro Kamera → „max session".
        client = vision_snap.get_client(source_id)
        if client is None:
            raise ValueError(
                f"edge-ai: kein Kamera-Client für {source_id} "
                "(Zugangsdaten in rtsp_cameras fehlen?)"
            )
        events = create_event_source(source_id, edge, client)
        try:
            await run_edge_ai_cycle(
                source_id=source_id,
                config=config,
                events=events,
                client=client,
                live=live,
                recorder=self._recorder,
                handler=self._edge_handler,
                is_current=is_current,
            )
        finally:
            await events.aclose()


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
