"""The vision page's settings: the eye (armed or not), how the image model stays and which one, face and person
detection, how long events are kept, the image model loaded or not. Thin over ``lib/vigilantia.py``; the cameras'
own settings are ``PATCH /vision/cameras/{id}`` (``api/cameras.py``)."""

import asyncio
from dataclasses import asdict

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from .app import api_app
from .schemas import ChoiceOption


class VigilantiaRow(BaseModel):
    plugin_enabled: bool = Field(description="The eye can only be armed with the vision plugin on")
    armed: bool
    mode: str
    model: str = Field(description="The chosen image model, as the list names it ('' = not in the list)")
    face_enabled: bool
    face_continuous: bool
    person_detect: bool
    retention_days: int
    modes: list[ChoiceOption]
    models: list[str] = Field(description="The image models (llama-swap describer profiles, then Ollama's)")
    alert_types: list[ChoiceOption] = Field(description="The event types a camera can alert on")
    event_intervals: list[ChoiceOption]


class VigilantiaChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str | None = None
    model: str | None = None
    face_enabled: bool | None = None
    face_continuous: bool | None = None
    person_detect: bool | None = None
    retention_days: int | None = None


class ArmedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    armed: bool


class ModelState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loaded: bool


def _row() -> VigilantiaRow:
    from ..config import VISION_ALERT_TYPES, VISION_EVENT_INTERVAL_CHOICES
    from ..formatting import format_number
    from ..i18n import t
    from ..prompt_loader import get_language
    from ..vigilantia import VISION_MODES, vigilantia
    from ..vision_routing import image_models, listed_name

    lang = get_language()
    current = vigilantia()
    models = image_models()
    fields = asdict(current)
    fields["model"] = listed_name(models, current.model) if current.model else ""
    mode_labels = {"on-demand": "vision_settings_mode_ondemand", "live": "vision_settings_mode_live"}
    return VigilantiaRow(
        **fields,
        modes=[ChoiceOption(id=mode, label=t(mode_labels[mode], lang=lang)) for mode in VISION_MODES],
        models=models,
        alert_types=[ChoiceOption(id=kind, label=t(f"vision_settings_alert_type_{kind}", lang=lang)) for kind in VISION_ALERT_TYPES],
        event_intervals=[ChoiceOption(id=f"{seconds:g}", label=f"{format_number(seconds, 0)} s") for seconds in VISION_EVENT_INTERVAL_CHOICES],
    )


@api_app.get("/vision/settings", response_model=VigilantiaRow, tags=["Vision"])
async def vision_settings() -> VigilantiaRow:
    """The eye and the general settings of the vision, with their choices."""
    return await asyncio.to_thread(_row)


@api_app.patch("/vision/settings", response_model=VigilantiaRow, tags=["Vision"])
async def vision_settings_change(change: VigilantiaChange) -> VigilantiaRow:
    """Change some of the general settings: a mode switched to live loads the model at once, a new model unloads
    the old one; 422 for a mode or retention the eye does not take."""
    from ..vigilantia import change as change_settings

    changes = change.model_dump(exclude_none=True)
    try:
        await change_settings(**changes)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    log_message(f"👁️ Vision settings: {sorted(changes)} changed")
    return await asyncio.to_thread(_row)


@api_app.put("/vision/armed", response_model=VigilantiaRow, tags=["Vision"])
async def vision_armed(change: ArmedChange) -> VigilantiaRow:
    """Arm or disarm the eye: every camera with its background switch on starts (inside its schedule) or stops.
    409 when the vision plugin is off."""
    from ..vigilantia import set_armed

    try:
        changed = await set_armed(change.armed)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    log_message(f"👁️ Eye {'armed' if change.armed else 'disarmed'}: {changed} watcher(s)")
    return await asyncio.to_thread(_row)


@api_app.get("/vision/model", response_model=ModelState, tags=["Vision"])
async def vision_model() -> ModelState:
    """Whether the image model is loaded, as its backend reports it."""
    from ..vigilantia import model_loaded

    return ModelState(loaded=await model_loaded())


@api_app.put("/vision/model", response_model=ModelState, tags=["Vision"])
async def vision_model_load(state: ModelState) -> ModelState:
    """Load the image model (also in on-demand mode) or unload it; answers with what the backend reports then.
    409 without a model chosen."""
    from ..vigilantia import load_model

    try:
        loaded = await load_model(state.loaded)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    log_message(f"👁️ Image model {'loaded' if loaded else 'not loaded'} (asked: {'load' if state.loaded else 'unload'})")
    return ModelState(loaded=loaded)


# ── Alert rules: which channels a category goes to, whether the image model describes it ──


class AlertRuleRow(BaseModel):
    category: str
    label: str
    sinks: list[str] = Field(description="The channels the category goes to")
    vlm: bool = Field(description="The image model describes the event before it goes out")


class AlertRules(BaseModel):
    channels: list[ChoiceOption] = Field(description="Every channel plugin an alert can go through")
    rules: list[AlertRuleRow]


class AlertRuleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sink: str | None = None
    sink_on: bool | None = None
    vlm: bool | None = None


def _rules() -> AlertRules:
    from ..alert_bus import vision_rules
    from ..i18n import t
    from ..plugin_base import plugin_display_name
    from ..plugin_registry import all_channels
    from ..prompt_loader import get_language

    lang = get_language()
    return AlertRules(
        channels=[ChoiceOption(id=name, label=plugin_display_name(channel, lang)) for name, channel in sorted(all_channels().items())],
        rules=[AlertRuleRow(category=category, label=t(f"alert_cat_{category}", lang=lang), **rule)
               for category, rule in vision_rules().items()],
    )


@api_app.get("/vision/alert-rules", response_model=AlertRules, tags=["Vision"])
async def vision_alert_rules() -> AlertRules:
    return await asyncio.to_thread(_rules)


@api_app.patch("/vision/alert-rules/{category}", response_model=AlertRules, tags=["Vision"])
async def vision_alert_rule_change(category: str, change: AlertRuleChange) -> AlertRules:
    """A channel of a category on or off (``sink`` with ``sink_on``), or its description by the image model; the
    alert dispatcher reads the rules again at once. 422 for an unknown category or channel."""
    from ..alert_bus import change_vision_rule

    if (change.sink is None) != (change.sink_on is None):
        raise HTTPException(status_code=422, detail="sink and sink_on come together")
    try:
        await asyncio.to_thread(change_vision_rule, category, sink=change.sink, sink_on=change.sink_on, vlm=change.vlm)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    log_message(f"🔔 Alert rule {category}: {change.model_dump(exclude_none=True)}")
    return await asyncio.to_thread(_rules)


# ── RTSP cameras: added, their connection changed, taken out (user and password never leave the server) ──


class RtspCamera(BaseModel):
    name: str = Field(description="The base of the source id; stays when the connection changes")
    source_id: str
    host: str
    port: int = 554
    path: str | None = None
    cred: str | None = Field(None, description="Names the credentials: RTSP_<CRED>_USER / _PASSWORD in .env")
    profile: str = "webcam"
    api_port: int | None = None
    onvif_port: int | None = None
    face_channel: int | None = None
    snap_channel: int | None = None
    channel: int | None = None
    ptz: bool | None = None
    user_set: bool
    password_set: bool


class RtspCameras(BaseModel):
    cameras: list[RtspCamera]
    profiles: list[str]


class RtspConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str | None = None
    port: int | None = None
    path: str | None = None
    cred: str | None = None
    profile: str | None = None
    api_port: int | None = None
    onvif_port: int | None = None
    face_channel: int | None = None
    snap_channel: int | None = None
    channel: int | None = None
    ptz: bool | None = None
    user: str = Field("", description="Empty: the stored user stays")
    password: str = Field("", description="Empty: the stored password stays")


class NewRtspCamera(RtspConnection):
    name: str


def _rtsp() -> RtspCameras:
    from ..rtsp_cameras import PROFILES, rtsp_cameras

    return RtspCameras(cameras=[RtspCamera(**camera) for camera in rtsp_cameras()], profiles=list(PROFILES))


@api_app.get("/vision/rtsp-cameras", response_model=RtspCameras, tags=["Vision"])
async def vision_rtsp_cameras() -> RtspCameras:
    return await asyncio.to_thread(_rtsp)


@api_app.post("/vision/rtsp-cameras", response_model=RtspCameras, status_code=201, tags=["Vision"])
async def vision_rtsp_camera_add(camera: NewRtspCamera) -> RtspCameras:
    """A new camera (its name must be new); 422 for a field it cannot take."""
    from ..rtsp_cameras import add_rtsp_camera

    fields = camera.model_dump(exclude_none=True, exclude={"name", "user", "password"})
    try:
        await asyncio.to_thread(add_rtsp_camera, camera.name, fields, camera.user, camera.password)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    log_message(f"📷 RTSP camera {camera.name!r} added ({sorted(fields)})")
    return await asyncio.to_thread(_rtsp)


@api_app.put("/vision/rtsp-cameras/{name}", response_model=RtspCameras, tags=["Vision"])
async def vision_rtsp_camera_change(name: str, connection: RtspConnection) -> RtspCameras:
    """Change the connection: the fields sent go into the entry, every other stays (a field sent as "" leaves it)."""
    from ..rtsp_cameras import change_rtsp_camera

    fields = connection.model_dump(exclude_unset=True, exclude={"user", "password"})
    try:
        await asyncio.to_thread(change_rtsp_camera, name, fields, connection.user, connection.password)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"no RTSP camera {name!r}") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    log_message(f"📷 RTSP camera {name!r} changed ({sorted(fields)})")
    return await asyncio.to_thread(_rtsp)


@api_app.delete("/vision/rtsp-cameras/{name}", response_model=RtspCameras, tags=["Vision"])
async def vision_rtsp_camera_delete(name: str) -> RtspCameras:
    """Take the camera out; its credentials leave the .env when no other camera uses them, its events stay."""
    from ..rtsp_cameras import delete_rtsp_camera

    try:
        await asyncio.to_thread(delete_rtsp_camera, name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"no RTSP camera {name!r}") from error
    log_message(f"📷 RTSP camera {name!r} taken out")
    return await asyncio.to_thread(_rtsp)
