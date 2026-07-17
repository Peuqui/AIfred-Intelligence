"""Document-Manager Vollbild-Page (Datei-Explorer + Preview + Upload)."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t, overlay_scaffold


# Document Manager Header — gemeinsame Groessen fuer Icons + Buttons,
# damit der Header als Ganzes skaliert (Radix hat keine Halbschritte
# zwischen size="1" und size="2").
from typing import Literal

DOC_HEADER_ICON_SIZE = 16
DOC_HEADER_BUTTON_SIZE: Literal["1", "2", "3", "4"] = "2"


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
        # Size (files) or recursive file count (folders)
        rx.cond(
            ~is_folder,
            rx.text(item["size"].to(str), font_size="10px", color="#666", min_width="60px"),
            rx.text(
                item["file_count"].to(str) + rx.cond(
                    AIState.ui_language == "de",
                    rx.cond(item["file_count"].to(int) == 1, " Datei", " Dateien"),
                    rx.cond(item["file_count"].to(int) == 1, " file", " files"),
                ),
                font_size="10px",
                color=rx.cond(item["file_count"].to(int) == 0, "#555", "#888"),
                min_width="60px",
            ),
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


def document_manager_page() -> rx.Component:
    """Document-Manager Vollbild-Page (vormals document_manager_modal).

    Lebt seit dem Multi-Route-Split auf der Route ``/documents`` —
    automatisches Code-Splitting durch Reflex+React-Router-7.
    """
    return overlay_scaffold(
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
                    custom_attrs={"data-modal-close": "true"},
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
                        rx.tooltip(
                            rx.icon_button(
                                rx.icon("database", size=DOC_HEADER_ICON_SIZE), size=DOC_HEADER_BUTTON_SIZE,
                                variant="ghost", color_scheme="green",
                                on_click=AIState.doc_index_folder, cursor="pointer",
                            ),
                            content=rx.cond(
                                AIState.ui_language == "de",
                                "Alle Dateien im Ordner (rekursiv) indexieren",
                                "Index all files in folder (recursive)",
                            ),
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
                                    custom_attrs={"data-modal-close": "true"},
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
        backdrop_color="#000000",
    )
