"""Wie eine autonome Meldung ihre Bilder in die Chat-Bubble bekommt.

Kern des Vertrags: ``media_gallery`` ist die SSoT, sobald der Aufrufer sie
mitgibt — auch leer. Eine leere Galerie heisst „nichts Neues zu zeigen"
(die Bilanz eines Vorkommnisses hat bereits Gezeigtes herausgefiltert) und
darf nicht auf das Einzelbild zurueckfallen, sonst stuende genau das
wiederholte Bild wieder in der Bubble.
"""

from __future__ import annotations

import pytest

import aifred.lib.message_processor as mp


class _FakeRoutingTable:
    def get_route(self, channel, channel_id):
        return None

    def set_route(self, channel, channel_id, session_id):
        return None


@pytest.fixture()
def recorded(monkeypatch) -> list[list[dict]]:
    """Alle Storage-Seiteneffekte abfangen; liefert die geschriebenen
    chat_history-Listen."""
    written: list[list[dict]] = []

    monkeypatch.setattr(mp, "routing_table", _FakeRoutingTable())
    monkeypatch.setattr(mp, "create_empty_session", lambda *a, **k: None)
    monkeypatch.setattr(mp, "write_hub_notification", lambda *a, **k: None)
    monkeypatch.setattr(
        mp, "update_chat_data",
        lambda session_id, chat_history, **k: written.append(chat_history),
    )
    import aifred.lib.session_storage as ss
    monkeypatch.setattr(ss, "load_session", lambda sid: {"data": {"chat_history": []}})
    return written


def _content(written: list[list[dict]]) -> str:
    assert len(written) == 1
    return str(written[0][-1]["content"])


def test_gallery_images_are_embedded(recorded):
    mp.record_autonomous_turn(
        "vision", "cam/door", "Unbekannte Person erkannt", "Text",
        media="/x/wide.jpg",
        media_gallery=["/_upload/wide.jpg", "/_upload/face_crops/a.jpg"],
    )
    content = _content(recorded)
    assert "![Unbekannte Person erkannt](/_upload/wide.jpg)" in content
    assert "![Unbekannte Person erkannt](/_upload/face_crops/a.jpg)" in content


def test_empty_gallery_means_no_image_at_all(recorded):
    mp.record_autonomous_turn(
        "vision", "cam/door", "Unbekannte Person erkannt", "Neues Kapitel",
        media="/x/wide.jpg", media_gallery=[],
    )
    assert _content(recorded) == "Neues Kapitel"


def test_without_gallery_the_single_image_carries_the_turn(recorded, monkeypatch):
    """``None`` = der Aufrufer kennt keine Galerie — dann traegt ``media``."""
    import aifred.lib.vision_utils as vu
    monkeypatch.setattr(vu, "get_image_url", lambda p: f"/_upload/{p.name}")
    mp.record_autonomous_turn(
        "vision", "cam/door", "Titel", "Text", media="/x/wide.jpg",
    )
    assert "![Titel](/_upload/wide.jpg)" in _content(recorded)
