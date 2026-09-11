"""Agent-Editor: Database-Tab — Dokumenten-Index (pro Dokument) + verwaiste Einträge."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t
from .header import _editor_header


def _database_view() -> rx.Component:
    """Database tab: the document index, one row per document, plus orphan cleanup."""
    return rx.vstack(
        _editor_header(),

        # Scrollable content
        rx.box(
            rx.vstack(
                # Title + document count + clear-all
                rx.hstack(
                    rx.icon("file-text", size=14, color="#4da6ff"),
                    rx.text(t("db_documents"), font_size="14px", font_weight="bold", color="#ddd"),
                    rx.spacer(),
                    rx.badge(
                        AIState.db_documents.length(),  # type: ignore[union-attr]
                        variant="soft",
                        color_scheme="orange",
                    ),
                    # Clear all button (with confirmation)
                    rx.cond(
                        AIState.db_documents.length() > 0,  # type: ignore[union-attr]
                        rx.cond(
                            AIState.db_clear_confirm,
                            # Confirmation: two buttons
                            rx.hstack(
                                rx.button(
                                    t("db_really_delete"),
                                    on_click=AIState.clear_db_index,
                                    size="1",
                                    variant="solid",
                                    color_scheme="red",
                                    cursor="pointer",
                                ),
                                rx.button(
                                    t("db_cancel"),
                                    on_click=AIState.confirm_clear_db,
                                    size="1",
                                    variant="soft",
                                    color_scheme="gray",
                                    cursor="pointer",
                                ),
                                spacing="1",
                            ),
                            # Normal: eraser icon
                            rx.tooltip(
                                rx.icon_button(
                                    rx.icon("eraser", size=16),
                                    on_click=AIState.confirm_clear_db,
                                    size="2",
                                    variant="soft",
                                    color_scheme="red",
                                    cursor="pointer",
                                ),
                                content=t("db_clear_all"),
                            ),
                        ),
                    ),
                    spacing="2",
                    width="100%",
                    align="center",
                ),

                _db_orphan_section(),

                # Document list
                rx.cond(
                    AIState.db_documents.length() > 0,  # type: ignore[union-attr]
                    rx.vstack(
                        rx.foreach(
                            AIState.db_documents,
                            lambda doc: _indexed_doc_row(doc, "file-text", "#4da6ff"),
                        ),
                        spacing="0", width="100%",
                        background="#161616",
                        border="1px solid #2a2a2a",
                        border_radius="6px",
                    ),
                    rx.text(
                        t("db_no_entries"),
                        color="#888",
                        font_size="13px",
                    ),
                ),

                spacing="3",
                width="100%",
            ),
            flex="1",
            overflow_y="auto",
            width="100%",
        ),

        spacing="3",
        width="100%",
        flex="1",
        min_height="0",
    )


def _indexed_doc_row(doc: rx.Var, icon: str, icon_color: str) -> rx.Component:
    """One indexed document (document list or orphan list): name, chunks, remove from index."""
    return rx.hstack(
        rx.icon(icon, size=14, color=icon_color),
        rx.vstack(
            rx.text(doc["filename"], font_size="12px", color="white"),
            rx.text(
                doc["total_chunks"].to(str) + t("db_chunks_suffix"),
                font_size="10px", color="#888",
            ),
            spacing="0", align="start", flex="1",
        ),
        rx.tooltip(
            rx.icon_button(
                rx.icon("trash-2", size=12), size="1",
                variant="ghost", color_scheme="red",
                on_click=AIState.db_deindex_document(doc["filename"]),
                cursor="pointer",
            ),
            content=t("db_remove_from_index"),
        ),
        spacing="2", align="center", width="100%",
        padding="6px 8px",
        border_bottom="1px solid #2a2a2a",
    )


def _db_orphan_section() -> rx.Component:
    """Collapsible section for documents indexed without a source file on disk."""
    return rx.box(
        rx.hstack(
            rx.icon_button(
                rx.icon(
                    rx.cond(AIState.db_orphans_visible, "chevron-down", "chevron-right"),
                    size=14,
                ),
                size="1", variant="ghost", color_scheme="gray",
                on_click=AIState.db_toggle_orphans,
                cursor="pointer",
            ),
            rx.icon("brush-cleaning", size=14, color="#d29922"),
            rx.text(
                t("db_orphans_title"),
                font_size="13px", font_weight="bold", color="#d29922",
                cursor="pointer",
                on_click=AIState.db_toggle_orphans,
            ),
            rx.cond(
                AIState.db_orphans_visible & (AIState.db_orphans.length() > 0),
                rx.badge(
                    AIState.db_orphans.length().to(str),
                    variant="soft", color_scheme="orange", font_size="10px",
                ),
            ),
            rx.spacer(),
            rx.cond(
                AIState.db_orphans_visible & (AIState.db_orphans.length() > 0),
                rx.button(
                    rx.icon("trash-2", size=12),
                    t("db_orphans_delete_all"),
                    size="1", variant="soft", color_scheme="red",
                    on_click=AIState.db_delete_all_orphans,
                    cursor="pointer",
                ),
            ),
            spacing="2", align="center", width="100%",
            padding="6px 8px",
            background="#161616",
            border="1px solid #2a2a2a",
            border_radius="6px",
        ),
        rx.cond(
            AIState.db_orphans_visible,
            rx.cond(
                AIState.db_orphans.length() > 0,
                rx.vstack(
                    rx.foreach(AIState.db_orphans, lambda doc: _indexed_doc_row(doc, "file-x-2", "#d29922")),
                    spacing="0", width="100%",
                    margin_top="4px",
                    background="#161616",
                    border="1px solid #2a2a2a",
                    border_radius="6px",
                    max_height="240px",
                    overflow_y="auto",
                ),
                rx.text(
                    t("db_orphans_none"),
                    font_size="12px", color="#666",
                    padding="12px 8px",
                    margin_top="4px",
                    background="#161616",
                    border="1px solid #2a2a2a",
                    border_radius="6px",
                ),
            ),
        ),
        width="100%",
    )
