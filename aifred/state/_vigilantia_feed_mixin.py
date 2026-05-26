"""Vigilantia-Live-Feed — Mini-Chronik im AIfred-Haupttab.

Akkordeon (analog „Gespeicherte Chats"), zeigt die letzten Vision-
Events der Hintergrund-Watcher. Ohne offenes Vorschau-Popup wäre der
User sonst blind für das, was die scharfgeschalteten Cams melden.

Polling statt SSE — die Liste lädt beim Aufklappen frisch und kann
über den Refresh-Button im Header nachgeladen werden. Reicht für
„Was war in den letzten paar Minuten los?"; Real-Time-Updates kommen
mit dem Vorschau-Popup, das schon SSE-verdrahtet ist.
"""

from __future__ import annotations

import logging
from typing import Any

import reflex as rx

logger = logging.getLogger(__name__)


# Zeitfenster für das „neue Events"-Badge: Events der letzten X Min
# werden im Header-Counter mitgezählt.
_BADGE_WINDOW_MIN = 10
# Anzahl Events die im aufgeklappten Akkordeon angezeigt werden.
_FEED_LIMIT = 10


class VigilantiaFeedMixin(rx.State, mixin=True):
    """UI state for the Vigilantia live feed accordion in the main tab."""

    # Sichtbarkeits-Setting für den Popover-Knopf in der Research-Modi-
    # Zeile. Default an — der Knopf ist klein, das Badge meldet sich nur
    # bei Events, und Nicht-Vision-User können ihn in den Vigilantia-
    # Settings ausblenden.
    vigilantia_feed_visible: bool = True
    # Akkordeon offen / zu
    vigilantia_feed_open: bool = False
    # Events der letzten _FEED_LIMIT Einträge (gemischt: motion, face_*,
    # vlm_analysis je nach was die Hintergrund-Watcher produzieren).
    vigilantia_feed_events: list[dict[str, Any]] = []
    # Anzahl Events im _BADGE_WINDOW_MIN-Zeitfenster — Badge im Header.
    vigilantia_feed_recent_count: int = 0
    # Hilfe-Modal-State — Klick auf die Glühbirne öffnet eine
    # Übersicht „Wo stelle ich was ein / wann passiert was".
    vigilantia_help_open: bool = False

    @rx.event
    def open_vigilantia_help(self) -> None:
        self.vigilantia_help_open = True

    @rx.event
    def close_vigilantia_help(self) -> None:
        self.vigilantia_help_open = False

    @rx.event
    def toggle_vigilantia_feed_visible(self) -> None:
        """User-Toggle: Akkordeon ein-/ausblenden (Persistenz im
        UI-Settings-Mixin via _save_settings)."""
        self.vigilantia_feed_visible = not self.vigilantia_feed_visible
        # Sofort einmal laden, damit beim Einblenden nicht „leer" steht.
        if self.vigilantia_feed_visible:
            self._refresh_vigilantia_feed()

    @rx.event
    def set_vigilantia_feed_visible(self, value: bool) -> None:
        self.vigilantia_feed_visible = bool(value)
        if self.vigilantia_feed_visible:
            self._refresh_vigilantia_feed()

    @rx.event
    def open_vigilantia_feed(self, value: list[str] | str = "") -> None:
        """Akkordeon-Open-Handler. Reflex Radix-Accordion ruft mit dem
        aktuell offenen Wert (collapsible=True → "" oder Item-Value).
        Beim Öffnen frisch laden."""
        is_open = bool(value) and value != ""
        self.vigilantia_feed_open = is_open
        if is_open:
            self._refresh_vigilantia_feed()

    @rx.event
    def refresh_vigilantia_feed(self) -> None:
        self._refresh_vigilantia_feed()

    def _refresh_vigilantia_feed(self) -> None:
        # Source-Liste bei Erst-Tick laden (Live-Card braucht
        # ``vigilantia_has_armed_source`` für den Smart-Hinweis).
        # Methode lebt im VisionSettingsMixin — ist im AIState via
        # Mixin-Composition verfügbar.
        try:
            if not self.vigilantia_sources and hasattr(
                self, "_reload_vigilantia_sources"
            ):
                self._reload_vigilantia_sources()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        try:
            from datetime import datetime, timedelta
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            events = store.list_events_with_summary(
                event_types=["motion", "face_known", "face_unsure",
                             "face_unknown", "vlm_analysis"],
                limit=_FEED_LIMIT,
                offset=0,
            )
            self.vigilantia_feed_events = events
            since = datetime.now() - timedelta(minutes=_BADGE_WINDOW_MIN)
            self.vigilantia_feed_recent_count = store.count_events(
                event_types=["motion", "face_known", "face_unsure",
                             "face_unknown", "vlm_analysis"],
                since=since,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("vigilantia-feed refresh failed: %s", e)
            self.vigilantia_feed_events = []
            self.vigilantia_feed_recent_count = 0
