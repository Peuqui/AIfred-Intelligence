"""Settings: TTS-Sektion + System-Neustart-Buttons (STT: Hamburger-Menü, Tab „STT“)."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import _NATIVE_SELECT_STYLE, t, native_select_tts


def _tts_section() -> rx.Component:
    # TTS (Text-to-Speech) Section
    return rx.vstack(
        # Row 1: Label + AutoPlay + Streaming toggles
        rx.hstack(
            rx.text(t("tts_heading"), font_weight="bold", font_size="12px"),
            # Lightbulb: explains why some engines are greyed out.
            rx.popover.root(
                rx.popover.trigger(
                    rx.tooltip(
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
                        content=t("tts_engine_disabled_tooltip"),
                    ),
                ),
                rx.popover.content(
                    rx.text(
                        t("tts_engine_disabled_tooltip"),
                        font_size="11px",
                        color="#ddd",
                        line_height="1.5",
                    ),
                    max_width="340px",
                    padding="10px",
                ),
            ),
            # Spacer
            rx.box(flex="1"),
            # Autoplay Toggle Group (only show when TTS enabled)
            rx.cond(
                AIState.enable_tts,
                rx.hstack(
                    rx.text(t("tts_autoplay_label"), font_size="11px", color="#d4a14a"),
                    rx.switch(
                        checked=AIState.tts_autoplay,
                        on_change=AIState.toggle_tts_autoplay,
                        size="1",
                    ),
                    rx.text(
                        rx.cond(AIState.tts_autoplay, "ON", "OFF"),
                        font_size="10px",
                        color=rx.cond(AIState.tts_autoplay, "#d4a14a", "#666"),
                    ),
                    spacing="1",
                    align="center",
                ),
                rx.box(),
            ),
            # Unit of the spoken output (per engine, system-wide): sentence-by-sentence streaming,
            # paragraph by paragraph, or the whole response at once. Always visible: it also
            # governs the Echo Dot, which does not depend on the browser's TTS/autoplay toggles.
            rx.hstack(
                rx.text(t("tts_speech_unit_label"), font_size="11px", color="#d4a14a"),
                rx.el.select(
                    rx.el.option(t("tts_unit_sentence"), value="sentence"),
                    rx.el.option(t("tts_unit_paragraph"), value="paragraph"),
                    rx.el.option(t("tts_unit_whole"), value="whole"),
                    value=AIState.tts_speech_unit,
                    on_change=AIState.set_tts_speech_unit,
                    style=_NATIVE_SELECT_STYLE,
                ),
                spacing="1",
                align="center",
            ),
            spacing="2",
            align="center",
            width="100%",
        ),
        # Row 2: Engine/Off dropdown + XTTS GPU toggle
        rx.hstack(
            rx.cond(
                AIState.is_mobile,
                native_select_tts(
                    AIState.tts_engine_or_off,
                    AIState.set_tts_engine_or_off,
                    AIState.tts_engine_options,
                ),
                rx.select.root(
                    rx.select.trigger(width="100%"),
                    rx.select.content(
                        rx.foreach(
                            AIState.tts_engine_options,
                            lambda opt: rx.select.item(
                                opt["label"],
                                value=opt["label"].to(str),
                                disabled=opt["disabled"].to(bool),
                                # Radix barely dims disabled
                                # items in the dark theme —
                                # force a visible greyed-out
                                # look.
                                opacity=rx.cond(
                                    opt["disabled"].to(bool),
                                    "0.4", "1",
                                ),
                            ),
                        ),
                    ),
                    value=AIState.tts_engine_or_off,
                    on_change=AIState.set_tts_engine_or_off,
                    size="2",
                    width="100%",
                ),
            ),
            spacing="2",
            align="center",
            width="100%",
        ),
        # Narrator (narrate_file) engine/voice: configured via the gear icon
        # in the Agent-Editor plugin tab (narrator_settings_modal).
        # Agent voices are configured in the Agent Editor modal
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
