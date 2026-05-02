"""Settings accordion UI component for AIfred Intelligence.

Extracted from aifred.py - contains the settings panel with all configuration
options (backend, models, TTS/STT, sampling parameters, etc.).
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from ..theme import COLORS
from .helpers import agent_emoji
from .helpers import (
    t,
    native_select_backend,
    native_select_model,
    native_select_tts,
    native_select_stt,
)



# ============================================================
# SAMPLING PARAMETER HELPERS
# ============================================================

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


# ============================================================
# CONTEXT CONTROL HELPERS
# ============================================================

def _ctx_column(
    emoji: str, label: str,
    enabled_var, toggle_handler,
    value_var, set_handler,
    placeholder: str = "16384",
    **extra_style,
) -> rx.Component:
    """Helper: One agent context column with toggle + input."""
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
            disabled=~enabled_var,  # type: ignore[arg-type]
            opacity=rx.cond(enabled_var, "1.0", "0.5"),
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


# ============================================================
# SAMPLING CONTROL SECTION
# ============================================================

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


# ============================================================
# LLM PARAMETERS ACCORDION
# ============================================================

def llm_parameters_accordion() -> rx.Component:
    """LLM Parameters as popover — floats over content, no layout shift."""
    return rx.popover.root(
        rx.popover.trigger(
            rx.button(
                rx.cond(
                    AIState.ui_language == "de",
                    "\u2699\ufe0f LLM-Parameter (Erweitert)",
                    "\u2699\ufe0f LLM Parameters (Advanced)"
                ),
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
                        rx.cond(
                            AIState.ui_language == "de",
                            "\U0001f4e6 Context Window",
                            "\U0001f4e6 Context Window"
                        ),
                        font_weight="bold",
                        font_size="12px"
                    ),

                    # Per-LLM Context Control - Four columns with toggle + input
                    rx.hstack(
                        _ctx_column(
                            "\U0001f3a9", "AIfred",
                            AIState.num_ctx_manual_aifred_enabled,
                            AIState.toggle_num_ctx_manual_aifred,
                            AIState.num_ctx_manual_aifred,
                            AIState.set_num_ctx_manual_aifred,
                        ),
                        _ctx_column(
                            "\U0001f3db\ufe0f", "Sokrates",
                            AIState.num_ctx_manual_sokrates_enabled,
                            AIState.toggle_num_ctx_manual_sokrates,
                            AIState.num_ctx_manual_sokrates,
                            AIState.set_num_ctx_manual_sokrates,
                        ),
                        _ctx_column(
                            "\U0001f451", "Salomo",
                            AIState.num_ctx_manual_salomo_enabled,
                            AIState.toggle_num_ctx_manual_salomo,
                            AIState.num_ctx_manual_salomo,
                            AIState.set_num_ctx_manual_salomo,
                        ),
                        # Vision num_ctx (PERSISTENT - saved to settings.json)
                        _ctx_column(
                            "\U0001f441\ufe0f", "Vision",
                            AIState.vision_num_ctx_enabled,
                            AIState.toggle_vision_num_ctx,
                            AIState.vision_num_ctx,  # type: ignore[arg-type]
                            AIState.set_vision_num_ctx,
                            placeholder="32768",
                            margin_left="8px",
                        ),
                        spacing="3",
                    ),

                    # Show Calculation Button (styled like "Text senden" button)
                    rx.button(
                        rx.cond(
                            AIState.ui_language == "de",
                            "\U0001f4ca Berechnung anzeigen",
                            "\U0001f4ca Show Calculation"
                        ),
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

                    # Info Text (Chat context resets, Vision is saved)
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "AIfred/Sokrates/Salomo: Neustart setzt zur\u00fcck | Vision: wird gespeichert",
                            "AIfred/Sokrates/Salomo: resets on restart | Vision: saved"
                        ),
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


# ============================================================
# MAIN SETTINGS ACCORDION
# ============================================================

def settings_accordion() -> rx.Component:
    """Settings accordion at bottom - Kompakt"""
    return rx.accordion.root(
        rx.accordion.item(
            value="settings",  # Eindeutige ID f\u00fcr das Accordion Item
            header=rx.box(
                rx.text(t("settings"), font_size="12px", font_weight="500", color=COLORS["text_primary"]),
                padding_y="2",  # Kompakter Header
            ),
            content=rx.vstack(
                # UI Language + User Name Row
                rx.hstack(
                    # UI Language Selection
                    rx.hstack(
                        rx.text(t("ui_language"), font_weight="bold", font_size="12px"),
                        rx.cond(
                            AIState.is_mobile,
                            # MOBILE: Native HTML <select> (static options, no rx.foreach)
                            rx.el.select(
                                rx.el.option("de", value="de"),
                                rx.el.option("en", value="en"),
                                value=AIState.ui_language,
                                on_change=AIState.set_ui_language,
                                style={
                                    "padding": "8px 12px",
                                    "font_size": "12px",
                                    "color": COLORS["text_primary"],
                                    "background": COLORS["input_bg"],
                                    "border": f"1px solid {COLORS['border']}",
                                    "border_radius": "6px",
                                    "min_height": "48px",
                                    "cursor": "pointer",
                                },
                            ),
                            # DESKTOP: Radix UI Select
                            rx.select(
                                ["de", "en"],
                                value=AIState.ui_language,
                                on_change=AIState.set_ui_language,
                                size="2",
                            ),
                        ),
                        spacing="2",
                        align="center",
                    ),
                    # User Name Input + Gender Toggle (Subtle Orange style)
                    rx.box(
                        rx.icon("user", size=16, color="#B8860B"),
                        rx.input(
                            placeholder=t("your_name"),
                            value=AIState.user_name,
                            on_change=AIState.set_user_name,
                            on_blur=AIState.save_user_name,
                            size="2",
                            width="140px",
                            class_name="username-input-subtle",
                        ),
                        # Gender Toggle (\u2642/\u2640)
                        rx.segmented_control.root(
                            rx.segmented_control.item("\u2642", value="male"),
                            rx.segmented_control.item("\u2640", value="female"),
                            value=AIState.user_gender,
                            on_change=AIState.set_user_gender,
                            size="1",
                        ),
                        display="flex",
                        align_items="center",
                        gap="6px",
                        background_color="rgba(204, 136, 0, 0.15)",
                        border_radius="8px",
                        padding_left="8px",
                        padding_right="4px",
                        padding_y="4px",
                    ),
                    spacing="4",
                    align="center",
                    width="100%",
                ),

                # Backend Selection - Mobile: Native select, Desktop: Radix UI
                rx.hstack(
                    rx.text(t("backend"), font_weight="bold", font_size="12px"),
                    # Conditional rendering: Native select for mobile, Radix UI for desktop
                    rx.cond(
                        AIState.is_mobile,
                        # MOBILE: Native HTML <select>
                        native_select_backend(
                            AIState.current_backend_label,
                            AIState.switch_backend_by_label,
                            AIState.backend_switching,
                            AIState.available_backends_list,
                        ),
                        # DESKTOP: Radix UI Select with grouped headers
                        rx.select.root(
                            rx.select.trigger(),
                            rx.select.content(
                                rx.cond(
                                    AIState.available_backends.contains("llamacpp"),
                                    rx.select.item("llama.cpp", value="llamacpp"),
                                ),
                                rx.cond(
                                    AIState.available_backends.contains("ollama"),
                                    rx.select.item("Ollama", value="ollama"),
                                ),
                                rx.cond(
                                    AIState.available_backends.contains("vllm"),
                                    rx.select.item("vLLM", value="vllm"),
                                ),
                                rx.cond(
                                    AIState.available_backends.contains("tabbyapi"),
                                    rx.select.item("TabbyAPI", value="tabbyapi"),
                                ),
                                rx.select.item("Cloud APIs", value="cloud_api"),
                            ),
                            value=AIState.backend_type,
                            on_change=AIState.switch_backend,
                            size="2",
                            disabled=AIState.backend_switching,
                        ),
                    ),

                    # Backend Switching Status Badge
                    rx.cond(
                        AIState.backend_switching,
                        rx.hstack(
                            rx.spinner(size="1", color="orange"),
                            rx.badge(
                                rx.cond(
                                    AIState.ui_language == "de",
                                    "Wechsle...",
                                    "Switching...",
                                ),
                                color_scheme="orange"
                            ),
                            spacing="2",
                            align="center",
                        ),
                    ),

                    # GPU Details Collapsible (next to Backend dropdown)
                    rx.cond(
                        AIState.gpu_detected,
                        rx.accordion.root(
                            rx.accordion.item(
                                value="gpu-details",
                                header=rx.box(
                                    rx.text(
                                        rx.cond(
                                            AIState.ui_language == "de",
                                            "\U0001f5a5\ufe0f GPU-Details",
                                            "\U0001f5a5\ufe0f GPU Details"
                                        ),
                                        font_size="11px",
                                        font_weight="500",
                                        color="#2a9d8f",
                                    ),
                                    padding_y="2",
                                ),
                                content=rx.vstack(
                                    # GPU Hardware Info
                                    rx.text(
                                        f"\U0001f3ae {AIState.gpu_display_text}",
                                        font_size="10px",
                                        color="#2a9d8f",
                                    ),
                                    # Backend Compatibility (only if Compute < 7.0)
                                    rx.cond(
                                        AIState.gpu_compute_cap < 7.0,
                                        rx.box(
                                            rx.text(
                                                rx.cond(
                                                    AIState.ui_language == "de",
                                                    "vLLM & TabbyAPI ben\u00f6tigen Compute 7.0+",
                                                    "vLLM & TabbyAPI require Compute 7.0+"
                                                ),
                                                font_size="10px",
                                                color="#aaa",
                                            ),
                                            rx.text(
                                                rx.cond(
                                                    AIState.ui_language == "de",
                                                    "Verf\u00fcgbar: " + AIState.gpu_compatible_text,
                                                    "Available: " + AIState.gpu_compatible_text,
                                                ),
                                                font_size="10px",
                                                color="#aaa",
                                                margin_top="2px",
                                            ),
                                            rx.text(
                                                rx.cond(
                                                    AIState.ui_language == "de",
                                                    "\U0001f4a1 Ollama & llama.cpp nutzen GGUF (Q4-Q8) - optimal f\u00fcr \u00e4ltere GPUs",
                                                    "\U0001f4a1 Ollama & llama.cpp use GGUF (Q4-Q8) - optimal for older GPUs"
                                                ),
                                                font_size="10px",
                                                color="#2a9d8f",
                                                margin_top="4px",
                                                font_style="italic",
                                            ),
                                            margin_top="4px",
                                        ),
                                    ),
                                    spacing="1",
                                    width="100%",
                                    align_items="start",
                                ),
                            ),
                            collapsible=True,
                            color_scheme="gray",
                            variant="ghost",
                        ),
                    ),

                    spacing="3",
                    align="center",
                ),

                # Single Model Warning (for backends that can't switch models)
                rx.cond(
                    ~AIState.backend_supports_dynamic_models,
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            f"\u2139\ufe0f {AIState.backend_type.upper()} kann nur EIN Modell gleichzeitig laden (Haupt- und Automatik-LLM nutzen dasselbe Modell)",
                            f"\u2139\ufe0f {AIState.backend_type.upper()} can only load ONE model at a time (Main and Automatik LLM use the same model)"
                        ),
                        font_size="11px",
                        color="#d4913d",  # Dunkles Orange - gut lesbar
                        line_height="1.5",
                        margin_top="8px",
                    ),
                ),

                # Context Calibration Row (Ollama + llama.cpp)
                rx.cond(
                    (AIState.backend_id == "ollama") | (AIState.backend_id == "llamacpp"),
                    rx.hstack(
                        rx.button(
                            rx.cond(
                                AIState.is_calibrating,
                                rx.hstack(
                                    rx.spinner(size="1"),
                                    rx.text(t("calibrating"), font_size="11px"),
                                    spacing="2",
                                    align="center",
                                ),
                                rx.hstack(
                                    rx.icon("gauge", size=14),
                                    rx.text(t("calibrate_context"), font_size="11px"),
                                    spacing="2",
                                    align="center",
                                ),
                            ),
                            on_click=AIState.calibrate_context,
                            disabled=AIState.is_calibrating | AIState.backend_switching,
                            size="1",
                            variant="outline",
                            color_scheme="orange",
                        ),
                        # Calibration-Mode dropdown — only for llama.cpp.
                        # The specific Cloud model used for AI mode is
                        # configured in the Agent Editor under the
                        # "Calibration" system agent.
                        rx.cond(
                            AIState.backend_id == "llamacpp",
                            rx.select.root(
                                rx.select.trigger(
                                    placeholder=t("calibration_mode_legacy"),
                                    variant="surface",
                                ),
                                rx.select.content(
                                    rx.select.item(t("calibration_mode_legacy"), value="legacy"),
                                    rx.select.item(
                                        AIState.calibration_ai_label,
                                        value="ai",
                                        disabled=~AIState.has_dashscope_key,
                                    ),
                                ),
                                value=AIState.calibration_mode,
                                on_change=AIState.set_calibration_mode,
                                size="1",
                                color_scheme="orange",
                                disabled=AIState.is_calibrating,
                            ),
                        ),
                        # Hybrid-Mode toggle — only for llama.cpp. When off,
                        # calibration fails fast for models that exceed GPU
                        # VRAM instead of falling back to slow CPU-offload.
                        # The "Hybrid" label colours up when active so the
                        # state is obvious from across the room. Light-bulb
                        # popover works on both desktop hover and mobile tap.
                        rx.cond(
                            AIState.backend_id == "llamacpp",
                            rx.hstack(
                                rx.switch(
                                    checked=AIState.calibration_allow_hybrid,
                                    on_change=AIState.toggle_calibration_allow_hybrid,
                                    size="1",
                                    color_scheme="orange",
                                    disabled=AIState.is_calibrating,
                                ),
                                rx.text(
                                    t("calibration_hybrid_label"),
                                    font_size="11px",
                                    color=rx.cond(
                                        AIState.calibration_allow_hybrid, "#FFA85C", "#888",
                                    ),
                                ),
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
                                            content=t("calibration_hybrid_tooltip"),
                                        ),
                                    ),
                                    rx.popover.content(
                                        rx.text(
                                            t("calibration_hybrid_tooltip"),
                                            font_size="11px",
                                            color="#ddd",
                                            line_height="1.5",
                                        ),
                                        max_width="320px",
                                        padding="10px",
                                    ),
                                ),
                                spacing="2",
                                align="center",
                            ),
                        ),
                        spacing="2",
                        align="center",
                    ),
                ),

                # Cloud API Provider Selection (only visible for cloud_api backend)
                rx.cond(
                    AIState.backend_type == "cloud_api",
                    rx.hstack(
                        rx.text(t("cloud_api_provider"), font_weight="bold", font_size="12px"),
                        rx.select(
                            ["Claude (Anthropic)", "Qwen (DashScope)", "DeepSeek", "Kimi (Moonshot)"],
                            value=AIState.cloud_api_provider_label,
                            on_change=AIState.set_cloud_api_provider_by_label,
                            size="2",
                        ),
                        # API Key Status Badge
                        rx.cond(
                            AIState.cloud_api_key_configured,
                            rx.badge(t("cloud_api_key_configured"), color_scheme="green", size="1"),
                            rx.badge(t("cloud_api_key_missing"), color_scheme="red", size="1"),
                        ),
                        spacing="3",
                        align="center",
                        width="100%",
                    ),
                ),

                # AIfred-LLM Selection
                rx.hstack(
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
                ),

                # AIfred RoPE + Personality + Reasoning (Ollama: all in one row, others: toggles only)
                rx.cond(
                    AIState.backend_id == "ollama",
                    # Ollama: RoPE + Personality + Reasoning in one row
                    rx.hstack(
                        rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
                        _rope_select(AIState.rope_factor_display, AIState.set_aifred_rope_factor),
                        _agent_toggle("\U0001f3a9", AIState.aifred_personality, AIState.toggle_aifred_personality, t("personality_aifred_tooltip")),
                        _agent_toggle("\U0001f4ad", AIState.aifred_reasoning, AIState.toggle_aifred_reasoning, t("reasoning_tooltip")),
                        _agent_toggle("\U0001f9e0", AIState.aifred_thinking, AIState.toggle_aifred_thinking, t("thinking_tooltip"), color_scheme="blue"),
                        _lightbulb_icon(),
                        spacing="2",
                        align="center",
                    ),
                    # Other backends: Personality + Reasoning + Thinking + Info + optional Speed toggle
                    rx.hstack(
                        _agent_toggle("\U0001f3a9", AIState.aifred_personality, AIState.toggle_aifred_personality, t("personality_aifred_tooltip")),
                        _agent_toggle("\U0001f4ad", AIState.aifred_reasoning, AIState.toggle_aifred_reasoning, t("reasoning_tooltip")),
                        _agent_toggle("\U0001f9e0", AIState.aifred_thinking, AIState.toggle_aifred_thinking, t("thinking_tooltip"), color_scheme="blue"),
                        _lightbulb_icon(),
                        _speed_toggle(AIState.aifred_has_speed_variant, AIState.aifred_speed_mode, AIState.toggle_aifred_speed_mode),
                        spacing="2",
                        align="center",
                    ),
                ),

                # Sokrates LLM Selection - Only visible when multi-agent mode is not "standard"
                rx.cond(
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
                ),

                # Sokrates RoPE + Personality + Reasoning (multi-agent only)
                rx.cond(
                    AIState.multi_agent_mode != "standard",
                    rx.cond(
                        AIState.backend_id == "ollama",
                        # Ollama: RoPE + Personality + Reasoning in one row
                        rx.hstack(
                            rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
                            _rope_select(AIState.sokrates_rope_display, AIState.set_sokrates_rope_factor),
                            _agent_toggle("\U0001f3db\ufe0f", AIState.sokrates_personality, AIState.toggle_sokrates_personality, t("personality_sokrates_tooltip")),
                            _agent_toggle("\U0001f4ad", AIState.sokrates_reasoning, AIState.toggle_sokrates_reasoning, t("reasoning_tooltip")),
                            _agent_toggle("\U0001f9e0", AIState.sokrates_thinking, AIState.toggle_sokrates_thinking, t("thinking_tooltip"), color_scheme="blue"),
                            _lightbulb_icon(),
                            spacing="2",
                            align="center",
                        ),
                        # Other backends: Personality + Reasoning + Thinking + Info + optional Speed toggle
                        rx.hstack(
                            _agent_toggle("\U0001f3db\ufe0f", AIState.sokrates_personality, AIState.toggle_sokrates_personality, t("personality_sokrates_tooltip")),
                            _agent_toggle("\U0001f4ad", AIState.sokrates_reasoning, AIState.toggle_sokrates_reasoning, t("reasoning_tooltip")),
                            _agent_toggle("\U0001f9e0", AIState.sokrates_thinking, AIState.toggle_sokrates_thinking, t("thinking_tooltip"), color_scheme="blue"),
                            _lightbulb_icon(),
                            _speed_toggle(AIState.sokrates_has_speed_variant, AIState.sokrates_speed_mode, AIState.toggle_sokrates_speed_mode, disabled_var=AIState.sokrates_model == ""),
                            spacing="2",
                            align="center",
                        ),
                    ),
                ),

                # Salomo LLM Selection - Only visible for auto_consensus or tribunal modes
                rx.cond(
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
                ),

                # Salomo RoPE + Personality + Reasoning (consensus/tribunal only)
                rx.cond(
                    (AIState.multi_agent_mode == "auto_consensus") | (AIState.multi_agent_mode == "tribunal"),
                    rx.cond(
                        AIState.backend_id == "ollama",
                        # Ollama: RoPE + Personality + Reasoning in one row
                        rx.hstack(
                            rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
                            _rope_select(AIState.salomo_rope_display, AIState.set_salomo_rope_factor),
                            _agent_toggle("\U0001f451", AIState.salomo_personality, AIState.toggle_salomo_personality, t("personality_salomo_tooltip")),
                            _agent_toggle("\U0001f4ad", AIState.salomo_reasoning, AIState.toggle_salomo_reasoning, t("reasoning_tooltip")),
                            _agent_toggle("\U0001f9e0", AIState.salomo_thinking, AIState.toggle_salomo_thinking, t("thinking_tooltip"), color_scheme="blue"),
                            _lightbulb_icon(),
                            spacing="2",
                            align="center",
                        ),
                        # Other backends: Personality + Reasoning + Thinking + Info
                        rx.hstack(
                            _agent_toggle("\U0001f451", AIState.salomo_personality, AIState.toggle_salomo_personality, t("personality_salomo_tooltip")),
                            _agent_toggle("\U0001f4ad", AIState.salomo_reasoning, AIState.toggle_salomo_reasoning, t("reasoning_tooltip")),
                            _agent_toggle("\U0001f9e0", AIState.salomo_thinking, AIState.toggle_salomo_thinking, t("thinking_tooltip"), color_scheme="blue"),
                            _lightbulb_icon(),
                            _speed_toggle(AIState.salomo_has_speed_variant, AIState.salomo_speed_mode, AIState.toggle_salomo_speed_mode, disabled_var=AIState.salomo_model == ""),
                            spacing="2",
                            align="center",
                        ),
                    ),
                ),

                # Automatik LLM Selection - Hidden for backends without dynamic model support
                rx.cond(
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
                ),

                # Automatik RoPE Scaling - Only visible for Ollama
                rx.cond(
                    (AIState.backend_id == "ollama") & AIState.backend_supports_dynamic_models,
                    rx.hstack(
                        rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
                        _rope_select(AIState.automatik_rope_display, AIState.set_automatik_rope_factor),
                        spacing="2",
                        align="center",
                    ),
                ),

                # Vision LLM Selection - Hidden for backends without dynamic model support
                rx.cond(
                    AIState.backend_supports_dynamic_models,
                    rx.hstack(
                        rx.text(
                            t("vision_llm"),
                            font_weight="bold",
                            font_size="12px",
                        ),
                        rx.cond(
                            AIState.is_mobile,
                            # MOBILE: Native HTML <select> (simple list)
                            native_select_model(
                                AIState.vision_model,  # Display name with size
                                AIState.set_vision_model,  # Original handler
                                AIState.backend_switching,
                                AIState.available_vision_models_list,  # Vision models list (state var, not computed)
                            ),
                            # DESKTOP: Radix UI Select
                            rx.select(
                                AIState.available_vision_models_list,  # State var instead of computed property
                                value=AIState.vision_model,
                                on_change=AIState.set_vision_model,
                                size="2",
                                position="popper",  # Better mobile positioning (adapts to viewport)
                                disabled=AIState.backend_switching,  # Disable during backend switch
                                placeholder="Select vision model..."
                            ),
                        ),
                        spacing="3",
                        align="center",
                    ),
                ),

                # Vision RoPE Scaling - Only visible for Ollama
                rx.cond(
                    (AIState.backend_id == "ollama") & AIState.backend_supports_dynamic_models,
                    rx.hstack(
                        rx.text("  \u2514\u2500 RoPE:", font_size="10px", color="gray"),
                        _rope_select(AIState.vision_rope_display, AIState.set_vision_rope_factor),
                        spacing="2",
                        align="center",
                    ),
                ),

                # Vision Personality + Reasoning + Thinking + optional Speed toggles
                rx.cond(
                    AIState.backend_supports_dynamic_models,
                    rx.hstack(
                        _agent_toggle("\U0001f4f7", AIState.vision_personality, AIState.toggle_vision_personality, t("personality_vision_tooltip")),
                        _agent_toggle("\U0001f4ad", AIState.vision_reasoning, AIState.toggle_vision_reasoning, t("reasoning_tooltip")),
                        _agent_toggle("\U0001f9e0", AIState.vision_thinking, AIState.toggle_vision_thinking, t("thinking_tooltip"), color_scheme="blue"),
                        _speed_toggle(AIState.vision_has_speed_variant, AIState.vision_speed_mode, AIState.toggle_vision_speed_mode),
                        spacing="2",
                        align="center",
                    ),
                ),

                # NOTE: Global "Thinking Mode" toggle removed in v2.23.0
                # Reasoning is now controlled per-agent via aifred_reasoning, sokrates_reasoning, salomo_reasoning
                # which control BOTH the reasoning prompt AND the enable_thinking flag

                # vLLM YaRN Context Extension (nur sichtbar bei vLLM)
                rx.cond(
                    AIState.backend_type == "vllm",
                    rx.vstack(
                        rx.divider(margin="0px 0px 12px 0px"),
                        rx.hstack(
                            rx.text(t("yarn_heading"), font_weight="bold", font_size="12px"),
                            rx.switch(
                                checked=AIState.enable_yarn,
                                on_change=AIState.toggle_yarn,
                                size="1",
                            ),
                            rx.text(
                                rx.cond(
                                    AIState.enable_yarn,
                                    f"ON ({AIState.yarn_factor}x)",
                                    "OFF"
                                ),
                                font_size="11px",
                                color=rx.cond(
                                    AIState.enable_yarn,
                                    "#4CAF50",
                                    "#999"
                                ),
                            ),
                            spacing="2",
                            align="center",
                        ),
                        rx.cond(
                            AIState.enable_yarn,
                            rx.vstack(
                                rx.hstack(
                                    rx.text(t("yarn_factor_label"), font_size="11px", font_weight="500"),
                                    rx.input(
                                        default_value=AIState.yarn_factor_input,
                                        on_blur=AIState.set_yarn_factor_input,
                                        type="number",
                                        step="0.1",
                                        min="1.0",
                                        max="8.0",  # No hard limit - let user experiment
                                        size="1",
                                        width="80px",
                                    ),
                                    rx.text(
                                        rx.cond(
                                            AIState.vllm_max_tokens > 0,
                                            f"(~{(AIState.vllm_max_tokens * AIState.yarn_factor).to(int)} tokens)",
                                            t("yarn_autodetect_hint")
                                        ),
                                        font_size="10px",
                                        color="#999",
                                    ),
                                    rx.button(
                                        t("yarn_apply_button"),
                                        on_click=AIState.apply_yarn_factor,
                                        size="1",
                                        variant="soft",
                                        color_scheme="blue",
                                    ),
                                    spacing="2",
                                    align="center",
                                ),
                                # Show maximum YaRN factor info (dynamic based on testing)
                                rx.cond(
                                    AIState.yarn_max_tested,
                                    # Maximum was tested (from VRAM crash)
                                    rx.text(
                                        "\U0001f4cf Maximum: ~" + AIState.yarn_max_factor.to(str) + "x",
                                        font_size="10px",
                                        color="#ff9800",  # Orange for better visibility
                                        font_weight="500",
                                        margin_top="2px",
                                    ),
                                    # Maximum unknown (not tested yet)
                                    rx.text(
                                        t("yarn_max_unknown"),
                                        font_size="10px",
                                        color="#999",  # Gray for unknown
                                        font_weight="400",
                                        margin_top="2px",
                                    ),
                                ),
                                spacing="2",
                            ),
                            rx.box(),
                        ),
                        rx.cond(
                            AIState.vllm_max_tokens > 0,
                            rx.text(
                                "\u2139\ufe0f " + (AIState.vllm_native_context / 1000).to(int).to(str) + "K nativ | HW: " + (AIState.vllm_max_tokens / 1000).to(int).to(str) + "K",
                                font_size="10px",
                                color="#999",
                                line_height="1.3",
                            ),
                            rx.text(
                                t("yarn_context_info"),
                                font_size="10px",
                                color="#999",
                                line_height="1.3",
                            ),
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    rx.box(),  # Empty box when not vLLM
                ),

                # TTS/STT Settings
                rx.divider(margin_top="12px", margin_bottom="12px"),

                # TTS (Text-to-Speech) Section
                rx.vstack(
                    # Row 1: Label + AutoPlay + Streaming toggles
                    rx.hstack(
                        rx.text(t("tts_heading"), font_weight="bold", font_size="12px"),
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
                                AIState.tts_engines,
                            ),
                            rx.select(
                                AIState.tts_engines,
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
                    # Agent voices are configured in the Agent Editor modal
                    spacing="2",
                    width="100%",
                ),

                # STT (Speech-to-Text) Section
                rx.divider(margin_top="12px", margin_bottom="12px"),
                rx.vstack(
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
                ),

                # Restart Buttons
                rx.divider(),
                rx.text(t("system_control"), font_weight="bold", font_size="12px"),
                rx.vstack(
                    # Row 1: Backend and AIfred restart buttons (side by side, each 50%)
                    rx.hstack(
                        rx.button(
                            rx.cond(
                                AIState.backend_type == "ollama",
                                t("restart_ollama"),
                                rx.cond(
                                    AIState.backend_type == "vllm",
                                    t("restart_vllm"),
                                    rx.text(f"\U0001f504 {AIState.backend_type.upper()} Neustart")
                                )
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
                ),
                # Neustart-Info Texte
                rx.vstack(
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
                ),

                spacing="4",
                width="100%",
            ),
        ),
        id="settings-accordion",  # ID for JavaScript height sync
        collapsible=True,  # WICHTIG: Macht Accordion schlie\u00dfbar!
        default_value="settings",  # Standardm\u00e4\u00dfig ge\u00f6ffnet
        color_scheme="gray",
        variant="soft",
    )
