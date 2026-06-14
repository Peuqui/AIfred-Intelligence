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
from .vision_filters.person_detect import (
    PersonDetector,
    get_default_detector as get_default_person_detector,
)
from .vision_store import VisionStore

if TYPE_CHECKING:
    from .frame_sources import Frame, FrameSource

logger = logging.getLogger(__name__)

_DEFAULT_FRAMES_DIR = DATA_DIR / "vigilantia" / "motion"

# Edge-AI-Poll-Defaults — SSoT für autostart (baut das ``edge_ai``-Dict) UND
# den Poll-Loop (Fallback, falls ein Key fehlt). Doppelte/abweichende Defaults
# an zwei Stellen waren ein Bug (settle 1.5 vs 1.0).
#
# poll_interval: Abstand zwischen ``GetAiState``-Abfragen. Ein HTTP-GET auf
#   persistenter Session — der frühere Session-Storm kam vom Re-Login, nicht
#   vom State-Poll. 0.5 s ⇒ Detektions-Latenz max. 0.5 s (statt 1.5 s).
# settle: Wartezeit nach der steigenden Flanke, bevor das Frame gezogen wird —
#   gibt dem schwenkenden PTZ-Kopf Zeit, das Subjekt zu zentrieren/zoomen.
EDGE_AI_POLL_INTERVAL_DEFAULT = 0.5
EDGE_AI_SETTLE_DEFAULT = 1.0

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
    # ── YOLO-Person-Detection pro Motion-Event ───────────────────────
    # Wenn aktiv, läuft bei Motion der YOLO-Körper-Detektor PARALLEL zur
    # Gesichtserkennung (kein Gate untereinander): findet er einen Körper,
    # wird ein ``person``-Event geschrieben (+ ggf. Alert). Die
    # Gesichtserkennung läuft unabhängig davon — sonst bliebe ein
    # formatfüllendes Gesicht (Nahaufnahme ohne ganzen Körper) unerkannt.
    run_person_detect_on_motion: bool = False
    # ── Motion-Gating ────────────────────────────────────────────────
    # True (Default, statische Kameras): die Detektions-Pipeline läuft
    # nur, wenn der Motion-Detektor Bewegung meldet. False (PTZ-/
    # Auto-Tracking-Kameras wie die Reolink TrackMix): jedes Frame wird
    # gesampelt (weiterhin durch ``min_event_interval_sec`` gedrosselt) —
    # bei schwenkender Kamera sind Motion-Detektor und Zonenmaske sinnlos
    # (das ganze Bild "bewegt" sich, die Maske wandert mit).
    motion_gated: bool = True
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
    # ── Trigger-Quelle ───────────────────────────────────────────────
    # "motion" (Default): AIfreds MOG2-Detektor löst die Pipeline aus.
    # "edge_ai": die Kamera erkennt Person/Fahrzeug/Tier on-device, AIfred
    # pollt nur ihren Zustand (siehe ``edge_ai``) und triggert darauf.
    # SSoT der erlaubten Werte: vision_profiles.TRIGGER_MOTION/EDGE_AI.
    trigger_mode: str = "motion"
    # Edge-AI-Poll-Konfiguration (nur bei trigger_mode="edge_ai"):
    #   host, api_port, cred, poll_interval_sec, channel
    edge_ai: dict[str, Any] | None = None
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
        person_detector: PersonDetector | None = None,
        frames_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._bus = bus or get_default_bus()
        # Lazy injection: detector and recognizer are only initialised
        # when the first frame triggers face_detect.
        self._explicit_face_detector = face_detector
        self._explicit_face_recognizer = face_recognizer
        self._explicit_person_detector = person_detector
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
        # Per-source Motion-Handler-Task: die Event-Verarbeitung (Face,
        # YOLO, Alert-Compose) läuft abgekoppelt vom frame_consumer —
        # der sampelt währenddessen lückenlos weiter, statt sekundenlang
        # blind zu sein (eine Person konnte unbemerkt durchs Bild laufen,
        # während ein Alert komponiert wurde). Der dict-Eintrag ist
        # zugleich In-Flight-Schutz (max. ein Handler pro Source) und
        # die starke Referenz, die fire-and-forget Tasks vor GC schützt.
        self._motion_tasks: dict[str, asyncio.Task[None]] = {}
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
        # Live-Clustering: ein inkrementeller Clusterer (per-Source-State)
        # weist Motion-/Face-Events beim Erkennen einen cluster_id zu —
        # pHash aus dem In-Memory-Frame, kein Disk-Reread. So haben Events
        # ihren cluster_id sofort (Alert-Dedup, Query-Dedup), und der
        # Batch-Describe muss nicht mehr neu clustern.
        from .vision_cluster import IncrementalClusterer
        self._clusterer = IncrementalClusterer()

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

    def _get_person_detector(self) -> PersonDetector:
        if self._explicit_person_detector is not None:
            return self._explicit_person_detector
        return get_default_person_detector()

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

        # Persistierte Per-Source-Auflösung anwenden (SSoT mit den HTTP-
        # Endpoints). Ohne das fordert der Watcher keine Auflösung an → der
        # FrameHub läuft auf Treiber-Default (z.B. 640×480 4:3), und die
        # gespeicherten Event-Frames hätten ein anderes Seitenverhältnis als
        # die im UI gewählte Auflösung. (0,0) = Treiber-Default, falls nichts
        # gesetzt ist.
        from .vision_utils import resolve_source_resolution
        cap_w, cap_h = resolve_source_resolution(source_id)

        # Shared state zwischen consumer + vlm-cycle. asyncio ist
        # single-threaded — Dict-Writes sind atomar.
        latest: dict[str, Any] = {"frame": None, "seq": 0}
        new_frame_evt = asyncio.Event()

        edge_ai_trigger = config.trigger_mode == "edge_ai" and bool(config.edge_ai)

        async def frame_consumer() -> None:
            seq = 0
            async for frame in hub.subscribe(
                source, name="watcher", fps=config.fps,
                width=cap_w, height=cap_h,
            ):
                seq += 1
                latest["frame"] = frame
                latest["seq"] = seq
                new_frame_evt.set()
                self._statuses[source_id].frames_seen += 1  # type: ignore[misc]

                # Edge-AI-Kamera: der Trigger kommt aus edge_ai_cycle (die
                # Kamera erkennt selbst). Hier nur Frames frischhalten +
                # auf den Bus geben — keine lokale MOG2-Detektion.
                if edge_ai_trigger:
                    continue

                motion_result = motion.process(frame)
                # motion_gated (Default): nur bei Bewegung weiter. Aus
                # (PTZ/Tracking): jedes Frame samplen, nur throttled.
                if config.motion_gated and not motion_result.motion:
                    continue
                last_evt = self._statuses[source_id].last_event_at
                if last_evt is not None:
                    delta = (datetime.now() - last_evt).total_seconds()
                    if delta < config.min_event_interval_sec:
                        continue
                # Verarbeitung abkoppeln: läuft noch ein Handler für diese
                # Source, wird dieses Motion-Frame übersprungen (gleiche
                # Drossel-Semantik wie der min_event_interval) — aber der
                # Consumer bleibt am Stream und sieht die nächste Bewegung.
                running = self._motion_tasks.get(source_id)
                if running is not None and not running.done():
                    continue
                self._motion_tasks[source_id] = asyncio.create_task(
                    self._run_motion_handler(frame, motion_result, config),
                    name=f"vision-motion:{source_id}",
                )

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

        async def edge_ai_cycle() -> None:
            """Edge-AI-Trigger: pollt die On-Device-Erkennung der Kamera
            (Person/Fahrzeug/Tier) und feuert bei einer steigenden Flanke
            (0→1) ein Event auf dem AKTUELL frischesten Frame. Ersetzt
            MOG2/YOLO für intelligente Kameras — die Roh-Detektion macht
            die Kamera selbst."""
            if not edge_ai_trigger:
                return
            from .reolink_ai import ReolinkAIClient, ReolinkAIError
            ec = config.edge_ai or {}
            client = ReolinkAIClient(
                host=str(ec.get("host", "")),
                api_port=int(ec.get("api_port", 80)),
                cred=str(ec.get("cred", "")),
                channel=int(ec.get("channel", 0)),
            )
            interval = float(ec.get("poll_interval_sec", EDGE_AI_POLL_INTERVAL_DEFAULT))
            settle = float(ec.get("settle_sec", EDGE_AI_SETTLE_DEFAULT))
            prev: dict[str, bool] = {}
            fails = 0
            # Zeitstempel des zuletzt befeuerten Frames — gegen eingefrorene
            # RTSP-Streams: friert der Stream ein, bleibt latest["frame"]
            # stehen (gleicher Timestamp), während die Kamera-Edge-AI nachts im
            # IR-Bild weiter Person/Tier flackert. Ohne Guard würde AIfred
            # dasselbe Standbild dutzendfach speichern + Fehlalarme feuern.
            last_fired_ts: Any = None
            try:
                while True:
                    try:
                        state = await client.get_ai_state()
                        fails = 0
                    except ReolinkAIError as e:
                        # Exponentielles Backoff: selbst wenn der Client-seitige
                        # Re-Login mal nicht greift, hämmern wir die Kamera nicht
                        # mit Logins zu (das war die Sturm-Ursache).
                        fails += 1
                        backoff = min(interval * (2 ** min(fails, 5)), 60.0)
                        logger.warning(
                            "edge-ai poll failed for %s (#%d, retry in %.0fs): %s",
                            source_id, fails, backoff, e,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    # Steigende Flanken: Klasse jetzt aktiv, vorher nicht.
                    triggered = [
                        cls for cls, active in state.items()
                        if active and not prev.get(cls, False)
                    ]
                    prev = state
                    if triggered:
                        last_evt = self._statuses[source_id].last_event_at
                        throttled = (
                            last_evt is not None
                            and (datetime.now() - last_evt).total_seconds()
                            < config.min_event_interval_sec
                        )
                        if not throttled:
                            # Settle: dem PTZ-Kopf Zeit geben, das Subjekt zu
                            # zentrieren — dann das frischeste Frame ziehen
                            # (der frame_consumer hält ``latest`` aktuell).
                            if settle > 0:
                                await asyncio.sleep(settle)
                            frame = latest["frame"]
                            if frame is None:
                                pass
                            elif frame.timestamp == last_fired_ts:
                                # Eingefrorener Stream: derselbe Frame wie beim
                                # letzten Feuern → kein echtes Ereignis, skip.
                                logger.warning(
                                    "edge-ai: eingefrorenes Frame für %s (Stream "
                                    "steht?) — Trigger verworfen", source_id,
                                )
                            else:
                                last_fired_ts = frame.timestamp
                                await self._handle_edge_ai_event(
                                    frame, triggered, config, client
                                )
                    await asyncio.sleep(interval)
            finally:
                await client.aclose()

        try:
            await asyncio.gather(
                frame_consumer(), vlm_cycle(), face_cycle(), edge_ai_cycle()
            )
        except asyncio.CancelledError:
            logger.info("Watch loop cancelled for %s", source_id)
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("Watch loop crashed for %s", source_id)
            self._statuses[source_id].last_error = str(e)  # type: ignore[misc]
            self._statuses[source_id].running = False  # type: ignore[misc]
        finally:
            # Abgekoppelten Motion-Handler nicht weiterlaufen lassen, wenn
            # die Watch-Session endet — sein Event/Alert gehört zu ihr.
            pending = self._motion_tasks.pop(source_id, None)
            if pending is not None and not pending.done():
                pending.cancel()

    async def _run_motion_handler(
        self,
        frame: "Frame",
        motion_result: Any,
        config: WatchConfig,
    ) -> None:
        """Abgekoppelter Motion-Handler (siehe ``_motion_tasks``): fängt
        Fehler selbst, damit ein Crash in Face/YOLO/Alert nicht stumm im
        verworfenen Task-Objekt verschwindet."""
        try:
            await self._handle_motion_event(frame, motion_result, config)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("motion handler crashed for %s", frame.source_id)

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
        # Captured as an output now → burn the overlay once. Save, VLM and
        # face detection downstream all use this one stamped frame.
        frame = await asyncio.to_thread(self._stamp_frame, frame)
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
            cluster_id=self._cluster_id_for(frame),
        )
        self._statuses[source_id].motion_events += 1  # type: ignore[misc]
        self._statuses[source_id].last_event_at = frame.timestamp  # type: ignore[misc]

        # Beide Detektions-Schichten laufen unabhängig (beide schreiben ihr
        # EVENT — YOLO „Körper im Bild", InsightFace die Identität; ein
        # formatfüllendes Gesicht ohne ganzen Körper bliebe sonst unerkannt).
        # ABER: für die ALERTS gilt — Gesicht zuerst; wird eins erkannt, ist
        # das die informativere Meldung und der generische Person-Alert wird
        # unterdrückt (sonst zwei Telegram-Nachrichten mit demselben Bild).
        face_found = False
        face_active = (
            config.run_face_detect_on_motion
            and not config.face_recognition_continuous
        )
        if face_active:
            face_found = await self._run_face_detection(
                frame, config, motion_event_id=motion_event_id, frame_path=frame_path
            )
        if config.run_person_detect_on_motion:
            await self._run_person_detection(
                frame, config, motion_event_id=motion_event_id,
                frame_path=frame_path, emit_alert=not face_found,
            )

    async def _handle_edge_ai_event(
        self,
        frame: "Frame",
        classes: list[str],
        config: WatchConfig,
        ai_client: Any = None,
    ) -> None:
        """Edge-AI-Event verarbeiten: die Kamera hat Person/Fahrzeug/Tier
        erkannt. Schreibt pro Klasse ein Event (Detektor ``edge_ai``),
        feuert die passenden Alerts und ergänzt — nur bei ``person`` — die
        Gesichts-Identität, die die Kamera nicht liefern kann. Für das
        Gesicht wird (Dual-Lens) das Zoom-Objektiv genutzt, sonst das
        Weitwinkel-Frame."""
        source_id = frame.source_id
        # Synchronisierter Dual-Lens-Snap: Wide + Zoom FRISCH und parallel mit
        # EINEM gemeinsamen Zeitstempel holen. Das stale RTSP-latest-Wide
        # verpasst das Subjekt oft (zeitversetzt), während der Zoom frisch war —
        # darum stimmten Wide und Crop nie überein. Ein paralleler Snap
        # garantiert, dass beide Ansichten denselben Augenblick treffen.
        wide_frame, face_frame = await self._edge_ai_synced_frames(
            frame, config, ai_client
        )
        wide_frame = self._blackout_frame(wide_frame)
        wide_frame = await asyncio.to_thread(self._stamp_frame, wide_frame)
        frame_path = ""
        zoom_frame_path = ""
        if config.save_event_frames:
            frame_path = await asyncio.to_thread(self._save_frame, wide_frame)
            # Zoom separat stempeln + speichern; der Crop unten kommt aus dem
            # ungestempelten face_frame (sauberes Gesicht).
            if face_frame is not wide_frame:
                stamped_zoom = await asyncio.to_thread(self._stamp_frame, face_frame)
                zoom_frame_path = await asyncio.to_thread(self._save_frame, stamped_zoom)
        from .config import EDGE_AI_CONFIRM
        from .vision_alerts import emit_object_alert, emit_person_alert

        cid = self._cluster_id_for(wide_frame)
        ts = wide_frame.timestamp
        self._statuses[source_id].motion_events += 1  # type: ignore[misc]
        self._statuses[source_id].last_event_at = ts  # type: ignore[misc]

        # Die Kamera ist nur der AUSLÖSER — entschieden wird nach UNSEREN
        # Detektoren (wie beim Webcam-Motion-Pfad). Pro Klasse sagt
        # EDGE_AI_CONFIRM, ob unser YOLO den Trigger bestätigen muss
        # (True, gegen IR-Halluzinationen) oder ob wir der Kamera glauben
        # (False, z.B. animal — Nano-YOLO ist da schwach). Alle zu
        # bestätigenden Klassen prüfen wir in EINER YOLO-Inferenz.
        confirm_needed = {c for c in classes if EDGE_AI_CONFIRM.get(c, True)}
        counts = await self._count_categories(wide_frame, confirm_needed)

        def _ok(cls: str) -> bool:
            # bestätigt = der Kamera vertraut (policy False) ODER eigenes
            # YOLO hat die Klasse gezählt (count > 0).
            return cls not in confirm_needed or counts.get(cls, 0) > 0

        def _count(cls: str) -> int:
            # YOLO-Stückzahl bei bestätigten Klassen; bei vertrauten Klassen
            # (z.B. animal) liefert die Kamera keine Zahl → 0 (= "kein Count").
            return counts.get(cls, 0) if cls in confirm_needed else 0

        def _detector_label(cls: str) -> str:
            return "yolo" if cls in confirm_needed else "edge_ai"

        # ── person ── Gesicht ZUERST (informativere Meldung als „person").
        face_found = False
        if "person" in classes and config.run_face_detect_on_motion:
            face_found = await self._run_face_detection(
                face_frame, config, motion_event_id=None,
                frame_path=frame_path, zoom_frame_path=zoom_frame_path,
                trigger="edge_ai",
            )
        if "person" in classes:
            if _ok("person"):
                # Event aus UNSERER Erkennung — Chronik zeigt nur bestätigte
                # Personen, keine Kamera-Phantome. Alert entfällt, wenn schon
                # ein Gesicht erkannt wurde (sonst Doppel-Meldung).
                p_count = max(1, _count("person"))
                self._store.add_event(
                    source_id=source_id, event_type="person", timestamp=ts,
                    frame_path=frame_path, confidence=1.0,
                    classification={"detector": _detector_label("person"),
                                    "count": p_count,
                                    "zoom_frame_path": zoom_frame_path},
                    metadata={"trigger": "edge_ai"}, cluster_id=cid,
                )
                if not face_found:
                    await emit_person_alert(
                        source_id=source_id, frame_path=frame_path,
                        zoom_frame_path=zoom_frame_path, cluster_id=cid,
                        count=p_count, timestamp=ts, store=self._store,
                    )
            elif not face_found:
                logger.info(
                    "edge-ai person NOT confirmed for %s — no event, no "
                    "alert (camera false-positive)", source_id,
                )

        # ── vehicle / animal ── Event + Alert nur bei Bestätigung (oder wenn
        # die Klasse der Kamera anvertraut ist, siehe EDGE_AI_CONFIRM).
        for cls in ("vehicle", "animal"):
            if cls not in classes:
                continue
            if not _ok(cls):
                logger.info(
                    "edge-ai %s NOT confirmed for %s — no event, no alert",
                    cls, source_id,
                )
                continue
            # count > 0 nur bei YOLO-bestätigten Klassen; vertraute Klassen
            # (animal) haben keine Stückzahl von der Kamera → 0 = "ohne Zahl".
            obj_count = _count(cls)
            self._store.add_event(
                source_id=source_id, event_type=cls, timestamp=ts,
                frame_path=frame_path, confidence=1.0,
                classification={"detector": _detector_label(cls),
                                "count": obj_count,
                                "zoom_frame_path": zoom_frame_path},
                metadata={"trigger": "edge_ai"}, cluster_id=cid,
            )
            await emit_object_alert(
                source_id=source_id, object_type=cls, frame_path=frame_path,
                zoom_frame_path=zoom_frame_path, cluster_id=cid,
                count=obj_count, timestamp=ts, store=self._store,
            )

    async def _count_categories(
        self, frame: "Frame", wanted: set[str],
    ) -> dict[str, int]:
        """YOLO-Zählung der Edge-AI-Trigger in EINER Inferenz. Returnt pro
        Kategorie die Anzahl (0 = nicht bestätigt). Dient zugleich als Gate
        (count > 0 = bestätigt) UND liefert die Stückzahl für den Alert.

        Best-effort: schlägt der Detektor INFRASTRUKTURELL fehl (Decode/
        Inferenz wirft), geben wir für alle ``wanted`` count=1 zurück (im
        Zweifel den Alarm NICHT verschlucken). Sauberer Lauf ohne Treffer →
        count=0 → Trigger wird verworfen."""
        if not wanted:
            return {}
        from .config import EDGE_AI_COCO_MAP
        try:
            detector = self._get_person_detector()
            return await asyncio.to_thread(
                detector.detect_category_counts, frame, EDGE_AI_COCO_MAP, wanted,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "category count failed for %s: %s — allowing all", frame.source_id, e,
            )
            return {c: 1 for c in wanted}

    async def _edge_ai_synced_frames(
        self, base_frame: "Frame", config: WatchConfig, ai_client: Any
    ) -> tuple["Frame", "Frame"]:
        """Wide- + Zoom-Objektiv FRISCH und parallel snappen, mit EINEM
        gemeinsamen Zeitstempel — so treffen beide Ansichten denselben Moment
        (Dual-Lens). Gibt ``(wide_frame, zoom_frame)`` zurück.

        Best-effort: Bei fehlendem Client fällt alles aufs übergebene Frame
        zurück; schlägt der Wide-Snap fehl, bleibt das übergebene Frame; ohne
        ``face_channel`` (oder bei Zoom-Snap-Fehler) ist der Zoom == Wide. Die
        Pipeline bricht nie ab."""
        from dataclasses import replace

        if ai_client is None:
            return base_frame, base_frame
        ec = config.edge_ai or {}
        wide_ch = int(ec.get("channel", 0))
        raw_face_ch = ec.get("face_channel")
        face_ch = int(raw_face_ch) if raw_face_ch is not None else -1
        has_zoom = face_ch >= 0

        snaps: list[Any] = [ai_client.snap(wide_ch)]
        if has_zoom:
            snaps.append(ai_client.snap(face_ch))
        results = await asyncio.gather(*snaps, return_exceptions=True)
        # Gemeinsamer Zeitstempel → beide Bilder gelten explizit als "derselbe
        # Moment", auch in Dateinamen + Event.
        ts = datetime.now()

        def _jpeg(r: Any) -> bytes | None:
            if isinstance(r, BaseException):
                logger.warning(
                    "edge-ai snap failed for %s: %s", base_frame.source_id, r
                )
                return None
            return bytes(r) if isinstance(r, (bytes, bytearray)) and len(r) >= 1000 else None

        wide_jpeg = _jpeg(results[0])
        wide_frame = (
            replace(base_frame, image_bytes=wide_jpeg, timestamp=ts)
            if wide_jpeg else base_frame
        )
        zoom_frame = wide_frame
        if has_zoom and len(results) > 1:
            zoom_jpeg = _jpeg(results[1])
            if zoom_jpeg:
                zoom_frame = replace(
                    base_frame, image_bytes=zoom_jpeg, timestamp=ts,
                    metadata={**base_frame.metadata, "lens": "zoom",
                              "face_channel": face_ch},
                )
        return wide_frame, zoom_frame

    async def _run_person_detection(
        self,
        frame: "Frame",
        config: WatchConfig,
        *,
        motion_event_id: int | None = None,
        frame_path: str = "",
        emit_alert: bool = True,
    ) -> None:
        """YOLO-Körper-Detektion. Schreibt bei Treffer ein ``person``-Event;
        der proaktive Alert (armed-gated) wird nur gesendet, wenn
        ``emit_alert=True`` — der Caller unterdrückt ihn, wenn parallel ein
        Gesicht erkannt wurde (sonst Doppel-Alert). Best-effort: Fehler
        brechen die Watch-Schleife nicht ab."""
        source_id = frame.source_id
        try:
            detector = self._get_person_detector()
            persons = await asyncio.to_thread(detector.detect, frame)
        except Exception as e:  # noqa: BLE001
            logger.warning("person_detect failed for %s: %s", source_id, e)
            return
        if not persons:
            return

        cid = self._cluster_id_for(frame)
        max_score = max(p.score for p in persons)
        self._store.add_event(
            source_id=source_id,
            event_type="person",
            timestamp=frame.timestamp,
            frame_path=frame_path,
            confidence=float(max_score),
            classification={
                "count": len(persons),
                "boxes": [list(p.bbox) for p in persons],
                "max_score": max_score,
            },
            metadata={
                "parent_event_id": motion_event_id,
                "trigger": "motion",
            },
            cluster_id=cid,
        )

        if emit_alert:
            from .vision_alerts import emit_person_alert
            await emit_person_alert(
                source_id=source_id,
                frame_path=frame_path,
                cluster_id=cid,
                count=len(persons),
                timestamp=frame.timestamp,
                store=self._store,
            )

    async def _run_face_detection(
        self,
        frame: "Frame",
        config: WatchConfig,
        *,
        motion_event_id: int | None = None,
        frame_path: str = "",
        zoom_frame_path: str = "",
        trigger: str | None = None,
    ) -> bool:
        """Eigentliche Face-Detection + Recognition + Event-Publishing.
        Returnt ``True``, wenn mindestens ein Gesicht erkannt wurde (der
        Caller unterdrückt dann den generischen Person-Alert).
        Wird aus dem motion-getriggerten Pfad, dem Continuous-face_cycle
        UND dem Edge-AI-Pfad gerufen. ``motion_event_id`` ist nur gesetzt
        wenn der Aufruf aus einem motion-Event kam. ``trigger`` überschreibt
        den Traceability-Marker im Event-Metadata; ohne Angabe wird er aus
        ``motion_event_id`` abgeleitet (motion vs continuous)."""
        trigger = trigger or ("motion" if motion_event_id else "continuous")
        source_id = frame.source_id
        try:
            detector = self._get_face_detector()
            detections = await asyncio.to_thread(detector.detect, frame)
        except Exception as e:  # noqa: BLE001
            logger.warning("face_detect failed for %s: %s", source_id, e)
            return False
        if not detections:
            return False

        recognizer = self._get_face_recognizer()
        recognizer.invalidate()  # pick up any newly-enrolled faces
        from .face_crop_store import get_default_store
        from .vision_event_bus import publish_vlm_event
        import base64 as _base64
        crop_store = get_default_store()
        # Pro Band aggregieren: ALLE Gesichter eines Vorkommnisses werden zu
        # EINER Meldung je Band zusammengefasst (nicht pro Gesicht einzeln,
        # sonst dedupliziert der Dispatcher gleichartige weg → nur ein Name).
        # known_names listet jeden erkannten Namen (offen, ungedeckelt);
        # die Counts speisen "N Personen". DB-Events bleiben pro Gesicht.
        known_names: list[str] = []
        unsure_names: list[str] = []
        band_count = {"face_known": 0, "face_unknown": 0, "face_unsure": 0}
        band_crop = {"face_known": "", "face_unknown": "", "face_unsure": ""}
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
            # Aggregation füllen (für die zusammengefasste Meldung nach der
            # Schleife). Namen ohne Duplikate, Reihenfolge = Erkennungsfolge.
            band_count[event_type] += 1
            if not band_crop[event_type]:
                band_crop[event_type] = crop_url
            if event_type == "face_known" and match.name and match.name not in known_names:
                known_names.append(match.name)
            elif event_type == "face_unsure" and match.name and match.name not in unsure_names:
                unsure_names.append(match.name)
            cid = self._cluster_id_for(frame)
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
                    "zoom_frame_path": zoom_frame_path,
                },
                metadata={
                    "parent_event_id": motion_event_id,
                    "trigger": trigger,
                    "session_id": session_id,
                },
                cluster_id=cid,
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

        # Aggregierte Meldungen — eine pro Band, das im Vorkommnis vorkam.
        # So werden ALLE bekannten Namen genannt (offen, ungedeckelt) und
        # Unbekannte/Unsichere gezählt, statt dass die Dedup gleichartige
        # Gesichter auf eine Meldung mit einem Namen zusammenstreicht.
        from .vision_alerts import emit_face_alert
        cid_final = self._cluster_id_for(frame)
        for band, names in (
            ("face_known", known_names),
            ("face_unsure", unsure_names),
            ("face_unknown", []),
        ):
            if band_count[band] <= 0:
                continue
            await emit_face_alert(
                source_id=source_id,
                event_type=band,
                frame_path=frame_path,
                zoom_frame_path=zoom_frame_path,
                crop_url=band_crop[band],
                cluster_id=cid_final,
                names=names,
                count=band_count[band],
                timestamp=frame.timestamp,
                store=self._store,
            )
        return True

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
        # Same single stamped frame as the saved still: the VLM reads name +
        # location + capture time straight from the image (frame.timestamp is
        # preserved, so the logging/event timestamps below stay correct).
        vlm_frame = await asyncio.to_thread(self._stamp_frame, frame)
        try:
            result = await analyze_sequence(
                [vlm_frame],
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

    def _cluster_id_for(self, frame: "Frame") -> str:
        """Live cluster_id for an event's (stamped) frame — pHash from memory,
        fed to the incremental clusterer. The batch path reads the same saved
        frame from disk, so the pHash (and thus the cluster) is consistent.
        Empty string on failure → the event is treated as solo."""
        try:
            from .vision_phash import phash_bytes
            ph = phash_bytes(frame.image_bytes)
        except Exception as e:  # noqa: BLE001
            logger.debug("live cluster phash failed for %s: %s", frame.source_id, e)
            return ""
        return self._clusterer.assign(frame.source_id, frame.timestamp, ph)

    def _stamp_frame(self, frame: "Frame") -> "Frame":
        """Burn the documentation overlay (name + location + capture time)
        into the frame once — at the moment it is taken as an output. The
        same stamped frame then flows to save, VLM and face detection; there
        is no separate clean copy. Motion detection runs upstream on the raw
        stream (it is the trigger, not a consumer of the captured still), so
        the detector never sees the overlay."""
        from dataclasses import replace

        from .vision_utils import annotate_frame, source_overlay_label
        return replace(
            frame,
            image_bytes=annotate_frame(
                frame.image_bytes,
                source_overlay_label(frame.source_id),
                timestamp=frame.timestamp,
            ),
        )

    def _save_frame(self, frame: "Frame") -> str:
        """Persist a JPEG-encoded frame to ``data/vigilantia/motion/<source>/<date>/``.

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
            # The frame is already overlay-stamped by _stamp_frame at the
            # moment it was taken as an output — write it as-is.
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
