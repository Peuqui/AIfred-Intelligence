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
