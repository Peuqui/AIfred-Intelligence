"""Tests for the vision alert producer (armed-gating + event-type filter +
event shape). The dispatcher/sink are faked — no Telegram, no real config."""

from __future__ import annotations

import asyncio
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


# ── Keine Wiederholungen innerhalb eines Vorkommnisses ────────────────
# Eine Bilanz meldet mehrfach (Bilanz + Follow-up-Kapitel). Frames und
# Crops des besten Ticks bleiben dabei dieselben — ohne Gedächtnis stand
# in jeder Folge-Bubble noch einmal exakt dasselbe Bild.


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


def test_gallery_repeats_nothing_within_one_happening(monkeypatch):
    disp = _dispatcher_with_urls(monkeypatch)
    shown: set[str] = set()
    for _ in range(2):
        asyncio.run(va.emit_face_alert(
            source_id="cam/door", event_type="face_unknown",
            frame_path="/x/wide.jpg", zoom_frame_path="/x/zoom.jpg",
            crop_url="/_upload/face_crops/a.jpg", cluster_id="cluster-1",
            count=1, timestamp=_TS, store=None, shown_media=shown,
        ))
    assert len(disp.events) == 2
    assert disp.events[0].media_gallery == [
        "/_upload/wide.jpg", "/_upload/zoom.jpg", "/_upload/face_crops/a.jpg",
    ]
    # Zweite Meldung: alles schon gezeigt → keine Bilder, aber die Meldung
    # geht raus (ihr Text ist das neue Kapitel).
    assert disp.events[1].media_gallery == []
    # media bleibt unangetastet — das VLM braucht sein Bild.
    assert disp.events[1].media == "/x/zoom.jpg"
    # …aber die Kanäle (Telegram-Album) bekommen es nur beim ersten Mal.
    assert [e.media_is_new for e in disp.events] == [True, False]


def test_media_is_new_again_when_the_best_frame_changes(monkeypatch):
    disp = _dispatcher_with_urls(monkeypatch)
    shown: set[str] = set()
    for zoom in ("/x/zoom.jpg", "/x/zoom.jpg", "/x/zoom2.jpg"):
        asyncio.run(va.emit_face_alert(
            source_id="cam/door", event_type="face_unknown",
            frame_path="/x/wide.jpg", zoom_frame_path=zoom,
            cluster_id="cluster-1", count=1, timestamp=_TS, store=None,
            shown_media=shown,
        ))
    # Weitwinkel schon gezeigt, aber ein neues Zoom-Bild → neu.
    assert [e.media_is_new for e in disp.events] == [True, False, True]


def test_media_is_always_new_without_shown_media(monkeypatch):
    disp = _dispatcher_with_urls(monkeypatch)
    for _ in range(2):
        asyncio.run(va.emit_face_alert(
            source_id="cam/door", event_type="face_unknown",
            frame_path="/x/wide.jpg", cluster_id="c", count=1,
            timestamp=_TS, store=None,
        ))
    assert [e.media_is_new for e in disp.events] == [True, True]


def test_gallery_still_shows_what_is_new(monkeypatch):
    disp = _dispatcher_with_urls(monkeypatch)
    shown: set[str] = set()
    asyncio.run(va.emit_face_alert(
        source_id="cam/door", event_type="face_unknown", frame_path="/x/wide.jpg",
        crop_url="/_upload/face_crops/a.jpg", cluster_id="c", count=1,
        timestamp=_TS, store=None, shown_media=shown,
    ))
    asyncio.run(va.emit_face_alert(
        source_id="cam/door", event_type="face_unknown", frame_path="/x/wide.jpg",
        crop_url="/_upload/face_crops/a.jpg",
        extra_crop_urls=["/_upload/face_crops/b.jpg"], cluster_id="c", count=2,
        timestamp=_TS, store=None, shown_media=shown,
    ))
    assert disp.events[1].media_gallery == ["/_upload/face_crops/b.jpg"]


def test_without_shown_media_nothing_is_filtered(monkeypatch):
    """Nicht-Burst-Aufrufer (eine Meldung pro Vorkommnis) bleiben unberührt."""
    disp = _dispatcher_with_urls(monkeypatch)
    for _ in range(2):
        asyncio.run(va.emit_face_alert(
            source_id="cam/door", event_type="face_unknown",
            frame_path="/x/wide.jpg", cluster_id="c", count=1,
            timestamp=_TS, store=None,
        ))
    assert disp.events[0].media_gallery == disp.events[1].media_gallery
    assert disp.events[1].media_gallery == ["/_upload/wide.jpg"]


class _CountingCropStore:
    """Zählt, wie oft derselbe Referenz-Frame zugeschnitten wird."""

    def __init__(self) -> None:
        self.calls = 0

    def save_person_crop(self, *, frame_bytes, bbox, source_id, index):
        self.calls += 1
        return f"/_upload/face_crops/p{index}_{self.calls}.jpg"


def test_person_crops_are_cut_once_per_reference_tick(monkeypatch):
    store = _CountingCropStore()
    import aifred.lib.face_crop_store as fcs
    monkeypatch.setattr(fcs, "get_default_store", lambda: store)

    report = va.BurstReport("cam/door", "cluster-1", None)
    report.observe_person_boxes(b"jpeg-bytes", [(0, 0, 10, 20)])

    first = report._person_crop_urls()
    second = report._person_crop_urls()
    assert first == second, "zweite Bilanz muss dieselben Dateien wiederverwenden"
    assert store.calls == 1, "derselbe Frame darf nur einmal geschnitten werden"

    # Ein Tick mit MEHR Personen löst den Referenz-Frame ab → neu schneiden.
    report.observe_person_boxes(b"other-bytes", [(0, 0, 10, 20), (30, 0, 40, 20)])
    third = report._person_crop_urls()
    assert third != first
    assert store.calls == 3


# ── Burst-Report: Erstmeldung, Hinweis bei Neuigkeiten, Zusammenfassung mit Film ──


class _StubStore:
    """Nur das, was der Burst-Report vom Store braucht."""

    def get_source(self, source_id):
        return None

    def list_cluster_event_ids(self, cluster_id):
        return []

    def cluster_time_span(self, cluster_id):
        return datetime(2026, 10, 29, 17, 0, 9), datetime(2026, 10, 29, 17, 6, 40)


def _burst_report(monkeypatch, films=None) -> tuple[va.BurstReport, _FakeDispatcher]:
    disp = _dispatcher_with_urls(monkeypatch)

    async def fake_films(store, cluster_id):
        return films or []

    import aifred.lib.vision_film as vf
    monkeypatch.setattr(vf, "make_cluster_films", fake_films)
    report = va.BurstReport("cam/door", "cluster-1", _StubStore())
    report.observe_person(1, 0.9, "/x/wide.jpg", "/x/zoom.jpg")
    return report, disp


def test_burst_first_report_is_due_only_once(monkeypatch):
    report, disp = _burst_report(monkeypatch)
    assert report.due(0.0) is True
    asyncio.run(report.send())
    assert len(disp.events) == 1
    assert disp.events[0].describe is True
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


def test_burst_final_summary_carries_the_films(monkeypatch):
    report, disp = _burst_report(monkeypatch, films=["/f/wide.mp4", "/f/zoom.mp4"])
    asyncio.run(report.send())
    asyncio.run(report.send_final())
    final = disp.events[-1]
    assert final.describe is True
    assert final.media_album == ["/f/wide.mp4", "/f/zoom.mp4"]


def test_burst_final_without_films_still_reports(monkeypatch):
    report, disp = _burst_report(monkeypatch, films=[])
    asyncio.run(report.send_final())  # kurzer Burst: nur die Zusammenfassung
    assert len(disp.events) == 1
    assert disp.events[0].media_album == []


def test_burst_header_names_the_occurrence_not_the_send_time(monkeypatch):
    report, disp = _burst_report(monkeypatch)
    asyncio.run(report.send())
    asyncio.run(report.send_final())
    first, final = disp.events
    # Erstmeldung: Beginn des Vorkommnisses; Zusammenfassung: Beginn bis Ende.
    assert "29.10.2026 - 17:00" in first.body and "bis" not in first.body
    assert "29.10.2026 - 17:00 bis 17:06" in final.body


def test_only_the_final_report_is_a_summary(monkeypatch):
    report, disp = _burst_report(monkeypatch)
    asyncio.run(report.send())
    asyncio.run(report.send_final())
    first, final = disp.events
    assert not first.metadata.get("summary")
    assert final.metadata["summary"] is True


def test_source_line_spans_midnight():
    line = va._source_line("Tür", datetime(2026, 10, 29, 23, 58), datetime(2026, 10, 30, 0, 3))
    assert line == "Tür · 29.10.2026 - 23:58 bis 30.10.2026 - 00:03"
    assert va._source_line("Tür", datetime(2026, 10, 29, 9, 0), datetime(2026, 10, 29, 9, 0)) == "Tür · 29.10.2026 - 09:00"
