"""The live analysis of a camera: the image model describing what it sees, faces recognised, or both, shown live on
the page (``/vision/events/{id}``, ``vision_event_bus``). It runs on the camera's one watcher with its own config.
When it ends — both switched off, or the last viewer gone — the camera returns to what the background watch says
(``vision_autostart.apply_background_watch``, the one place that decides it); while it runs, the background decisions
(the eye, the window supervisor) leave the camera alone. The state is the server's: every page sees the same.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass

from .config import VISION_LIVE_ANALYSIS_MIN_FPS
from .user_error import Missing, NotDone
from .vision_event_bus import expect_viewer
from .vision_watch_types import WatchConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveAnalysis:
    vlm: bool = False     # the image model describes the picture, one description per cooldown
    faces: bool = False   # faces (and with the eye's setting persons) are looked for and recognised

    @property
    def on(self) -> bool:
        return self.vlm or self.faces


_running: dict[str, LiveAnalysis] = {}


def analysis(source_id: str) -> LiveAnalysis:
    return _running.get(source_id, LiveAnalysis())


def is_live(source_id: str) -> bool:
    return source_id in _running


def live_config(source_id: str, wanted: LiveAnalysis) -> WatchConfig:
    """The watcher's config for the analysis: the live view's pace (frames, cooldown, face events), the eye's face
    and person settings, the camera's trigger threshold and its profile (an ai_camera detects on its own)."""
    from .vigilantia import vigilantia
    from .vision_autostart import motion_threshold, profile_watch_overrides
    from .vision_cameras import preview_settings
    from .vision_settings import load_vision_settings
    from .vision_store import VisionStore

    preview = preview_settings()
    eye = vigilantia()
    settings = (VisionStore().get_source(source_id) or {}).get("settings") or {}
    config = WatchConfig(
        fps=max(VISION_LIVE_ANALYSIS_MIN_FPS, preview.fps),
        motion_min_area_ratio=motion_threshold(settings),
        run_vlm_continuous=wanted.vlm,
        vlm_cooldown_sec=preview.vlm_cooldown_sec,
        run_face_detect_on_motion=wanted.faces,
        run_person_detect_on_motion=wanted.faces and eye.person_detect,
        face_recognition_continuous=wanted.faces and eye.face_continuous,
        min_event_interval_sec=preview.face_throttle_sec,
    )
    overrides = profile_watch_overrides(source_id, settings, load_vision_settings())
    return dataclasses.replace(config, **overrides) if overrides else config


async def set_analysis(source_id: str, wanted: LiveAnalysis) -> LiveAnalysis:
    """Start, change or end the live analysis of a camera; returns what runs now. ``Missing`` for no such camera,
    ``NotDone`` when the watcher does not start (the camera is back in its background state then)."""
    from . import frame_sources
    from .vision_autostart import apply_background_watch
    from .vision_watcher import get_default_watcher

    if frame_sources.get(source_id) is None:
        raise Missing("unknown_camera", camera=source_id)
    if not wanted.on:
        await end_analysis(source_id)
        return LiveAnalysis()
    config = await asyncio.to_thread(live_config, source_id, wanted)
    watcher = get_default_watcher()
    _running[source_id] = wanted
    await watcher.stop(source_id)        # the config of a running watcher is fixed: a new one takes over
    try:
        await watcher.start(source_id, config)
    except Exception as error:  # camera, detector or model: the page sees why, the log keeps the traceback
        logger.warning("live analysis of %s did not start: %s", source_id, error, exc_info=True)
        _running.pop(source_id, None)
        await apply_background_watch(source_id)
        raise NotDone("live_analysis_not_started", camera=source_id, reason=str(error)) from error
    expect_viewer(source_id)
    logger.info("live analysis of %s: %s", source_id, wanted)
    return wanted


async def renew_all() -> None:
    """Every running analysis takes the live view's pace anew (its frames, cooldown, face events changed)."""
    for source_id, wanted in list(_running.items()):
        await set_analysis(source_id, wanted)


async def end_analysis(source_id: str) -> None:
    """End the camera's live analysis (nothing to do without one) and give it back to the background watch."""
    from .vision_autostart import apply_background_watch
    from .vision_watcher import get_default_watcher

    if _running.pop(source_id, None) is None:
        return
    await get_default_watcher().stop(source_id)
    await apply_background_watch(source_id)
    logger.info("live analysis of %s ended", source_id)
