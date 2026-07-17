"""Settings: Sampling-Parameter-Zeilen (Temp/Top-P/Top-K/Penalty) pro Agent."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import t, agent_emoji


def _sampling_input(agent: str, param: str, width: str = "55px") -> rx.Component:
    """Helper: Input field for a sampling parameter (always editable)."""
    attr_name = f"{agent}_{param}"
    return rx.input(
        default_value=getattr(AIState, attr_name).to(str),
        on_blur=lambda v, a=agent, p=param: getattr(AIState, f"set_{a}_sampling")(p, v),
        key=AIState.sampling_reset_key.to(str) + f"_{agent}_{param}",
        type="number",
        width=width,
        size="1",
        height="28px",
    )


def _temp_input(agent: str, width: str = "50px") -> rx.Component:
    """Helper: Temperature input field for an agent (disabled in Auto mode, except Vision)."""
    # Vision always uses manual temperature (no Auto mode)
    is_auto = AIState.temperature_mode == "auto" if agent != "vision" else False
    # AIfred uses self.temperature, others use self.{agent}_temperature
    if agent == "aifred":
        attr = AIState.temperature
    else:
        attr = getattr(AIState, f"{agent}_temperature")
    handler = getattr(AIState, f"set_{agent}_temperature_input")
    return rx.input(
        default_value=attr.to(str),
        on_blur=handler,
        key=AIState.sampling_reset_key.to(str) + f"_{agent}_temp",
        type="number",
        width=width,
        size="1",
        height="28px",
        disabled=is_auto,
        opacity=rx.cond(is_auto, "0.5", "1.0") if agent != "vision" else "1.0",
    )


def _sampling_agent_row(agent: str, emoji: str, label: str, reset_handler) -> rx.Component:
    """Helper: One agent row with temp + 4 sampling inputs + reset button."""
    return rx.hstack(
        rx.hstack(
            agent_emoji(emoji, size="14px"),
            rx.text(label, font_size="10px", font_weight="bold", color=COLORS["text_primary"]),
            spacing="1", align="center", width="75px",
        ),
        _temp_input(agent),
        _sampling_input(agent, "top_k"),
        _sampling_input(agent, "top_p"),
        _sampling_input(agent, "min_p"),
        _sampling_input(agent, "repeat_penalty"),
        rx.tooltip(
            rx.icon(
                "rotate-ccw",
                size=13,
                color=COLORS["primary"],
                cursor="pointer",
                on_click=reset_handler,
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
        # Agent rows
        _sampling_agent_row("aifred", "\U0001f3a9", "AIfred", AIState.reset_aifred_sampling),
        _sampling_agent_row("sokrates", "\U0001f3db\ufe0f", "Sokrates", AIState.reset_sokrates_sampling),
        _sampling_agent_row("salomo", "\U0001f451", "Salomo", AIState.reset_salomo_sampling),
        _sampling_agent_row("vision", "\U0001f4f7", "Vision", AIState.reset_vision_sampling),
        width="100%",
        spacing="1",
    )
