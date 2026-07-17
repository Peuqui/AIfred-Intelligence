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
                AIState.aifred_model,  # Display name with size
                AIState.set_aifred_model,  # Original handler
                AIState.backend_switching,
                AIState.available_models,  # Simple list of display names
            ),
            # DESKTOP: Radix UI Select
            rx.select(
                AIState.available_models,
                value=AIState.aifred_model,
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
            _agent_toggle("\U0001f3a9", AIState.aifred_personality, AIState.toggle_aifred_personality, t("personality_aifred_tooltip")),
            _agent_toggle("\U0001f4ad", AIState.aifred_reasoning, AIState.toggle_aifred_reasoning, t("reasoning_tooltip")),
            _agent_thinking_select(AIState.aifred_thinking_mode, AIState.aifred_thinking_options, AIState.set_aifred_thinking_mode, t("thinking_tooltip")),
            _lightbulb_icon(),
            spacing="2",
            align="center",
        ),
        # Other backends: Personality + Reasoning + Thinking + Info + optional Speed toggle
        rx.hstack(
            _agent_toggle("\U0001f3a9", AIState.aifred_personality, AIState.toggle_aifred_personality, t("personality_aifred_tooltip")),
            _agent_toggle("\U0001f4ad", AIState.aifred_reasoning, AIState.toggle_aifred_reasoning, t("reasoning_tooltip")),
            _agent_thinking_select(AIState.aifred_thinking_mode, AIState.aifred_thinking_options, AIState.set_aifred_thinking_mode, t("thinking_tooltip")),
            _lightbulb_icon(),
            _speed_toggle(AIState.aifred_has_speed_variant, AIState.aifred_speed_mode, AIState.toggle_aifred_speed_mode),
            spacing="2",
            align="center",
        ),
    )


def _sokrates_model_row() -> rx.Component:
    # Sokrates LLM Selection - Only visible when multi-agent mode is not "standard"
    return rx.cond(
        AIState.multi_agent_mode != "standard",
        rx.hstack(
            rx.text(
                t("sokrates_llm"),
                font_weight="bold",
                font_size="12px",
            ),
            rx.cond(
                AIState.is_mobile,
                # MOBILE: Native HTML <select> (simple list)
                native_select_model(
                    AIState.sokrates_model_select_value,
                    AIState.set_sokrates_model,
                    AIState.backend_switching,
                    AIState.sokrates_available_models,
                ),
                # DESKTOP: Radix UI Select with "(wie AIfred-LLM)" as first option
                rx.select(
                    AIState.sokrates_available_models,
                    value=AIState.sokrates_model_select_value,
                    on_change=AIState.set_sokrates_model,
                    size="2",
                    position="popper",
                    disabled=AIState.backend_switching,
                ),
            ),
            spacing="3",
            align="center",
        ),
    )


def _sokrates_toggles_row() -> rx.Component:
    # Sokrates RoPE + Personality + Reasoning (multi-agent only)
    return rx.cond(
        AIState.multi_agent_mode != "standard",
        rx.cond(
            AIState.backend_id == "ollama",
            # Ollama: RoPE + Personality + Reasoning in one row
            rx.hstack(
                rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
                _rope_select(AIState.sokrates_rope_display, AIState.set_sokrates_rope_factor),
                _agent_toggle("\U0001f3db\ufe0f", AIState.sokrates_personality, AIState.toggle_sokrates_personality, t("personality_sokrates_tooltip")),
                _agent_toggle("\U0001f4ad", AIState.sokrates_reasoning, AIState.toggle_sokrates_reasoning, t("reasoning_tooltip")),
                _agent_thinking_select(AIState.sokrates_thinking_mode, AIState.sokrates_thinking_options, AIState.set_sokrates_thinking_mode, t("thinking_tooltip")),
                _lightbulb_icon(),
                spacing="2",
                align="center",
            ),
            # Other backends: Personality + Reasoning + Thinking + Info + optional Speed toggle
            rx.hstack(
                _agent_toggle("\U0001f3db\ufe0f", AIState.sokrates_personality, AIState.toggle_sokrates_personality, t("personality_sokrates_tooltip")),
                _agent_toggle("\U0001f4ad", AIState.sokrates_reasoning, AIState.toggle_sokrates_reasoning, t("reasoning_tooltip")),
                _agent_thinking_select(AIState.sokrates_thinking_mode, AIState.sokrates_thinking_options, AIState.set_sokrates_thinking_mode, t("thinking_tooltip")),
                _lightbulb_icon(),
                _speed_toggle(AIState.sokrates_has_speed_variant, AIState.sokrates_speed_mode, AIState.toggle_sokrates_speed_mode, disabled_var=AIState.sokrates_model == ""),
                spacing="2",
                align="center",
            ),
        ),
    )


def _salomo_model_row() -> rx.Component:
    # Salomo LLM Selection - Only visible for auto_consensus or tribunal modes
    return rx.cond(
        (AIState.multi_agent_mode == "auto_consensus") | (AIState.multi_agent_mode == "tribunal"),
        rx.hstack(
            rx.text(
                t("salomo_llm"),
                font_weight="bold",
                font_size="12px",
            ),
            rx.cond(
                AIState.is_mobile,
                # MOBILE: Native HTML <select> (simple list)
                native_select_model(
                    AIState.salomo_model_select_value,
                    AIState.set_salomo_model,
                    AIState.backend_switching,
                    AIState.salomo_available_models,
                ),
                # DESKTOP: Radix UI Select with "(wie AIfred-LLM)" as first option
                rx.select(
                    AIState.salomo_available_models,
                    value=AIState.salomo_model_select_value,
                    on_change=AIState.set_salomo_model,
                    size="2",
                    position="popper",
                    disabled=AIState.backend_switching,
                ),
            ),
            spacing="3",
            align="center",
        ),
    )


def _salomo_toggles_row() -> rx.Component:
    # Salomo RoPE + Personality + Reasoning (consensus/tribunal only)
    return rx.cond(
        (AIState.multi_agent_mode == "auto_consensus") | (AIState.multi_agent_mode == "tribunal"),
        rx.cond(
            AIState.backend_id == "ollama",
            # Ollama: RoPE + Personality + Reasoning in one row
            rx.hstack(
                rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
                _rope_select(AIState.salomo_rope_display, AIState.set_salomo_rope_factor),
                _agent_toggle("\U0001f451", AIState.salomo_personality, AIState.toggle_salomo_personality, t("personality_salomo_tooltip")),
                _agent_toggle("\U0001f4ad", AIState.salomo_reasoning, AIState.toggle_salomo_reasoning, t("reasoning_tooltip")),
                _agent_thinking_select(AIState.salomo_thinking_mode, AIState.salomo_thinking_options, AIState.set_salomo_thinking_mode, t("thinking_tooltip")),
                _lightbulb_icon(),
                spacing="2",
                align="center",
            ),
            # Other backends: Personality + Reasoning + Thinking + Info
            rx.hstack(
                _agent_toggle("\U0001f451", AIState.salomo_personality, AIState.toggle_salomo_personality, t("personality_salomo_tooltip")),
                _agent_toggle("\U0001f4ad", AIState.salomo_reasoning, AIState.toggle_salomo_reasoning, t("reasoning_tooltip")),
                _agent_thinking_select(AIState.salomo_thinking_mode, AIState.salomo_thinking_options, AIState.set_salomo_thinking_mode, t("thinking_tooltip")),
                _lightbulb_icon(),
                _speed_toggle(AIState.salomo_has_speed_variant, AIState.salomo_speed_mode, AIState.toggle_salomo_speed_mode, disabled_var=AIState.salomo_model == ""),
                spacing="2",
                align="center",
            ),
        ),
    )


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
                    AIState.vision_model,
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
                    value=AIState.vision_model,
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
            _rope_select(AIState.vision_rope_display, AIState.set_vision_rope_factor),
            spacing="2",
            align="center",
        ),
    )


def _vision_toggles_row() -> rx.Component:
    # Vision Personality + Reasoning + Thinking + optional Speed toggles
    return rx.cond(
        AIState.backend_supports_dynamic_models,
        rx.hstack(
            _agent_toggle("\U0001f4f7", AIState.vision_personality, AIState.toggle_vision_personality, t("personality_vision_tooltip")),
            _agent_toggle("\U0001f4ad", AIState.vision_reasoning, AIState.toggle_vision_reasoning, t("reasoning_tooltip")),
            _agent_thinking_select(AIState.vision_thinking_mode, AIState.vision_thinking_options, AIState.set_vision_thinking_mode, t("thinking_tooltip")),
            _speed_toggle(AIState.vision_has_speed_variant, AIState.vision_speed_mode, AIState.toggle_vision_speed_mode),
            spacing="2",
            align="center",
        ),
    )
