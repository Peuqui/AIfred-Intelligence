"""Burst-Abschluss: Filme, EINE Beschreibung, Einzelbilder weg (echtes ffmpeg, Stellvertreter für den Beschreiber)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from _vision_fakes import noise_jpeg

from aifred.lib import alert_bus
import aifred.lib.vision_alerts as va
import aifred.lib.vision_burst_media as vbm
import aifred.lib.vision_film as vf
from aifred.lib.config import DATA_DIR, VISION_ALERT_ANALYZED_BY
from aifred.lib.vision_routing import NoVisionModelError
from aifred.lib.vision_store import VisionStore

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")

_T0 = datetime(2026, 11, 19, 12, 0, 0)
_CLUSTER = "c-burst"


@pytest.fixture()
def store(tmp_path, monkeypatch) -> VisionStore:
    monkeypatch.setattr(vf, "FILMS_DIR", tmp_path / "films")
    return VisionStore(tmp_path / "vision.db")


@pytest.fixture()
def pictures() -> Iterator[Path]:
    # Gelöscht wird nur unterhalb von DATA_DIR (im Test ein eigener Temp-Ordner, siehe conftest).
    folder = DATA_DIR / "burst-media-test"
    folder.mkdir(parents=True, exist_ok=True)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def _tick(store: VisionStore, folder: Path, index: int, *, cluster: str = _CLUSTER, twin: bool = False) -> list[int]:
    """Ein Tick: Weitwinkel + Zoom; mit ``twin`` ein zweites Ereignis (Gesicht + Person) auf demselben Bild."""
    wide = folder / f"{cluster}-wide{index}.jpg"
    zoom = folder / f"{cluster}-zoom{index}.jpg"
    wide.write_bytes(noise_jpeg(seed=index))
    zoom.write_bytes(noise_jpeg(seed=100 + index))
    ids = []
    for event_type in ("face_unknown", "person") if twin else ("person",):
        ids.append(store.add_event(
            "cam/door", event_type, timestamp=_T0 + timedelta(seconds=index), frame_path=str(wide),
            classification={"zoom_frame_path": str(zoom)}, cluster_id=cluster,
        ))
    return ids


def _burst(store: VisionStore, folder: Path, count: int = 4, **kw) -> list[int]:
    ids: list[int] = []
    for index in range(count):
        ids += _tick(store, folder, index, twin=index == 1, **kw)  # tick 1 is the doubled one
    return ids


def _describer(monkeypatch, *, fail: Exception | None = None) -> list[list[int]]:
    asked: list[list[int]] = []

    async def fake(event_ids, **kw):
        asked.append(list(event_ids))
        if fail is not None:
            raise fail
        return "Zwei Personen gehen zur Tür."

    monkeypatch.setattr(vbm, "analyze_cluster_with_vlm", fake)
    return asked


def _finish(store: VisionStore, cluster: str = _CLUSTER) -> vbm.BurstMedia:
    return asyncio.run(vbm.finish_burst_media(store, cluster, headcount=2))


def _all_events(store: VisionStore, ids: list[int]) -> list[dict]:
    return [event for event in (store.get_event(i) for i in ids) if event]


def test_a_normal_end_makes_films_and_drops_every_picture_of_the_set(store, pictures, monkeypatch):
    _describer(monkeypatch)
    ids = _burst(store, pictures)
    assert len(store.list_cluster_event_ids(_CLUSTER)) == len(ids) - 1  # the twin is hidden by the picture dedup
    media = _finish(store)
    assert [Path(f).name for f in media.films] == ["c-burst_120000_wide.mp4", "c-burst_120000_zoom.mp4"]
    assert all(Path(f).stat().st_size > 0 for f in media.films)
    assert list(pictures.glob("*.jpg")) == []
    for event in _all_events(store, ids):  # also the twin the dedup does not list
        assert event["frame_path"] == ""
        assert "zoom_frame_path" not in event["classification"]


def test_b_the_description_lies_at_every_event_of_the_set(store, pictures, monkeypatch):
    asked = _describer(monkeypatch)
    ids = _burst(store, pictures)
    media = _finish(store)
    assert media.description == "Zwei Personen gehen zur Tür."
    assert len(asked) == 1 and len(asked[0]) == len(ids) - 1  # described once, one event per picture
    for event in _all_events(store, ids):
        assert event["classification"]["description"] == media.description
        assert event["classification"]["analyzed_by"] == VISION_ALERT_ANALYZED_BY


def test_d_a_failing_film_keeps_pictures_and_paths(store, pictures, monkeypatch):
    _describer(monkeypatch)

    async def broken(cluster_id, events):
        raise vf.FilmEncodeError("ffmpeg kaputt")

    monkeypatch.setattr(vbm, "make_cluster_films", broken)
    ids = _burst(store, pictures)
    before = {e["id"]: (e["frame_path"], e["classification"]["zoom_frame_path"]) for e in _all_events(store, ids)}
    media = _finish(store)
    assert media.films == [] and media.description != ""
    assert len(list(pictures.glob("*.jpg"))) == 8
    assert {e["id"]: (e["frame_path"], e["classification"]["zoom_frame_path"]) for e in _all_events(store, ids)} == before


@pytest.mark.parametrize("failure", [NoVisionModelError("no describer free"), RuntimeError("VLM returned empty response")])
def test_e_a_failing_describer_still_makes_films_and_drops(store, pictures, monkeypatch, failure, caplog):
    _describer(monkeypatch, fail=failure)
    caplog.set_level("INFO", logger=vbm.logger.name)
    ids = _burst(store, pictures)
    media = _finish(store)
    assert media.description == "" and len(media.films) == 2
    assert list(pictures.glob("*.jpg")) == []
    assert all(e["frame_path"] == "" and "description" not in e["classification"] for e in _all_events(store, ids))
    (record,) = [r for r in caplog.records if "description" in r.getMessage()]
    # expected case: an info line without traceback; a real error: a warning with traceback
    assert (record.levelname, record.exc_info is not None) == (
        ("INFO", False) if isinstance(failure, NoVisionModelError) else ("WARNING", True)
    )


def test_f_a_second_burst_films_only_its_own_pictures(store, pictures, monkeypatch):
    _describer(monkeypatch)
    _burst(store, pictures, count=4)
    first = _finish(store)
    for index in range(10, 12):
        _tick(store, pictures, index)
    second = _finish(store)
    assert [Path(f).name for f in second.films] == ["c-burst_120010_wide.mp4", "c-burst_120010_zoom.mp4"]
    assert all(Path(f).exists() for f in first.films)  # the first film stays
    assert _frames(Path(second.films[0])) == 2 and _frames(Path(first.films[0])) == 4


def _frames(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return int(out.strip())


def test_the_set_is_empty_without_pictures(store, monkeypatch):
    asked = _describer(monkeypatch)
    assert _finish(store) == vbm.BurstMedia() and asked == []


# ── über den Report: Meldung ja/nein ──


class _Dispatcher:
    def __init__(self) -> None:
        self.events: list[alert_bus.AlertEvent] = []

    async def emit(self, ev):
        self.events.append(ev)
        return 1


def _report(store: VisionStore, monkeypatch, *, observed: bool) -> tuple[va.BurstReport, _Dispatcher]:
    dispatcher = _Dispatcher()
    monkeypatch.setattr(va, "_vigilantia_armed", lambda: True)
    monkeypatch.setattr(alert_bus, "get_default_dispatcher", lambda: dispatcher)
    report = va.BurstReport("cam/door", _CLUSTER, store)
    if observed:
        report.observe_person(2)
    return report, dispatcher


def test_c_without_observation_the_media_are_finished_but_nothing_is_sent(store, pictures, monkeypatch):
    _describer(monkeypatch)
    ids = _burst(store, pictures)
    report, dispatcher = _report(store, monkeypatch, observed=False)
    asyncio.run(report.finish())
    assert dispatcher.events == []
    assert list(pictures.glob("*.jpg")) == [] and all(e["frame_path"] == "" for e in _all_events(store, ids))


def test_the_summary_carries_films_and_description_but_no_still_image(store, pictures, monkeypatch):
    _describer(monkeypatch)
    _burst(store, pictures)
    report, dispatcher = _report(store, monkeypatch, observed=True)
    asyncio.run(report.finish())
    (summary,) = dispatcher.events
    assert summary.description == "Zwei Personen gehen zur Tür."
    assert [Path(f).suffix for f in summary.media_album] == [".mp4", ".mp4"]
    assert summary.media is None and summary.media_gallery == []


def test_e_without_a_describer_the_summary_goes_out_without_description(store, pictures, monkeypatch):
    _describer(monkeypatch, fail=RuntimeError("no describer"))
    _burst(store, pictures)
    report, dispatcher = _report(store, monkeypatch, observed=True)
    asyncio.run(report.finish())
    (summary,) = dispatcher.events
    assert summary.description == "" and summary.describe is False and len(summary.media_album) == 2
