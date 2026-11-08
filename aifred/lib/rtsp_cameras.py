"""The RTSP cameras of the vision, for every interface: listed without their secrets, added, changed and taken out.
A camera lives in the plugin's ``settings.json`` (``rtsp_cameras``); its user and password in AIfred's .env
(``RTSP_<CRED>_USER``/``_PASSWORD``, through the credential broker). Its name is the base of its source id
(``cam/rtsp_<slug>``) and stays when the connection changes; the name shown is the camera's alias.

A change keeps every field of the entry the form does not know (the old form rebuilt the entry and lost
``ptz``, ``onvif_port`` and ``snap_channel``).
"""

from __future__ import annotations

import re
from typing import Any

from .user_error import Conflict, Invalid, Missing
from .vision_settings import load_vision_settings, save_vision_settings

PROFILES = ("webcam", "ai_camera")    # a plain stream, or a camera with its own detection (Reolink)
_INT_FIELDS = {"port": (1, 65535), "api_port": (1, 65535), "onvif_port": (1, 65535),
               "face_channel": (0, 15), "snap_channel": (0, 15), "channel": (0, 15)}
_TEXT_FIELDS = ("host", "path", "cred", "profile")
FIELDS = (*_TEXT_FIELDS, *_INT_FIELDS, "ptz")
_CRED = re.compile(r"[a-z0-9_]+")


class NameTaken(Conflict):
    """Another camera has this name."""


def _credential(cred: str) -> str:
    return f"rtsp_{cred}"


def _entries() -> list[dict[str, Any]]:
    cameras = load_vision_settings().get("rtsp_cameras")
    return [entry for entry in cameras if isinstance(entry, dict)] if isinstance(cameras, list) else []


def rtsp_cameras() -> list[dict[str, Any]]:
    """Every RTSP camera with its source id and whether user and password are set, never their values."""
    from .credential_broker import broker
    from .frame_sources.rtsp_source import source_id_for

    listed = []
    for entry in _entries():
        cred = str(entry.get("cred") or "")
        listed.append({
            **{key: entry[key] for key in ("name", *FIELDS) if key in entry},
            "source_id": source_id_for(str(entry.get("name", ""))),
            "user_set": bool(cred and broker.get(_credential(cred), "user")),
            "password_set": bool(cred and broker.get(_credential(cred), "password")),
        })
    return listed


def _checked(fields: dict[str, Any]) -> dict[str, Any]:
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise Invalid("unknown_fields", fields=", ".join(sorted(unknown)))
    checked: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _INT_FIELDS:
            if value is None:
                checked[key] = None
                continue
            low, high = _INT_FIELDS[key]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise Invalid("number_range", value=value, low=low, high=high)
            checked[key] = value
        elif key == "ptz":
            checked[key] = bool(value)
        else:
            checked[key] = str(value).strip()
    if "host" in checked and not checked["host"]:
        raise Invalid("camera_needs_host")
    if "path" in checked:
        checked["path"] = checked["path"].lstrip("/")
    if checked.get("cred") and not _CRED.fullmatch(checked["cred"]):
        raise Invalid("credential_name")
    if "profile" in checked and checked["profile"] not in PROFILES:
        raise Invalid("unknown_profile", value=checked["profile"])
    return checked


def _save(cameras: list[dict[str, Any]]) -> None:
    from .frame_sources import rescan

    settings = load_vision_settings()
    settings["rtsp_cameras"] = cameras
    save_vision_settings(settings)
    rescan()


def _keep_secrets(cred: str, user: str, password: str) -> None:
    """An empty field leaves a stored secret as it is (the form never shows them)."""
    from .credential_broker import broker

    if user:
        broker.persist(_credential(cred), "user", user)
    if password:
        broker.persist(_credential(cred), "password", password)


def add_rtsp_camera(name: str, fields: dict[str, Any], user: str = "", password: str = "") -> None:
    """A new camera; its name must be new (:class:`NameTaken` otherwise, ValueError for a field it cannot take;
    nothing is written then)."""
    name = name.strip()
    if not name:
        raise Invalid("camera_needs_name")
    if any(str(entry.get("name")) == name for entry in _entries()):
        raise NameTaken("camera_name_taken", name=name)
    checked = _checked({"profile": PROFILES[0], "port": 554, **fields})
    if "host" not in checked:
        raise Invalid("camera_needs_host")
    entry = {"name": name, **{key: value for key, value in checked.items() if value not in (None, "")}}
    _save([*_entries(), entry])
    if entry.get("cred"):
        _keep_secrets(str(entry["cred"]), user, password)


def change_rtsp_camera(name: str, fields: dict[str, Any], user: str = "", password: str = "") -> None:
    """Change a camera's connection: the fields given go into its entry, every other field stays; a field set to
    None or "" leaves the entry. KeyError for a camera that does not exist."""
    cameras = _entries()
    entry = next((camera for camera in cameras if str(camera.get("name")) == name), None)
    if entry is None:
        raise Missing("no_rtsp_camera", name=name)
    for key, value in _checked(fields).items():
        if value in (None, ""):
            entry.pop(key, None)
        else:
            entry[key] = value
    if "host" not in entry:
        raise Invalid("camera_needs_host")
    _save(cameras)
    if entry.get("cred"):
        _keep_secrets(str(entry["cred"]), user, password)


def delete_rtsp_camera(name: str) -> None:
    """Take a camera out; its user and password leave the .env when no other camera uses them. Its events stay
    (vision.db keeps the camera's record). KeyError for a camera that does not exist."""
    from .credential_broker import broker

    cameras = _entries()
    entry = next((camera for camera in cameras if str(camera.get("name")) == name), None)
    if entry is None:
        raise Missing("no_rtsp_camera", name=name)
    rest = [camera for camera in cameras if camera is not entry]
    _save(rest)
    cred = str(entry.get("cred") or "")
    if cred and not any(str(camera.get("cred") or "") == cred for camera in rest):
        broker.forget(_credential(cred), "user")
        broker.forget(_credential(cred), "password")
