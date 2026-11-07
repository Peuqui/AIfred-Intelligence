"""The live view of the vision page: the cameras (one list, ``lib/vision_cameras.py``), the view's own settings,
a camera's resolution and briefing, the head of a camera that can move (press, keep alive, release, home) and a
photo in full resolution. The images themselves come from ``/vision/stream`` and ``/vision/snapshot``
(``api/vision.py``). Probing a camera opens the device: every call that lists cameras runs in a thread."""

import asyncio
from dataclasses import asdict
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from .app import api_app
from .schemas import ChoiceOption

PtzDirection = Literal["left", "right", "up", "down", "zoom_in", "zoom_out"]


class CameraRow(BaseModel):
    id: str
    label: str = Field(description="The alias, else the hardware name (VisionStore.source_label)")
    alias: str
    hardware_name: str
    kind: str
    available: bool
    ptz: bool = Field(description="The head can be moved")
    auto_start: bool = Field(description="Watched in the background while the eye is armed")
    resolution: str = Field(description="'default' or 'WxH'")
    prompt_context: str = Field(description="The briefing the image analysis gets for this camera")
    motion_min_area_ratio: float
    alerts_enabled: bool = Field(description="Off: the camera still records, but sends no alerts")
    alert_types: list[str]
    quiet_enabled: bool
    quiet_start: int = Field(description="Hour 0–23")
    quiet_end: int
    schedule_enabled: bool = Field(description="Watched only inside the window")
    schedule_start: str = Field(description="HH:MM")
    schedule_end: str
    min_event_interval_sec: float


class PreviewSettingsRow(BaseModel):
    fps: float = Field(description="0 = a still image, renewed by hand")
    teleprompter_mode: Literal["overlay", "below"]
    vlm_cooldown_sec: float
    face_throttle_sec: float


class LiveView(BaseModel):
    cameras: list[CameraRow]
    visible: list[str] = Field(description="The cameras the view shows")
    settings: PreviewSettingsRow
    fps_choices: list[ChoiceOption]
    ptz_speeds: list[ChoiceOption]
    ptz_default_speed: str
    ptz_heartbeat_ms: int = Field(description="How often a held key sends keepalive")


class PreviewChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fps: float | None = None
    teleprompter_mode: Literal["overlay", "below"] | None = None
    vlm_cooldown_sec: float | None = None
    face_throttle_sec: float | None = None
    visible_sources: list[str] | None = None


class CameraChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str | None = None
    resolution: str | None = None
    prompt_context: str | None = None
    auto_start: bool | None = None
    alerts_enabled: bool | None = None
    alert_types: list[str] | None = None
    quiet_enabled: bool | None = None
    quiet_start: int | None = None
    quiet_end: int | None = None
    schedule_enabled: bool | None = None
    schedule_start: str | None = None
    schedule_end: str | None = None
    min_event_interval_sec: float | None = None


class PtzPress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: PtzDirection
    speed: str


class PhotoTaken(BaseModel):
    url: str


def _fps_label(fps: float, lang: str) -> str:
    from ..formatting import format_number
    from ..i18n import t

    if fps == 0:
        return t("vision_fps_still", lang=lang)
    if fps < 1:
        return t("vision_fps_slow", lang=lang, fps=format_number(fps, 1), seconds=format_number(1 / fps, 0))
    return t("vision_fps", lang=lang, fps=format_number(fps, 0))


def _live() -> LiveView:
    from ..config import (
        VISION_PREVIEW_FPS_CHOICES,
        VISION_PTZ_DEFAULT_SPEED,
        VISION_PTZ_HEARTBEAT_SEC,
        VISION_PTZ_SPEEDS,
    )
    from ..i18n import t
    from ..prompt_loader import get_language
    from ..vision_cameras import cameras, preview_settings, visible_cameras

    lang = get_language()
    found = cameras()
    settings = preview_settings()
    return LiveView(
        cameras=[CameraRow(**asdict(camera)) for camera in found],
        visible=visible_cameras(settings, found),
        settings=PreviewSettingsRow(**{key: value for key, value in asdict(settings).items() if key != "visible_sources"}),
        fps_choices=[ChoiceOption(id=f"{fps:g}", label=_fps_label(fps, lang)) for fps in VISION_PREVIEW_FPS_CHOICES],
        ptz_speeds=[ChoiceOption(id=speed, label=t(f"vision_preview_ptz_speed_{speed}", lang=lang)) for speed in VISION_PTZ_SPEEDS],
        ptz_default_speed=VISION_PTZ_DEFAULT_SPEED,
        ptz_heartbeat_ms=round(VISION_PTZ_HEARTBEAT_SEC * 1000),
    )


@api_app.get("/vision/live", response_model=LiveView, tags=["Vision"])
async def vision_live() -> LiveView:
    """The cameras, which of them the view shows and the view's settings."""
    return await asyncio.to_thread(_live)


@api_app.patch("/vision/live", response_model=LiveView, tags=["Vision"])
async def vision_live_change(change: PreviewChange) -> LiveView:
    """Frame rate, layout, rhythm of the analysis or the cameras shown; 422 for a value outside its range."""
    from ..vision_cameras import save_preview_settings

    try:
        await asyncio.to_thread(save_preview_settings, **change.model_dump(exclude_none=True))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return await asyncio.to_thread(_live)


@api_app.post("/vision/cameras/rescan", response_model=LiveView, tags=["Vision"])
async def vision_cameras_rescan() -> LiveView:
    """Look for cameras again (a webcam plugged in, an RTSP camera added)."""
    from ..frame_sources import rescan

    await asyncio.to_thread(rescan)
    return await asyncio.to_thread(_live)


@api_app.get("/vision/cameras/{source_id:path}/resolutions", response_model=list[ChoiceOption], tags=["Vision"])
async def vision_camera_resolutions(source_id: str) -> list[ChoiceOption]:
    """The modes the camera offers (a probe of the device): its driver's default first."""
    from ..i18n import t
    from ..prompt_loader import get_language
    from ..vision_cameras import resolutions

    try:
        offered = await asyncio.to_thread(resolutions, source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"unknown camera: {source_id}") from error
    lang = get_language()
    return [ChoiceOption(id=value, label=label or t("vision_resolution_default", lang=lang)) for value, label in offered]


@api_app.patch("/vision/cameras/{source_id:path}", response_model=CameraRow, tags=["Vision"])
async def vision_camera_change(source_id: str, change: CameraChange) -> CameraRow:
    """What the user sets for a camera: name, resolution, briefing, background switch, alerts, quiet hours, active
    window, event interval; 422 for a value the camera cannot take. A change of the background switch or the
    window takes effect at once (the background watcher starts or stops as the eye and the window say)."""
    from ..vision_autostart import apply_background_watch
    from ..vision_cameras import camera, change_camera

    try:
        watch = await asyncio.to_thread(lambda: change_camera(source_id, **change.model_dump(exclude_none=True)))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"unknown camera: {source_id}") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if watch:
        await apply_background_watch(source_id)
    changed = await asyncio.to_thread(camera, source_id)
    if changed is None:
        raise HTTPException(status_code=404, detail=f"unknown camera: {source_id}")
    log_message(f"📷 Camera {source_id}: {sorted(change.model_dump(exclude_none=True))} changed")
    return CameraRow(**asdict(changed))


async def _ptz(action: str, source_id: str, *args: str) -> None:
    from ..ptz_control import PTZError, ptz_driver

    try:
        await getattr(ptz_driver, action)(source_id, *args)
    except PTZError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@api_app.post("/vision/ptz/{source_id:path}/press", status_code=204, tags=["Vision"])
async def vision_ptz_press(source_id: str, press: PtzPress) -> None:
    """The head moves while the key is held: the page sends ``keepalive`` every few hundred ms and ``release``
    at the end; without a sign of life the server stops it after VISION_PTZ_DEADMAN_SEC."""
    from ..config import VISION_PTZ_SPEEDS

    if press.speed not in VISION_PTZ_SPEEDS:
        raise HTTPException(status_code=422, detail=f"unknown speed {press.speed!r}")
    await _ptz("press", source_id, press.direction, press.speed)


@api_app.post("/vision/ptz/{source_id:path}/keepalive", status_code=204, tags=["Vision"])
async def vision_ptz_keepalive(source_id: str) -> None:
    await _ptz("keep_alive", source_id)


@api_app.post("/vision/ptz/{source_id:path}/release", status_code=204, tags=["Vision"])
async def vision_ptz_release(source_id: str) -> None:
    await _ptz("release", source_id)


@api_app.post("/vision/ptz/{source_id:path}/home", status_code=204, tags=["Vision"])
async def vision_ptz_home(source_id: str) -> None:
    """The head goes to the camera's guard position."""
    await _ptz("home", source_id)


@api_app.post("/vision/photo/{source_id:path}", response_model=PhotoTaken, tags=["Vision"])
async def vision_photo(source_id: str) -> PhotoTaken:
    """A photo in full resolution (the camera's own snapshot when it has one), kept under the vigilantia folder."""
    from ..vision_photo import PhotoError, take_photo

    try:
        url = await take_photo(source_id)
    except PhotoError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    log_message(f"📸 Photo of {source_id}: {url}")
    return PhotoTaken(url=url)
