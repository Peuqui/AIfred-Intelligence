"""Settings: Kontext-Spalten, Agent-Toggles, Thinking-/Speed-/RoPE-Controls."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import t, agent_emoji


def _ctx_column(
    emoji: str, label: str,
    enabled_var, toggle_handler,
    value_var, set_handler,
    placeholder: str = "16384",
    extra_disabled=False,
    **extra_style,
) -> rx.Component:
    """Helper: One agent context column with toggle + input.

    ``extra_disabled`` (Reflex-Var oder Python-bool): zusätzlicher
    Disabled-Grund. Für Chat-Agenten typischerweise
    ``AIState.backend_type != "ollama"`` — bei llama.cpp/vLLM
    wird ``num_ctx`` zur Modell-Lade-Zeit fix gesetzt, per Request
    nicht mehr änderbar; Toggle + Input greifen also nicht und werden
    grau. Vision ist davon ausgenommen (läuft immer via Ollama)."""
    input_disabled = ~enabled_var | extra_disabled  # type: ignore[operator]
    input_active = enabled_var & ~extra_disabled  # type: ignore[operator]
    return rx.vstack(
        rx.hstack(
            agent_emoji(emoji, size="13px"),
            rx.text(
                label,
                font_size="11px",
                font_weight="bold",
                color=COLORS["text_secondary"],
            ),
            rx.switch(
                checked=enabled_var,
                on_change=toggle_handler,
                disabled=extra_disabled,
                size="1",
            ),
            spacing="1",
            align="center",
        ),
        rx.input(
            placeholder=placeholder,
            default_value=value_var.to(str),
            on_blur=set_handler,
            type="number",
            width="78px",
            disabled=input_disabled,
            opacity=rx.cond(input_active, "1.0", "0.5"),
        ),
        spacing="1",
        **extra_style,
    )


# ============================================================
# AGENT TOGGLE + ROPE HELPERS
# ============================================================

def _agent_toggle(
    emoji: str,
    checked_var,
    on_change_handler,
    tooltip_text: str | rx.Var,
    color_scheme: str = "orange",
) -> rx.Component:
    """Single agent toggle with tooltip (Personality/Reasoning/Thinking)."""
    return rx.tooltip(
        rx.hstack(
            agent_emoji(emoji, size="14px"),
            rx.checkbox(
                checked=checked_var,
                on_change=on_change_handler,
                size="1",
                color_scheme=color_scheme,
                variant="surface",
            ),
            spacing="1",
            align="center",
        ),
        content=tooltip_text,
    )


def _agent_thinking_select(
    mode_var,
    options_var,
    on_change_handler,
    tooltip_text: str | rx.Var,
) -> rx.Component:
    """Thinking-mode dropdown (off / on / model-specific effort levels).

    One control for every model — only the option count varies: models
    whose chat template offers reasoning-effort levels (detected
    model-agnostically from the GGUF, e.g. DeepSeek-V4 "max") get them
    as extra entries; all others show just off/on.
    """
    return rx.tooltip(
        rx.hstack(
            agent_emoji("\U0001f9e0", size="14px"),
            rx.select(
                options_var,
                value=mode_var,
                on_change=on_change_handler,
                size="1",
                color_scheme="blue",
                position="popper",
            ),
            spacing="1",
            align="center",
        ),
        content=tooltip_text,
    )


def _lightbulb_icon() -> rx.Component:
    """Reasoning/Thinking help lightbulb icon with tooltip."""
    return rx.tooltip(
        rx.icon(
            "lightbulb",
            size=14,
            color="#FFD700",
            cursor="pointer",
            on_click=AIState.open_reasoning_thinking_help,
            style={
                "transition": "transform 0.2s ease",
                "&:hover": {"transform": "scale(1.15)"},
            },
        ),
        content=t("reasoning_thinking_help_lightbulb_tooltip"),
    )


def _speed_toggle(
    has_speed_var,
    speed_mode_var,
    toggle_handler,
    disabled_var=None,
) -> rx.Component:
    """Optional Ctx/Speed toggle, shown only when agent has speed variant."""
    switch_kwargs: dict = {
        "checked": speed_mode_var,
        "on_change": toggle_handler,
        "size": "1",
    }
    if disabled_var is not None:
        switch_kwargs["disabled"] = disabled_var
    return rx.cond(
        has_speed_var,
        rx.tooltip(
            rx.hstack(
                rx.text(
                    "Ctx",
                    font_size="10px",
                    color=rx.cond(speed_mode_var, "#666", "#4CAF50"),
                ),
                rx.switch(**switch_kwargs),
                rx.text(
                    "\u26a1",
                    font_size="10px",
                    color=rx.cond(speed_mode_var, "#FFA500", "#666"),
                ),
                spacing="1",
                align="center",
            ),
            content=AIState.speed_switch_tooltip,
        ),
    )


def _rope_select(value_var, on_change_handler) -> rx.Component:
    """RoPE factor select dropdown (1.0x / 1.5x / 2.0x)."""
    return rx.select.root(
        rx.select.trigger(placeholder=value_var),
        rx.select.content(
            rx.select.item("1.0x", value="1.0x"),
            rx.select.item("1.5x", value="1.5x"),
            rx.select.item("2.0x", value="2.0x"),
        ),
        value=value_var,
        on_change=on_change_handler,
        size="1",
    )
