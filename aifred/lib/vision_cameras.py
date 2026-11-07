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
    VISION_MOTION_MIN_AREA_RATIO,
    VISION_PREVIEW_FACE_THROTTLE_RANGE,
    VISION_PREVIEW_FPS_RANGE,
    VISION_PREVIEW_VLM_COOLDOWN_RANGE,
)

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
        ))
    return found


def camera(source_id: str) -> Camera | None:
    return next((found for found in cameras() if found.id == source_id), None)


def resolutions(source_id: str) -> list[tuple[str, str]]:
    """The modes the camera offers, as (value, name): the driver's default first, then what the driver
    honoured in the probe. Only the default for a camera that is away or cannot be probed."""
    from .frame_sources import get

    source = get(source_id)
    if source is None:
        raise KeyError(source_id)
    offered: list[tuple[str, str]] = [(DEFAULT_RESOLUTION, "")]
    detect = getattr(source, "detect_resolutions", None)
    if not callable(detect) or not source.info().available:
        return offered
    for width, height in detect():
        name = RESOLUTION_NAMES.get((width, height))
        offered.append((f"{width}x{height}", f"{width} × {height}" + (f" ({name})" if name else "")))
    return offered


def change_camera(source_id: str, *, alias: str | None = None, resolution: str | None = None,
                  prompt_context: str | None = None) -> None:
    """Keep what the user set for a camera (vision.db). A resolution must be one the camera offers."""
    from .vision_store import VisionStore

    store = VisionStore()
    settings: dict[str, Any] = {}
    if alias is not None:
        settings["alias"] = alias.strip()
    if resolution is not None:
        if resolution not in {value for value, _ in resolutions(source_id)}:
            raise ValueError(f"{source_id} offers no resolution {resolution!r}")
        settings["resolution"] = resolution
    if settings:
        store.patch_source_settings(source_id, settings)
    if prompt_context is not None:
        store.update_source_fields(source_id, prompt_context=prompt_context.strip())


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
            raise ValueError(f"live view setting {key} must lie in {bounds}, got {value!r}")
    if settings.teleprompter_mode not in TELEPROMPTER_MODES:
        raise ValueError(f"live view setting teleprompter_mode must be one of {TELEPROMPTER_MODES}")
    if settings.visible_sources is not None and not isinstance(settings.visible_sources, list):
        raise ValueError("live view setting visible_sources must be a list of camera ids")


def save_preview_settings(**changes: Any) -> PreviewSettings:
    """Change some of the live view's settings; refuses unknown keys and values outside their range
    (ValueError), the file stays as it was."""
    current = asdict(preview_settings())
    unknown = set(changes) - set(current)
    if unknown:
        raise ValueError(f"unknown live view settings: {sorted(unknown)}")
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
