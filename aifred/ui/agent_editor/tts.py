"""Agent-Editor: TTS-Tab — die Eskalationsliste der Sprachausgabe bearbeiten.

Reihenfolge, An/Aus und Sprecheinheit der Einträge, andere Rechner mit
TTS-Containern, neue Einträge. Die Liste lebt in ``settings.json``
(``tts_escalation``, ``tts_hosts``); der State dazu ist
``state/_tts_config_mixin.py``. An/Aus der Sprachausgabe, Autoplay und die
Rechner-Schalter stehen auf der Hauptseite (Audio-Bereich).
"""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

from typing import Any

import reflex as rx

from ...state import AIState
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


def _entry_row(row: rx.Var) -> rx.Component:
    """One escalation entry: order, on/off, label, speech unit, delete."""
    index = row["index"].to(int)
    return rx.hstack(
        _icon_action("chevron-up", AIState.move_tts_entry(index, -1), "tts_entry_up"),
        _icon_action("chevron-down", AIState.move_tts_entry(index, 1), "tts_entry_down"),
        rx.switch(
            checked=row["enabled"].to(bool),
            on_change=lambda enabled: AIState.set_tts_entry_enabled(index, enabled),
            size="1",
        ),
        rx.text(
            row["label"].to(str),
            font_size="12px",
            color=rx.cond(row["enabled"].to(bool), "#ddd", "#666"),
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
            row["note"].to(str) != "",
            rx.badge(row["note"].to(str), color_scheme="red", size="1"),
            rx.fragment(),
        ),
        rx.el.select(
            rx.el.option(t("tts_unit_sentence"), value="sentence"),
            rx.el.option(t("tts_unit_paragraph"), value="paragraph"),
            rx.el.option(t("tts_unit_whole"), value="whole"),
            value=row["unit"].to(str),
            on_change=lambda unit: AIState.set_tts_entry_unit(row["engine"].to(str), unit),
            style={**_NATIVE_SELECT_STYLE_COMPACT, "flex_shrink": "0"},
        ),
        _icon_action("trash-2", AIState.remove_tts_entry(index), "tts_entry_remove"),
        spacing="1",
        align="center",
        width="100%",
    )


def _add_entry_row() -> rx.Component:
    return rx.hstack(
        rx.el.select(
            rx.el.option(t("tts_add_engine_placeholder"), value="", disabled=True),
            rx.foreach(AIState.tts_add_engine_options, lambda label: rx.el.option(label, value=label)),
            value=AIState.tts_new_entry_engine,
            on_change=AIState.set_tts_new_entry_engine,
            style={**_NATIVE_SELECT_STYLE_COMPACT, "flex": "1", "min_width": "0"},
        ),
        rx.el.select(
            rx.foreach(AIState.tts_add_host_options, lambda label: rx.el.option(label, value=label)),
            value=AIState.tts_new_entry_host,
            on_change=AIState.set_tts_new_entry_host,
            style={**_NATIVE_SELECT_STYLE_COMPACT, "flex": "1", "min_width": "0"},
        ),
        rx.button(
            rx.icon("plus", size=12),
            t("tts_add_entry"),
            on_click=AIState.add_tts_entry,
            disabled=AIState.tts_new_entry_engine == "",
            size="1",
            variant="soft",
        ),
        spacing="1",
        align="center",
        width="100%",
    )


def _hosts_block() -> rx.Component:
    """Other machines that run TTS containers with the same API."""
    return rx.vstack(
        rx.text(t("tts_hosts_heading"), font_size="11px", color="#d4a14a"),
        rx.foreach(
            AIState.tts_host_rows,
            lambda host: rx.hstack(
                rx.text(host["name"], font_size="12px", color="#ddd"),
                rx.text(host["address"], font_size="11px", color="#888"),
                rx.text(host["ssh"], font_size="11px", color="#666", flex="1"),
                _icon_action("trash-2", AIState.remove_tts_host(host["name"]), "tts_host_remove"),
                spacing="2",
                align="center",
                width="100%",
            ),
        ),
        rx.hstack(
            rx.input(
                value=AIState.tts_new_host_name,
                on_change=AIState.set_tts_new_host_name,
                placeholder=t("tts_host_name_placeholder"),
                size="1",
                flex="1",
            ),
            rx.input(
                value=AIState.tts_new_host_address,
                on_change=AIState.set_tts_new_host_address,
                placeholder=t("tts_host_address_placeholder"),
                size="1",
                flex="1",
            ),
            rx.input(
                value=AIState.tts_new_host_ssh,
                on_change=AIState.set_tts_new_host_ssh,
                placeholder=t("tts_host_ssh_placeholder"),
                size="1",
                flex="1",
            ),
            rx.button(
                rx.icon("plus", size=12),
                on_click=AIState.add_tts_host,
                size="1",
                variant="soft",
            ),
            spacing="1",
            align="center",
            width="100%",
        ),
        spacing="1",
        width="100%",
    )


def _tts_view() -> rx.Component:
    """TTS-Tab: Eskalationsliste, andere Rechner, neue Einträge."""
    return rx.vstack(
        _editor_header(),
        rx.box(
            rx.vstack(
                rx.hstack(
                    rx.text(t("tts_list_heading"), font_size="12px", color="#d4a14a"),
                    rx.popover.root(
                        rx.popover.trigger(rx.icon("lightbulb", size=14, color="#FFD700", cursor="pointer")),
                        rx.popover.content(
                            rx.text(t("tts_list_tooltip"), font_size="11px", color="#ddd", line_height="1.5"),
                            max_width="340px",
                            padding="10px",
                        ),
                    ),
                    spacing="2",
                    align="center",
                ),
                rx.foreach(AIState.tts_escalation_rows, _entry_row),
                rx.divider(),
                _hosts_block(),
                rx.divider(),
                rx.text(t("tts_add_heading"), font_size="11px", color="#d4a14a"),
                _add_entry_row(),
                spacing="2",
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
