"""Settings: Sampling-Parameter-Zeilen (Temp/Top-P/Top-K/Penalty) pro Agent.

Rendert generisch über ``AIState.sampling_rows`` (rx.foreach) — jeder
registrierte Agent (auch Custom) bekommt seine Zeile automatisch.
"""

from __future__ import annotations

import reflex as rx
from reflex.vars import ObjectVar

from ...lib.agent_tuning import SamplingRow
from ...state import AIState
from ...theme import COLORS
from ..helpers import agent_emoji_var, clickable_tip, t


def _sampling_input(row: ObjectVar[SamplingRow], param: str, value: rx.Var, width: str = "55px") -> rx.Component:
    """Helper: Input field for a sampling parameter (always editable)."""
    return rx.input(
        default_value=value,
        on_blur=lambda v: AIState.set_agent_sampling_for(row.id, param, v),
        key=AIState.sampling_reset_key.to(str) + "_" + row.id.to(str) + "_" + param,
        type="number",
        width=width,
        size="1",
        height="28px",
    )


def _temp_input(row: ObjectVar[SamplingRow], width: str = "50px") -> rx.Component:
    """Helper: Temperature input (disabled in Auto mode, except Vision)."""
    return rx.input(
        default_value=row.temp,
        on_blur=lambda v: AIState.set_agent_temperature_for(row.id, v),
        key=AIState.sampling_reset_key.to(str) + "_" + row.id.to(str) + "_temp",
        type="number",
        width=width,
        size="1",
        height="28px",
        disabled=row.temp_disabled,
        opacity=rx.cond(row.temp_disabled, "0.5", "1.0"),
    )


def _sampling_agent_row(row: ObjectVar[SamplingRow]) -> rx.Component:
    """One agent row with temp + 4 sampling inputs + reset button."""
    return rx.hstack(
        rx.hstack(
            agent_emoji_var(row.emoji, size="14px"),
            rx.text(row.label, font_size="10px", font_weight="bold", color=COLORS["text_primary"]),
            spacing="1", align="center", width="75px",
        ),
        _temp_input(row),
        _sampling_input(row, "top_k", row.top_k),
        _sampling_input(row, "top_p", row.top_p),
        _sampling_input(row, "min_p", row.min_p),
        _sampling_input(row, "repeat_penalty", row.repeat_penalty),
        rx.tooltip(
            rx.icon(
                "rotate-ccw",
                size=13,
                color=COLORS["primary"],
                cursor="pointer",
                on_click=AIState.reset_agent_sampling_for(row.id),
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
                # Erklaerung per Klick statt Hover-Tooltip: der Tooltip am Schalter ging
                # beim Oeffnen des Modals auf (Fokus landet auf dem Schalter) und verdeckte
                # die Tabelle.
                clickable_tip(
                    rx.icon(
                        "lightbulb",
                        size=14,
                        color=COLORS["accent_gold"],
                        cursor="pointer",
                        style={
                            "transition": "transform 0.2s ease",
                            "&:hover": {"transform": "scale(1.15)"},
                        },
                    ),
                    rx.text(t("sampling_temp_toggle_tooltip"), font_size="12px", color=COLORS["text_primary"]),
                ),
                spacing="1",
                align="center",
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
