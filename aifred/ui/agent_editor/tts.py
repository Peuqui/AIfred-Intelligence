"""Agent-Editor: TTS-Tab — die Eskalationsliste der Sprachausgabe bearbeiten.

Oben die anderen Rechner mit TTS-Containern (Verbindungstest), darunter die
Liste in Sprechreihenfolge mit Status je Eintrag, darunter das Hinzufügen
(eine Auswahl „Engine · Ort“). Die Liste lebt in ``settings.json``
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


def _section_heading(key: str, *extra: rx.Component) -> rx.Component:
    return rx.hstack(
        rx.text(t(key), font_size="12px", font_weight="bold", color="#d4a14a"),
        *extra,
        spacing="2",
        align="center",
        width="100%",
    )


# ── Rechner ───────────────────────────────────────────────────────

def _host_card(host: rx.Var) -> rx.Component:
    name = host["name"].to(str)
    return rx.vstack(
        rx.hstack(
            rx.icon("server", size=14, color="#888"),
            rx.text(name, font_size="13px", font_weight="bold", color="#ddd"),
            rx.text(host["address"].to(str), font_size="11px", color="#888"),
            rx.text(host["ssh"].to(str), font_size="11px", color="#666"),
            rx.box(flex="1"),
            rx.button(
                t("tts_host_test_button"),
                on_click=AIState.test_tts_host(name),
                size="1",
                variant="soft",
                color_scheme="gray",
            ),
            _icon_action("trash-2", AIState.remove_tts_host(name), "tts_host_remove"),
            spacing="2",
            align="center",
            width="100%",
        ),
        rx.cond(
            AIState.tts_host_test.contains(name),
            rx.text(AIState.tts_host_test[name], font_size="11px", color="#aaa"),
            rx.fragment(),
        ),
        spacing="1",
        width="100%",
        padding="8px 10px",
        border="1px solid #333",
        border_radius="8px",
    )


def _host_form() -> rx.Component:
    return rx.hstack(
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
        rx.button(t("tts_host_save"), on_click=AIState.add_tts_host, size="1"),
        spacing="1",
        align="center",
        width="100%",
    )


def _hosts_section() -> rx.Component:
    return rx.vstack(
        _section_heading(
            "tts_hosts_heading",
            rx.box(flex="1"),
            rx.button(
                rx.icon("plus", size=12),
                t("tts_hosts_add"),
                on_click=AIState.toggle_tts_host_form,
                size="1",
                variant="soft",
            ),
        ),
        rx.foreach(AIState.tts_host_rows, _host_card),
        rx.cond(AIState.tts_host_form_open, _host_form(), rx.fragment()),
        spacing="2",
        width="100%",
    )


# ── Liste ─────────────────────────────────────────────────────────

def _entry_row(row: rx.Var) -> rx.Component:
    """One escalation entry: order, on/off, label, status, speech unit, delete."""
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
        rx.text(row["status"].to(str), font_size="11px", color="#888", white_space="nowrap"),
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


def _list_section() -> rx.Component:
    return rx.vstack(
        _section_heading(
            "tts_list_heading",
            rx.popover.root(
                rx.popover.trigger(rx.icon("lightbulb", size=14, color="#FFD700", cursor="pointer")),
                rx.popover.content(
                    rx.text(t("tts_list_tooltip"), font_size="11px", color="#ddd", line_height="1.5"),
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
        rx.foreach(AIState.tts_escalation_rows, _entry_row),
        # One choice adds the entry at the end of the list; arrows place it.
        rx.el.select(
            rx.el.option(t("tts_add_placeholder"), value=""),
            rx.foreach(
                AIState.tts_add_options,
                lambda option: rx.el.option(option["label"], value=option["value"]),
            ),
            value="",
            on_change=AIState.add_tts_entry,
            style={**_NATIVE_SELECT_STYLE_COMPACT, "width": "100%"},
        ),
        spacing="2",
        width="100%",
    )


def _tts_view() -> rx.Component:
    """TTS-Tab: Rechner, Eskalationsliste, Hinzufügen."""
    return rx.vstack(
        _editor_header(),
        rx.box(
            rx.vstack(
                _hosts_section(),
                rx.divider(),
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
