"""The eye of the vision and its general settings, for every interface (the vision page of Vue; the Reflex copy keeps
its own setters until it goes): armed or not with its consequence (every background watcher started or stopped),
how the image model stays (on demand or live), which model, the face and person detection, how long events are
kept, the image model loaded or not. Everything lives in the plugin's ``settings.json`` (``vision_settings``).

Changes take effect as before: a mode switched to live loads the model at once, a new model unloads the old one
(an Ollama model; llama-swap swaps a describer itself), face and person detection act at a watcher's next start.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .user_error import Conflict, Invalid
from .vision_settings import load_vision_settings, save_vision_settings

VISION_MODES = ("on-demand", "live")


@dataclass(frozen=True)
class Vigilantia:
    plugin_enabled: bool      # the eye can only be armed with the vision plugin on
    armed: bool
    mode: str                 # on-demand: the image model loads when asked; live: it stays loaded
    model: str
    face_enabled: bool
    face_continuous: bool     # faces looked for all the time, not only on motion
    person_detect: bool       # persons looked for on motion
    retention_days: int       # how long frames, faces and events are kept


def vigilantia() -> Vigilantia:
    """The stored settings. A mode the eye does not know is an error (ValueError), not a default."""
    from .plugin_registry import is_plugin_enabled
    from .vision_cleanup import retention_days

    settings = load_vision_settings()
    mode = str(settings.get("vision_mode", VISION_MODES[0]))
    if mode not in VISION_MODES:
        raise ValueError(f"vision_mode must be one of {VISION_MODES}, is {mode!r}")
    faces = settings.get("face_recognition") or {}
    watch = settings.get("watch") or {}
    return Vigilantia(
        plugin_enabled=is_plugin_enabled("vision"),
        armed=bool(settings.get("vigilantia_armed", False)),
        mode=mode,
        model=str((settings.get("vlm") or {}).get("model") or ""),
        face_enabled=bool(faces.get("enabled", True)),
        face_continuous=bool(faces.get("continuous", False)),
        person_detect=bool(watch.get("run_person_detect_on_motion", False)),
        retention_days=retention_days(),
    )


def _write(change: dict[str, Any]) -> None:
    """Merge ``change`` (top level, one level deep for the sections) into the plugin's settings."""
    settings = load_vision_settings()
    for key, value in change.items():
        if isinstance(value, dict):
            settings[key] = {**(settings.get(key) or {}), **value}
        else:
            settings[key] = value
    save_vision_settings(settings)


async def set_armed(armed: bool) -> int:
    """Arm or disarm the eye: every background watcher of a camera with its background switch on starts (inside
    its schedule) or stops. Returns how many changed. Arming needs the vision plugin (ValueError)."""
    from .vision_autostart import (
        start_all_background_watchers,
        stop_all_background_watchers,
    )

    if armed and not vigilantia().plugin_enabled:
        raise Conflict("eye_needs_plugin")
    _write({"vigilantia_armed": armed})
    return await (start_all_background_watchers() if armed else stop_all_background_watchers())


async def change(
    *, mode: str | None = None, model: str | None = None, face_enabled: bool | None = None,
    face_continuous: bool | None = None, person_detect: bool | None = None, retention_days: int | None = None,
) -> Vigilantia:
    """Change some of the general settings, with their consequences (see the module). Refuses a mode the eye does
    not know and a retention outside its range (ValueError), nothing is written then."""
    from .vision_cleanup import RETENTION_DAYS_RANGE

    if mode is not None and mode not in VISION_MODES:
        raise Invalid("unknown_mode", value=mode)
    if retention_days is not None and not RETENTION_DAYS_RANGE[0] <= retention_days <= RETENTION_DAYS_RANGE[1]:
        raise Invalid("number_range", value=retention_days, low=RETENTION_DAYS_RANGE[0], high=RETENTION_DAYS_RANGE[1])
    before = vigilantia()
    faces = {key: value for key, value in (("enabled", face_enabled), ("continuous", face_continuous),
                                           ("retention_days", retention_days)) if value is not None}
    update: dict[str, Any] = {}
    if mode is not None:
        update["vision_mode"] = mode
    if model is not None:
        update["vlm"] = {"model": model}
    if faces:
        update["face_recognition"] = faces
    if person_detect is not None:
        update["watch"] = {"run_person_detect_on_motion": person_detect}
    _write(update)
    if mode == "live" and before.mode != "live":
        from .vision_prewarm import prewarm_vlm

        await prewarm_vlm()
    if model is not None and before.model and before.model != model:
        from .vision_routing import visiond_profile_for

        if visiond_profile_for(before.model) is None:   # an Ollama model; llama-swap swaps a describer itself
            from .vision_prewarm import unload_vlm_model

            await unload_vlm_model(before.model)
    return vigilantia()


async def model_loaded() -> bool:
    from .vision_prewarm import is_vlm_loaded

    model = vigilantia().model
    return bool(model) and await is_vlm_loaded(model)


async def load_model(load: bool) -> bool:
    """Load the image model (also in on-demand mode, with the plugin's keep_alive) or unload it; returns whether it
    is loaded afterwards, as the backend reports it, never as the call answered."""
    from .config import VISION_VLM_KEEP_ALIVE_DEFAULT
    from .vision_prewarm import prewarm_vlm, unload_vlm_model

    model = vigilantia().model
    if not model:
        raise Conflict("no_image_model")
    if load:
        keep_alive = str((load_vision_settings().get("vlm") or {}).get("keep_alive", VISION_VLM_KEEP_ALIVE_DEFAULT))
        await prewarm_vlm(keep_alive_override=keep_alive)
    else:
        await unload_vlm_model(model)
    return await model_loaded()
