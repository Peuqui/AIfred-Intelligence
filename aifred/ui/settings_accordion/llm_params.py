"""Settings: LLM-Parameter-Accordion (Popover in der Eingabe-Zeile)."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import t
from .controls import _ctx_column
from .sampling import sampling_control_section


def llm_parameters_accordion() -> rx.Component:
    """LLM Parameters as popover — floats over content, no layout shift."""
    return rx.popover.root(
        rx.popover.trigger(
            rx.button(
                t("llm_parameters"),
                variant="soft",
                color_scheme="gray",
                size="2",
                height="32px",
                font_size="11px",
                cursor="pointer",
            ),
        ),
        rx.popover.content(
            rx.vstack(
                # Sampling Parameters (includes Temperature)
                sampling_control_section(),

                # Context Window Control
                rx.vstack(
                    rx.text(
                        t("context_window"),
                        font_weight="bold",
                        font_size="12px"
                    ),

                    # Hint shown only when fields are disabled (non-Ollama backend):
                    # the context is fixed by the loaded llama-swap/vLLM profile.
                    rx.cond(
                        AIState.backend_type != "ollama",
                        rx.text(
                            t("context_window_fixed_hint"),
                            font_size="10px",
                            color=COLORS["warning_text"],
                            font_style="italic",
                        ),
                    ),

                    # Per-LLM Context Control \u2014 one column per registered
                    # agent (custom agents included), rendered via foreach.
                    # Chat-Agenten: num_ctx wird nur bei Ollama zur Request-
                    # Zeit \u00fcbernommen. Bei llama.cpp/vLLM ist die
                    # ctx-Gr\u00f6\u00dfe zur Modell-Lade-Zeit fix (yaml/CLI-Args).
                    rx.hstack(
                        rx.foreach(
                            AIState.ctx_rows,
                            lambda row: _ctx_column(
                                row.id,
                                row.emoji,
                                row.label,
                                row.enabled,
                                row.value,
                                extra_disabled=AIState.backend_type != "ollama",
                            ),
                        ),
                        spacing="3",
                        flex_wrap="wrap",
                    ),

                    # Show Calculation Button (styled like "Text senden" button)
                    rx.button(
                        t("context_show_calculation"),
                        on_click=AIState.calculate_manual_context,
                        size="1",
                        variant="solid",
                        margin_top="8px",
                        style={
                            "background": "#3d2a00 !important",
                            "color": COLORS["accent_warning"] + " !important",
                            "border": f"1px solid {COLORS['accent_warning']}",
                            "font_weight": "600",
                            "&:hover": {
                                "background": "#4d3500 !important",
                                "color": "#ffb84d !important",
                            },
                        },
                    ),

                    # Chat agents' manual num_ctx is not persisted, vision's is
                    # (_settings_mixin.save_settings); the hint covers every chat agent.
                    rx.text(
                        t("context_window_persist_hint"),
                        font_size="11px",
                        color=COLORS["warning_text"],
                        font_style="italic",
                    ),

                    width="100%",
                    spacing="2",
                ),

                spacing="4",
                width="100%",
            ),
            style={
                "max_width": "600px",
                "max_height": "80vh",
                "overflow_y": "auto",
            },
            side="top",
            align="end",
        ),
    )
