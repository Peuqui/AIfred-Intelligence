"""Agent-Editor: STT-Tab — ob der Dienst erreichbar ist; eingestellt wird er auf seiner eigenen Oberfläche."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import t
from .header import _editor_header


def _stt_view() -> rx.Component:
    """STT-Tab: Erreichbarkeit des Dienstes; alles Weitere stellt die STT-Suite selbst ein."""
    return rx.vstack(
        _editor_header(),
        rx.cond(
            ~AIState.stt_available,
            rx.callout(
                t("stt_unavailable"),
                icon="triangle-alert",
                color_scheme="red",
                width="100%",
            ),
            rx.vstack(
                rx.text(t("stt_suite_hint"), font_size="12px", color=COLORS["text_secondary"], width="100%"),
                spacing="3",
                width="100%",
                min_width="0",
            ),
        ),
        spacing="3",
        width="100%",
        height="100%",
        min_width="0",
        overflow_x="hidden",
    )
