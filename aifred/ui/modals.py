"""Modal dialogs for AIfred UI."""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from .helpers import t, agent_emoji

# Document Manager Header — gemeinsame Groessen fuer Icons + Buttons,
# damit der Header als Ganzes skaliert (Radix hat keine Halbschritte
# zwischen size="1" und size="2").
DOC_HEADER_ICON_SIZE = 16
DOC_HEADER_BUTTON_SIZE = "2"


def multi_agent_help_modal() -> rx.Component:
    """
    Fullscreen Overlay für Multi-Agent Modus-Übersicht.
    Zeigt alle Modi mit Ablauf und wer entscheidet.
    """
    return rx.cond(
        AIState.multi_agent_help_open,
        # Fullscreen Overlay
        rx.box(
            # Backdrop (klickbar zum Schließen)
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.85)",
                on_click=AIState.close_multi_agent_help,
            ),

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

            # Fullscreen container
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index="1000",
            display="flex",
            justify_content="center",
            align_items="center",
        ),
    )


def reasoning_thinking_help_modal() -> rx.Component:
    """
    Fullscreen Overlay explaining Reasoning vs. Thinking toggles.
    Same visual style as multi_agent_help_modal().
    """
    return rx.cond(
        AIState.reasoning_thinking_help_open,
        # Fullscreen Overlay
        rx.box(
            # Backdrop (klickbar zum Schließen)
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.85)",
                on_click=AIState.close_reasoning_thinking_help,
            ),

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

            # Fullscreen container
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index="1000",
            display="flex",
            justify_content="center",
            align_items="center",
        ),
    )


def research_help_modal() -> rx.Component:
    """Fullscreen Overlay explaining the research modes (Auto, Knowledge, Web 3, Web 7)."""
    return rx.cond(
        AIState.research_help_open,
        rx.box(
            # Backdrop
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.85)",
                on_click=AIState.close_research_help,
            ),
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
                                rx.table.cell("\u26a1 Web 3", style={"white_space": "nowrap", "font_weight": "bold"}),
                                rx.table.cell(t("research_help_web3_desc")),
                            ),
                            rx.table.row(
                                rx.table.cell("\U0001f30d Web 7", style={"white_space": "nowrap", "font_weight": "bold"}),
                                rx.table.cell(t("research_help_web7_desc")),
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
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index="1000",
            display="flex",
            justify_content="center",
            align_items="center",
        ),
    )


def login_dialog() -> rx.Component:
    """
    Fullscreen Overlay für Login/Registrierung.
    Zeigt Login-Form oder Registrierungs-Form basierend auf login_mode.
    Enter-Taste im Passwort-Feld löst Login/Register aus.
    """
    return rx.cond(
        AIState.login_dialog_open,
        # Fullscreen Overlay
        rx.box(
            # Backdrop (nicht klickbar - Login ist erforderlich)
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.9)",
            ),

            # Modal Content - zentriert
            rx.vstack(
                # Logo/Header
                rx.vstack(
                    agent_emoji("\U0001f3a9", size="48px"),
                    rx.text("AIfred Intelligence", color="white", font_weight="bold", font_size="24px"),
                    spacing="1",
                    align="center",
                ),

                # Form Container - wrapped in <form> for browser password manager
                rx.el.form(
                    rx.box(
                        rx.vstack(
                            # Mode Toggle
                            rx.hstack(
                                rx.button(
                                    "Anmelden",
                                    on_click=lambda: AIState.open_login_dialog("login"),  # type: ignore[arg-type]
                                    variant=rx.cond(AIState.login_mode == "login", "solid", "ghost"),
                                    color_scheme="orange",
                                    size="2",
                                    type="button",  # Prevent form submit
                                ),
                                rx.button(
                                    "Registrieren",
                                    on_click=lambda: AIState.open_login_dialog("register"),  # type: ignore[arg-type]
                                    variant=rx.cond(AIState.login_mode == "register", "solid", "ghost"),
                                    color_scheme="orange",
                                    size="2",
                                    type="button",  # Prevent form submit
                                ),
                                spacing="2",
                                justify="center",
                            ),

                            # Username Input (with autocomplete for browser password manager)
                            rx.input(
                                placeholder="Username",
                                value=AIState.login_username,
                                on_change=AIState.set_login_username,
                                name="username",
                                custom_attrs={"autocomplete": "username"},
                                width="100%",
                                size="3",
                            ),

                            # Password Input (Enter triggers login/register, autocomplete for password manager)
                            rx.input(
                                placeholder="Passwort",
                                type="password",
                                value=AIState.login_password,
                                on_change=AIState.set_login_password,
                                on_key_down=AIState.handle_login_key_down,
                                name="password",
                                custom_attrs={"autocomplete": "current-password"},
                                width="100%",
                                size="3",
                            ),

                            # Error Message
                            rx.cond(
                                AIState.login_error != "",
                                rx.text(AIState.login_error, color="red", font_size="14px"),
                            ),

                            # Submit Button
                            rx.cond(
                                AIState.login_mode == "login",
                                rx.button(
                                    "Anmelden",
                                    on_click=AIState.do_login,
                                    color_scheme="orange",
                                    width="100%",
                                    size="3",
                                    type="submit",
                                ),
                                rx.button(
                                    "Account erstellen",
                                    on_click=AIState.do_register,
                                    color_scheme="green",
                                    width="100%",
                                    size="3",
                                    type="submit",
                                ),
                            ),

                            spacing="4",
                            width="100%",
                        ),
                        background_color="#1a1a1a",
                        border_radius="12px",
                        padding="24px",
                        width="320px",
                        border="1px solid #333",
                    ),
                    on_submit=AIState.handle_login_submit,
                    method="post",
                ),

                spacing="6",
                align="center",
                position="absolute",
                top="50%",
                left="50%",
                transform="translate(-50%, -50%)",
            ),

            # Fullscreen container
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index="9999",
        ),
    )


def crop_modal() -> rx.Component:
    """
    Fullscreen Overlay für Bild-Zuschnitt.
    Verwendet rx.cond statt rx.dialog für bessere Mobile-Kompatibilität.
    """
    return rx.cond(
        AIState.crop_modal_open,
        # Fullscreen Overlay
        rx.box(
            # Backdrop (klickbar zum Schließen)
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.85)",
                on_click=AIState.cancel_crop,
            ),

            # Modal Content - zentriert
            rx.vstack(
                # Header
                rx.hstack(
                    rx.icon("crop", size=20, color="white"),
                    rx.text(t("crop_modal_title"), color="white", font_weight="bold"),
                    spacing="2",
                    align="center",
                ),

                # Bild + Crop-Overlay Container
                rx.box(
                    # Das Bild
                    rx.image(
                        src=AIState.crop_preview_url,
                        id="crop-image",
                        max_width="90vw",
                        max_height="60vh",
                        object_fit="contain",
                        border_radius="8px",
                        display="block",
                    ),
                    # Crop-Overlay
                    rx.box(
                        rx.box(
                            # 4 Ecken
                            rx.box(class_name="crop-handle crop-handle-nw", id="crop-nw"),
                            rx.box(class_name="crop-handle crop-handle-ne", id="crop-ne"),
                            rx.box(class_name="crop-handle crop-handle-sw", id="crop-sw"),
                            rx.box(class_name="crop-handle crop-handle-se", id="crop-se"),
                            # 4 Kanten
                            rx.box(class_name="crop-handle crop-handle-n", id="crop-n"),
                            rx.box(class_name="crop-handle crop-handle-s", id="crop-s"),
                            rx.box(class_name="crop-handle crop-handle-w", id="crop-w"),
                            rx.box(class_name="crop-handle crop-handle-e", id="crop-e"),
                            id="crop-box",
                            class_name="crop-box",
                        ),
                        id="crop-overlay",
                        class_name="crop-overlay",
                    ),
                    id="crop-container",
                    position="relative",
                    display="inline-block",  # Passt sich an Bildgröße an
                ),

                # Info-Text
                rx.text(
                    t("crop_modal_hint"),
                    font_size="12px",
                    color="#888",
                    text_align="center",
                ),

                # Buttons
                rx.hstack(
                    rx.button(
                        t("crop_cancel"),
                        on_click=AIState.cancel_crop,
                        variant="soft",
                        color_scheme="gray",
                        size="3",
                    ),
                    rx.button(
                        rx.icon("rotate-ccw", size=16),
                        on_click=AIState.rotate_crop_image_left,
                        variant="soft",
                        color_scheme="blue",
                        size="3",
                        title="90° links",
                    ),
                    rx.button(
                        rx.icon("rotate-cw", size=16),
                        on_click=AIState.rotate_crop_image_right,
                        variant="soft",
                        color_scheme="blue",
                        size="3",
                        title="90° rechts",
                    ),
                    rx.button(
                        rx.hstack(
                            rx.icon("check", size=16),
                            rx.text(t("crop_apply")),
                            spacing="1",
                        ),
                        on_click=rx.call_script(
                            """
                            (() => {
                                const cropBox = document.getElementById('crop-box');
                                if (cropBox) {
                                    const left = parseFloat(cropBox.style.left) || 0;
                                    const top = parseFloat(cropBox.style.top) || 0;
                                    const width = parseFloat(cropBox.style.width) || 100;
                                    const height = parseFloat(cropBox.style.height) || 100;
                                    return JSON.stringify({ x: left, y: top, width: width, height: height });
                                }
                                return JSON.stringify({ x: 0, y: 0, width: 100, height: 100 });
                            })()
                            """,
                            callback=AIState.apply_crop_with_coords
                        ),
                        color_scheme="green",
                        size="3",
                    ),
                    spacing="3",
                ),

                spacing="4",
                align="center",
                padding="20px",
                background_color="#1a1a1a",
                border_radius="12px",
                max_width="95vw",
                max_height="90vh",
                position="relative",
                z_index="1001",
            ),

            # Fullscreen container
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index="1000",
            display="flex",
            justify_content="center",
            align_items="center",
            style={"touch_action": "none"},  # Verhindert Browser-Scroll
        ),
    )


def image_lightbox_modal() -> rx.Component:
    """
    Fullscreen Overlay for viewing chat history images full-size.
    Click anywhere to close.
    """
    return rx.cond(
        AIState.lightbox_open,
        # Fullscreen Overlay
        rx.box(
            # Backdrop (click to close)
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.92)",
                on_click=AIState.close_lightbox,
                cursor="pointer",
            ),

            # Close button (top-right corner)
            rx.box(
                rx.icon("x", size=28, color="white"),
                position="absolute",
                top="20px",
                right="20px",
                cursor="pointer",
                padding="8px",
                border_radius="50%",
                background_color="rgba(255, 255, 255, 0.1)",
                on_click=AIState.close_lightbox,
                z_index="1002",
                style={
                    "transition": "background-color 0.2s ease",
                    "&:hover": {
                        "background_color": "rgba(255, 255, 255, 0.2)",
                    },
                },
            ),

            # Image - centered, click to close
            rx.image(
                src=AIState.lightbox_image_url,
                max_width="90vw",
                max_height="85vh",
                object_fit="contain",
                border_radius="8px",
                on_click=AIState.close_lightbox,
                cursor="pointer",
                position="relative",
                z_index="1001",
            ),

            # Fullscreen container
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index="1000",
            display="flex",
            justify_content="center",
            align_items="center",
            style={"touch_action": "none"},  # Prevent browser scroll
        ),
    )


def _doc_file_row(item: rx.Var) -> rx.Component:
    """Single row in the document file explorer."""
    name = item["name"].to(str)
    is_folder = item["type"].to(str) == "folder"
    is_indexed = item["indexed"].to(bool)
    chunks = item["chunks"].to(int)

    return rx.hstack(
        # Icon: folder or file with index indicator
        rx.cond(
            is_folder,
            rx.icon("folder", size=16, color="#d29922", cursor="pointer",
                     on_click=AIState.doc_navigate_folder(name)),
            rx.icon("file-text", size=16, color=rx.cond(is_indexed, "#4CAF50", "#888")),
        ),
        # Name — inline rename input or clickable text
        rx.cond(
            AIState.doc_rename_target == name,
            # Rename mode: input field
            rx.hstack(
                rx.input(
                    value=AIState.doc_rename_value,
                    on_change=AIState.doc_set_rename_value,
                    on_key_down=lambda key: rx.cond(
                        key == "Enter",
                        AIState.doc_confirm_rename(),
                        rx.cond(key == "Escape", AIState.doc_cancel_rename(), rx.noop()),  # type: ignore[arg-type]
                    ),
                    size="1", font_size="12px", width="150px",
                    auto_focus=True,
                ),
                rx.icon_button(
                    rx.icon("check", size=12), size="1",
                    variant="ghost", color_scheme="green",
                    on_click=AIState.doc_confirm_rename, cursor="pointer",
                ),
                rx.icon_button(
                    rx.icon("x", size=12), size="1",
                    variant="ghost", color_scheme="gray",
                    on_click=AIState.doc_cancel_rename, cursor="pointer",
                ),
                spacing="1", align="center",
            ),
            # Normal mode: clickable name
            rx.text(
                name,
                font_size="12px",
                color=rx.cond(is_folder, "#d29922", "white"),
                cursor="pointer",
                _hover={"text_decoration": "underline"},
                word_break="break-all",
                min_width="0",
                on_click=rx.cond(
                    is_folder,
                    AIState.doc_navigate_folder(name),
                    AIState.preview_document(name),
                ),
            ),
        ),
        rx.spacer(),
        # Size (files only)
        rx.cond(
            ~is_folder,
            rx.text(item["size"].to(str), font_size="10px", color="#666", min_width="60px"),
        ),
        # Index status badge
        rx.cond(
            ~is_folder,
            rx.cond(
                is_indexed,
                rx.text(chunks.to(str) + " chunks", font_size="10px", color="#4CAF50", min_width="60px"),
                rx.text("—", font_size="10px", color="#555", min_width="60px"),
            ),
        ),
        # Actions (files only)
        rx.cond(
            ~is_folder,
            rx.hstack(
                # Index / Deindex toggle
                rx.cond(
                    is_indexed,
                    rx.tooltip(
                        rx.icon_button(
                            rx.icon("database-zap", size=14),
                            size="1", variant="ghost", color_scheme="orange",
                            on_click=AIState.doc_deindex_file(name), cursor="pointer",
                        ),
                        content=rx.cond(AIState.ui_language == "de", "Deindexieren", "Deindex"),
                    ),
                    rx.tooltip(
                        rx.icon_button(
                            rx.icon("database", size=14),
                            size="1", variant="ghost", color_scheme="gray",
                            on_click=AIState.doc_index_file(name), cursor="pointer",
                        ),
                        content=rx.cond(AIState.ui_language == "de", "Indexieren", "Index"),
                    ),
                ),
                # Rename
                rx.icon_button(
                    rx.icon("pencil", size=14),
                    size="1", variant="ghost", color_scheme="yellow",
                    on_click=AIState.doc_start_rename(name), cursor="pointer",
                ),
                # Preview
                rx.icon_button(
                    rx.icon("eye", size=14),
                    size="1", variant="ghost", color_scheme="blue",
                    on_click=AIState.preview_document(name), cursor="pointer",
                ),
                # Delete
                rx.icon_button(
                    rx.icon("trash-2", size=14),
                    size="1", variant="ghost", color_scheme="red",
                    on_click=AIState.doc_open_delete_dialog(name), cursor="pointer",
                ),
                spacing="0",
                align="center",
            ),
        ),
        # Folder actions: rename + delete
        rx.cond(
            is_folder,
            rx.hstack(
                rx.icon_button(
                    rx.icon("pencil", size=14),
                    size="1", variant="ghost", color_scheme="yellow",
                    on_click=AIState.doc_start_rename(name), cursor="pointer",
                ),
                rx.icon_button(
                    rx.icon("trash-2", size=14),
                    size="1", variant="ghost", color_scheme="red",
                    on_click=AIState.doc_open_delete_folder_dialog(name), cursor="pointer",
                ),
                spacing="0", align="center",
            ),
        ),
        width="100%",
        padding="5px 8px",
        align="center",
        border_bottom="1px solid #2a2a2a",
        _hover={"background_color": "rgba(255, 255, 255, 0.05)"},
    )


def _doc_delete_dialog() -> rx.Component:
    """Delete confirmation dialog with disk/index checkboxes."""
    return rx.cond(
        AIState.doc_delete_target != "",
        rx.box(
            rx.vstack(
                rx.text(
                    rx.cond(
                        AIState.doc_delete_is_folder,
                        rx.cond(AIState.ui_language == "de", "Ordner löschen (rekursiv)", "Delete folder (recursive)"),
                        t("doc_delete_confirm_title"),
                    ),
                    font_weight="bold", font_size="14px", color="white",
                ),
                rx.text(
                    AIState.doc_delete_target,
                    font_size="12px", color="#d29922", font_weight="bold",
                ),
                rx.vstack(
                    rx.hstack(
                        rx.checkbox(
                            t("doc_delete_from_disk"),
                            checked=AIState.doc_delete_from_disk,
                            on_change=AIState.doc_toggle_delete_disk,
                            size="1",
                        ),
                        spacing="2", align="center",
                    ),
                    rx.hstack(
                        rx.checkbox(
                            t("doc_delete_from_index"),
                            checked=AIState.doc_delete_from_index,
                            on_change=AIState.doc_toggle_delete_index,
                            size="1",
                        ),
                        spacing="2", align="center",
                    ),
                    spacing="2", width="100%",
                ),
                rx.hstack(
                    rx.button(
                        rx.cond(AIState.ui_language == "de", "Abbrechen", "Cancel"),
                        on_click=AIState.doc_close_delete_dialog,
                        variant="soft", color_scheme="gray", size="1", flex="1",
                    ),
                    rx.button(
                        rx.cond(AIState.ui_language == "de", "Löschen", "Delete"),
                        on_click=AIState.doc_confirm_delete,
                        variant="solid", color_scheme="red", size="1", flex="1",
                    ),
                    spacing="2", width="100%",
                ),
                spacing="3",
                padding="16px",
                background="#1a1a1a",
                border_radius="8px",
                border="1px solid #c0392b",
                box_shadow="0 -4px 20px rgba(0, 0, 0, 0.8)",
                width="100%",
            ),
        ),
    )


def document_manager_modal() -> rx.Component:
    """File explorer modal for document management."""
    return rx.cond(
        AIState.document_manager_open,
        rx.box(
            # Backdrop
            rx.box(
                position="absolute", top="0", left="0",
                width="100%", height="100%",
                background_color="#000000",
                on_click=AIState.close_document_manager,
            ),

            # Modal Content
            rx.vstack(
                # Header
                rx.hstack(
                    rx.icon("folder-open", size=24, color="#d29922"),
                    rx.text(t("doc_manager_title"), color="white",
                            font_weight="bold", font_size="18px"),
                    rx.spacer(),
                    rx.icon_button(
                        rx.icon("x", size=DOC_HEADER_ICON_SIZE), size=DOC_HEADER_BUTTON_SIZE,
                        variant="ghost", color_scheme="gray",
                        on_click=AIState.close_document_manager, cursor="pointer",
                    ),
                    width="100%", align="center",
                ),

                # Breadcrumb navigation + create folder + refresh
                # Action-Buttons direkt links neben dem Pfad — vorher
                # waren sie ganz rechts zu klein und wurden übersehen.
                rx.hstack(
                    rx.icon_button(
                        rx.icon("home", size=DOC_HEADER_ICON_SIZE), size=DOC_HEADER_BUTTON_SIZE,
                        variant="ghost", color_scheme="yellow",
                        on_click=AIState.doc_navigate_root, cursor="pointer",
                    ),
                    rx.cond(
                        AIState.doc_current_folder != "",
                        rx.icon_button(
                            rx.icon("arrow-left", size=DOC_HEADER_ICON_SIZE), size=DOC_HEADER_BUTTON_SIZE,
                            variant="ghost", color_scheme="gray",
                            on_click=AIState.doc_navigate_up, cursor="pointer",
                        ),
                    ),
                    rx.cond(
                        AIState.doc_creating_folder,
                        rx.hstack(
                            rx.input(
                                value=AIState.doc_new_folder_name,
                                on_change=AIState.doc_set_new_folder_name,
                                on_key_down=lambda key: rx.cond(
                                    key == "Enter",
                                    AIState.doc_confirm_create_folder(),
                                    rx.cond(key == "Escape", AIState.doc_cancel_create_folder(), rx.noop()),  # type: ignore[arg-type]
                                ),
                                placeholder=rx.cond(AIState.ui_language == "de", "Ordnername", "Folder name"),
                                size="1", font_size="12px", width="160px",
                                auto_focus=True,
                            ),
                            rx.icon_button(
                                rx.icon("check", size=DOC_HEADER_ICON_SIZE), size=DOC_HEADER_BUTTON_SIZE,
                                variant="ghost", color_scheme="green",
                                on_click=AIState.doc_confirm_create_folder, cursor="pointer",
                            ),
                            rx.icon_button(
                                rx.icon("x", size=DOC_HEADER_ICON_SIZE), size=DOC_HEADER_BUTTON_SIZE,
                                variant="ghost", color_scheme="gray",
                                on_click=AIState.doc_cancel_create_folder, cursor="pointer",
                            ),
                            spacing="1", align="center",
                        ),
                        rx.hstack(
                            rx.tooltip(
                                rx.icon_button(
                                    rx.icon("folder-plus", size=DOC_HEADER_ICON_SIZE), size=DOC_HEADER_BUTTON_SIZE,
                                    variant="ghost", color_scheme="yellow",
                                    on_click=AIState.doc_open_create_folder, cursor="pointer",
                                ),
                                content=rx.cond(AIState.ui_language == "de", "Ordner anlegen", "Create folder"),
                            ),
                            rx.tooltip(
                                rx.icon_button(
                                    rx.icon("refresh-cw", size=DOC_HEADER_ICON_SIZE), size=DOC_HEADER_BUTTON_SIZE,
                                    variant="ghost", color_scheme="gray",
                                    on_click=AIState.doc_refresh, cursor="pointer",
                                ),
                                content=rx.cond(AIState.ui_language == "de", "Aktualisieren", "Refresh"),
                            ),
                            spacing="1", align="center",
                        ),
                    ),
                    rx.cond(
                        AIState.doc_current_folder != "",
                        rx.text(
                            "/ " + AIState.doc_current_folder,
                            font_size="12px", color="#888",
                        ),
                    ),
                    rx.spacer(),
                    spacing="2", align="center", width="100%",
                ),

                # Two-column layout: file list | preview (flex=1 fills remaining modal height)
                rx.flex(
                    # Left: File list
                    rx.vstack(
                        # Upload drop zone
                        rx.upload(
                            rx.hstack(
                                rx.icon("upload", size=14, color="#888"),
                                rx.text(
                                    rx.cond(AIState.ui_language == "de",
                                            "Dateien hierher ziehen", "Drop files here"),
                                    font_size="11px", color="#888",
                                ),
                                spacing="2", align="center", justify="center",
                                width="100%", padding="8px",
                                border="1px dashed #444", border_radius="6px",
                                _hover={"border_color": "#d29922", "color": "#d29922"},
                            ),
                            id="doc-explorer-upload",
                            on_drop=AIState.handle_document_upload,
                            multiple=True,
                            border="none", padding="0", width="100%",
                        ),

                        # File listing (own scroll container)
                        rx.box(
                            rx.cond(
                                AIState.doc_file_list.length() > 0,
                                rx.vstack(
                                    rx.foreach(AIState.doc_file_list, _doc_file_row),
                                    # File count
                                    rx.text(
                                        AIState.doc_file_list.length().to(str) + rx.cond(
                                            AIState.ui_language == "de", " Dateien", " files"),
                                        font_size="10px", color="#555", padding="4px 0",
                                    ),
                                    spacing="0", width="100%",
                                ),
                                rx.text(
                                    rx.cond(AIState.ui_language == "de",
                                            "Leerer Ordner", "Empty folder"),
                                    color="#666", font_size="13px", padding="20px 0",
                                ),
                            ),
                            flex="1",
                            min_height="0",
                            overflow_y="scroll",
                            width="100%",
                        ),

                        # Status message
                        rx.cond(
                            AIState.document_upload_status != "",
                            rx.text(AIState.document_upload_status,
                                    font_size="12px", color="#aaa"),
                        ),

                        # Delete confirmation dialog (absolute overlay)
                        rx.box(
                            _doc_delete_dialog(),
                            position="absolute",
                            bottom="0",
                            left="0",
                            right="0",
                            z_index="10",
                            background="#1a1a1a",
                        ),

                        position="relative",
                        flex=["1 1 100%", "1 1 100%", "0 0 45%"],
                        height="100%",
                        min_height="0",
                        overflow="hidden",
                        padding_right=["0", "0", "15px"],
                        border_right=["none", "none", "1px solid #333"],
                        spacing="2",
                    ),

                    # Right: Preview
                    rx.vstack(
                        rx.cond(
                            AIState.document_preview_filename != "",
                            rx.vstack(
                                rx.hstack(
                                    rx.icon("eye", size=16, color="#58a6ff"),
                                    rx.text(AIState.document_preview_filename,
                                            color="#58a6ff", font_weight="bold", font_size="14px"),
                                    rx.spacer(),
                                    rx.icon_button(
                                        rx.icon("x", size=14), size="1",
                                        variant="ghost", color_scheme="gray",
                                        on_click=AIState.close_document_preview, cursor="pointer",
                                    ),
                                    width="100%", align="center",
                                ),
                                rx.box(
                                    rx.text(AIState.document_preview_content,
                                            white_space="pre-wrap", font_size="12px",
                                            color="#ccc", font_family="monospace"),
                                    flex="1", min_height="0",
                                    overflow_y="auto", width="100%",
                                    padding="10px", background_color="rgba(0, 0, 0, 0.3)",
                                    border_radius="6px", border="1px solid #333",
                                ),
                                spacing="2", width="100%",
                                height="100%",
                            ),
                            rx.vstack(
                                rx.icon("eye-off", size=32, color="#444"),
                                rx.text(
                                    rx.cond(AIState.ui_language == "de",
                                            "Klicke auf eine Datei", "Click a file to preview"),
                                    color="#666", font_size="13px",
                                ),
                                align="center", justify="center", height="100%", spacing="3",
                            ),
                        ),
                        flex=["1 1 100%", "1 1 100%", "0 0 55%"],
                        height="100%",
                        min_height="0",
                        overflow="hidden",
                        padding_left=["0", "0", "15px"],
                    ),

                    width="100%", align="start", gap="0",
                    direction=rx.breakpoints(initial="column", md="row"),
                    flex="1",
                    min_height="0",
                    overflow="hidden",
                ),

                spacing="3",
                padding="25px",
                background_color="#1a1a1a",
                border_radius="12px",
                width=["95vw", "95vw", "1100px"],
                height=["90vh", "90vh", "700px"],
                max_width="95vw",
                max_height="90vh",
                overflow_y="hidden",
                position="relative",
                z_index="1001",
                color="white",
            ),

            position="fixed", top="0", left="0",
            width="100vw", height="100vh", z_index="1000",
            display="flex", justify_content="center", align_items="center",
        ),
    )


# ============================================================
# CHANNEL CREDENTIALS MODAL (generic for all channel plugins)
# ============================================================

def _cred_field_input(field: rx.Var) -> rx.Component:
    """Render a single credential field from a field descriptor dict.

    The field var is a dict with keys: env_key, label_key, placeholder, is_password, group, width_ratio.
    """
    env_key = field["env_key"]
    value = AIState.channel_credential_values[env_key].to(str)

    # Password field with eye toggle
    password_input = rx.vstack(
        rx.text(field["label_key"].to(str), font_size="11px", color="#999"),
        rx.box(
            rx.cond(
                AIState.channel_cred_show_password,
                rx.input(
                    value=value,
                    on_change=lambda val: AIState.update_channel_credential([env_key, val]),
                    placeholder="••••••••",
                    size="2",
                    width="100%",
                ),
                rx.input(
                    value=value,
                    on_change=lambda val: AIState.update_channel_credential([env_key, val]),
                    type="password",
                    placeholder="••••••••",
                    size="2",
                    width="100%",
                ),
            ),
            rx.icon_button(
                rx.cond(
                    AIState.channel_cred_show_password,
                    rx.icon("eye-off", size=14),
                    rx.icon("eye", size=14),
                ),
                on_click=AIState.toggle_channel_cred_show_password,
                size="1",
                variant="ghost",
                color_scheme="gray",
                position="absolute",
                right="6px",
                top="50%",
                transform="translateY(-50%)",
                cursor="pointer",
            ),
            position="relative",
            width="100%",
        ),
        spacing="1",
        width="100%",
    )

    # Label with optional tooltip (convention: label_key + "_tooltip")
    tooltip_key = field["label_key"].to(str) + "_tooltip"
    label_with_tooltip = rx.tooltip(
        rx.text(
            field["label_key"].to(str),
            font_size="11px", color="#999", cursor="help",
        ),
        content=t(tooltip_key),
    )
    # Dropdown input (when options are provided)
    # UI shows labels from channel_credential_values (pre-mapped in State)
    # State handler maps label→value on save
    dropdown_input = rx.vstack(
        label_with_tooltip,
        rx.select(
            field["option_labels"].to(str).split(","),
            value=value,
            on_change=lambda val: AIState.update_channel_credential([env_key, val]),
            size="2",
            width="100%",
        ),
        spacing="1",
        width="100%",
    )

    # Normal text input
    text_input = rx.vstack(
        label_with_tooltip,
        rx.input(
            value=value,
            on_change=lambda val: AIState.update_channel_credential([env_key, val]),
            placeholder=field["placeholder"].to(str),
            size="2",
            width="100%",
        ),
        spacing="1",
        width="100%",
    )

    return rx.cond(
        field["is_password"].to(str) == "1",
        password_input,
        rx.cond(
            field["options"].to(str) != "",
            dropdown_input,
            text_input,
        ),
    )


def channel_credentials_modal() -> rx.Component:
    """Generic modal dialog for entering channel credentials.

    Renders fields dynamically from AIState.channel_credential_fields.
    """
    return rx.cond(
        AIState.channel_credentials_modal_open,
        rx.box(
            # Backdrop
            rx.box(
                on_click=AIState.close_channel_credentials,
                position="fixed",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.92)",
            ),
            # Modal content
            rx.vstack(
                # Title: channel name + "Credentials"
                rx.text(
                    AIState.channel_credentials_display_name,
                    font_weight="bold",
                    font_size="16px",
                    color="white",
                ),

                # Dynamic fields
                rx.foreach(
                    AIState.channel_credential_fields,
                    _cred_field_input,
                ),

                # OAuth Connect-Button (nur wenn Plugin oauth_provider gesetzt hat)
                rx.cond(
                    AIState.oauth_connect_provider != "",
                    rx.vstack(
                        rx.divider(),
                        rx.match(
                            AIState.oauth_connect_status,
                            (
                                "connecting",
                                rx.button(
                                    rx.hstack(
                                        rx.spinner(size="1"),
                                        rx.text(t("oauth_connect_connecting")),
                                        spacing="2", align="center",
                                    ),
                                    variant="solid",
                                    color_scheme="amber",
                                    size="2",
                                    width="100%",
                                    disabled=True,
                                ),
                            ),
                            (
                                "connected",
                                rx.hstack(
                                    rx.button(
                                        rx.hstack(
                                            rx.icon("check-circle", size=14),
                                            rx.text(t("oauth_connect_connected")),
                                            spacing="2", align="center",
                                        ),
                                        variant="solid",
                                        color_scheme="green",
                                        size="2",
                                        flex="1",
                                        disabled=True,
                                    ),
                                    rx.button(
                                        t("oauth_connect_disconnect"),
                                        on_click=AIState.disconnect_oauth,
                                        variant="soft",
                                        color_scheme="red",
                                        size="2",
                                    ),
                                    spacing="2",
                                    width="100%",
                                ),
                            ),
                            (
                                "error",
                                rx.cond(
                                    AIState.oauth_auth_url != "",
                                    rx.link(
                                        rx.button(
                                            rx.hstack(
                                                rx.icon("circle-alert", size=14),
                                                rx.text(t("oauth_connect_error")),
                                                spacing="2", align="center",
                                            ),
                                            variant="solid",
                                            color_scheme="red",
                                            size="2",
                                            width="100%",
                                        ),
                                        href=AIState.oauth_auth_url,
                                        target="_blank",
                                        is_external=True,
                                        on_click=AIState.start_oauth_connection,
                                        width="100%",
                                        underline="none",
                                    ),
                                    rx.button(
                                        rx.hstack(
                                            rx.icon("circle-alert", size=14),
                                            rx.text(t("oauth_connect_no_credentials")),
                                            spacing="2", align="center",
                                        ),
                                        variant="soft",
                                        color_scheme="gray",
                                        size="2",
                                        width="100%",
                                        disabled=True,
                                    ),
                                ),
                            ),
                            # Default = "idle"
                            rx.cond(
                                AIState.oauth_auth_url != "",
                                rx.link(
                                    rx.button(
                                        rx.hstack(
                                            rx.icon("link", size=14),
                                            rx.text(t("oauth_connect_idle")),
                                            spacing="2", align="center",
                                        ),
                                        variant="solid",
                                        color_scheme="blue",
                                        size="2",
                                        width="100%",
                                    ),
                                    href=AIState.oauth_auth_url,
                                    target="_blank",
                                    is_external=True,
                                    on_click=AIState.start_oauth_connection,
                                    width="100%",
                                    underline="none",
                                ),
                                rx.button(
                                    rx.hstack(
                                        rx.icon("link", size=14),
                                        rx.text(t("oauth_connect_no_credentials")),
                                        spacing="2", align="center",
                                    ),
                                    variant="soft",
                                    color_scheme="gray",
                                    size="2",
                                    width="100%",
                                    disabled=True,
                                ),
                            ),
                        ),
                        rx.text(
                            t("oauth_connect_hint"),
                            font_size="10px",
                            color="#888",
                        ),
                        spacing="2",
                        width="100%",
                    ),
                ),

                # Buttons
                rx.hstack(
                    rx.button(
                        t("cred_cancel"),
                        on_click=AIState.close_channel_credentials,
                        variant="soft",
                        color_scheme="gray",
                        size="1",
                        flex="1",
                    ),
                    rx.button(
                        t("cred_save"),
                        on_click=AIState.save_channel_credentials,
                        variant="solid",
                        color_scheme="blue",
                        size="1",
                        flex="1",
                    ),
                    spacing="2",
                    width="100%",
                ),

                spacing="3",
                padding="24px",
                background="#1a1a2e",
                border_radius="12px",
                border="1px solid var(--gray-a6)",
                width="500px",
                max_width="90vw",
                position="relative",
                z_index="1101",
            ),
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index="1100",
            display="flex",
            justify_content="center",
            align_items="center",
        ),
    )


# ============================================================
# AUDIT LOG MODAL
# ============================================================

def audit_log_modal() -> rx.Component:
    """Modal showing recent tool execution audit log."""

    def _audit_row(entry: dict) -> rx.Component:
        return rx.table.row(
            rx.table.cell(rx.text(entry["timestamp"], font_size="11px"), white_space="nowrap"),
            rx.table.cell(rx.text(entry["source"], font_size="11px")),
            rx.table.cell(rx.text(entry["tool_name"], font_size="11px", font_weight="500")),
            rx.table.cell(rx.text(entry["tool_tier"], font_size="11px")),
            rx.table.cell(
                rx.cond(
                    entry["success"] == "OK",
                    rx.text("OK", font_size="11px", color="green"),
                    rx.text("FAIL", font_size="11px", color="red"),
                )
            ),
            rx.table.cell(rx.text(entry["duration"], font_size="11px")),
        )

    return rx.cond(
        AIState.audit_log_open,
        rx.box(
            # Backdrop
            rx.box(
                on_click=AIState.close_audit_log,
                position="fixed",
                top="0", left="0",
                width="100vw", height="100vh",
                bg="rgba(0,0,0,0.5)",
                z_index="1000",
            ),
            # Modal content
            rx.box(
                rx.vstack(
                    rx.hstack(
                        rx.icon("shield-check", size=18),
                        rx.text("Audit Log", font_weight="bold", font_size="14px"),
                        rx.box(flex="1"),
                        rx.icon_button(
                            rx.icon("x", size=14),
                            on_click=AIState.close_audit_log,
                            size="1", variant="ghost",
                        ),
                        align="center",
                        width="100%",
                    ),
                    rx.text(t("audit_log_subtitle"), font_size="11px", color="gray"),
                    rx.box(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    rx.table.column_header_cell(rx.text(t("audit_col_time"), font_size="11px")),
                                    rx.table.column_header_cell(rx.text(t("audit_col_source"), font_size="11px")),
                                    rx.table.column_header_cell(rx.text(t("audit_col_tool"), font_size="11px")),
                                    rx.table.column_header_cell(rx.text(t("audit_col_tier"), font_size="11px")),
                                    rx.table.column_header_cell(rx.text(t("audit_col_status"), font_size="11px")),
                                    rx.table.column_header_cell(rx.text(t("audit_col_duration"), font_size="11px")),
                                ),
                            ),
                            rx.table.body(
                                rx.foreach(AIState.audit_log_entries, _audit_row),
                            ),
                            width="100%",
                            size="1",
                        ),
                        max_height="400px",
                        overflow_y="auto",
                        width="100%",
                    ),
                    spacing="3",
                    width="100%",
                ),
                background="#1a1a2e",
                border_radius="12px",
                border="1px solid var(--gray-a6)",
                padding="16px",
                width="700px",
                max_width="95vw",
                position="relative",
                z_index="1001",
            ),
            position="fixed",
            top="0", left="0",
            width="100vw", height="100vh",
            z_index="1000",
            display="flex",
            justify_content="center",
            align_items="center",
        ),
    )


# ============================================================
# Agent Bundle Export / Import Modals
# ============================================================


def _bundle_export_row(agent: dict) -> rx.Component:
    """One agent in the export selection list."""
    return rx.hstack(
        rx.checkbox(
            checked=AIState.bundle_export_selected.contains(agent["agent_id"]),
            on_change=lambda _: AIState.toggle_bundle_export_agent(agent["agent_id"]),
        ),
        rx.text(agent["emoji"], font_size="14px"),
        rx.text(agent["display_name"], font_size="13px", color="white"),
        rx.text(f"({agent['agent_id']})", font_size="11px", color="#888"),
        spacing="2",
        align="center",
        padding="6px 8px",
        border_radius="6px",
        _hover={"background": "rgba(255,255,255,0.04)"},
        cursor="pointer",
    )


def bundle_export_modal() -> rx.Component:
    """Modal: pick agents to bundle into a downloadable ZIP."""
    return rx.cond(
        AIState.bundle_export_open,
        rx.box(
            rx.box(
                on_click=AIState.close_bundle_export,
                position="fixed", top="0", left="0",
                width="100vw", height="100vh",
                bg="rgba(0,0,0,0.5)", z_index="1000",
            ),
            rx.box(
                rx.vstack(
                    rx.hstack(
                        rx.icon("package", size=18, color="#FFD700"),
                        rx.text(t("bundle_export_title"), font_weight="bold", font_size="15px", color="white"),
                        rx.spacer(),
                        rx.icon_button(
                            rx.icon("x", size=14),
                            on_click=AIState.close_bundle_export,
                            size="1", variant="ghost",
                        ),
                        align="center", width="100%",
                    ),
                    rx.text(t("bundle_export_desc"), font_size="11px", color="#aaa"),
                    rx.box(
                        rx.foreach(AIState.bundle_all_agents, _bundle_export_row),
                        max_height="50vh",
                        overflow_y="auto",
                        width="100%",
                        border="1px solid #333",
                        border_radius="6px",
                        padding="4px",
                    ),
                    rx.hstack(
                        rx.spacer(),
                        rx.button(
                            t("bundle_cancel"),
                            on_click=AIState.close_bundle_export,
                            variant="soft", color_scheme="gray", size="2",
                        ),
                        rx.button(
                            rx.icon("download", size=14),
                            t("bundle_export_btn"),
                            on_click=AIState.confirm_bundle_export,
                            disabled=AIState.bundle_export_selected.length() == 0,
                            variant="solid", color_scheme="blue", size="2",
                        ),
                        spacing="2", width="100%",
                    ),
                    spacing="3", width="100%",
                ),
                background="#1a1a2e",
                border_radius="12px",
                border="1px solid var(--gray-a6)",
                padding="16px",
                width="500px",
                max_width="95vw",
                position="relative",
                z_index="1001",
            ),
            position="fixed", top="0", left="0",
            width="100vw", height="100vh",
            z_index="1000",
            display="flex",
            justify_content="center",
            align_items="center",
        ),
    )


def _bundle_import_row(agent: dict) -> rx.Component:
    """One agent in the import bundle's selection list."""
    return rx.hstack(
        rx.checkbox(
            checked=AIState.bundle_import_selected.contains(agent["agent_id"]),
            on_change=lambda _: AIState.toggle_bundle_import_agent(agent["agent_id"]),
        ),
        rx.text(agent["display_name"], font_size="13px", color="white"),
        rx.text(f"({agent['agent_id']})", font_size="11px", color="#888"),
        rx.cond(
            agent["exists_locally"],
            rx.text(
                t("bundle_exists_locally"),
                font_size="10px",
                color="#FFA500",
                font_style="italic",
            ),
        ),
        spacing="2",
        align="center",
        padding="6px 8px",
        border_radius="6px",
        _hover={"background": "rgba(255,255,255,0.04)"},
        width="100%",
    )


def _bundle_conflict_select() -> rx.Component:
    """Conflict-strategy dropdown via rx.select.root — needs separate
    value/label pairs that the simple ``rx.select`` doesn't support."""
    return rx.select.root(
        rx.select.trigger(),
        rx.select.content(
            rx.select.item(t("bundle_conflict_rename"), value="rename"),
            rx.select.item(t("bundle_conflict_overwrite"), value="overwrite"),
            rx.select.item(t("bundle_conflict_abort"), value="abort"),
        ),
        value=AIState.bundle_import_conflict,
        on_change=AIState.set_bundle_import_conflict,
        size="1",
    )


def bundle_import_modal() -> rx.Component:
    """Modal: pick a ZIP, then choose which agents to import + conflict strategy."""
    upload_zone = rx.upload(
        rx.box(
            rx.vstack(
                rx.icon("upload", size=22, color="#888"),
                rx.text(
                    t("bundle_import_drop"),
                    font_size="12px", color="#888", text_align="center",
                ),
                spacing="2", align="center",
            ),
            padding="20px",
            border="1px dashed #555",
            border_radius="8px",
            width="100%",
            _hover={"border_color": "#d29922"},
        ),
        id="agent-bundle-upload",
        on_drop=AIState.handle_bundle_upload(rx.upload_files(upload_id="agent-bundle-upload")),
        accept={"application/zip": [".zip"]},
        multiple=False,
        border="none", padding="0", width="100%",
    )

    agent_selection = rx.cond(
        AIState.bundle_import_agents.length() > 0,
        rx.vstack(
            rx.text(t("bundle_import_desc"), font_size="11px", color="#aaa"),
            rx.box(
                rx.foreach(AIState.bundle_import_agents, _bundle_import_row),
                max_height="40vh",
                overflow_y="auto",
                width="100%",
                border="1px solid #333",
                border_radius="6px",
                padding="4px",
            ),
            rx.hstack(
                rx.text(t("bundle_conflict_label"), font_size="11px", color="#aaa"),
                _bundle_conflict_select(),
                spacing="2", align="center",
            ),
            spacing="2", width="100%",
        ),
    )

    error_box = rx.cond(
        AIState.bundle_import_error != "",
        rx.box(
            rx.hstack(
                rx.icon("circle-alert", size=14, color="#f88"),
                rx.text(AIState.bundle_import_error, font_size="11px", color="#f88"),
                spacing="2", align="center",
            ),
            padding="8px",
            border="1px solid #553",
            border_radius="6px",
            background="rgba(255,80,80,0.08)",
        ),
    )

    return rx.cond(
        AIState.bundle_import_open,
        rx.box(
            rx.box(
                on_click=AIState.close_bundle_import,
                position="fixed", top="0", left="0",
                width="100vw", height="100vh",
                bg="rgba(0,0,0,0.5)", z_index="1000",
            ),
            rx.box(
                rx.vstack(
                    rx.hstack(
                        rx.icon("package-open", size=18, color="#FFD700"),
                        rx.text(t("bundle_import_title"), font_weight="bold", font_size="15px", color="white"),
                        rx.spacer(),
                        rx.icon_button(
                            rx.icon("x", size=14),
                            on_click=AIState.close_bundle_import,
                            size="1", variant="ghost",
                        ),
                        align="center", width="100%",
                    ),
                    upload_zone,
                    error_box,
                    agent_selection,
                    rx.hstack(
                        rx.spacer(),
                        rx.button(
                            t("bundle_cancel"),
                            on_click=AIState.close_bundle_import,
                            variant="soft", color_scheme="gray", size="2",
                        ),
                        rx.button(
                            rx.icon("check", size=14),
                            t("bundle_import_btn"),
                            on_click=AIState.confirm_bundle_import,
                            disabled=AIState.bundle_import_selected.length() == 0,
                            variant="solid", color_scheme="green", size="2",
                        ),
                        spacing="2", width="100%",
                    ),
                    spacing="3", width="100%",
                ),
                background="#1a1a2e",
                border_radius="12px",
                border="1px solid var(--gray-a6)",
                padding="16px",
                width="540px",
                max_width="95vw",
                position="relative",
                z_index="1001",
            ),
            position="fixed", top="0", left="0",
            width="100vw", height="100vh",
            z_index="1000",
            display="flex",
            justify_content="center",
            align_items="center",
        ),
    )
