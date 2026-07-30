"""Settings: LLM-Auswahl + Toggle-Zeilen pro Agent (AIfred/Sokrates/Salomo/Automatik/Vision)."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t, native_select_model
from .controls import (
    _agent_thinking_select,
    _agent_toggle,
    _lightbulb_icon,
    _rope_select,
    _speed_toggle,
)


def _aifred_model_row() -> rx.Component:
    # AIfred-LLM Selection
    return rx.hstack(
        rx.text(t("main_llm"), font_weight="bold", font_size="12px"),
        rx.cond(
            AIState.is_mobile,
            # MOBILE: Native HTML <select> (simple list)
            native_select_model(
                AIState.agent_tuning["aifred"].model,  # Display name with size
                AIState.set_aifred_model,  # Original handler
                AIState.backend_switching,
                AIState.available_models,  # Simple list of display names
            ),
            # DESKTOP: Radix UI Select
            rx.select(
                AIState.available_models,
                value=AIState.agent_tuning["aifred"].model,
                on_change=AIState.set_aifred_model,
                size="2",
                position="popper",  # Better mobile positioning (adapts to viewport)
                disabled=AIState.backend_switching,  # Disable during backend switch
            ),
        ),
        spacing="3",
        align="center",
    )


def _aifred_toggles_row() -> rx.Component:
    # AIfred RoPE + Personality + Reasoning (Ollama: all in one row, others: toggles only)
    return rx.cond(
        AIState.backend_id == "ollama",
        # Ollama: RoPE + Personality + Reasoning in one row
        rx.hstack(
            rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
            _rope_select(AIState.rope_factor_display, AIState.set_aifred_rope_factor),
            _agent_toggle("\U0001f3a9", AIState.agent_tuning["aifred"].personality, AIState.toggle_agent_personality_for("aifred"), t("personality_aifred_tooltip")),  # type: ignore[arg-type]
            _agent_toggle("\U0001f4ad", AIState.agent_tuning["aifred"].reasoning, AIState.toggle_agent_reasoning_for("aifred"), t("reasoning_tooltip")),  # type: ignore[arg-type]
            _agent_thinking_select(AIState.aifred_thinking_mode, AIState.aifred_thinking_options, AIState.set_agent_thinking_mode_for("aifred"), t("thinking_tooltip")),  # type: ignore[arg-type]
            _lightbulb_icon(),
            spacing="2",
            align="center",
        ),
        # Other backends: Personality + Reasoning + Thinking + Info + optional Speed toggle
        rx.hstack(
            _agent_toggle("\U0001f3a9", AIState.agent_tuning["aifred"].personality, AIState.toggle_agent_personality_for("aifred"), t("personality_aifred_tooltip")),  # type: ignore[arg-type]
            _agent_toggle("\U0001f4ad", AIState.agent_tuning["aifred"].reasoning, AIState.toggle_agent_reasoning_for("aifred"), t("reasoning_tooltip")),  # type: ignore[arg-type]
            _agent_thinking_select(AIState.aifred_thinking_mode, AIState.aifred_thinking_options, AIState.set_agent_thinking_mode_for("aifred"), t("thinking_tooltip")),  # type: ignore[arg-type]
            _lightbulb_icon(),
            _speed_toggle(AIState.agent_tuning["aifred"].has_speed_variant, AIState.agent_tuning["aifred"].speed_mode, AIState.toggle_agent_speed_mode_for("aifred")),  # type: ignore[arg-type]
            spacing="2",
            align="center",
        ),
    )


def _secondary_agent_row(row) -> rx.Component:
    """Model-Select + Toggle-Zeile für einen Sekundär-Agenten (foreach-Row).

    Deckt Sokrates, Salomo und alle Custom-Agenten ab — Sichtbarkeit
    entscheidet serverseitig ``AIState.agent_model_rows``.
    """
    model_select = rx.cond(
        AIState.is_mobile,
        # MOBILE: Native HTML <select> (simple list)
        native_select_model(
            row.select_value,
            lambda v: AIState.set_agent_model_for(row.id, v),
            AIState.backend_switching,
            AIState.secondary_available_models,
        ),
        # DESKTOP: Radix UI Select with "(wie AIfred-LLM)" as first option
        rx.select(
            AIState.secondary_available_models,
            value=row.select_value,
            on_change=lambda v: AIState.set_agent_model_for(row.id, v),
            size="2",
            position="popper",
            disabled=AIState.backend_switching,
        ),
    )
    toggles = [
        _agent_toggle(row.emoji, row.personality,
                      lambda v: AIState.toggle_agent_personality_for(row.id),
                      row.personality_tooltip, emoji_is_var=True),
        _agent_toggle("\U0001f4ad", row.reasoning,
                      lambda v: AIState.toggle_agent_reasoning_for(row.id),
                      t("reasoning_tooltip")),
        _agent_thinking_select(row.thinking_mode, row.thinking_options,
                               lambda v: AIState.set_agent_thinking_mode_for(row.id, v),
                               t("thinking_tooltip")),
        _lightbulb_icon(),
    ]
    return rx.vstack(
        rx.hstack(
            rx.text(row.label, font_weight="bold", font_size="12px"),
            model_select,
            spacing="3",
            align="center",
        ),
        rx.cond(
            AIState.backend_id == "ollama",
            # Ollama: RoPE + Toggles in one row
            rx.hstack(
                rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
                _rope_select(row.rope_display,
                             lambda v: AIState.set_agent_rope_factor_for(row.id, v)),
                *toggles,
                spacing="2",
                align="center",
            ),
            # Other backends: Toggles + optional Speed toggle
            rx.hstack(
                *toggles,
                _speed_toggle(row.has_speed_variant, row.speed_mode,
                              lambda v: AIState.toggle_agent_speed_mode_for(row.id),
                              disabled_var=row.model_empty),
                spacing="2",
                align="center",
            ),
        ),
        spacing="2",
        width="100%",
    )


def _secondary_agent_rows() -> rx.Component:
    """Alle Sekundär-Agenten-Zeilen (Sokrates/Salomo/Custom) via foreach."""
    return rx.foreach(AIState.agent_model_rows, _secondary_agent_row)


def _automatik_model_row() -> rx.Component:
    # Automatik LLM Selection - Hidden for backends without dynamic model support
    return rx.cond(
        AIState.backend_supports_dynamic_models,
        rx.hstack(
            rx.text(
                t("automatic_llm"),
                font_weight="bold",
                font_size="12px",
            ),
            rx.cond(
                AIState.is_mobile,
                # MOBILE: Native HTML <select> (simple list)
                native_select_model(
                    AIState.automatik_model_select_value,
                    AIState.set_automatik_model,
                    AIState.backend_switching,
                    AIState.automatik_available_models,
                ),
                # DESKTOP: Radix UI Select with "(wie AIfred-LLM)" as first option
                rx.select(
                    AIState.automatik_available_models,
                    value=AIState.automatik_model_select_value,
                    on_change=AIState.set_automatik_model,
                    size="2",
                    position="popper",
                    disabled=AIState.backend_switching,
                ),
            ),
            spacing="3",
            align="center",
        ),
    )


def _automatik_rope_row() -> rx.Component:
    # Automatik RoPE Scaling - Only visible for Ollama
    return rx.cond(
        (AIState.backend_id == "ollama") & AIState.backend_supports_dynamic_models,
        rx.hstack(
            rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
            _rope_select(AIState.automatik_rope_display, AIState.set_automatik_rope_factor),
            spacing="2",
            align="center",
        ),
    )


def _vision_model_row() -> rx.Component:
    # Vision LLM Selection - Hidden for backends without dynamic model support
    return rx.cond(
        AIState.backend_supports_dynamic_models,
        rx.hstack(
            rx.text(
                t("vision_llm"),
                font_weight="bold",
                font_size="12px",
            ),
            rx.cond(
                AIState.is_mobile,
                # MOBILE: Native HTML <select> mit Swap-Badge im
                # Options-Text (native <select> kann nicht färben —
                # das ⚡/🔄-Emoji trägt die Info; value bleibt der
                # reine Name).
                native_select_model(
                    AIState.agent_tuning["vision"].model,
                    AIState.set_vision_model,
                    AIState.backend_switching,
                    rich_list=AIState.available_vision_models_rich,
                ),
                # DESKTOP: Radix UI Select mit Swap-Badge pro Zeile.
                # Custom-Items (value = reiner Name, Anzeige =
                # Name + gefärbtes ⚡ No Swap / 🔄 Swap-Badge), damit
                # der Auswahl-Wert sauber bleibt und trotzdem sichtbar
                # ist, ob eine Bildanfrage das Chat-Modell swappt.
                rx.select.root(
                    rx.select.trigger(
                        placeholder="Select vision model...",
                        disabled=AIState.backend_switching,
                    ),
                    rx.select.content(
                        rx.foreach(
                            AIState.available_vision_models_rich,
                            lambda row: rx.select.item(
                                rx.hstack(
                                    rx.text(row["name"]),
                                    rx.cond(
                                        row["badge"] != "",
                                        rx.text(
                                            row["badge"],
                                            color=row["color"],
                                            font_size="11px",
                                            weight="medium",
                                        ),
                                    ),
                                    spacing="2",
                                    align="center",
                                ),
                                value=row["name"],
                            ),
                        ),
                    ),
                    value=AIState.agent_tuning["vision"].model,
                    on_change=AIState.set_vision_model,
                    size="2",
                    position="popper",
                    disabled=AIState.backend_switching,
                ),
            ),
            spacing="3",
            align="center",
        ),
    )


def _vision_rope_row() -> rx.Component:
    # Vision RoPE Scaling - Only visible for Ollama
    return rx.cond(
        (AIState.backend_id == "ollama") & AIState.backend_supports_dynamic_models,
        rx.hstack(
            rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
            _rope_select(AIState.vision_rope_display, AIState.set_agent_rope_factor_for("vision")),  # type: ignore[arg-type]
            spacing="2",
            align="center",
        ),
    )


def _vision_toggles_row() -> rx.Component:
    # Vision Personality + Reasoning + Thinking + optional Speed toggles
    return rx.cond(
        AIState.backend_supports_dynamic_models,
        rx.hstack(
            _agent_toggle("\U0001f4f7", AIState.agent_tuning["vision"].personality, AIState.toggle_agent_personality_for("vision"), t("personality_vision_tooltip")),  # type: ignore[arg-type]
            _agent_toggle("\U0001f4ad", AIState.agent_tuning["vision"].reasoning, AIState.toggle_agent_reasoning_for("vision"), t("reasoning_tooltip")),  # type: ignore[arg-type]
            _agent_thinking_select(AIState.vision_thinking_mode, AIState.vision_thinking_options, AIState.set_agent_thinking_mode_for("vision"), t("thinking_tooltip")),  # type: ignore[arg-type]
            _speed_toggle(AIState.agent_tuning["vision"].has_speed_variant, AIState.agent_tuning["vision"].speed_mode, AIState.toggle_agent_speed_mode_for("vision")),  # type: ignore[arg-type]
            spacing="2",
            align="center",
        ),
    )
