"""Settings: Sampling-Parameter-Zeilen (Temp/Top-P/Top-K/Penalty) pro Agent.

Rendert generisch über ``AIState.sampling_rows`` (rx.foreach) — jeder
registrierte Agent (auch Custom) bekommt seine Zeile automatisch.
"""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import t, agent_emoji_var


def _sampling_input(row: rx.Var, param: str, value: rx.Var, width: str = "55px") -> rx.Component:
    """Helper: Input field for a sampling parameter (always editable)."""
    return rx.input(
        default_value=value,
        on_blur=lambda v: AIState.set_agent_sampling_for(row.id, param, v),  # type: ignore[attr-defined,union-attr]
        key=AIState.sampling_reset_key.to(str) + "_" + row.id + "_" + param,  # type: ignore[attr-defined,union-attr,operator]
        type="number",
        width=width,
        size="1",
        height="28px",
    )


def _temp_input(row: rx.Var, width: str = "50px") -> rx.Component:
    """Helper: Temperature input (disabled in Auto mode, except Vision)."""
    return rx.input(
        default_value=row.temp,  # type: ignore[attr-defined,union-attr]
        on_blur=lambda v: AIState.set_agent_temperature_for(row.id, v),  # type: ignore[attr-defined,union-attr]
        key=AIState.sampling_reset_key.to(str) + "_" + row.id + "_temp",  # type: ignore[attr-defined,union-attr,operator]
        type="number",
        width=width,
        size="1",
        height="28px",
        disabled=row.temp_disabled,  # type: ignore[attr-defined,union-attr]
        opacity=rx.cond(row.temp_disabled, "0.5", "1.0"),  # type: ignore[attr-defined,union-attr]
    )


def _sampling_agent_row(row: rx.Var) -> rx.Component:
    """One agent row with temp + 4 sampling inputs + reset button."""
    return rx.hstack(
        rx.hstack(
            agent_emoji_var(row.emoji, size="14px"),  # type: ignore[attr-defined,union-attr]
            rx.text(row.label, font_size="10px", font_weight="bold", color=COLORS["text_primary"]),  # type: ignore[attr-defined,union-attr]
            spacing="1", align="center", width="75px",
        ),
        _temp_input(row),
        _sampling_input(row, "top_k", row.top_k),  # type: ignore[attr-defined,union-attr]
        _sampling_input(row, "top_p", row.top_p),  # type: ignore[attr-defined,union-attr]
        _sampling_input(row, "min_p", row.min_p),  # type: ignore[attr-defined,union-attr]
        _sampling_input(row, "repeat_penalty", row.repeat_penalty),  # type: ignore[attr-defined,union-attr]
        rx.tooltip(
            rx.icon(
                "rotate-ccw",
                size=13,
                color=COLORS["primary"],
                cursor="pointer",
                on_click=AIState.reset_agent_sampling_for(row.id),  # type: ignore[attr-defined,union-attr]
                style={
                    "transition": "all 0.2s ease",
                    "&:hover": {"color": COLORS["primary_hover"], "transform": "scale(1.15)"},
                },
            ),
            content=t("sampling_reset_tooltip"),
        ),
        spacing="2",
        align="center",
    )


def sampling_control_section() -> rx.Component:
    """Sampling parameters with Auto/Manual toggle and per-agent controls."""
    return rx.vstack(
        # Title row with Auto/Manual toggle
        rx.hstack(
            rx.text(t("sampling_section_label"), font_weight="bold", font_size="12px"),
            rx.spacer(),
            rx.tooltip(
                rx.hstack(
                    rx.text(t("sampling_temp_label"), font_size="10px", font_weight="bold",
                            color=COLORS["text_primary"]),
                    rx.text("Auto", font_size="10px", color=COLORS["text_secondary"]),
                    rx.switch(
                        checked=AIState.temperature_mode == "manual",
                        on_change=AIState.set_temperature_mode,
                        size="1",
                    ),
                    rx.text("Manual", font_size="10px", color=COLORS["text_secondary"]),
                    spacing="1",
                    align="center",
                ),
                content=t("sampling_temp_toggle_tooltip"),
                max_width="280px",
            ),
            width="100%",
            align="center",
        ),
        # Header row: label + param columns
        rx.hstack(
            rx.text("", width="75px"),
            rx.text("Temp", font_size="9px", font_weight="bold", width="50px", text_align="center",
                     color=COLORS["text_primary"]),
            rx.text("Top-K", font_size="9px", font_weight="bold", width="55px", text_align="center",
                     color=COLORS["text_primary"]),
            rx.text("Top-P", font_size="9px", font_weight="bold", width="55px", text_align="center",
                     color=COLORS["text_primary"]),
            rx.text("Min-P", font_size="9px", font_weight="bold", width="55px", text_align="center",
                     color=COLORS["text_primary"]),
            rx.text("Rep.P", font_size="9px", font_weight="bold", width="55px", text_align="center",
                     color=COLORS["text_primary"]),
            rx.text("", width="13px"),
            spacing="2",
            align="center",
        ),
        # Agent rows — one per registered agent (custom agents included)
        rx.foreach(AIState.sampling_rows, _sampling_agent_row),
        width="100%",
        spacing="1",
    )
