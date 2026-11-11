"""Aufnahme auf Zuruf (vision_recording.py), Bilder aus einem Film (vision_film.film_frames) und das Mitlöschen der
Burst-Filme (vision_store): mit echtem ffmpeg, die Kamera ist ein Stellvertreter."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from _vision_fakes import FakeSource, noise_jpeg

import aifred.lib.vision_recording as vr
import aifred.lib.vision_utils as vu
from aifred.lib.config import VISION_RECORD_MAX_DURATION_S, VISION_RECORD_MIN_INTERVAL_S
from aifred.lib.frame_sources import Frame
from aifred.lib.user_error import Invalid
from aifred.lib.vision_film import encode_film, film_frames
from aifred.lib.vision_store import VisionStore

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


def _frame_count(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return int(out.strip())


def test_a_film_gives_back_pictures_spread_over_its_length(tmp_path: Path) -> None:
    pictures = []
    for index in range(10):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(noise_jpeg(seed=index))
        pictures.append(str(path))
    film = tmp_path / "film.mp4"
    asyncio.run(encode_film(pictures, film, fps=5))
    assert _frame_count(film) == 10
    jpegs = asyncio.run(film_frames(film, 4))
    assert len(jpegs) == 4 and all(jpeg[:2] == b"\xff\xd8" for jpeg in jpegs)   # JPEG start marker


def test_limits_come_from_the_config() -> None:
    vr.check_request(VISION_RECORD_MAX_DURATION_S, VISION_RECORD_MIN_INTERVAL_S)   # exactly at the limits: fine
    for duration, interval in (
        (VISION_RECORD_MAX_DURATION_S + 1, 0.5), (0, 0.5), (10, VISION_RECORD_MIN_INTERVAL_S / 2),
    ):
        with pytest.raises(Invalid):
            vr.check_request(duration, interval)


def test_a_recording_plays_in_real_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vr, "RECORDINGS_DIR", tmp_path / "recordings")
    monkeypatch.setattr(vr, "resolve_source_resolution", lambda source_id: (0, 0))
    monkeypatch.setattr(vr, "source_overlay_label", lambda source_id: "Test")
    monkeypatch.setattr(vr, "resolve_source_alias", lambda source_id, fallback="": "tuer")
    monkeypatch.setattr(vr, "annotate_frame", lambda image, label, timestamp: image)
    taken = iter(range(100))

    async def grab(source, width, height):
        return Frame(source_id=source.source_id, timestamp=datetime.now(), image_bytes=noise_jpeg(seed=next(taken)))

    monkeypatch.setattr(vr, "_grab", grab)
    recording = asyncio.run(vr.record_film(FakeSource("cam/test", []), duration_s=0.7, interval_s=0.2))
    assert recording.n_frames == 4                       # at 0.0 0.2 0.4 0.6 s
    assert recording.path.parent.parent.name == "cam_test" and recording.path.parent.name == datetime.now().strftime("%Y-%m-%d")
    assert 4.0 < recording.fps < 6.0                     # measured, about the 5 pictures per second asked for
    assert _frame_count(recording.path) == 4


def test_burst_films_go_with_the_last_event_of_their_happening(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vu, "FILMS_DIR", tmp_path / "films")
    day = tmp_path / "films" / "cam_door" / "2026-11-12"
    day.mkdir(parents=True)
    for lens in ("wide", "zoom"):
        (day / f"c-1_{lens}.mp4").write_bytes(b"film")
    (day / "c-2_wide.mp4").write_bytes(b"film")
    store = VisionStore(tmp_path / "vision.db")
    first = store.add_event("cam/door", "person", cluster_id="c-1")
    second = store.add_event("cam/door", "person", cluster_id="c-1")
    store.add_event("cam/door", "person", cluster_id="c-2")
    assert [f.name for f in vu.cluster_film_files("cam/door", "c-1")] == ["c-1_wide.mp4", "c-1_zoom.mp4"]
    store.delete_event(first)
    assert (day / "c-1_wide.mp4").exists()               # the happening still has an event: its film stays
    store.delete_event(second)
    assert not (day / "c-1_wide.mp4").exists() and not (day / "c-1_zoom.mp4").exists()
    assert (day / "c-2_wide.mp4").exists()               # another happening: untouched
    assert store.delete_events_filtered(source_id="cam/door") == 1
    assert not (day / "c-2_wide.mp4").exists()           # the bulk delete takes the films of what it deletes
