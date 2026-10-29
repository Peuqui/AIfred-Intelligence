"""Agent-Editor: STT-Tab — das bevorzugte Gerät (siehe ``state/_stt_settings_mixin.py``)."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import t
from .header import _editor_header


def _stt_row(label, control: rx.Component, hint=None) -> rx.Component:
    """Eine Einstellungs-Zeile: Label links, Control rechts, optionaler
    Hinweistext darunter.

    ``label``/``hint`` sind übersetzte Werte und damit Reflex-Vars — die
    dürfen nicht in ein bool gecastet werden (kein ``or``, kein ``if``).
    Ob die Hinweiszeile existiert, entscheidet daher Python beim Bauen
    des Trees, nicht ``rx.cond`` zur Laufzeit.
    """
    rows: list[rx.Component] = [
        rx.hstack(
            # flex_grow + min_width 0 lässt das Label schrumpfen statt die
            # Zeile breiter werden zu lassen — sonst schiebt die Summe aus
            # Label- und Control-Breite den Tab in einen H-Scrollbalken.
            rx.text(
                label, font_size="13px", color=COLORS["text_soft"],
                flex_grow="1", min_width="0",
            ),
            rx.box(control, flex_shrink="0"),
            width="100%",
            spacing="3",
            align="center",
        ),
    ]
    if hint is not None:
        rows.append(rx.text(hint, font_size="11px", color=COLORS["text_muted"], width="100%"))
    return rx.vstack(
        *rows,
        spacing="1",
        width="100%",
        min_width="0",
        padding_y="6px",
        border_bottom="1px solid var(--aifred-input-bg)",
    )


def _stt_view() -> rx.Component:
    """STT-Tab: das bevorzugte Gerät; alles Weitere stellt die STT-Suite selbst ein."""
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
                _stt_row(
                    t("stt_device"),
                    rx.select(
                        AIState.stt_device_labels,
                        value=AIState.stt_device_label,
                        on_change=AIState.stt_set_device,
                        size="1",
                    ),
                    t("stt_device_hint"),
                ),
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
