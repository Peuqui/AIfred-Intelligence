"""Settings: Sprache/User, Backend-Wahl, Kalibrierung, Cloud-Provider, YaRN."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import t, native_select_backend
from .calibration_picker import _calibration_picker_button, vllm_calibration_button


def _language_user_row() -> rx.Component:
    # UI Language + User Name Row
    return rx.hstack(
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
    )


def _backend_row() -> rx.Component:
    # Backend Selection - Mobile: Native select, Desktop: Radix UI
    return rx.hstack(
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
                                        "vLLM ben\u00f6tigt Compute 7.0+",
                                        "vLLM requires Compute 7.0+"
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

        # Model lifecycle help (when does what load — base/VLM/TTS/LLM)
        rx.tooltip(
            rx.icon(
                "lightbulb",
                size=14,
                color="#FFD700",
                cursor="pointer",
                on_click=AIState.open_model_lifecycle_help,
            ),
            content=t("model_lifecycle_help_tooltip"),
        ),

        spacing="3",
        align="center",
    )


def _calibration_row() -> rx.Component:
    # Context Calibration Row (Ollama + llama.cpp + vLLM)
    return rx.cond(
        (AIState.backend_id == "ollama")
        | (AIState.backend_id == "llamacpp")
        | (AIState.backend_id == "vllm"),
        rx.hstack(
            rx.cond(
                AIState.backend_id == "llamacpp",
                # llama.cpp: button opens a picker so the user
                # can deselect TTS variants for big models.
                _calibration_picker_button(),
                rx.cond(
                # vLLM: eigener Popover — Bestaetigungsstufe vor einem
                # stundenlangen Lauf (llama.cpp-Muskelgedaechtnis erwartet
                # einen Dialog) plus Kalibrier-Status und Side-Channel-Info.
                # Keine VLM x TTS-Matrix: side_channel_uuids() haelt diese
                # Karten aus der Leiter, jede Zelle maesse dasselbe.
                AIState.backend_id == "vllm",
                vllm_calibration_button(),
                # Ollama: kein Varianten-Picker → Direktklick.
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
                    rx.cond(
                        AIState.is_calibrating,
                        rx.button(
                            rx.icon("circle-stop", size=14),
                            on_click=AIState.cancel_calibration,
                            size="1",
                            variant="soft",
                            color_scheme="red",
                            title=t("calibration_cancel"),
                        ),
                    ),
                    spacing="1",
                    align="center",
                ),
                ),
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
    )


def _cloud_provider_row() -> rx.Component:
    # Cloud API Provider Selection (only visible for cloud_api backend)
    return rx.cond(
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
    )


def _yarn_section() -> rx.Component:
    # vLLM YaRN Context Extension (nur sichtbar bei vLLM)
    return rx.cond(
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
    )
