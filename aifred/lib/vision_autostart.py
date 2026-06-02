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


def _build_background_config(
    source_record: dict[str, Any],
    plugin_settings: dict[str, Any],
) -> Any:
    """Baut die ``WatchConfig`` für den Hintergrund-Watcher einer Source.

    Liest per-Source aus dem Store-Record, fällt für globale Flags
    (face_recognition) auf das Plugin-Settings-JSON zurück.
    """
    from .vision_watcher import WatchConfig

    settings = source_record.get("settings") or {}
    mma = settings.get("motion_min_area_ratio")
    if not isinstance(mma, (int, float)) or not 0.001 <= mma <= 0.5:
        mma = 0.02

    fr = plugin_settings.get("face_recognition") or {}
    face_enabled = bool(fr.get("enabled", True))
    face_continuous = bool(fr.get("continuous", False))

    return WatchConfig(
        fps=2.0,  # Hintergrund-Default — niedrig, GPU-schonend
        motion_min_area_ratio=float(mma),
        save_event_frames=True,
        run_face_detect_on_motion=face_enabled,
        face_recognition_continuous=face_continuous and face_enabled,
        # VLM bleibt im Hintergrund AUS — opt-in nur über Live-Popup.
        run_vlm_on_motion=False,
        run_vlm_continuous=False,
        min_event_interval_sec=1.0,
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
    cfg = _build_background_config(record, plugin)
    watcher = get_default_watcher()
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
        cfg = _build_background_config(record, plugin)
        try:
            await watcher.start(source_id, cfg)
            started += 1
            logger.info("autostart: background watcher running for %s", source_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("autostart: start failed for %s: %s", source_id, e)
    return started


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
        else:
            loop.run_until_complete(start_all_background_watchers())
    except RuntimeError:
        # Kein Event-Loop im aktuellen Thread — neuen erzeugen.
        try:
            asyncio.run(start_all_background_watchers())
        except Exception as e:  # noqa: BLE001
            logger.warning("autostart: scheduling failed: %s", e)
