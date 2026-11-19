"""Burst-Film: je Linse ein MP4 aus den Bildern des Vorkommnisses (echtes ffmpeg)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image
from _vision_fakes import noise_jpeg

import aifred.lib.vision_film as vf
from aifred.lib.config import VISION_FILM_KEYFRAME_SEC
from aifred.lib.vision_store import VisionStore

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")

_T0 = datetime(2026, 10, 29, 17, 0, 0)


@pytest.fixture()
def store(tmp_path, monkeypatch) -> VisionStore:
    monkeypatch.setattr(vf, "FILMS_DIR", tmp_path / "films")
    return VisionStore(tmp_path / "vision.db")


def _event(store: VisionStore, tmp_path: Path, index: int, *, zoom: bool, cluster: str = "c-1") -> int:
    wide = tmp_path / f"wide{index}.jpg"
    wide.write_bytes(noise_jpeg(seed=index))
    classification: dict = {}
    if zoom:
        zoom_path = tmp_path / f"zoom{index}.jpg"
        zoom_path.write_bytes(noise_jpeg(seed=100 + index))
        classification["zoom_frame_path"] = str(zoom_path)
    return store.add_event(
        "cam/door", "person", timestamp=_T0 + timedelta(seconds=index),
        frame_path=str(wide), classification=classification, cluster_id=cluster,
    )


def _frame_count(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return int(out.strip())


def test_one_film_per_lens_in_time_order(store, tmp_path):
    for index in range(6):
        _event(store, tmp_path, index, zoom=True)
    films = asyncio.run(vf.make_cluster_films("c-1", store.list_cluster_frame_events("c-1")))
    assert [vf.film_label(str(f)) for f in films] == ["Weitwinkel", "Zoom"]
    assert all(f.exists() and f.stat().st_size > 0 for f in films)
    assert all(_frame_count(f) == 6 for f in films)
    assert films[0].parent.name == "2026-10-29"  # Tagesordner für die Aufbewahrungsfrist


def test_a_lens_with_a_single_frame_still_makes_a_film(store, tmp_path):
    _event(store, tmp_path, 0, zoom=True)
    films = asyncio.run(vf.make_cluster_films("c-1", store.list_cluster_frame_events("c-1")))
    assert [vf.film_label(str(f)) for f in films] == ["Weitwinkel", "Zoom"]
    assert all(_frame_count(f) == 1 for f in films)


def test_missing_files_are_skipped(store, tmp_path):
    for index in range(7):
        _event(store, tmp_path, index, zoom=False)
    (tmp_path / "wide1.jpg").unlink()
    films = asyncio.run(vf.make_cluster_films("c-1", store.list_cluster_frame_events("c-1")))
    assert _frame_count(films[0]) == 6


def test_unknown_cluster_has_no_films(store):
    assert asyncio.run(vf.make_cluster_films("gibt-es-nicht", store.list_cluster_frame_events("gibt-es-nicht"))) == []


def _jpeg(path: Path, size: tuple[int, int], color: int = 128) -> str:
    Image.new("RGB", size, (color, 60, 200 - color // 2)).save(path, "JPEG")
    return str(path)


def _probe(path: Path, *args: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-of", "json", *args, str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    probe: dict = json.loads(out)
    return probe


def _size(path: Path) -> tuple[int, int]:
    stream = _probe(path, "-show_entries", "stream=width,height")["streams"][0]
    return stream["width"], stream["height"]


def test_a_film_keeps_the_native_resolution(tmp_path):
    paths = [_jpeg(tmp_path / f"{i}.jpg", (1920, 1080), 100 + i) for i in range(3)]
    out = tmp_path / "film.mp4"
    asyncio.run(vf.encode_film(paths, out))
    assert _size(out) == (1920, 1080)


def test_the_canvas_is_the_largest_image_and_smaller_ones_get_borders(tmp_path):
    small = _jpeg(tmp_path / "small.jpg", (896, 512))
    large = _jpeg(tmp_path / "large.jpg", (1920, 1080), 200)
    out = tmp_path / "film.mp4"
    asyncio.run(vf.encode_film([small, large, small], out))
    assert _size(out) == (1920, 1080)
    assert _frame_count(out) == 3


def test_an_odd_size_is_rounded_up_to_even(tmp_path):
    out = tmp_path / "film.mp4"
    asyncio.run(vf.encode_film([_jpeg(tmp_path / f"{i}.jpg", (101, 75)) for i in range(2)], out))
    assert _size(out) == (102, 76)


def test_key_frames_sit_at_the_configured_interval(tmp_path):
    fps = 2
    count = int(VISION_FILM_KEYFRAME_SEC * fps * 2) + 5  # two full intervals and a bit
    out = tmp_path / "film.mp4"
    asyncio.run(vf.encode_film([_jpeg(tmp_path / f"{i}.jpg", (64, 48), i * 3) for i in range(count)], out, fps))
    frames = _probe(out, "-show_entries", "frame=key_frame,pts_time")["frames"]
    key_times = [round(float(f["pts_time"])) for f in frames if f["key_frame"] == 1]
    assert key_times == [0, VISION_FILM_KEYFRAME_SEC, 2 * VISION_FILM_KEYFRAME_SEC]


def test_the_file_name_carries_the_time_of_the_first_image_of_the_film(store, tmp_path):
    for index in range(7):
        _event(store, tmp_path, index, zoom=index >= 2)  # the zoom lens starts two seconds later
    films = asyncio.run(vf.make_cluster_films("c-1", store.list_cluster_frame_events("c-1")))
    assert [f.name for f in films] == ["c-1_170000_wide.mp4", "c-1_170002_zoom.mp4"]
    assert [vf.film_lens(str(f)) for f in films] == ["wide", "zoom"]
