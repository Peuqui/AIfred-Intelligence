"""Agent-Editor: Vollbild-Page (Route /agent-editor) — Tab-Dispatch."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import overlay_scaffold
from .audit import _audit_view
from .config_view import _config_view
from .database import _database_view
from .memory import _memory_view
from .plugins import _plugins_view
from .scheduler import _scheduler_view
from .storage import _storage_view


def agent_editor_page() -> rx.Component:
    """Full-page agent editor (formerly agent_editor_modal overlay).

    Wird auf der Route ``/agent-editor`` als eigene Page gerendert. Reflex
    + React Router 7 macht damit automatisches Code-Splitting: dieser
    Sub-Tree (~258 KB JSX) landet in einem eigenen Lazy-Chunk und wird
    nur geladen wenn der User in den Editor navigiert. Initial-Bundle
    der Chat-Page wird entsprechend kleiner — das Bun-Frontend bleibt
    unter dem Babel-Limit von 500 KB.

    Visuell sieht die Page weiterhin wie das alte Modal aus (Vollbild-
    Overlay), nur dass die URL sich auf ``/agent-editor`` ändert.
    Browser-Back schließt den Editor, F5 hält ihn offen, URL ist
    bookmarkbar. Der Close-Button ruft ``AIState.close_agent_editor``
    auf, was per ``rx.redirect("/")`` zurück zum Chat navigiert.
    """
    return overlay_scaffold(
        # Editor content — switches between tabs
        rx.box(
            rx.match(
                AIState.agent_editor_mode,
                ("memory", _memory_view()),
                ("database", _database_view()),
                ("plugins", _plugins_view()),
                ("scheduler", _scheduler_view()),
                ("audit", _audit_view()),
                ("storage", _storage_view()),
                _config_view(),  # default
            ),
            padding="25px",
            background_color="#1a1a1a",
            border_radius="12px",
            max_width="95vw",
            width="750px",
            height="90vh",
            max_height="95vh",
            overflow_x="hidden",
            overflow_y="hidden",
            display="flex",
            flex_direction="column",
            position="relative",
            z_index="1001",
            color="white",
        ),
        backdrop_color="rgba(0, 0, 0, 0.85)",
    )
