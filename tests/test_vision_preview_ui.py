"""Die Live-Ansicht muss sich als Komponente bauen lassen. Reflex prüft beim
Bauen, ob Event-Handler zu den Ereignissen der Komponenten passen (z. B. der
Typ des Arguments von ``on_change``); ein Fehler dort verhindert den Start des
ganzen Dienstes — die Unit-Tests der Logik sehen ihn nicht."""

from __future__ import annotations

from aifred.ui.vision_preview import vision_preview_page


def test_live_preview_page_builds():
    assert vision_preview_page() is not None
