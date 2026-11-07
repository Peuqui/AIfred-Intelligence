"""Agent-Editor: Database-Tab — Dokumenten-Index (pro Dokument) + verwaiste Einträge."""

from __future__ import annotations

from typing import Any

import reflex as rx
from reflex.vars import ObjectVar
from reflex_components_lucide.icon import Icon

from ...state import AIState
from ...theme import COLORS
from ..helpers import confirm_delete_all, t
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
                    Icon.create("file-text", size=14, color=COLORS["accent_blue"]),
                    rx.text(t("db_documents"), font_size="14px", font_weight="bold", color=COLORS["text_soft"]),
                    rx.spacer(),
                    rx.badge(
                        AIState.db_documents.length(),
                        variant="soft",
                        color_scheme="orange",
                    ),
                    # confirm_clear_db toggles the question on and off
                    confirm_delete_all(
                        confirming=AIState.db_clear_confirm,
                        on_request=AIState.confirm_clear_db,
                        on_confirm=AIState.clear_db_index,
                        on_cancel=AIState.confirm_clear_db,
                        count=AIState.db_documents.length(),
                    ),
                    spacing="2",
                    width="100%",
                    align="center",
                ),

                _db_orphan_section(),

                # Document list
                rx.cond(
                    AIState.db_documents.length() > 0,
                    rx.vstack(
                        rx.foreach(
                            AIState.db_documents,
                            lambda doc: _indexed_doc_row(doc, "file-text", COLORS["accent_blue"]),
                        ),
                        spacing="0", width="100%",
                        background=COLORS["card_bg"],
                        border="1px solid var(--aifred-input-bg)",
                        border_radius="6px",
                    ),
                    rx.text(
                        t("db_no_entries"),
                        color=COLORS["text_secondary"],
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


def _indexed_doc_row(doc: ObjectVar[dict[str, Any]], icon: str, icon_color: str) -> rx.Component:
    """One indexed document (document list or orphan list): name, chunks, remove from index."""
    return rx.hstack(
        Icon.create(icon, size=14, color=icon_color),
        rx.vstack(
            rx.text(doc["filename"], font_size="12px", color="white"),
            rx.text(
                doc["total_chunks"].to(str) + t("db_chunks_suffix"),
                font_size="10px", color=COLORS["text_secondary"],
            ),
            spacing="0", align="start", flex="1",
        ),
        rx.tooltip(
            rx.icon_button(
                Icon.create("trash-2", size=12), size="1",
                variant="ghost", color_scheme="red",
                on_click=AIState.db_deindex_document(doc["filename"]),
                cursor="pointer",
            ),
            content=t("db_remove_from_index"),
        ),
        spacing="2", align="center", width="100%",
        padding="6px 8px",
        border_bottom="1px solid var(--aifred-input-bg)",
    )


def _db_orphan_section() -> rx.Component:
    """Collapsible section for documents indexed without a source file on disk."""
    return rx.box(
        rx.hstack(
            rx.icon_button(
                Icon.create(
                    rx.cond(AIState.db_orphans_visible, "chevron-down", "chevron-right"),
                    size=14,
                ),
                size="1", variant="ghost", color_scheme="gray",
                on_click=AIState.db_toggle_orphans,
                cursor="pointer",
            ),
            Icon.create("brush-cleaning", size=14, color=COLORS["accent_warning"]),
            rx.text(
                t("db_orphans_title"),
                font_size="13px", font_weight="bold", color=COLORS["accent_warning"],
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
                    Icon.create("trash-2", size=12),
                    t("db_orphans_delete_all"),
                    size="1", variant="soft", color_scheme="red",
                    on_click=AIState.db_delete_all_orphans,
                    cursor="pointer",
                ),
            ),
            spacing="2", align="center", width="100%",
            padding="6px 8px",
            background=COLORS["card_bg"],
            border="1px solid var(--aifred-input-bg)",
            border_radius="6px",
        ),
        rx.cond(
            AIState.db_orphans_visible,
            rx.cond(
                AIState.db_orphans.length() > 0,
                rx.vstack(
                    rx.foreach(AIState.db_orphans, lambda doc: _indexed_doc_row(doc, "file-x-2", COLORS["accent_warning"])),
                    spacing="0", width="100%",
                    margin_top="4px",
                    background=COLORS["card_bg"],
                    border="1px solid var(--aifred-input-bg)",
                    border_radius="6px",
                    max_height="240px",
                    overflow_y="auto",
                ),
                rx.text(
                    t("db_orphans_none"),
                    font_size="12px", color=COLORS["text_muted"],
                    padding="12px 8px",
                    margin_top="4px",
                    background=COLORS["card_bg"],
                    border="1px solid var(--aifred-input-bg)",
                    border_radius="6px",
                ),
            ),
        ),
        width="100%",
    )
