"""Saving the RTSP connection form keeps every field of the camera the form does not show.

07.11.2026: save_rtsp_camera rebuilt the entry from the form and lost ptz, onvif_port and
snap_channel of the real Reolink (the head no longer moved, snapshots took the wrong lens). A new
camera with the name of an existing one replaced it the same way.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from aifred.lib import frame_sources, vision_settings
from aifred.lib.i18n import t
from aifred.state._vision_settings_mixin import VisionSettingsMixin

if TYPE_CHECKING:
    # For mypy the stand-in is the mixin, so its fields and borrowed method are checked against the
    # real ones. At runtime it stays a plain class: a Reflex state refuses undeclared attributes.
    _StateBase = VisionSettingsMixin
else:
    _StateBase = object

REOLINK = {"name": "Reolink", "host": "192.0.2.10", "port": 554, "path": "h264Preview_01_sub", "cred": "reolink",
           "ptz": True, "onvif_port": 8000, "profile": "ai_camera", "api_port": 443, "face_channel": 1, "snap_channel": 0}


class _Form(_StateBase):
    """Only the fields and methods saving touches."""

    save_rtsp_camera = VisionSettingsMixin.save_rtsp_camera

    def __init__(self, form: dict[str, Any], editing: str) -> None:
        self.rtsp_form = form
        self.rtsp_form_editing = editing
        self.rtsp_form_error = ""
        self.rtsp_form_open = True
        self.ui_language = "de"
        self.persisted: list[tuple[str, str, str]] = []

    def _persist_rtsp_credentials(self, cred: str, user: str, password: str) -> None:
        self.persisted.append((cred, user, password))

    def _reload_vigilantia_sources(self) -> None:
        return None


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"rtsp_cameras": [dict(REOLINK)]}))
    monkeypatch.setattr(vision_settings, "VISION_SETTINGS_PATH", path)
    monkeypatch.setattr(frame_sources, "rescan", lambda: None)
    return path


def _cameras(path: Path) -> list[dict[str, Any]]:
    cameras: list[dict[str, Any]] = json.loads(path.read_text())["rtsp_cameras"]
    return cameras


def test_changing_the_connection_keeps_what_the_form_does_not_show(settings_file: Path) -> None:
    # The form as open_rtsp_camera_edit_by_source fills it, the host changed and the face channel emptied.
    form = _Form({"host": "192.0.2.11", "port": "554", "path": "h264Preview_01_sub", "cred": "reolink",
                  "profile": "ai_camera", "api_port": "443", "face_channel": "", "user": "", "password": ""}, "Reolink")
    form.save_rtsp_camera()
    assert form.rtsp_form_error == "" and form.rtsp_form_open is False
    assert _cameras(settings_file) == [{**{key: value for key, value in REOLINK.items() if key != "face_channel"},
                                        "host": "192.0.2.11"}]
    assert form.persisted == [("reolink", "", "")]


def test_a_new_camera_with_the_name_of_an_existing_one_is_refused(settings_file: Path) -> None:
    before = settings_file.read_text()
    form = _Form({"name": "Reolink", "host": "192.0.2.99", "port": "554", "path": "", "cred": "", "profile": "webcam",
                  "api_port": "443", "face_channel": "", "user": "", "password": ""}, "")
    form.save_rtsp_camera()
    assert form.rtsp_form_error == t("vision_rtsp_name_taken", lang="de") and form.rtsp_form_open is True
    assert settings_file.read_text() == before


def test_a_new_camera_gets_only_the_fields_filled_in(settings_file: Path) -> None:
    form = _Form({"name": "Garage", "host": "192.0.2.20", "port": "554", "path": "/stream1", "cred": "", "profile": "webcam",
                  "api_port": "443", "face_channel": "", "user": "", "password": ""}, "")
    form.save_rtsp_camera()
    assert _cameras(settings_file) == [REOLINK, {"name": "Garage", "host": "192.0.2.20", "port": 554, "path": "stream1",
                                                 "profile": "webcam"}]
    assert form.persisted == []
