"""The crop modal of the Reflex UI turns and cuts through lib/turn_images.py, like the Vue app: the pending
image becomes another file under a new name, the old one goes, a box over the whole image cuts nothing.

07.11.2026: the state had its own copy (turning overwrote the file and the URL carried ``?t=``, a cut
PNG kept its suffix over JPEG content)."""

from __future__ import annotations

import asyncio
import io
import itertools
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from PIL import Image

from aifred.lib import vision_utils
from aifred.state._image_mixin import ImageMixin as M

if TYPE_CHECKING:
    # For mypy the stand-in is the mixin, so its fields and borrowed methods are checked against the
    # real ones. At runtime it stays a plain class: a Reflex state refuses undeclared attributes.
    _StateBase = M
else:
    _StateBase = object

SESSION = "c" * 32


class _Modal(_StateBase):
    _rotate_crop_image = M._rotate_crop_image
    _crop_target = M._crop_target
    _replace_pending_image = M._replace_pending_image
    _do_apply_crop = M._do_apply_crop
    cancel_crop = M.cancel_crop

    def __init__(self, pending: dict[str, str]) -> None:
        self.session_id = SESSION
        self.pending_images = [pending]
        self.crop_image_index = 0
        self.crop_preview_url = pending["url"]
        self.crop_modal_open = True
        self.crop_rotation = 0
        self.crop_box_x = self.crop_box_y = 0.0
        self.crop_box_width = self.crop_box_height = 100.0
        self.lines: list[str] = []

    def add_debug(self, message: str) -> None:
        self.lines.append(message)


@pytest.fixture
def modal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Modal:
    """The modal open on a PNG upload, 40 × 30, the left half red; each save its own millisecond."""
    monkeypatch.setattr(vision_utils, "UPLOAD_IMAGES_DIR", tmp_path / "images")
    ticks = itertools.count()
    monkeypatch.setattr(vision_utils, "filename_timestamp", lambda _now: f"2026-11-07_10-00-00_{next(ticks):03d}")
    picture = Image.new("RGB", (40, 30), "blue")
    picture.paste((255, 0, 0), (0, 0, 20, 30))
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    path = vision_utils.save_session_upload(buffer.getvalue(), SESSION, "foto", "png")
    return _Modal({"name": path.name, "path": str(path), "url": vision_utils.get_image_url(path), "size_kb": "0"})


def _pending(modal: _Modal) -> dict[str, Any]:
    pending: dict[str, Any] = modal.pending_images[0]
    return pending


def test_turning_gives_a_new_file_and_the_preview_follows(modal: _Modal) -> None:
    old = Path(_pending(modal)["path"])
    modal._rotate_crop_image(clockwise=True)
    new = _pending(modal)
    assert not old.exists() and Path(new["path"]).is_file() and new["name"] != old.name
    assert modal.crop_preview_url == new["url"] and "?" not in new["url"]
    turned = Image.open(new["path"]).convert("RGB")
    assert turned.size == (30, 40) and turned.getpixel((15, 5)) == (255, 0, 0)
    assert modal.crop_rotation == 90 and modal.crop_modal_open


def test_cutting_replaces_the_image_and_a_whole_box_keeps_it(modal: _Modal) -> None:
    before = dict(_pending(modal))
    asyncio.run(modal._do_apply_crop(0.3, 0.0, 99.6, 100.0))
    assert _pending(modal) == before and Path(before["path"]).is_file() and "No crop needed" in modal.lines[-1]
    modal.crop_image_index = 0
    asyncio.run(modal._do_apply_crop(50.0, 0.0, 50.0, 100.0))
    cut = _pending(modal)
    assert not Path(before["path"]).exists() and cut["name"].endswith(".jpg")
    assert Image.open(cut["path"]).size == (20, 30)
    assert not modal.crop_modal_open and modal.crop_image_index == -1
