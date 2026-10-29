"""Burst-Film: je Linse ein MP4 aus den Bildern des Vorkommnisses (echtes ffmpeg)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from _vision_fakes import noise_jpeg

import aifred.lib.vision_film as vf
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
    for index in range(4):
        _event(store, tmp_path, index, zoom=True)
    films = asyncio.run(vf.make_cluster_films(store, "c-1"))
    assert [vf.film_label(str(f)) for f in films] == ["Weitwinkel", "Zoom"]
    assert all(f.exists() and f.stat().st_size > 0 for f in films)
    assert all(_frame_count(f) == 4 for f in films)
    assert films[0].parent.name == "2026-10-29"  # Tagesordner für die Aufbewahrungsfrist


def test_a_lens_with_a_single_frame_makes_no_film(store, tmp_path):
    for index in range(3):
        _event(store, tmp_path, index, zoom=index == 0)  # nur ein Zoom-Bild
    films = asyncio.run(vf.make_cluster_films(store, "c-1"))
    assert [vf.film_label(str(f)) for f in films] == ["Weitwinkel"]


def test_missing_files_are_skipped(store, tmp_path):
    ids = [_event(store, tmp_path, index, zoom=False) for index in range(4)]
    (tmp_path / "wide1.jpg").unlink()
    films = asyncio.run(vf.make_cluster_films(store, "c-1"))
    assert _frame_count(films[0]) == 3
    assert ids


def test_unknown_cluster_has_no_films(store):
    assert asyncio.run(vf.make_cluster_films(store, "gibt-es-nicht")) == []
