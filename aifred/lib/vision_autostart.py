"""Vigilantia-Autostart — Hintergrund-Watcher beim Service-Boot.

Liest alle ``vision_store.sources`` mit ``auto_start=True`` und startet
für jede einen ``VisionWatcher``-Task. Wird vom AIfred-Service-Boot
(``aifred.py``) aufgerufen — d.h. der Watcher läuft auch ohne offenes
Live-Vorschau-Popup im Browser.

Pro Source wird die ``WatchConfig`` aus den per-Source-Settings + den
globalen Plugin-Settings zusammengebaut:

* ``motion_min_area_ratio`` — per-Source aus ``settings_json``
* ``run_face_detect_on_motion`` — aus ``face_recognition.enabled``
* ``face_recognition_continuous`` — aus ``face_recognition.continuous``
* VLM bleibt **aus** im Hintergrund — der ist GPU-hungrig und nur im
  Live-Popup als bewusstes Opt-in sinnvoll.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from datetime import datetime
from typing import Any

from .config import (
    VISION_BACKGROUND_WATCH_FPS,
    VISION_MIN_EVENT_INTERVAL_RANGE,
    VISION_MOTION_MIN_AREA_RANGE,
    VISION_SCHEDULE_DEFAULT,
)
from .vision_settings import load_vision_settings
from .vision_watch_types import EdgeAiConfig, WatchConfig

logger = logging.getLogger(__name__)


def _resolve_edge_ai(
    source_id: str,
    cam_cfg: dict[str, Any] | None,
    plugin_settings: dict[str, Any],
) -> EdgeAiConfig | None:
    """Edge-AI-Konfiguration für den Watcher bauen — aus dem ``rtsp_cameras``-
    Eintrag (Kanäle) plus globalem Poll-Intervall. ``None`` wenn kein Host
    bekannt ist (dann kann der Watcher nicht pollen). Verbindungsdaten
    (Host, Port, Zugang) holt sich der geteilte Kamera-Client selbst aus
    ``rtsp_cameras``."""
    if not cam_cfg:
        logger.warning(
            "autostart: %s has profile ai_camera but no rtsp_cameras "
            "entry — edge-AI trigger not possible", source_id,
        )
        return None
    if not str(cam_cfg.get("host", "")).strip():
        return None
    defaults = EdgeAiConfig()
    watch = plugin_settings.get("watch") or {}
    face_channel = cam_cfg.get("face_channel")
    source = str(watch.get("edge_ai_event_source", defaults.event_source))
    return EdgeAiConfig(
        event_source=source,
        host=str(cam_cfg["host"]).strip(),
        onvif_port=int(cam_cfg.get("onvif_port", defaults.onvif_port)),
        cred=str(cam_cfg.get("cred", "")).strip(),
        channel=int(cam_cfg.get("channel", defaults.channel)),
        face_channel=int(face_channel) if face_channel is not None else None,
        poll_interval_sec=float(
            watch.get("edge_ai_poll_interval_sec", defaults.poll_interval_sec)
        ),
        settle_sec=float(watch.get("edge_ai_settle_sec", defaults.settle_sec)),
    )


_CLOCK_TIME = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")


def clock_minutes(value: Any) -> int:
    """"HH:MM" -> minutes since midnight (0-1439). The one parser of every stored time of day of a camera
    (active window, quiet hours); anything else, a bare hour included, is a ValueError."""
    match = _CLOCK_TIME.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"not a time as HH:MM: {value!r}")
    return int(match[1]) * 60 + int(match[2])


def in_clock_window(start: Any, end: Any, now: datetime) -> bool:
    """Is ``now`` inside the window from ``start`` up to (not including) ``end`` ("HH:MM")? A window that ends
    before it starts runs over midnight (22:30-06:15); start == end is the whole day."""
    first, last, current = clock_minutes(start), clock_minutes(end), now.hour * 60 + now.minute
    if first == last:
        return True
    return (first <= current < last) if first < last else (current >= first or current < last)


def _schedule_active_now(settings: dict[str, Any]) -> bool:
    """True, wenn die Kamera laut Pro-Quelle-Zeitplan JETZT aktiv (scharf)
    sein soll. Kein Zeitplan (``schedule_enabled`` False/fehlt) → immer aktiv;
    ohne gespeicherte Zeiten gelten die Standardzeiten (dieselben, die die
    Einstellungen zeigen)."""
    if not settings.get("schedule_enabled"):
        return True
    return in_clock_window(
        settings.get("schedule_start", VISION_SCHEDULE_DEFAULT[0]),
        settings.get("schedule_end", VISION_SCHEDULE_DEFAULT[1]),
        datetime.now(),
    )


def motion_threshold(source_settings: dict[str, Any]) -> float:
    """The camera's trigger threshold (share of the picture that must move), the default outside its range."""
    value = source_settings.get("motion_min_area_ratio")
    low, high = VISION_MOTION_MIN_AREA_RANGE
    return float(value) if isinstance(value, (int, float)) and low <= value <= high else WatchConfig().motion_min_area_ratio


def _build_background_config(
    source_record: dict[str, Any],
    plugin_settings: dict[str, Any],
) -> Any:
    """Baut die ``WatchConfig`` für den Hintergrund-Watcher einer Source.

    Das Fähigkeitsprofil der Quelle (``webcam`` / ``ai_camera``, SSoT in
    ``vision_profiles``) entscheidet über Trigger und lokale Roh-Erkennung:

    * **webcam** — MOG2-Motion triggert, YOLO-Person läuft (Setting),
      Gesicht + VLM wie gehabt.
    * **ai_camera** — die Kamera erkennt Person/Fahrzeug/Tier on-device;
      MOG2/YOLO bleiben AUS, der Edge-AI-Poll triggert. Gesichtserkennung
      bleibt (das kann die Kamera nicht).

    Gesichtserkennung folgt weiter den ``face_recognition``-Settings und
    gilt für BEIDE Profile.
    """
    import dataclasses

    defaults = WatchConfig()

    source_id = str(source_record.get("source_id") or "")
    settings = source_record.get("settings") or {}

    # Pro-Kamera-Drossel (UI: "Min. Event-Abstand"). Eine belebte
    # Außenkamera und ein selten überwachter Schreibtisch brauchen
    # unterschiedliche Werte — deshalb per-Kamera statt global.
    mies = settings.get("min_event_interval_sec")
    if not isinstance(mies, (int, float)) or not VISION_MIN_EVENT_INTERVAL_RANGE[0] <= mies <= VISION_MIN_EVENT_INTERVAL_RANGE[1]:
        mies = defaults.min_event_interval_sec

    fr = plugin_settings.get("face_recognition") or {}
    face_enabled = bool(fr.get("enabled", True))
    face_continuous = bool(fr.get("continuous", False))

    watch = plugin_settings.get("watch") or {}
    _ego = watch.get("motion_ego_shift_px")
    if not isinstance(_ego, (int, float)) or not 0.5 <= _ego <= 50.0:
        _ego = defaults.motion_ego_shift_px

    # Face-Hunt-Burst-Parameter (Edge-AI): validiert, sonst Code-Defaults.
    def _num(key: str, default: float, lo: float, hi: float) -> float:
        v = watch.get(key)
        return float(v) if isinstance(v, (int, float)) and lo <= v <= hi else default

    base = WatchConfig(
        fps=VISION_BACKGROUND_WATCH_FPS,
        motion_min_area_ratio=motion_threshold(settings),
        motion_ego_shift_px=float(_ego),
        save_event_frames=True,
        run_face_detect_on_motion=face_enabled,
        run_person_detect_on_motion=bool(watch.get("run_person_detect_on_motion", False)),
        motion_gated=bool(settings.get("motion_gated", True)),
        face_recognition_continuous=face_continuous and face_enabled,
        # VLM bleibt im Hintergrund AUS — opt-in nur über Live-Popup.
        run_vlm_continuous=False,
        min_event_interval_sec=float(mies),
        burst_interval_sec=_num("burst_interval_sec", defaults.burst_interval_sec, 0.0, 10.0),
        burst_linger_sec=_num("burst_linger_sec", defaults.burst_linger_sec, 0.0, 60.0),
        preroll_sec=_num("preroll_sec", defaults.preroll_sec, 0.0, 60.0),
    )
    overrides = profile_watch_overrides(source_id, settings, plugin_settings)
    return dataclasses.replace(base, **overrides) if overrides else base


def profile_watch_overrides(
    source_id: str,
    source_settings: dict[str, Any],
    plugin_settings: dict[str, Any],
) -> dict[str, Any]:
    """``WatchConfig``-Overrides aus dem Kamera-Profil (SSoT).

    Wird von Autostart UND ``vision_start_watch`` genutzt, damit der Tool-Pfad
    die Profil-Logik nicht umgeht:

    * **webcam** (``allow_local_detection``) — ``{}``: MOG2/YOLO wie
      konfiguriert.
    * **ai_camera** — Kamera erkennt on-device: MOG2-Gating + YOLO aus,
      Edge-AI-Poll als Trigger.
    """
    from .frame_sources.rtsp_source import find_camera_config
    from .vision_profiles import camera_profile

    cam_cfg = find_camera_config(source_id) if source_id else None
    profile = camera_profile(cam_cfg, source_settings)
    if profile.allow_local_detection:
        return {}

    overrides: dict[str, Any] = {
        "run_person_detect_on_motion": False,
        "motion_gated": False,
    }
    edge_ai = _resolve_edge_ai(source_id, cam_cfg, plugin_settings)
    if edge_ai:
        overrides["edge_ai"] = edge_ai
    return overrides


def eye_is_on(plugin_settings: dict[str, Any]) -> bool:
    """The eye watches only with the vision plugin on and the eye armed (``vigilantia_armed``): the one rule of
    every background watcher and of the supervisor."""
    from .plugin_registry import is_plugin_enabled

    return is_plugin_enabled("vision") and bool(plugin_settings.get("vigilantia_armed", False))


def _device_not_connected(record: dict[str, Any]) -> bool:
    """A USB camera that is not plugged in is not a registered source: nothing to start, no error
    (it registers when the device is found, see ``frame_sources.rescan``)."""
    from . import frame_sources

    return record.get("kind") == "webcam" and frame_sources.get(record["source_id"]) is None


async def start_background_watcher(source_id: str) -> bool:
    """Startet einen einzelnen Hintergrund-Watcher (z.B. wenn der User
    den auto_start-Toggle live umschaltet). Returnt False, falls die
    Source nicht im Store ist."""
    from .vision_store import VisionStore
    from .vision_watcher import get_default_watcher

    store = VisionStore()
    record = store.get_source(source_id)
    if not record:
        logger.warning("autostart: source %s not in store", source_id)
        return False
    if _device_not_connected(record):
        logger.info("autostart: %s is not plugged in — not started", source_id)
        return False
    plugin = load_vision_settings()
    watcher = get_default_watcher()
    # Pro-Kamera-Zeitplan: außerhalb des Aktiv-Fensters nicht starten
    # (der schedule_supervisor zieht sie hoch, sobald das Fenster beginnt).
    if not _schedule_active_now(record.get("settings") or {}):
        logger.info("autostart: %s outside its schedule window — not started", source_id)
        await watcher.stop(source_id)
        return True
    cfg = _build_background_config(record, plugin)
    # Idempotent — wenn schon was läuft, Stop+Start damit die neue
    # Config greift.
    await watcher.stop(source_id)
    try:
        await watcher.start(source_id, cfg)
        logger.info("autostart: background watcher running for %s", source_id)
        return True
    except Exception as e:  # camera or detector: logged, the caller sees False
        logger.warning("autostart: start failed for %s: %s", source_id, e, exc_info=True)
        return False


async def start_all_background_watchers() -> int:
    """Beim AIfred-Service-Boot aufgerufen. Startet alle Sources, die
    in der DB ``auto_start=True`` haben. Returnt Anzahl gestarteter
    Watcher (für Log/Telemetry).

    Respektiert das Master-Flag ``vigilantia_armed`` aus den Plugin-
    Settings: ist es ``False``, wird nichts gestartet — wie eine
    Alarmanlage, die zwar Sensoren hat aber nicht scharf geschaltet
    ist. Failure-Tolerant: scheitert einer, machen die anderen weiter.
    """
    from .vision_store import VisionStore

    try:
        store = VisionStore()
        sources = store.list_sources()
    except sqlite3.Error as e:
        logger.warning("autostart: source listing failed: %s", e)
        return 0
    plugin = load_vision_settings()
    if not eye_is_on(plugin):
        logger.info("autostart: vigilantia disarmed or the vision plugin off — skipping all sources")
        return 0
    from .vision_live_analysis import is_live

    # A camera in a live analysis keeps it; it returns to the background watch when the analysis ends.
    targets = [r for r in sources if r.get("auto_start") and not is_live(str(r.get("source_id") or ""))]
    if not targets:
        return 0
    from .vision_watcher import get_default_watcher
    watcher = get_default_watcher()
    started = 0
    for record in targets:
        source_id = record.get("source_id") or ""
        if not source_id:
            continue
        if _device_not_connected(record):
            logger.info("autostart: %s is not plugged in — skipped", source_id)
            continue
        # Pro-Kamera-Zeitplan: außerhalb des Aktiv-Fensters überspringen.
        if not _schedule_active_now(record.get("settings") or {}):
            logger.info("autostart: %s outside its schedule window — skipped", source_id)
            continue
        cfg = _build_background_config(record, plugin)
        try:
            await watcher.start(source_id, cfg)
            started += 1
            logger.info("autostart: background watcher running for %s", source_id)
        except Exception as e:  # one camera that fails to start must not stop the others
            logger.warning("autostart: start failed for %s: %s", source_id, e, exc_info=True)
    return started


async def _start_newly_plugged_cameras(records: list[dict[str, Any]]) -> None:
    """Eine USB-Kamera mit ``auto_start``, die beim Boot fehlte und jetzt angesteckt ist: anmelden
    (nur dieses Gerät, siehe ``connect_device``) und ihren Watcher starten."""
    from .frame_sources.v4l2_source import connect_device

    for record in records:
        source_id = record.get("source_id") or ""
        if not record.get("auto_start") or not source_id or not _device_not_connected(record):
            continue
        if await asyncio.to_thread(connect_device, source_id):
            logger.info("autostart: %s was plugged in — starting the watcher", source_id)
            await start_background_watcher(source_id)


async def apply_background_watch(source_id: str) -> None:
    """Start or stop the camera's background watcher as its settings say right now: the eye armed (and the
    vision not off), the camera's background switch on, inside its schedule. The schedule supervisor, the
    settings page and the end of a live analysis decide through here; a camera in a live analysis is left alone."""
    from .vision_live_analysis import is_live
    from .vision_store import VisionStore
    from .vision_watcher import get_default_watcher

    if is_live(source_id):
        return

    plugin = load_vision_settings()
    record = VisionStore().get_source(source_id) or {}
    wanted = (
        eye_is_on(plugin)
        and bool(record.get("auto_start")) and _schedule_active_now(record.get("settings") or {})
    )
    watcher = get_default_watcher()
    running = watcher.is_running(source_id)
    if wanted and not running:
        await start_background_watcher(source_id)
        logger.info("background watch: %s started", source_id)
    elif not wanted and running:
        await watcher.stop(source_id)
        logger.info("background watch: %s stopped", source_id)


async def schedule_supervisor() -> None:
    """Dauer-Supervisor: prüft minütlich die Pro-Kamera-Zeitpläne und startet/
    stoppt die Watcher entsprechend — aber nur wenn global scharf. Kameras ohne
    Zeitplan bleiben unangetastet (die regelt der armed-Master). Meldet zudem
    nachträglich angesteckte USB-Kameras an und startet sie. Läuft ab
    Service-Boot, failure-tolerant."""
    from .vision_store import VisionStore

    while True:
        try:
            await asyncio.sleep(60)
            plugin = load_vision_settings()
            if not eye_is_on(plugin):
                continue
            records = VisionStore().list_sources()
            await _start_newly_plugged_cameras(records)
            for record in records:
                source_id = record.get("source_id") or ""
                if source_id and record.get("auto_start") and (record.get("settings") or {}).get("schedule_enabled"):
                    await apply_background_watch(source_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # the supervisor loop goes on
            logger.warning("schedule supervisor error: %s", e, exc_info=True)


async def stop_all_background_watchers() -> int:
    """Entwaffnet die Alarmanlage — stoppt alle laufenden Watcher der
    Sources mit ``auto_start=True``. Eine laufende Live-Analyse bleibt
    (``vision_live_analysis``); endet sie, entscheidet
    ``apply_background_watch``."""
    from .vision_live_analysis import is_live
    from .vision_store import VisionStore
    from .vision_watcher import get_default_watcher

    try:
        store = VisionStore()
        sources = store.list_sources()
    except sqlite3.Error as e:
        logger.warning("autostart: source listing failed: %s", e)
        return 0
    watcher = get_default_watcher()
    stopped = 0
    for record in sources:
        if not record.get("auto_start"):
            continue
        source_id = record.get("source_id") or ""
        if not source_id or is_live(source_id):
            continue
        ok = await watcher.stop(source_id)
        if ok:
            stopped += 1
            logger.info("autostart: background watcher stopped for %s", source_id)
    return stopped
