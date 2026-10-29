"""Meldungen, die die Kamera ausgelöst hat, ohne dass YOLO die Klasse fand: gelten als echt,
der Befund steht in Text und Metadaten (die Auswertung bekommt ihn mit)."""

import asyncio
from typing import Any

import pytest

import aifred.lib.vision_alerts as va


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_emit(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(va, "_emit", fake_emit)
    monkeypatch.setattr(va, "_vigilantia_armed", lambda: True)
    monkeypatch.setattr(va, "_alerts_enabled", lambda source_id, store: True)
    monkeypatch.setattr(va, "_alert_passes_filters", lambda source_id, store, category: True)
    monkeypatch.setattr(va, "_source_alias", lambda source_id, store: "Hauseingang")
    return calls


def person_alert(**changes: Any) -> None:
    asyncio.run(va.emit_person_alert(source_id="cam/x", frame_path="/f.jpg", cluster_id="c1", **changes))


def test_an_unconfirmed_person_carries_the_finding(emitted: list[dict[str, Any]]) -> None:
    person_alert(yolo_unconfirmed=True)
    (call,) = emitted
    assert va.YOLO_UNCONFIRMED_NOTE in call["body"]
    assert call["metadata"]["yolo_unconfirmed"] is True


def test_a_confirmed_person_has_no_note(emitted: list[dict[str, Any]]) -> None:
    person_alert()
    (call,) = emitted
    assert va.YOLO_UNCONFIRMED_NOTE not in call["body"]
    assert "yolo_unconfirmed" not in call["metadata"]


def test_an_unconfirmed_object_carries_the_finding(emitted: list[dict[str, Any]]) -> None:
    asyncio.run(va.emit_object_alert(
        source_id="cam/x", object_type="vehicle", frame_path="/f.jpg", cluster_id="c1", yolo_unconfirmed=True,
    ))
    (call,) = emitted
    assert va.YOLO_UNCONFIRMED_NOTE in call["body"] and call["metadata"]["yolo_unconfirmed"] is True


def test_the_burst_report_names_the_finding_only_while_yolo_never_saw_a_person(
    emitted: list[dict[str, Any]],
) -> None:
    report = va.BurstReport("cam/x", "c1", None)
    report.observe_person(1, 0.0, "/f.jpg", "", yolo_unconfirmed=True)
    asyncio.run(report._send_once(segment_ids=[]))
    report.observe_person(1, 0.6, "/g.jpg", "")  # a later tick: YOLO sees the person
    asyncio.run(report._send_once(segment_ids=[]))
    assert [va.YOLO_UNCONFIRMED_NOTE in call["body"] for call in emitted] == [True, False]
