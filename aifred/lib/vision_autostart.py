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
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_VISION_SETTINGS_PATH = (
    Path(__file__).parent.parent
    / "plugins" / "tools" / "vision" / "settings.json"
)


def _load_plugin_settings() -> dict[str, Any]:
    if not _VISION_SETTINGS_PATH.exists():
        return {}
    try:
        parsed = json.loads(_VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("vision settings.json unreadable: %s", e)
        return {}


def _resolve_edge_ai(
    source_id: str,
    cam_cfg: dict[str, Any] | None,
    plugin_settings: dict[str, Any],
) -> dict[str, Any] | None:
    """Edge-AI-Poll-Block für den Watcher bauen — aus dem ``rtsp_cameras``-
    Eintrag (host/cred/api_port) plus globalem Poll-Intervall. ``None`` wenn
    kein Host bekannt ist (dann kann der Watcher nicht pollen)."""
    if not cam_cfg:
        logger.warning(
            "autostart: %s hat Profil ai_camera, aber keinen rtsp_cameras-"
            "Eintrag — Edge-AI-Trigger nicht möglich", source_id,
        )
        return None
    host = str(cam_cfg.get("host", "")).strip()
    if not host:
        return None
    from .vision_watcher import EDGE_AI_POLL_INTERVAL_DEFAULT, EDGE_AI_SETTLE_DEFAULT

    watch = plugin_settings.get("watch") or {}
    face_channel = cam_cfg.get("face_channel")
    return {
        "host": host,
        "api_port": int(cam_cfg.get("api_port", 443)),
        "cred": str(cam_cfg.get("cred", "")).strip(),
        "channel": int(cam_cfg.get("channel", 0)),
        "poll_interval_sec": float(
            watch.get("edge_ai_poll_interval_sec", EDGE_AI_POLL_INTERVAL_DEFAULT)
        ),
        # Wartezeit nach der Erkennung, bevor das Frame gezogen wird — gibt
        # dem schwenkenden PTZ-Kopf Zeit, das Subjekt zu zentrieren/zoomen,
        # damit Gesicht + VLM ein scharfes, mittiges Bild bekommen.
        "settle_sec": float(watch.get("edge_ai_settle_sec", EDGE_AI_SETTLE_DEFAULT)),
        # Dual-Lens: Kanal des Zoom-/Tele-Objektivs für die Gesichts-
        # erkennung (mehr Pixel im Gesicht). None = Gesicht auf dem
        # Weitwinkel-Frame (kein separater Zoom-Snap).
        "face_channel": int(face_channel) if face_channel is not None else None,
    }


def _schedule_active_now(settings: dict[str, Any]) -> bool:
    """True, wenn die Kamera laut Pro-Quelle-Zeitplan JETZT aktiv (scharf)
    sein soll. Kein Zeitplan (``schedule_enabled`` False/fehlt) → immer aktiv.
    Über Mitternacht (z.B. 18→8) korrekt; start==end → immer aktiv."""
    if not settings.get("schedule_enabled"):
        return True
    try:
        start = int(settings.get("schedule_start", 0)) % 24
        end = int(settings.get("schedule_end", 0)) % 24
    except (TypeError, ValueError):
        return True
    if start == end:
        return True
    from datetime import datetime
    hour = datetime.now().hour
    return (start <= hour < end) if start < end else (hour >= start or hour < end)


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
    from .frame_sources.rtsp_source import find_camera_config
    from .vision_profiles import TRIGGER_MOTION, resolve_profile
    from .vision_watcher import WatchConfig

    source_id = str(source_record.get("source_id") or "")
    settings = source_record.get("settings") or {}
    mma = settings.get("motion_min_area_ratio")
    if not isinstance(mma, (int, float)) or not 0.001 <= mma <= 0.5:
        mma = 0.02

    fr = plugin_settings.get("face_recognition") or {}
    face_enabled = bool(fr.get("enabled", True))
    face_continuous = bool(fr.get("continuous", False))

    # Profil: rtsp_cameras-Eintrag > per-Source-Settings > Default (webcam).
    cam_cfg = find_camera_config(source_id) if source_id else None
    profile_name = (cam_cfg or {}).get("profile") or settings.get("profile") or ""
    profile = resolve_profile(str(profile_name))

    watch = plugin_settings.get("watch") or {}
    if profile.allow_local_detection:
        # Dumme Webcam: lokale Roh-Erkennung wie bisher.
        person_enabled = bool(watch.get("run_person_detect_on_motion", False))
        motion_gated = bool(settings.get("motion_gated", True))
        edge_ai = None
    else:
        # Intelligente Kamera: Roh-Erkennung macht die Kamera.
        person_enabled = False
        motion_gated = False
        edge_ai = _resolve_edge_ai(source_id, cam_cfg, plugin_settings)

    return WatchConfig(
        fps=2.0,  # Hintergrund-Default — niedrig, GPU-schonend
        motion_min_area_ratio=float(mma),
        save_event_frames=True,
        run_face_detect_on_motion=face_enabled,
        run_person_detect_on_motion=person_enabled,
        motion_gated=motion_gated,
        face_recognition_continuous=face_continuous and face_enabled,
        # VLM bleibt im Hintergrund AUS — opt-in nur über Live-Popup.
        run_vlm_on_motion=False,
        run_vlm_continuous=False,
        min_event_interval_sec=1.0,
        trigger_mode=profile.trigger_mode if edge_ai else TRIGGER_MOTION,
        edge_ai=edge_ai,
    )


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
    plugin = _load_plugin_settings()
    watcher = get_default_watcher()
    # Pro-Kamera-Zeitplan: außerhalb des Aktiv-Fensters nicht starten
    # (der schedule_supervisor zieht sie hoch, sobald das Fenster beginnt).
    if not _schedule_active_now(record.get("settings") or {}):
        logger.info("autostart: %s außerhalb des Zeitplan-Fensters — nicht gestartet", source_id)
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
    except Exception as e:  # noqa: BLE001
        logger.warning("autostart: start failed for %s: %s", source_id, e)
        return False


async def restore_or_stop_after_preview(source_id: str) -> None:
    """Called when the last live-preview SSE viewer for a source
    disconnects (popup closed). Tears down the popup's on-demand
    VLM/face overlay and returns the source to its baseline state.

    Two concepts, kept separate:

    * Continuous-VLM is GPU-hungry and only a deliberate opt-in in the
      live popup — it always ends here.
    * Motion + (CPU) face recognition is the permanent surveillance and
      belongs to the armed/``auto_start`` path, not the popup.

    So: a source that is armed AND ``auto_start`` is restored to its
    background watcher (motion + face per settings, VLM off); any other
    source — opened ad-hoc in the popup without being armed — is stopped
    entirely so nothing keeps running once the window is gone.
    """
    from .vision_store import VisionStore
    from .vision_watcher import get_default_watcher

    plugin = _load_plugin_settings()
    armed = bool(plugin.get("vigilantia_armed", False))
    record = VisionStore().get_source(source_id)
    auto_start = bool(record.get("auto_start")) if record else False

    if armed and auto_start:
        await start_background_watcher(source_id)
    else:
        await get_default_watcher().stop(source_id)


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
    except Exception as e:  # noqa: BLE001
        logger.warning("autostart: source listing failed: %s", e)
        return 0
    plugin = _load_plugin_settings()
    if plugin.get("vision_mode") == "off":
        logger.info("autostart: vision_mode='off' — skipping all sources")
        return 0
    if not plugin.get("vigilantia_armed", False):
        logger.info("autostart: vigilantia disarmed — skipping all sources")
        return 0
    targets = [r for r in sources if r.get("auto_start")]
    if not targets:
        return 0
    from .vision_watcher import get_default_watcher
    watcher = get_default_watcher()
    started = 0
    for record in targets:
        source_id = record.get("source_id") or ""
        if not source_id:
            continue
        # Pro-Kamera-Zeitplan: außerhalb des Aktiv-Fensters überspringen.
        if not _schedule_active_now(record.get("settings") or {}):
            logger.info("autostart: %s außerhalb des Zeitplan-Fensters — übersprungen", source_id)
            continue
        cfg = _build_background_config(record, plugin)
        try:
            await watcher.start(source_id, cfg)
            started += 1
            logger.info("autostart: background watcher running for %s", source_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("autostart: start failed for %s: %s", source_id, e)
    return started


async def schedule_supervisor() -> None:
    """Dauer-Supervisor: prüft minütlich die Pro-Kamera-Zeitpläne und startet/
    stoppt die Watcher entsprechend — aber nur wenn global scharf. Kameras ohne
    Zeitplan bleiben unangetastet (die regelt der armed-Master). Läuft ab
    Service-Boot, failure-tolerant."""
    from .vision_store import VisionStore
    from .vision_watcher import get_default_watcher
    while True:
        try:
            await asyncio.sleep(60)
            plugin = _load_plugin_settings()
            if plugin.get("vision_mode") == "off" or not plugin.get("vigilantia_armed", False):
                continue
            store = VisionStore()
            watcher = get_default_watcher()
            for record in store.list_sources():
                if not record.get("auto_start"):
                    continue
                settings = record.get("settings") or {}
                if not settings.get("schedule_enabled"):
                    continue
                source_id = record.get("source_id") or ""
                if not source_id:
                    continue
                active = _schedule_active_now(settings)
                running = watcher.is_running(source_id)
                if active and not running:
                    await start_background_watcher(source_id)
                    logger.info("schedule: %s im Zeitfenster → gestartet", source_id)
                elif not active and running:
                    await watcher.stop(source_id)
                    logger.info("schedule: %s außerhalb Zeitfenster → gestoppt", source_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("schedule supervisor error: %s", e)


_supervisor_task: "asyncio.Task[None] | None" = None


def ensure_schedule_supervisor() -> None:
    """Startet den :func:`schedule_supervisor` GENAU EINMAL pro Prozess.

    Muss aus einem laufenden Event-Loop gerufen werden (App-Lifespan).
    Idempotent über ein Modul-Task-Handle — ein zweiter Aufruf (z.B.
    Granian-Worker-Respawn, der den Lifespan erneut durchläuft) ist ein
    No-Op, solange der Task noch lebt. Ohne diesen Launcher lief der
    Supervisor nie: der Boot-Pfad rief nur start_all_background_watchers,
    sodass Kameras am Fenster-ENDE (z.B. 06:00) nie gestoppt wurden."""
    global _supervisor_task
    if _supervisor_task is not None and not _supervisor_task.done():
        return
    _supervisor_task = asyncio.create_task(schedule_supervisor())
    logger.info("schedule supervisor launched")


async def stop_all_background_watchers() -> int:
    """Entwaffnet die Alarmanlage — stoppt alle laufenden Watcher der
    Sources mit ``auto_start=True``. Lässt UI-getriggerte Watcher
    intakt, die zu Sources gehören, die NICHT auf auto_start stehen
    (die werden durch das Vorschau-Popup verwaltet)."""
    from .vision_store import VisionStore
    from .vision_watcher import get_default_watcher

    try:
        store = VisionStore()
        sources = store.list_sources()
    except Exception as e:  # noqa: BLE001
        logger.warning("autostart: source listing failed: %s", e)
        return 0
    watcher = get_default_watcher()
    stopped = 0
    for record in sources:
        if not record.get("auto_start"):
            continue
        source_id = record.get("source_id") or ""
        if not source_id:
            continue
        try:
            ok = await watcher.stop(source_id)
            if ok:
                stopped += 1
                logger.info("autostart: background watcher stopped for %s", source_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("autostart: stop failed for %s: %s", source_id, e)
    return stopped


def schedule_autostart() -> None:
    """Wird beim App-Boot aus aifred.py aufgerufen. Erzeugt einen
    fire-and-forget Task — ohne running event loop schedulen wir
    den Start in einen neuen Loop pro App-Worker."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(start_all_background_watchers())
            # Dauer-Supervisor für die Pro-Kamera-Zeitpläne (nur im laufenden
            # Loop sinnvoll — die one-shot-Pfade unten haben keinen).
            asyncio.create_task(schedule_supervisor())
        else:
            loop.run_until_complete(start_all_background_watchers())
    except RuntimeError:
        # Kein Event-Loop im aktuellen Thread — neuen erzeugen.
        try:
            asyncio.run(start_all_background_watchers())
        except Exception as e:  # noqa: BLE001
            logger.warning("autostart: scheduling failed: %s", e)
