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
import sqlite3
from typing import TYPE_CHECKING, Any

import reflex as rx

# Zeitfenster des „neue Events"-Badge und Anzahl im aufgeklappten Akkordeon: config.py (auch für die Vue-Seite).
from ..lib.config import VISION_BADGE_EVENTS as _FEED_LIMIT
from ..lib.config import VISION_BADGE_WINDOW_MIN as _BADGE_WINDOW_MIN

logger = logging.getLogger(__name__)


class VigilantiaFeedMixin(rx.State, mixin=True):
    """UI state for the Vigilantia live feed accordion in the main tab."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        vigilantia_sources: rx.Field[list[dict[str, Any]]]

        def _reload_vigilantia_sources(self) -> None: ...

    # Sichtbarkeits-Setting für den Popover-Knopf in der Research-Modi-
    # Zeile. Default an — der Knopf ist klein, das Badge meldet sich nur
    # bei Events, und Nicht-Vision-User können ihn in den Vigilantia-
    # Settings ausblenden.
    vigilantia_feed_visible: rx.Field[bool] = rx.field(True)
    # Akkordeon offen / zu
    vigilantia_feed_open: rx.Field[bool] = rx.field(False)
    # Live-Card-Popover offen / zu (rechte Modus-Zeile). Wir steuern den
    # Radix-Popover controlled, damit Klick-daneben / ESC den Feed nicht
    # zumachen — User wollte explizit nur via X schliessen koennen.
    vigilantia_feed_popover_open: rx.Field[bool] = rx.field(False)
    # Events der letzten _FEED_LIMIT Einträge (gemischt: motion, face_*,
    # vlm_analysis je nach was die Hintergrund-Watcher produzieren).
    vigilantia_feed_events: rx.Field[list[dict[str, Any]]] = rx.field(default_factory=list)
    # Anzahl Events im _BADGE_WINDOW_MIN-Zeitfenster — Badge im Header.
    vigilantia_feed_recent_count: rx.Field[int] = rx.field(0)
    # Hilfe-Modal-State — Klick auf die Glühbirne öffnet eine
    # Übersicht „Wo stelle ich was ein / wann passiert was".
    vigilantia_help_open: rx.Field[bool] = rx.field(False)

    @rx.event
    def open_vigilantia_help(self) -> None:
        self.vigilantia_help_open = True

    @rx.event
    def close_vigilantia_help(self) -> None:
        self.vigilantia_help_open = False

    @rx.event
    def set_vigilantia_feed_visible(self, value: bool) -> None:
        self.vigilantia_feed_visible = bool(value)
        if self.vigilantia_feed_visible:
            self._refresh_vigilantia_feed()

    @rx.event
    def refresh_vigilantia_feed(self) -> None:
        self._refresh_vigilantia_feed()

    @rx.event
    def open_vigilantia_feed_popover(self) -> None:
        """Open the live-card popover and refresh the feed."""
        self.vigilantia_feed_popover_open = True
        self._refresh_vigilantia_feed()

    @rx.event
    def close_vigilantia_feed_popover(self) -> None:
        """Explicit close — bound to the X button inside the popover."""
        self.vigilantia_feed_popover_open = False

    @rx.event
    def handle_vigilantia_feed_popover_change(self, value: bool) -> None:
        """Bridge for Radix popover.root's on_open_change. Reflex passes
        the new open state on every interaction (outside click, ESC,
        trigger click). We only honor opens — closes must go through
        the explicit X button — so the popover doesn't disappear on
        accidental side-clicks while the user is reading the feed."""
        if value:
            self.vigilantia_feed_popover_open = True
            self._refresh_vigilantia_feed()

    def _refresh_vigilantia_feed(self) -> None:
        # Source-Liste bei Erst-Tick laden (Live-Card braucht
        # ``vigilantia_has_armed_source`` für den Smart-Hinweis).
        # Methode lebt im VisionSettingsMixin — ist im AIState via
        # Mixin-Composition verfügbar; Fehler einer Quelle fängt sie selbst.
        if not self.vigilantia_sources:
            self._reload_vigilantia_sources()
        try:
            from ..lib.casus import recent
            events, count = recent(_BADGE_WINDOW_MIN, _FEED_LIMIT)
            # Only assign when the result actually changed. The 500ms tick
            # invokes us on every refresh_debug_console pass while the feed
            # is visible — unconditional assignment makes Reflex flag the
            # var dirty and ship a state delta every tick, which React
            # reconciles by re-mounting the chat-bubble subtree and wipes
            # the user's text selection (same regression class as the
            # debug_messages issue fixed in commit 531c84d).
            if events != self.vigilantia_feed_events:
                self.vigilantia_feed_events = events
            if count != self.vigilantia_feed_recent_count:
                self.vigilantia_feed_recent_count = count
        except sqlite3.Error as e:  # the event database; this runs in the 500 ms tick
            logger.warning("vigilantia-feed refresh failed: %s", e)
            if self.vigilantia_feed_events:
                self.vigilantia_feed_events = []
            if self.vigilantia_feed_recent_count:
                self.vigilantia_feed_recent_count = 0
