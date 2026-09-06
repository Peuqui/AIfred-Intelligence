"""Help-Modals: Multi-Agent, Reasoning/Thinking, Research, Model-Lifecycle."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t, agent_emoji, overlay_scaffold


def multi_agent_help_modal() -> rx.Component:
    """
    Fullscreen Overlay für Multi-Agent Modus-Übersicht.
    Zeigt alle Modi mit Ablauf und wer entscheidet.
    """
    return overlay_scaffold(
        # Modal Content - zentriert
        rx.vstack(
            # Header
            rx.hstack(
                rx.icon("lightbulb", size=24, color="#FFD700"),
                rx.text(t("multi_agent_help_title"), color="white", font_weight="bold", font_size="18px"),
                spacing="3",
                align="center",
            ),

            # Tabelle der Modi
            rx.box(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell(t("multi_agent_help_mode"), style={"color": "#FFD700", "font_weight": "bold"}),
                            rx.table.column_header_cell(t("multi_agent_help_flow"), style={"color": "#FFD700", "font_weight": "bold"}),
                            rx.table.column_header_cell(t("multi_agent_help_decision"), style={"color": "#FFD700", "font_weight": "bold"}),
                        ),
                    ),
                    rx.table.body(
                        # Standard
                        rx.table.row(
                            rx.table.cell(rx.cond(AIState.ui_language == "de", "Standard", "Standard")),
                            rx.table.cell(t("multi_agent_help_standard_flow")),
                            rx.table.cell(t("multi_agent_help_standard_decision")),
                        ),
                        # Kritische Prüfung / Critical Review
                        rx.table.row(
                            rx.table.cell(rx.cond(AIState.ui_language == "de", "Kritische Prüfung", "Critical Review")),
                            rx.table.cell(t("multi_agent_help_critical_review_flow")),
                            rx.table.cell(t("multi_agent_help_critical_review_decision")),
                        ),
                        # Auto-Konsens / Auto Consensus
                        rx.table.row(
                            rx.table.cell(rx.cond(AIState.ui_language == "de", "Auto-Konsens", "Auto Consensus")),
                            rx.table.cell(t("multi_agent_help_auto_consensus_flow")),
                            rx.table.cell(t("multi_agent_help_auto_consensus_decision")),
                        ),
                        # Tribunal
                        rx.table.row(
                            rx.table.cell("Tribunal"),
                            rx.table.cell(t("multi_agent_help_tribunal_flow")),
                            rx.table.cell(t("multi_agent_help_tribunal_decision")),
                        ),
                        # Symposion
                        rx.table.row(
                            rx.table.cell("Symposion"),
                            rx.table.cell(t("multi_agent_help_symposion_flow")),
                            rx.table.cell(t("multi_agent_help_symposion_decision")),
                        ),
                    ),
                    style={
                        "width": "100%",
                        "border_collapse": "collapse",
                        "& th, & td": {
                            "padding": "10px 15px",
                            "text_align": "left",
                            "border_bottom": "1px solid #444",
                        },
                    },
                ),
                width="100%",
                overflow_x="auto",
            ),

            # Agenten-Beschreibungen
            rx.divider(color="#444", margin_y="15px"),
            rx.text(t("multi_agent_help_agents_title"), color="#FFD700", font_weight="bold", font_size="14px"),
            rx.vstack(
                rx.hstack(
                    rx.hstack(agent_emoji("\U0001f3a9", size="18px"), rx.text("AIfred:", font_weight="bold"), spacing="1", align="center", color="white", min_width="120px"),
                    rx.text(t("multi_agent_help_aifred_desc"), color="#ccc"),
                    spacing="2",
                    align="start",
                ),
                rx.hstack(
                    rx.text("🏛️ Sokrates:", color="white", font_weight="bold", min_width="120px"),
                    rx.text(t("multi_agent_help_sokrates_desc"), color="#ccc"),
                    spacing="2",
                    align="start",
                ),
                rx.hstack(
                    rx.text("👑 Salomo:", color="white", font_weight="bold", min_width="120px"),
                    rx.text(t("multi_agent_help_salomo_desc"), color="#ccc"),
                    spacing="2",
                    align="start",
                ),
                spacing="2",
                width="100%",
                align_items="start",
            ),

            # Schließen-Button
            rx.button(
                t("multi_agent_help_close"),
                on_click=AIState.close_multi_agent_help,
                variant="soft",
                color_scheme="gray",
                size="3",
                margin_top="15px",
                custom_attrs={"data-modal-close": "true"},
            ),

            spacing="4",
            align="center",
            padding="25px",
            background_color="#1a1a1a",
            border_radius="12px",
            max_width="95vw",
            width="600px",
            max_height="90vh",
            overflow_y="auto",
            position="relative",
            z_index="1001",
            color="white",
        ),
        open_var=AIState.multi_agent_help_open,
        backdrop_color="rgba(0, 0, 0, 0.85)",
    )


def reasoning_thinking_help_modal() -> rx.Component:
    """
    Fullscreen Overlay explaining Reasoning vs. Thinking toggles.
    Same visual style as multi_agent_help_modal().
    """
    return overlay_scaffold(
        # Modal Content - zentriert
        rx.vstack(
            # Header
            rx.hstack(
                rx.icon("lightbulb", size=24, color="#FFD700"),
                rx.text(t("reasoning_thinking_help_title"), color="white", font_weight="bold", font_size="20px"),
                spacing="3",
                align="center",
            ),

            # Reasoning Section
            rx.vstack(
                rx.text(t("reasoning_thinking_help_reasoning_title"), color="#FFA500", font_weight="bold", font_size="17px"),
                rx.text(t("reasoning_thinking_help_reasoning_desc"), color="#ccc", font_size="15px"),
                rx.text(t("reasoning_thinking_help_reasoning_effect"), color="#aaa", font_size="14px", font_style="italic"),
                spacing="2",
                width="100%",
                padding="12px",
                background_color="#2a2a2a",
                border_radius="8px",
            ),

            # Thinking Section
            rx.vstack(
                rx.text(t("reasoning_thinking_help_thinking_title"), color="#4FC3F7", font_weight="bold", font_size="17px"),
                rx.text(t("reasoning_thinking_help_thinking_desc"), color="#ccc", font_size="15px"),
                rx.text(t("reasoning_thinking_help_thinking_levels"), color="#ccc", font_size="15px"),
                rx.text(t("reasoning_thinking_help_thinking_effect"), color="#aaa", font_size="14px", font_style="italic"),
                spacing="2",
                width="100%",
                padding="12px",
                background_color="#2a2a2a",
                border_radius="8px",
            ),

            # Combinations Section
            rx.divider(color="#444", margin_y="10px"),
            rx.text(t("reasoning_thinking_help_combinations_title"), color="#FFD700", font_weight="bold", font_size="16px"),
            rx.vstack(
                rx.hstack(
                    rx.text("💭+🧠", min_width="60px", font_size="16px"),
                    rx.text(t("reasoning_thinking_help_both_on"), color="#ccc", font_size="15px"),
                    spacing="2",
                    align="center",
                ),
                rx.hstack(
                    rx.text("💭", min_width="60px", font_size="16px"),
                    rx.text(t("reasoning_thinking_help_reasoning_only"), color="#ccc", font_size="15px"),
                    spacing="2",
                    align="center",
                ),
                rx.hstack(
                    rx.text("🧠", min_width="60px", font_size="16px"),
                    rx.text(t("reasoning_thinking_help_thinking_only"), color="#ccc", font_size="15px"),
                    spacing="2",
                    align="center",
                ),
                rx.hstack(
                    rx.text("—", min_width="60px", font_size="16px", color="#666"),
                    rx.text(t("reasoning_thinking_help_both_off"), color="#ccc", font_size="15px"),
                    spacing="2",
                    align="center",
                ),
                spacing="2",
                width="100%",
            ),

            # Close button
            rx.button(
                t("reasoning_thinking_help_close"),
                on_click=AIState.close_reasoning_thinking_help,
                variant="soft",
                color_scheme="gray",
                size="3",
                margin_top="15px",
                custom_attrs={"data-modal-close": "true"},
            ),

            spacing="4",
            align="center",
            padding="25px",
            background_color="#1a1a1a",
            border_radius="12px",
            max_width="95vw",
            width="550px",
            max_height="90vh",
            overflow_y="auto",
            position="relative",
            z_index="1001",
            color="white",
        ),
        open_var=AIState.reasoning_thinking_help_open,
        backdrop_color="rgba(0, 0, 0, 0.85)",
    )


def research_help_modal() -> rx.Component:
    """Fullscreen Overlay explaining the research modes (Auto, Knowledge, Web Quick, Web Deep)."""
    return overlay_scaffold(
        # Modal Content
        rx.vstack(
            # Header
            rx.hstack(
                rx.icon("lightbulb", size=24, color="#FFD700"),
                rx.text(t("research_help_title"), color="white", font_weight="bold", font_size="18px"),
                spacing="3",
                align="center",
            ),
            # Table
            rx.box(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell(t("research_help_mode"), style={"color": "#FFD700", "font_weight": "bold"}),
                            rx.table.column_header_cell(t("research_help_desc"), style={"color": "#FFD700", "font_weight": "bold"}),
                        ),
                    ),
                    rx.table.body(
                        rx.table.row(
                            rx.table.cell("✨ Automatik", style={"white_space": "nowrap", "font_weight": "bold"}),
                            rx.table.cell(t("research_help_auto_desc")),
                        ),
                        rx.table.row(
                            rx.table.cell("\U0001f4a1 Wissen", style={"white_space": "nowrap", "font_weight": "bold"}),
                            rx.table.cell(t("research_help_knowledge_desc")),
                        ),
                        rx.table.row(
                            rx.table.cell("\u26a1 Web Quick", style={"white_space": "nowrap", "font_weight": "bold"}),
                            rx.table.cell(t("research_help_quick_desc")),
                        ),
                        rx.table.row(
                            rx.table.cell("\U0001f30d Web Deep", style={"white_space": "nowrap", "font_weight": "bold"}),
                            rx.table.cell(t("research_help_deep_desc")),
                        ),
                    ),
                    style={
                        "width": "100%",
                        "border_collapse": "collapse",
                        "& th, & td": {
                            "padding": "10px 15px",
                            "text_align": "left",
                            "border_bottom": "1px solid #444",
                        },
                    },
                ),
                width="100%",
                overflow_x="auto",
            ),
            # Close button
            rx.button(
                t("research_help_close"),
                on_click=AIState.close_research_help,
                variant="soft",
                color_scheme="gray",
                size="3",
                margin_top="15px",
                custom_attrs={"data-modal-close": "true"},
            ),
            spacing="4",
            align="center",
            padding="25px",
            background_color="#1a1a1a",
            border_radius="12px",
            max_width="95vw",
            width="600px",
            max_height="90vh",
            overflow_y="auto",
            position="relative",
            z_index="1001",
            color="white",
        ),
        open_var=AIState.research_help_open,
        backdrop_color="rgba(0, 0, 0, 0.85)",
    )


def _lifecycle_section(
    title_key: str,
    size_key: str,
    body_key: str,
    triggers_key: str,
) -> rx.Component:
    """One model-family block in the lifecycle modal. Title (bold) +
    size/provider one-liner (monospace, dim) + main body + triggers."""
    return rx.vstack(
        rx.text(t(title_key), font_weight="bold", font_size="14px", color="#FFD700"),
        rx.text(
            t(size_key),
            font_size="11px",
            color="#aaa",
            style={"font_family": "monospace"},
        ),
        rx.text(t(body_key), font_size="12px", color="white", style={"line_height": "1.5"}),
        rx.text(t(triggers_key), font_size="12px", color="#ccc", style={"line_height": "1.5"}),
        spacing="1",
        align="start",
        width="100%",
    )


def model_lifecycle_help_modal() -> rx.Component:
    """Fullscreen overlay explaining the model lifecycles
    (InsightFace base, VLM, TTS, LLM). Crosscutting reference linked
    from Vigilantia-Help, Audio-Settings-Help, and the GPU-Details
    row in the settings panel.
    """
    return overlay_scaffold(
        rx.vstack(
            rx.hstack(
                rx.icon("lightbulb", size=24, color="#FFD700"),
                rx.text(
                    t("model_lifecycle_help_title"),
                    color="white",
                    font_weight="bold",
                    font_size="18px",
                ),
                spacing="3",
                align="center",
            ),
            rx.text(
                t("model_lifecycle_help_intro"),
                color="#ccc",
                font_size="12px",
                style={"line_height": "1.5"},
            ),
            rx.divider(),
            _lifecycle_section(
                "model_lifecycle_help_base_title",
                "model_lifecycle_help_base_size",
                "model_lifecycle_help_base_body",
                "model_lifecycle_help_base_triggers",
            ),
            rx.divider(),
            _lifecycle_section(
                "model_lifecycle_help_vlm_title",
                "model_lifecycle_help_vlm_size",
                "model_lifecycle_help_vlm_body",
                "model_lifecycle_help_vlm_triggers",
            ),
            rx.divider(),
            _lifecycle_section(
                "model_lifecycle_help_tts_title",
                "model_lifecycle_help_tts_size",
                "model_lifecycle_help_tts_body",
                "model_lifecycle_help_tts_triggers",
            ),
            rx.divider(),
            _lifecycle_section(
                "model_lifecycle_help_llm_title",
                "model_lifecycle_help_llm_size",
                "model_lifecycle_help_llm_body",
                "model_lifecycle_help_llm_triggers",
            ),
            rx.divider(),
            rx.vstack(
                rx.text(
                    t("model_lifecycle_help_vram_title"),
                    font_weight="bold",
                    font_size="14px",
                    color="#FFD700",
                ),
                rx.text(
                    t("model_lifecycle_help_vram_body"),
                    font_size="12px",
                    color="white",
                    style={"line_height": "1.5"},
                ),
                spacing="1",
                align="start",
                width="100%",
            ),
            rx.divider(),
            rx.vstack(
                rx.text(
                    t("model_lifecycle_help_calibration_title"),
                    font_weight="bold",
                    font_size="14px",
                    color="#FFD700",
                ),
                rx.text(
                    t("model_lifecycle_help_calibration_body"),
                    font_size="12px",
                    color="white",
                    style={"line_height": "1.5"},
                ),
                rx.text(
                    t("model_lifecycle_help_calibration_resweep"),
                    font_size="12px",
                    color="white",
                    style={"line_height": "1.5"},
                ),
                spacing="1",
                align="start",
                width="100%",
            ),
            rx.button(
                t("model_lifecycle_help_close"),
                on_click=AIState.close_model_lifecycle_help,
                variant="soft",
                color_scheme="gray",
                size="3",
                margin_top="10px",
                custom_attrs={"data-modal-close": "true"},
            ),
            spacing="3",
            align="stretch",
            padding="25px",
            background_color="#1a1a1a",
            border_radius="12px",
            max_width="95vw",
            width="720px",
            max_height="90vh",
            overflow_y="auto",
            position="relative",
            z_index="1001",
            color="white",
        ),
        open_var=AIState.model_lifecycle_help_open,
        backdrop_color="rgba(0, 0, 0, 0.85)",
    )
