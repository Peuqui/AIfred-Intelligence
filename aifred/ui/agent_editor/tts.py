"""Agent-Editor: TTS-Tab — die Eskalationsliste der Sprachausgabe bearbeiten.

Die Liste in Sprechreihenfolge mit Status je Eintrag (ein-/ausschalten, verschieben,
Einheit). Es wird nichts hinzugefügt oder entfernt: die Liste ist immer der Abgleich aus
AIfreds gespeicherter Reihenfolge/Schaltern (``tts_escalation``) und dem Katalog des
TTS-Dienstes; neue Zeilen kommen ausgeschaltet ans Ende. Anlegen von Rechnern, Start und
Stopp gibt es nur in der Weboberfläche der Suite; der State dazu ist
``state/_tts_config_mixin.py``. Antwortet der Dienst nicht, steht die Meldung über
der Liste. An/Aus der Sprachausgabe und Autoplay stehen auf der Hauptseite
(Audio-Bereich).
"""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

from typing import Any

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import _NATIVE_SELECT_STYLE_COMPACT, t
from .header import _editor_header


def _icon_action(icon: str, on_click: Any, tooltip_key: str) -> rx.Component:
    return rx.tooltip(
        rx.icon_button(
            rx.icon(icon, size=12),
            on_click=on_click,
            size="1",
            variant="ghost",
            color_scheme="gray",
        ),
        content=t(tooltip_key),
    )


def _section_heading(key: str, *extra: rx.Component) -> rx.Component:
    return rx.hstack(
        rx.text(t(key), font_size="12px", font_weight="bold", color=COLORS["accent_warning"]),
        *extra,
        spacing="2",
        align="center",
        width="100%",
    )


# ── Liste ─────────────────────────────────────────────────────────

def _entry_row(row: rx.Var) -> rx.Component:
    """One escalation entry: order, on/off, label, status, speech unit."""
    index = row["index"].to(int)
    return rx.hstack(
        _icon_action("chevron-up", AIState.move_tts_entry(index, -1), "tts_entry_up"),
        _icon_action("chevron-down", AIState.move_tts_entry(index, 1), "tts_entry_down"),
        rx.switch(
            checked=row["enabled"].to(bool),
            on_change=lambda enabled: AIState.set_tts_entry_enabled(index, enabled),
            disabled=row["unavailable"].to(bool),
            size="1",
        ),
        rx.text(
            row["label"].to(str),
            font_size="12px",
            color=rx.cond(row["enabled"].to(bool), COLORS["text_soft"], COLORS["text_muted"]),
            flex="1",
            min_width="0",
            white_space="nowrap",
            overflow="hidden",
            text_overflow="ellipsis",
        ),
        rx.cond(
            row["reserved"].to(bool),
            rx.tooltip(
                rx.badge(t("tts_entry_reserved"), color_scheme="amber", size="1"),
                content=t("tts_entry_reserved_tooltip"),
            ),
            rx.fragment(),
        ),
        rx.cond(
            row["warn"].to(bool),
            rx.tooltip(
                rx.icon("triangle-alert", size=14, color=COLORS["accent_warning"]),
                content=t("tts_entry_unreachable_warning"),
            ),
            rx.fragment(),
        ),
        rx.cond(
            row["note"].to(str) != "",
            rx.badge(row["note"].to(str), color_scheme="red", size="1"),
            rx.fragment(),
        ),
        rx.text(row["status"].to(str), font_size="11px", color=COLORS["text_secondary"], white_space="nowrap"),
        rx.el.select(
            rx.el.option(t("tts_unit_sentence"), value="sentence"),
            rx.el.option(t("tts_unit_paragraph"), value="paragraph"),
            rx.el.option(t("tts_unit_whole"), value="whole"),
            value=row["unit"].to(str),
            on_change=lambda unit: AIState.set_tts_entry_unit(row["engine"].to(str), unit),
            disabled=row["unavailable"].to(bool),
            style={**_NATIVE_SELECT_STYLE_COMPACT, "flex_shrink": "0"},
        ),
        spacing="1",
        align="center",
        width="100%",
    )


def _list_section() -> rx.Component:
    return rx.vstack(
        _section_heading(
            "tts_list_heading",
            rx.popover.root(
                rx.popover.trigger(rx.icon("lightbulb", size=14, color=COLORS["accent_gold"], cursor="pointer")),
                rx.popover.content(
                    rx.text(t("tts_list_tooltip"), font_size="11px", color=COLORS["text_primary"], line_height="1.5"),
                    max_width="340px",
                    padding="10px",
                ),
            ),
            rx.box(flex="1"),
            rx.button(
                rx.icon("refresh-cw", size=12),
                t("tts_check_status"),
                on_click=AIState.check_tts_status,
                loading=AIState.tts_status_checking,
                size="1",
                variant="soft",
                color_scheme="gray",
            ),
        ),
        rx.cond(
            AIState.tts_service_error != "",
            rx.text(AIState.tts_service_error, font_size="12px", color=COLORS["danger"]),
            rx.fragment(),
        ),
        rx.foreach(AIState.tts_escalation_rows, _entry_row),
        spacing="2",
        width="100%",
    )


def _tts_view() -> rx.Component:
    """TTS-Tab: Eskalationsliste."""
    return rx.vstack(
        _editor_header(),
        rx.box(
            rx.vstack(
                _list_section(),
                spacing="3",
                width="100%",
            ),
            overflow_y="auto",
            flex="1",
            min_height="0",
            width="100%",
            padding_right="4px",
        ),
        spacing="3",
        width="100%",
        height="100%",
    )
