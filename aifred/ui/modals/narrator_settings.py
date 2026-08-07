"""Narrator plugin settings modal.

Opened via the gear icon in the Agent-Editor plugin tab
(``NarratorPlugin.settings_event_name = "open_narrator_settings"``).
Family-A overlay (same look as Vigilantia/Casus): engine
("(same as spoken output)" + usable engines), GPU-free fallback
(auto mode only), and the narration voice.
"""

import reflex as rx

from ...state import AIState
from ..helpers import t, overlay_modal


def _row(
    label_key: str, control: rx.Component, tooltip_key: str = "",
) -> rx.Component:
    # Tooltip only on the label — wrapping the whole row keeps the
    # tooltip hovered while the select is open and covers its options.
    label: rx.Component = rx.text(t(label_key), font_size="13px", width="90px")
    if tooltip_key:
        label = rx.tooltip(label, content=t(tooltip_key))
    return rx.hstack(
        label,
        control,
        spacing="2",
        align="center",
        width="100%",
    )


def narrator_settings_modal() -> rx.Component:
    return overlay_modal(
        AIState.narrator_settings_open,
        rx.vstack(
            # Header: icon + title + X-close (family-A pattern)
            rx.hstack(
                rx.icon("audio-lines", size=20),
                rx.text("Narrator", font_weight="bold", size="4"),
                rx.spacer(),
                rx.icon_button(
                    rx.icon("x", size=16),
                    on_click=AIState.close_narrator_settings,
                    size="1",
                    variant="ghost",
                    color_scheme="gray",
                    custom_attrs={"data-modal-close": "true"},
                ),
                spacing="2",
                align="center",
                width="100%",
            ),
            rx.text(
                t("narrator_settings_subtitle"),
                color="gray",
                size="2",
                margin_bottom="0.5em",
            ),
            rx.divider(),
            _row(
                "narrator_engine_label",
                rx.select(
                    AIState.narrator_engine_options,
                    value=AIState.narrator_engine_display,
                    on_change=AIState.set_narrator_engine,
                    size="2",
                ),
            ),
            rx.cond(
                AIState.narrator_engine == "auto",
                _row(
                    "narrator_fallback_label",
                    rx.select(
                        AIState.narrator_fallback_options,
                        value=AIState.narrator_fallback_display,
                        on_change=AIState.set_narrator_fallback_engine,
                        size="2",
                    ),
                    tooltip_key="narrator_fallback_tooltip",
                ),
                rx.fragment(),
            ),
            _row(
                "narrator_voice_label",
                rx.select(
                    AIState.narrator_voice_options,
                    value=AIState.narrator_voice_display,
                    on_change=AIState.set_narrator_voice,
                    size="2",
                ),
            ),
            spacing="3",
            width="100%",
        ),
        on_close=AIState.close_narrator_settings,
        width="420px",
    )
