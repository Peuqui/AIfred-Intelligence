"""Settings: TTS-Sektion + System-Neustart-Buttons (STT: Hamburger-Menü, Tab „STT“)."""

from __future__ import annotations

from typing import Any

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import _NATIVE_SELECT_STYLE_COMPACT, t


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
                rx.text(host["address"], font_size="11px", color="#888", flex="1"),
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


def _tts_section() -> rx.Component:
    """Spoken output: on/off, autoplay, and the escalation list (the first
    entry from the top that can speak right now speaks)."""
    return rx.vstack(
        rx.hstack(
            rx.text(t("tts_heading"), font_weight="bold", font_size="12px"),
            rx.popover.root(
                rx.popover.trigger(
                    rx.icon(
                        "lightbulb",
                        size=14,
                        color="#FFD700",
                        cursor="pointer",
                        style={
                            "transition": "transform 0.2s ease",
                            "&:hover": {"transform": "scale(1.15)"},
                        },
                    ),
                ),
                rx.popover.content(
                    rx.text(t("tts_list_tooltip"), font_size="11px", color="#ddd", line_height="1.5"),
                    max_width="340px",
                    padding="10px",
                ),
            ),
            rx.box(flex="1"),
            rx.switch(
                checked=AIState.enable_tts,
                on_change=AIState.set_enable_tts,
                size="1",
            ),
            rx.text(
                rx.cond(AIState.enable_tts, "ON", "OFF"),
                font_size="10px",
                color=rx.cond(AIState.enable_tts, "#d4a14a", "#666"),
            ),
            rx.text(t("tts_autoplay_label"), font_size="11px", color="#d4a14a"),
            rx.switch(
                checked=AIState.tts_autoplay,
                on_change=AIState.toggle_tts_autoplay,
                size="1",
            ),
            spacing="2",
            align="center",
            width="100%",
        ),
        # The list also governs the Echo Dot and the narrator, so it stays
        # visible while the browser's spoken output is off.
        rx.text(t("tts_list_heading"), font_size="11px", color="#d4a14a"),
        rx.foreach(AIState.tts_escalation_rows, _entry_row),
        _hosts_block(),
        rx.text(t("tts_add_heading"), font_size="11px", color="#d4a14a"),
        _add_entry_row(),
        spacing="2",
        width="100%",
    )


def _restart_buttons() -> rx.Component:
    return rx.vstack(
        # Row 1: Backend and AIfred restart buttons (side by side, each 50%)
        rx.hstack(
            rx.button(
                rx.cond(
                    AIState.backend_type == "ollama",
                    t("restart_ollama"),
                    rx.text(f"\U0001f504 {AIState.backend_type.upper()} Neustart")
                ),
                on_click=AIState.restart_backend,
                size="2",
                variant="soft",
                color_scheme="blue",
                disabled=AIState.backend_switching,
                flex="1",
                style={
                    "&:hover:not([disabled])": {
                        "background": "var(--blue-a6) !important",
                        "transform": "scale(1.02)",
                    },
                    "&:active:not([disabled])": {
                        "background": "var(--blue-a8) !important",
                        "transform": "scale(0.98)",
                    },
                },
            ),
            rx.button(
                t("restart_aifred"),
                on_click=AIState.restart_aifred,
                size="2",
                variant="soft",
                color_scheme="orange",
                disabled=AIState.backend_switching,
                flex="1",
                style={
                    "&:hover:not([disabled])": {
                        "background": "var(--orange-a6) !important",
                        "transform": "scale(1.02)",
                    },
                    "&:active:not([disabled])": {
                        "background": "var(--orange-a8) !important",
                        "transform": "scale(0.98)",
                    },
                },
            ),
            spacing="3",
            width="100%",
        ),
        # Row 2: Load Default Settings button
        rx.button(
            "\U0001f4be Grundeinstellungen laden",
            on_click=AIState.load_default_settings,
            size="2",
            variant="solid",
            color_scheme="blue",
            disabled=AIState.backend_switching,
            width="100%",
            style={
                "&:hover:not([disabled])": {
                    "background": "var(--blue-a9) !important",
                    "transform": "scale(1.02)",
                },
                "&:active:not([disabled])": {
                    "background": "var(--blue-a11) !important",
                    "transform": "scale(0.98)",
                },
            },
        ),
        spacing="2",
        width="100%",
    )


def _restart_info() -> rx.Component:
    return rx.vstack(
        rx.text(
            t("backend_restart_info"),
            font_size="10px",
            color=COLORS["text_secondary"],
        ),
        rx.text(
            t("aifred_restart_info"),
            font_size="10px",
            color=COLORS["text_secondary"],
        ),
        spacing="1",
        width="100%",
    )
