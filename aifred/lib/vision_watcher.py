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
    vlm_prompt: str = ""           # Override für default_prompt aus settings.json
    vlm_cooldown_sec: float = 10.0  # Mindestabstand zwischen VLM-Calls
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
        # Per-source timestamp of the last VLM call; used to throttle
        # so we don't fire vlm_cooldown_sec → see _maybe_run_vlm.
        self._last_vlm_at: dict[str, datetime] = {}

    # ── Public API ────────────────────────────────────────────────

    async def start(self, source_id: str, config: WatchConfig) -> WatchStatus:
        """Start watching a registered source. Idempotent — if already
        running, returns the existing status without restarting."""
        with self._lock:
            existing = self._tasks.get(source_id)
            if existing is not None and not existing.done():
                return self._statuses[source_id]
            source = get_source(source_id)
            if source is None:
                raise ValueError(f"unknown source: {source_id}")
            if not source.is_available():
                raise RuntimeError(f"source not available: {source_id}")
            self._configs[source_id] = config
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
            if source_id in self._statuses:
                self._statuses[source_id].running = False  # type: ignore[misc]
        return True

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
        return FaceRecognizer(self._store)

    async def _watch_loop(self, source: "FrameSource", config: WatchConfig) -> None:
        """The actual per-source loop. Runs until task cancellation or
        unrecoverable error."""
        source_id = source.source_id
        motion = MotionDetector(
            history=config.motion_history_frames,
            var_threshold=config.motion_var_threshold,
            min_area_ratio=config.motion_min_area_ratio,
            warmup_frames=config.motion_warmup_frames,
        )
        try:
            async for frame in source.stream(fps=config.fps):
                # Publish to bus first — other consumers (preview, etc.)
                # see every frame regardless of motion result.
                await self._bus.publish(source_id, frame)
                self._statuses[source_id].frames_seen += 1  # type: ignore[misc]

                result = motion.process(frame)
                if not result.motion:
                    continue
                # Min-event-interval throttle
                last = self._statuses[source_id].last_event_at
                if last is not None:
                    delta = (datetime.now() - last).total_seconds()
                    if delta < config.min_event_interval_sec:
                        continue
                await self._handle_motion_event(frame, result, config)
        except asyncio.CancelledError:
            logger.info("Watch loop cancelled for %s", source_id)
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("Watch loop crashed for %s", source_id)
            self._statuses[source_id].last_error = str(e)  # type: ignore[misc]
            self._statuses[source_id].running = False  # type: ignore[misc]

    async def _handle_motion_event(
        self,
        frame: "Frame",
        motion_result: Any,
        config: WatchConfig,
    ) -> None:
        source_id = frame.source_id
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

        # Face-Detection is CPU/GPU-intensive — run in a thread so the
        # event loop stays responsive for other sources.
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
        for det in detections:
            match = recognizer.match(det.embedding)
            event_type = (
                "face_known"
                if match.confidence_band == "known"
                else "face_unknown"
                if match.confidence_band == "unknown"
                else "face_unsure"
            )
            self._store.add_event(
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
                },
                metadata={"parent_event_id": motion_event_id},
            )
            self._statuses[source_id].face_events += 1  # type: ignore[misc]

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
