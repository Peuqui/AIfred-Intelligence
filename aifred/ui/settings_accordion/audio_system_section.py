"""Settings: TTS-Sektion + System-Neustart-Buttons (STT: Hamburger-Menü, Tab „STT“)."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import t


def _tts_section() -> rx.Component:
    """Spoken output on the main page: on/off, autoplay, one switch per other
    machine (off stops its TTS containers). The escalation list itself is
    edited in the agent editor's TTS tab (gear icon)."""
    return rx.vstack(
        rx.hstack(
            rx.text(t("tts_heading"), font_weight="bold", font_size="12px"),
            rx.tooltip(
                rx.icon_button(
                    rx.icon("settings", size=14),
                    on_click=AIState.open_tts_settings,
                    size="1",
                    variant="ghost",
                    color_scheme="gray",
                ),
                content=t("tts_open_settings"),
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
        rx.foreach(
            AIState.tts_host_rows,
            lambda host: rx.hstack(
                rx.icon("server", size=12, color="#888"),
                rx.text(host["name"], font_size="11px", color="#ccc"),
                rx.box(flex="1"),
                rx.switch(
                    checked=host["enabled"].to(bool),
                    on_change=lambda enabled: AIState.set_tts_host_enabled(host["name"].to(str), enabled),
                    size="1",
                ),
                spacing="2",
                align="center",
                width="100%",
            ),
        ),
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
