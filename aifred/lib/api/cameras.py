"""The live view of the vision page: the cameras (one list, ``lib/vision_cameras.py``), the view's own settings,
a camera's resolution and briefing, the head of a camera that can move (press, keep alive, release, home) and a
photo in full resolution. The images themselves come from ``/vision/stream`` and ``/vision/snapshot``
(``api/vision.py``). Probing a camera opens the device: every call that lists cameras runs in a thread."""

import asyncio
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from ..user_error import Invalid, Missing
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
    motion_min_area_ratio: float = Field(description="Share of the picture that must move to trigger the detection")
    detects_itself: bool = Field(description="An ai_camera: it detects on its own, ignore and ROI zones do not act there")
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


class LiveAnalysisRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vlm: bool = Field(description="The image model describes the picture")
    faces: bool = Field(description="Faces (and with the eye's setting persons) are recognised")


class LiveView(BaseModel):
    cameras: list[CameraRow]
    visible: list[str] = Field(description="The cameras the view shows")
    analysis: dict[str, LiveAnalysisRow] = Field(description="The cameras in a live analysis (the server's, every page sees it)")
    settings: PreviewSettingsRow
    fps_choices: list[ChoiceOption]
    cooldown_choices: list[ChoiceOption] = Field(description="Seconds between two descriptions of a live analysis")
    face_throttle_choices: list[ChoiceOption] = Field(description="Seconds between two face events of a live analysis")
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
    motion_min_area_ratio: float | None = None


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


def _interval_label(seconds: float, lang: str) -> str:
    """Seconds of a pause; below one second also how many a second."""
    from ..formatting import format_number
    from ..i18n import t

    digits = 0 if seconds == int(seconds) else 1
    if seconds < 1:
        return t("vision_interval_rate", lang=lang, seconds=format_number(seconds, digits), rate=format_number(1 / seconds, 0))
    return t("vision_interval", lang=lang, seconds=format_number(seconds, digits))


def _live() -> LiveView:
    from ..config import (
        VISION_PREVIEW_FACE_THROTTLE_CHOICES,
        VISION_PREVIEW_FPS_CHOICES,
        VISION_PREVIEW_VLM_COOLDOWN_CHOICES,
        VISION_PTZ_DEFAULT_SPEED,
        VISION_PTZ_HEARTBEAT_SEC,
        VISION_PTZ_SPEEDS,
    )
    from ..i18n import t
    from ..prompt_loader import get_language
    from ..vision_cameras import cameras, preview_settings, visible_cameras
    from ..vision_live_analysis import analysis

    lang = get_language()
    found = cameras()
    settings = preview_settings()
    return LiveView(
        cameras=[CameraRow(**asdict(camera)) for camera in found],
        visible=visible_cameras(settings, found),
        analysis={camera.id: LiveAnalysisRow(**asdict(state)) for camera in found if (state := analysis(camera.id)).on},
        settings=PreviewSettingsRow(**{key: value for key, value in asdict(settings).items() if key != "visible_sources"}),
        fps_choices=[ChoiceOption(id=f"{fps:g}", label=_fps_label(fps, lang)) for fps in VISION_PREVIEW_FPS_CHOICES],
        cooldown_choices=[ChoiceOption(id=f"{value:g}", label=_interval_label(value, lang)) for value in VISION_PREVIEW_VLM_COOLDOWN_CHOICES],
        face_throttle_choices=[ChoiceOption(id=f"{value:g}", label=_interval_label(value, lang))
                               for value in VISION_PREVIEW_FACE_THROTTLE_CHOICES],
        ptz_speeds=[ChoiceOption(id=speed, label=t(f"vision_preview_ptz_speed_{speed}", lang=lang)) for speed in VISION_PTZ_SPEEDS],
        ptz_default_speed=VISION_PTZ_DEFAULT_SPEED,
        ptz_heartbeat_ms=round(VISION_PTZ_HEARTBEAT_SEC * 1000),
    )


@api_app.get("/vision/live", response_model=LiveView, tags=["Vision"])
async def vision_live() -> LiveView:
    """The cameras, which of them the view shows and the view's settings."""
    return await asyncio.to_thread(_live)


@api_app.put("/vision/live/analysis/{source_id:path}", response_model=LiveAnalysisRow, tags=["Vision"])
async def vision_live_analysis(source_id: str, wanted: LiveAnalysisRow) -> LiveAnalysisRow:
    """Start, change or end a camera's live analysis (both off: back to its background watch). It lasts while a
    page follows ``GET /vision/events/{id}`` (5 s after the last one leaves it ends); 404 for no such camera, 500
    when the watcher does not start."""
    from ..vision_live_analysis import LiveAnalysis, set_analysis

    running = await set_analysis(source_id, LiveAnalysis(vlm=wanted.vlm, faces=wanted.faces))
    log_message(f"👁️ Live analysis {source_id}: image model {'on' if running.vlm else 'off'}, faces {'on' if running.faces else 'off'}")
    return LiveAnalysisRow(vlm=running.vlm, faces=running.faces)


@api_app.patch("/vision/live", response_model=LiveView, tags=["Vision"])
async def vision_live_change(change: PreviewChange) -> LiveView:
    """Frame rate, layout, rhythm of the analysis or the cameras shown; 422 for a value outside its range. A running
    live analysis takes a new pace at once."""
    from ..vision_cameras import save_preview_settings
    from ..vision_live_analysis import renew_all

    changes = change.model_dump(exclude_none=True)
    await asyncio.to_thread(save_preview_settings, **changes)
    if changes.keys() & {"fps", "vlm_cooldown_sec", "face_throttle_sec"}:
        await renew_all()
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

    offered = await asyncio.to_thread(resolutions, source_id)
    lang = get_language()
    return [ChoiceOption(id=value, label=label or t("vision_resolution_default", lang=lang)) for value, label in offered]


@api_app.patch("/vision/cameras/{source_id:path}", response_model=CameraRow, tags=["Vision"])
async def vision_camera_change(source_id: str, change: CameraChange) -> CameraRow:
    """What the user sets for a camera: name, resolution, briefing, background switch, alerts, quiet hours, active
    window, event interval; 422 for a value the camera cannot take. A change of the background switch or the
    window takes effect at once (the background watcher starts or stops as the eye and the window say)."""
    from ..vision_autostart import apply_background_watch
    from ..vision_cameras import camera, change_camera

    watch = await asyncio.to_thread(lambda: change_camera(source_id, **change.model_dump(exclude_none=True)))
    if watch:
        await apply_background_watch(source_id)
    changed = await asyncio.to_thread(camera, source_id)
    if changed is None:
        raise Missing("unknown_camera", camera=source_id)
    log_message(f"📷 Camera {source_id}: {sorted(change.model_dump(exclude_none=True))} changed")
    return CameraRow(**asdict(changed))


async def _ptz(action: str, source_id: str, *args: str) -> None:
    from ..ptz_control import ptz_driver

    await getattr(ptz_driver, action)(source_id, *args)


@api_app.post("/vision/ptz/{source_id:path}/press", status_code=204, tags=["Vision"])
async def vision_ptz_press(source_id: str, press: PtzPress) -> None:
    """The head moves while the key is held: the page sends ``keepalive`` every few hundred ms and ``release``
    at the end; without a sign of life the server stops it after VISION_PTZ_DEADMAN_SEC."""
    from ..config import VISION_PTZ_SPEEDS

    if press.speed not in VISION_PTZ_SPEEDS:
        raise Invalid("unknown_speed", speed=press.speed)
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
    from ..vision_photo import take_photo

    url = await take_photo(source_id)
    log_message(f"📸 Photo of {source_id}: {url}")
    return PhotoTaken(url=url)
