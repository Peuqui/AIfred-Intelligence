"""The cameras of the vision, for every interface: ONE list with what each camera is and how it is set (the
live view and the settings read the same; vision.db through ``VisionStore``, the name always
``VisionStore.source_label``), the resolutions a camera offers (a probe, on request) and the live view's own
settings (``data/vision_preview.json``: frame rate, layout, rhythm of the analysis, visible cameras).

Asking a source for its ``info()`` probes the device (V4L2 open, RTSP connection): callers run these
functions in a thread.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import (
    DATA_DIR,
    VISION_ALERT_TYPES,
    VISION_MIN_EVENT_INTERVAL_RANGE,
    VISION_MIN_EVENT_INTERVAL_SEC,
    VISION_MOTION_MIN_AREA_RATIO,
    VISION_PREVIEW_FACE_THROTTLE_RANGE,
    VISION_PREVIEW_FPS_RANGE,
    VISION_PREVIEW_VLM_COOLDOWN_RANGE,
    VISION_QUIET_HOURS_DEFAULT,
    VISION_SCHEDULE_DEFAULT,
)
from .user_error import Invalid, Missing

logger = logging.getLogger(__name__)

PREVIEW_SETTINGS_FILE = DATA_DIR / "vision_preview.json"

# Names of common modes for the resolution choice; a camera offers only those its driver honours.
RESOLUTION_NAMES: dict[tuple[int, int], str] = {
    (320, 240): "QVGA", (640, 480): "VGA", (800, 600): "SVGA", (1024, 768): "XGA", (1280, 720): "HD",
    (1280, 960): "SXGA-", (1600, 1200): "UXGA", (1920, 1080): "Full HD", (2560, 1440): "QHD", (3840, 2160): "4K",
}
DEFAULT_RESOLUTION = "default"   # whatever the driver picks without width and height


@dataclass(frozen=True)
class Camera:
    id: str
    label: str              # VisionStore.source_label: the alias, else the hardware name
    alias: str
    hardware_name: str
    kind: str               # webcam, rtsp, …
    available: bool
    ptz: bool               # the head can be moved (rtsp_cameras: ptz)
    auto_start: bool        # watched in the background while the eye is armed
    resolution: str         # DEFAULT_RESOLUTION or "WxH"
    prompt_context: str     # the briefing the image analysis gets for this camera
    motion_min_area_ratio: float
    alerts_enabled: bool    # off: the camera still records, but sends no alerts
    alert_types: list[str]  # the event types it alerts on (VISION_ALERT_TYPES)
    quiet_enabled: bool     # no alerts between quiet_start and quiet_end (whole hours)
    quiet_start: int
    quiet_end: int
    schedule_enabled: bool  # watched only between schedule_start and schedule_end ("HH:MM")
    schedule_start: str
    schedule_end: str
    min_event_interval_sec: float


def cameras() -> list[Camera]:
    """Every registered source, with its stored settings (one store query). A source whose ``info()``
    fails is left out and logged: one broken camera must not empty the list."""
    from .frame_sources import list_all
    from .ptz_control import get_controller
    from .vision_store import VisionStore

    stored = {str(record["source_id"]): record for record in VisionStore().list_sources()}
    found: list[Camera] = []
    for source in list_all():
        try:
            info = source.info()
        except Exception as error:  # one broken source must not empty the list; logged with its traceback
            logger.warning("camera %s left out, info() failed: %s", source.source_id, error, exc_info=True)
            continue
        hardware = str(info.display_name or info.source_id)
        record = stored.get(info.source_id) or {"source_id": info.source_id, "display_name": hardware, "settings": {}}
        settings = record.get("settings") or {}
        found.append(Camera(
            id=info.source_id,
            label=VisionStore.source_label({**record, "display_name": record.get("display_name") or hardware}),
            alias=str(settings.get("alias") or "").strip(),
            hardware_name=hardware,
            kind=str(info.kind),
            available=bool(info.available),
            ptz=get_controller(info.source_id) is not None,
            auto_start=bool(record.get("auto_start", False)),
            resolution=str(settings.get("resolution") or DEFAULT_RESOLUTION),
            prompt_context=str(record.get("prompt_context") or "").strip(),
            motion_min_area_ratio=float(settings.get("motion_min_area_ratio", VISION_MOTION_MIN_AREA_RATIO)),
            alerts_enabled=bool(settings.get("alerts_enabled", True)),
            alert_types=[str(kind) for kind in settings.get("alert_types", VISION_ALERT_TYPES)],
            quiet_enabled=bool(settings.get("quiet_enabled", False)),
            quiet_start=int(settings.get("quiet_start", VISION_QUIET_HOURS_DEFAULT[0])),
            quiet_end=int(settings.get("quiet_end", VISION_QUIET_HOURS_DEFAULT[1])),
            schedule_enabled=bool(settings.get("schedule_enabled", False)),
            schedule_start=_hhmm(settings.get("schedule_start", VISION_SCHEDULE_DEFAULT[0])),
            schedule_end=_hhmm(settings.get("schedule_end", VISION_SCHEDULE_DEFAULT[1])),
            min_event_interval_sec=float(settings.get("min_event_interval_sec", VISION_MIN_EVENT_INTERVAL_SEC)),
        ))
    return found


def _hhmm(value: Any) -> str:
    from .vision_autostart import schedule_minutes

    minutes = schedule_minutes(value)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def camera(source_id: str) -> Camera | None:
    return next((found for found in cameras() if found.id == source_id), None)


def resolutions(source_id: str) -> list[tuple[str, str]]:
    """The modes the camera offers, as (value, name): the driver's default first, then what the driver
    honoured in the probe. Only the default for a camera that is away or cannot be probed."""
    from .frame_sources import get

    source = get(source_id)
    if source is None:
        raise Missing("unknown_camera", camera=source_id)
    offered: list[tuple[str, str]] = [(DEFAULT_RESOLUTION, "")]
    detect = getattr(source, "detect_resolutions", None)
    if not callable(detect) or not source.info().available:
        return offered
    for width, height in detect():
        name = RESOLUTION_NAMES.get((width, height))
        offered.append((f"{width}x{height}", f"{width} × {height}" + (f" ({name})" if name else "")))
    return offered


WATCH_FIELDS = frozenset({"auto_start", "schedule_enabled", "schedule_start", "schedule_end"})


def change_camera(source_id: str, **change: Any) -> bool:
    """Keep what the user set for a camera (vision.db): ``alias``, ``resolution`` (one the camera offers),
    ``prompt_context``, ``auto_start``, ``alerts_enabled``, ``alert_types`` (of VISION_ALERT_TYPES),
    ``quiet_enabled``, ``quiet_start``/``quiet_end`` (hours 0–23), ``schedule_enabled``,
    ``schedule_start``/``schedule_end`` ("HH:MM"), ``min_event_interval_sec`` (its range). Refuses an unknown
    field or a value it cannot take (ValueError), nothing is written then. Returns whether the change concerns
    the background watcher (``vision_autostart.apply_background_watch`` decides then)."""
    import re

    from .vision_store import VisionStore

    unknown = set(change) - {"alias", "resolution", "prompt_context", "auto_start", "alerts_enabled", "alert_types",
                             "quiet_enabled", "quiet_start", "quiet_end", "schedule_enabled", "schedule_start",
                             "schedule_end", "min_event_interval_sec"}
    if unknown:
        raise Invalid("unknown_fields", fields=", ".join(sorted(unknown)))
    settings: dict[str, Any] = {}
    for key in ("alerts_enabled", "quiet_enabled", "schedule_enabled"):
        if key in change:
            settings[key] = bool(change[key])
    if "alias" in change:
        settings["alias"] = str(change["alias"]).strip()
    if "resolution" in change:
        if change["resolution"] not in {value for value, _ in resolutions(source_id)}:
            raise Invalid("no_such_resolution", camera=source_id, resolution=change["resolution"])
        settings["resolution"] = change["resolution"]
    if "alert_types" in change:
        kinds = list(change["alert_types"])
        if not set(kinds) <= set(VISION_ALERT_TYPES):
            raise Invalid("unknown_fields", fields=", ".join(sorted(set(kinds) - set(VISION_ALERT_TYPES))))
        settings["alert_types"] = [kind for kind in VISION_ALERT_TYPES if kind in kinds]
    for key in ("quiet_start", "quiet_end"):
        if key in change:
            if not 0 <= int(change[key]) <= 23:
                raise Invalid("number_range", value=change[key], low=0, high=23)
            settings[key] = int(change[key])
    for key in ("schedule_start", "schedule_end"):
        if key in change:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(change[key])):
                raise Invalid("time_format", value=change[key])
            settings[key] = str(change[key])
    if "min_event_interval_sec" in change:
        interval = float(change["min_event_interval_sec"])
        if not VISION_MIN_EVENT_INTERVAL_RANGE[0] <= interval <= VISION_MIN_EVENT_INTERVAL_RANGE[1]:
            raise Invalid("number_range", value=interval, low=VISION_MIN_EVENT_INTERVAL_RANGE[0], high=VISION_MIN_EVENT_INTERVAL_RANGE[1])
        settings["min_event_interval_sec"] = interval
    store = VisionStore()
    if settings:
        store.patch_source_settings(source_id, settings)
    fields: dict[str, Any] = {}
    if "prompt_context" in change:
        fields["prompt_context"] = str(change["prompt_context"]).strip()
    if "auto_start" in change:
        fields["auto_start"] = bool(change["auto_start"])
    if fields:
        store.update_source_fields(source_id, **fields)
    return bool(WATCH_FIELDS & set(change))


# ── The live view's own settings ─────────────────────────────────────────


@dataclass(frozen=True)
class PreviewSettings:
    fps: float = 1.0                    # 0 = a still image, renewed by hand
    teleprompter_mode: str = "overlay"  # overlay or below: where the analysis' text stands
    vlm_cooldown_sec: float = 5.0       # pause between two image analyses
    face_throttle_sec: float = 1.0      # pause between two face events of a camera
    visible_sources: list[str] | None = field(default=None)   # None: never chosen (the first available shows)


TELEPROMPTER_MODES = ("overlay", "below")


def preview_settings() -> PreviewSettings:
    """The stored settings; a key never set has its default. Only AIfred writes the file (checked,
    ``save_preview_settings``): a value outside its range is an error, not a default (ValueError)."""
    data: dict[str, Any] = json.loads(PREVIEW_SETTINGS_FILE.read_text(encoding="utf-8")) if PREVIEW_SETTINGS_FILE.exists() else {}
    stored = {key: data[key] for key in asdict(PreviewSettings()) if key in data}
    settings = PreviewSettings(**stored)
    _check(settings)
    return settings


def _check(settings: PreviewSettings) -> None:
    for key, bounds in (("fps", VISION_PREVIEW_FPS_RANGE), ("vlm_cooldown_sec", VISION_PREVIEW_VLM_COOLDOWN_RANGE),
                        ("face_throttle_sec", VISION_PREVIEW_FACE_THROTTLE_RANGE)):
        value = getattr(settings, key)
        if not isinstance(value, (int, float)) or not bounds[0] <= value <= bounds[1]:
            raise Invalid("number_range", value=value, low=bounds[0], high=bounds[1])
    if settings.teleprompter_mode not in TELEPROMPTER_MODES:
        raise Invalid("unknown_mode", value=settings.teleprompter_mode)
    if settings.visible_sources is not None and not isinstance(settings.visible_sources, list):
        raise Invalid("unreadable_request", fields="visible_sources")


def save_preview_settings(**changes: Any) -> PreviewSettings:
    """Change some of the live view's settings; refuses unknown keys and values outside their range
    (ValueError), the file stays as it was."""
    current = asdict(preview_settings())
    unknown = set(changes) - set(current)
    if unknown:
        raise Invalid("unknown_fields", fields=", ".join(sorted(unknown)))
    settings = PreviewSettings(**{**current, **changes})
    _check(settings)
    PREVIEW_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PREVIEW_SETTINGS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    temporary.replace(PREVIEW_SETTINGS_FILE)
    return settings


def visible_cameras(settings: PreviewSettings, found: list[Camera]) -> list[str]:
    """The cameras the live view shows: the chosen ones that still exist (an empty choice stays empty),
    the first available one while nothing was ever chosen."""
    ids = {camera.id for camera in found}
    if settings.visible_sources is not None:
        return [source for source in settings.visible_sources if source in ids]
    first = next((camera.id for camera in found if camera.available), None)
    return [first] if first else []
