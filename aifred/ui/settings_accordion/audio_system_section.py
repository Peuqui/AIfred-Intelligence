"""Settings: TTS-/STT-Sektionen + System-Neustart-Buttons."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import t, native_select_tts, native_select_stt


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
            # Streaming TTS Toggle Group
            rx.hstack(
                rx.text("Streaming", font_size="11px", color="#d4a14a"),
                rx.switch(
                    checked=AIState.tts_streaming_enabled,
                    on_change=AIState.toggle_tts_streaming,
                    size="1",
                    disabled=~(AIState.enable_tts & AIState.tts_autoplay),
                ),
                rx.text(
                    rx.cond(AIState.tts_streaming_enabled, "ON", "OFF"),
                    font_size="10px",
                    color=rx.cond(AIState.tts_streaming_enabled, "#d4a14a", "#666"),
                ),
                spacing="1",
                align="center",
                opacity=rx.cond(AIState.enable_tts & AIState.tts_autoplay, "1", "0"),
                pointer_events=rx.cond(AIState.enable_tts & AIState.tts_autoplay, "auto", "none"),
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
            # XTTS CPU Mode Toggle (only when XTTS active)
            rx.cond(
                AIState.enable_tts & (AIState.tts_engine == "xtts"),
                rx.tooltip(
                  rx.hstack(
                    rx.switch(
                        checked=AIState.xtts_gpu_enabled,
                        on_change=AIState.toggle_xtts_gpu,
                        size="1",
                    ),
                    rx.text(
                        rx.cond(
                            AIState.xtts_force_cpu,
                            rx.cond(AIState.ui_language == "de", "CPU (langsamer)", "CPU (slower)"),
                            rx.cond(AIState.ui_language == "de", "GPU (schneller)", "GPU (faster)"),
                        ),
                        font_size="10px",
                        color="#d4a14a",
                    ),
                    spacing="1",
                    align="center",
                  ),
                  content=rx.cond(
                      AIState.ui_language == "de",
                      "Container-Neustart dauert einige Sekunden",
                      "Container restart takes a few seconds",
                  ),
                ),
                rx.fragment(),
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


def _stt_section() -> rx.Component:
    return rx.vstack(
        rx.text(t("stt_heading"), font_weight="bold", font_size="12px"),
        # Whisper Model Selection
        rx.hstack(
            rx.text(t("stt_model_label"), font_size="11px", font_weight="500", width="80px"),
            rx.cond(
                AIState.is_mobile,
                # Mobile: Native select
                native_select_stt(
                    AIState.whisper_model_display,
                    AIState.set_whisper_model,
                    [t("stt_model_tiny"), t("stt_model_base"), t("stt_model_small"), t("stt_model_medium"), t("stt_model_large")],
                ),
                # Desktop: Radix UI select
                rx.select(
                    [t("stt_model_tiny"), t("stt_model_base"), t("stt_model_small"), t("stt_model_medium"), t("stt_model_large")],
                    value=AIState.whisper_model_display,
                    on_change=AIState.set_whisper_model,
                    size="2",
                ),
            ),
            spacing="2",
            align="center",
            width="100%",
        ),
        # Device is now fixed to CPU (configured in config.py)
        # GPU would use precious VRAM needed for LLM inference
        # REMOVED: Show Transcription Toggle (moved to top, near recording buttons)
        spacing="3",
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
