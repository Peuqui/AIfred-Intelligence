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
