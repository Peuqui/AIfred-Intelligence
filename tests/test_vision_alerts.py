"""Tests for the vision alert producer (armed-gating + event-type filter +
event shape). The dispatcher/sink are faked — no Telegram, no real config."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from datetime import datetime

import aifred.lib.vision_alerts as va
from aifred.lib.alert_bus import AlertEvent

_TS = datetime(2026, 6, 3, 14, 5, 0)


class _FakeDispatcher:
    def __init__(self) -> None:
        self.events: list[AlertEvent] = []

    async def emit(self, ev: AlertEvent) -> int:
        self.events.append(ev)
        return 1


def _run(monkeypatch, *, armed: bool, event_type: str = "face_unknown") -> _FakeDispatcher:
    disp = _FakeDispatcher()
    monkeypatch.setattr(va, "_vigilantia_armed", lambda: armed)
    monkeypatch.setattr(va, "get_default_dispatcher", lambda: disp, raising=False)
    # get_default_dispatcher is imported inside emit_face_alert from alert_bus;
    # patch it there too.
    import aifred.lib.alert_bus as ab
    monkeypatch.setattr(ab, "get_default_dispatcher", lambda: disp)
    asyncio.run(va.emit_face_alert(
        source_id="cam/office", event_type=event_type, frame_path="/x/f.jpg",
        cluster_id="cluster-1", names=[], count=1, timestamp=_TS, store=None,
    ))
    return disp


def test_not_armed_no_alert(monkeypatch):
    disp = _run(monkeypatch, armed=False)
    assert disp.events == []


def test_armed_face_unknown_emits(monkeypatch):
    disp = _run(monkeypatch, armed=True, event_type="face_unknown")
    assert len(disp.events) == 1
    ev = disp.events[0]
    assert ev.producer == "vision"
    assert ev.category == "face_unknown"
    assert ev.source_id == "cam/office"
    assert ev.severity == "warning"
    assert ev.dedup_key == "cluster-1"
    assert ev.media == "/x/f.jpg"
    assert "Unbekannte Person" in ev.title


def test_non_face_event_ignored(monkeypatch):
    disp = _run(monkeypatch, armed=True, event_type="motion")
    assert disp.events == []


def test_dedup_key_falls_back_without_cluster(monkeypatch):
    disp = _FakeDispatcher()
    monkeypatch.setattr(va, "_vigilantia_armed", lambda: True)
    import aifred.lib.alert_bus as ab
    monkeypatch.setattr(ab, "get_default_dispatcher", lambda: disp)
    asyncio.run(va.emit_face_alert(
        source_id="cam/door", event_type="face_known", frame_path="", cluster_id="",
        names=["Peuqui"], count=1, timestamp=_TS, store=None,
    ))
    assert disp.events[0].dedup_key == "cam/door:face_known"
    assert disp.events[0].severity == "info"
    assert "Peuqui" in disp.events[0].title


# ── Meldungen ohne Burst behalten Standbild und Beschreibung ──────────


def _dispatcher_with_urls(monkeypatch) -> _FakeDispatcher:
    """Dispatcher + URL-Bildung faken (get_image_url wird in _emit lokal
    aus vision_utils importiert, also dort patchen)."""
    disp = _FakeDispatcher()
    monkeypatch.setattr(va, "_vigilantia_armed", lambda: True)
    import aifred.lib.alert_bus as ab
    monkeypatch.setattr(ab, "get_default_dispatcher", lambda: disp)
    import aifred.lib.vision_utils as vu
    monkeypatch.setattr(vu, "get_image_url", lambda p: f"/_upload/{p.name}")
    return disp


def test_a_face_alert_without_burst_keeps_its_still_image_and_description(monkeypatch):
    """Der Einzel-Trigger (FacePipeline ohne collect) meldet wie bisher mit Standbild, Galerie und Beschreibung."""
    import numpy as np

    from aifred.lib.frame_sources import Frame
    from aifred.lib.vision_faces import FacePipeline

    disp = _dispatcher_with_urls(monkeypatch)

    class _Match:
        name = "Anna"
        similarity = 0.9
        confidence_band = "known"

    class _Det:
        embedding = np.zeros(4)
        detection_score = 0.9

    class _Recognizer:
        def match(self, embedding):
            return _Match()

    class _Detect:
        face_recognizer = _Recognizer()

        async def detect_faces(self, frame):
            return [_Det()]

    class _Recorder:
        def save_face_crop(self, image_bytes, source_id, det, match):
            return None

        def cluster_id_for(self, frame):
            return "cluster-1"

        def record_face(self, **kwargs):
            return None

    pipeline = FacePipeline(None, _Detect(), _Recorder())  # type: ignore[arg-type]
    asyncio.run(pipeline.run(
        Frame(source_id="cam/door", timestamp=_TS, image_bytes=b"jpeg"), trigger="motion", frame_path="/x/wide.jpg", zoom_frame_path="/x/zoom.jpg",
    ))
    (ev,) = disp.events
    assert ev.describe is True
    assert ev.media == "/x/zoom.jpg" and ev.media_context == "/x/wide.jpg"
    assert ev.media_gallery == ["/_upload/wide.jpg", "/_upload/zoom.jpg"]


def test_a_non_burst_alert_with_crops_shows_every_crop(monkeypatch):
    disp = _dispatcher_with_urls(monkeypatch)
    asyncio.run(va.emit_person_alert(
        source_id="cam/door", frame_path="/x/wide.jpg", cluster_id="c", count=2,
        extra_crop_urls=["/_upload/face_crops/p1.jpg", "/_upload/face_crops/p2.jpg"],
        timestamp=_TS, store=None,
    ))
    assert disp.events[0].media_gallery == [
        "/_upload/wide.jpg", "/_upload/face_crops/p1.jpg", "/_upload/face_crops/p2.jpg",
    ]
    assert disp.events[0].describe is True


# ── Burst-Meldungen: Erstmeldung und Hinweis als Text, Zusammenfassung mit Film ──


class _StubStore:
    """Nur das, was der Burst-Report vom Store braucht."""

    def get_source(self, source_id):
        return None

    def list_cluster_event_ids(self, cluster_id):
        return []

    def cluster_time_span(self, cluster_id):
        return datetime(2026, 10, 29, 17, 0, 9), datetime(2026, 10, 29, 17, 6, 40)


def _burst_report(monkeypatch, films=None, description="") -> tuple[va.BurstReport, _FakeDispatcher]:
    disp = _dispatcher_with_urls(monkeypatch)

    async def fake_finish(store, cluster_id, headcount):
        return va.BurstMedia(films=films or [], description=description)

    monkeypatch.setattr(va, "finish_burst_media", fake_finish)
    report = va.BurstReport("cam/door", "cluster-1", _StubStore())
    report.observe_person(1)
    return report, disp


def test_burst_first_report_is_due_only_once(monkeypatch):
    report, disp = _burst_report(monkeypatch)
    assert report.due(0.0) is True
    asyncio.run(report.send())
    assert len(disp.events) == 1
    # Danach nicht mehr im Takt — die Bilder zeigen den Verlauf (Film am Ende).
    assert report.due(0.0) is False


def test_burst_news_is_a_short_text_without_images_or_vlm(monkeypatch):
    report, disp = _burst_report(monkeypatch)
    asyncio.run(report.send())
    report.observe_headcount(3)  # mehr Personen als gemeldet = Neuigkeit
    assert report.has_news()
    asyncio.run(report.send())
    news = disp.events[1]
    assert news.describe is False
    assert news.media is None and news.media_gallery == []
    assert "3 Personen" in news.title


def test_burst_unsure_face_upgrade_is_no_news(monkeypatch):
    report, _disp = _burst_report(monkeypatch)
    asyncio.run(report.send())
    report.observe_face("face_unsure", "", 0.4)
    assert not report.has_news()


def test_burst_unknown_face_upgrade_is_news(monkeypatch):
    report, _disp = _burst_report(monkeypatch)
    asyncio.run(report.send())
    report.observe_face("face_unknown", "", 0.2)
    assert report.has_news()


def test_burst_final_summary_carries_the_films(monkeypatch):
    report, disp = _burst_report(monkeypatch, films=["/f/wide.mp4", "/f/zoom.mp4"])
    asyncio.run(report.send())
    asyncio.run(report.finish())
    final = disp.events[-1]
    assert final.media_album == ["/f/wide.mp4", "/f/zoom.mp4"]


def test_burst_final_without_films_still_reports(monkeypatch):
    report, disp = _burst_report(monkeypatch, films=[])
    asyncio.run(report.finish())  # kurzer Burst: nur die Zusammenfassung
    assert len(disp.events) == 1
    assert disp.events[0].media_album == []


def test_burst_header_names_the_occurrence_not_the_send_time(monkeypatch):
    report, disp = _burst_report(monkeypatch)
    asyncio.run(report.send())
    asyncio.run(report.finish())
    first, final = disp.events
    # Erstmeldung: Beginn des Vorkommnisses; Zusammenfassung: Beginn bis Ende.
    assert "29.10.2026 - 17:00" in first.body and "bis" not in first.body
    assert "29.10.2026 - 17:00 bis 17:06" in final.body


def test_the_final_report_brings_its_description_and_no_still_image(monkeypatch):
    report, disp = _burst_report(monkeypatch, films=["/f/wide.mp4"], description="Zwei Personen am Tor.")
    report.observe_face("face_unknown", "", 0.2)
    asyncio.run(report.send())
    asyncio.run(report.finish())
    first, final = disp.events
    assert first.description == "" and first.describe is False and first.media is None
    assert final.description == "Zwei Personen am Tor."
    assert final.describe is False  # nothing to fetch: the description is there (or there is none)
    assert final.media is None and final.media_context is None and final.media_gallery == []
    assert final.media_album == ["/f/wide.mp4"]


@pytest.mark.parametrize("failure", [
    sqlite3.OperationalError("database is locked"),
    ValueError("not a decodable image"),        # a picture the preview cannot be made of
    OSError(28, "No space left on device"),     # the preview cannot be written
])
def test_a_failing_media_finish_keeps_the_pictures_but_the_summary_still_goes_out(monkeypatch, failure):
    report, disp = _burst_report(monkeypatch)

    async def failing(store, cluster_id, headcount):
        raise failure

    monkeypatch.setattr(va, "finish_burst_media", failing)
    asyncio.run(report.finish())
    (final,) = disp.events
    assert final.media_album == [] and final.description == ""


def test_finish_without_observation_sends_nothing_but_still_finishes_the_media(monkeypatch):
    _report, disp = _burst_report(monkeypatch)
    finished: list[str] = []

    async def fake_finish(store, cluster_id, headcount):
        finished.append(cluster_id)
        return va.BurstMedia()

    monkeypatch.setattr(va, "finish_burst_media", fake_finish)
    silent = va.BurstReport("cam/door", "cluster-2", _StubStore())  # observed nothing
    asyncio.run(silent.finish())
    assert finished == ["cluster-2"] and disp.events == []


def test_source_line_spans_midnight():
    line = va._source_line("Tür", datetime(2026, 10, 29, 23, 58), datetime(2026, 10, 30, 0, 3))
    assert line == "Tür · 29.10.2026 - 23:58 bis 30.10.2026 - 00:03"
    assert va._source_line("Tür", datetime(2026, 10, 29, 9, 0), datetime(2026, 10, 29, 9, 0)) == "Tür · 29.10.2026 - 09:00"


def test_news_waits_until_the_state_has_settled(monkeypatch):
    """Zwei Erkennungen kurz nacheinander („Unbekannte", dann der Name) ergeben EINEN Hinweis."""
    from datetime import timedelta

    report, _disp = _burst_report(monkeypatch)
    asyncio.run(report.send())
    assert report.news_due(5.0) is False           # nichts Neues
    report.observe_headcount(3)
    assert report.has_news()
    assert report.news_due(5.0) is False           # gerade erst aufgekommen: warten
    report._news_since = datetime.now() - timedelta(seconds=6)
    assert report.news_due(5.0) is True            # beruhigt: melden
    asyncio.run(report.send())
    assert report.news_due(5.0) is False           # gemeldet, nichts mehr offen


def test_the_first_burst_report_is_plain_text_with_the_recognition_result(monkeypatch):
    """Erstmeldung: Namen und Personenzahl im Titel, aber kein Bild, keine Galerie, kein Album, kein VLM."""
    report, disp = _burst_report(monkeypatch)
    report.observe_face("face_known", "Anna", 0.9)
    report.observe_headcount(3)
    asyncio.run(report.send())
    (first,) = disp.events
    assert first.describe is False and first.description == ""
    assert first.media is None and first.media_context is None
    assert first.media_gallery == [] and first.media_album == []
    assert "Anna" in first.title and "2 weitere" in first.title


def test_the_first_burst_report_of_a_person_without_face_names_the_count(monkeypatch):
    report, disp = _burst_report(monkeypatch)
    report.observe_headcount(2)
    asyncio.run(report.send())
    (first,) = disp.events
    assert first.category == "person" and "2 Personen" in first.title
    assert first.describe is False and first.media is None and first.media_gallery == []
