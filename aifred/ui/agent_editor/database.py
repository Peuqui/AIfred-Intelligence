"""Agent-Editor: Database-Tab — System-Collections browsen + loeschen."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t
from .header import _editor_header


def _database_view() -> rx.Component:
    """Database tab: Research Cache + Documents with same browse/delete UI as Memory."""
    return rx.vstack(
        _editor_header(),

        # Scrollable content
        rx.box(
            rx.vstack(
                # Collection selector buttons + clear-all
                rx.hstack(
                    rx.button(
                        rx.icon("search", size=14),
                        " ", t("db_research_cache"),
                        on_click=AIState.select_db_collection("research_cache"),
                        size="2",
                        variant=rx.cond(
                            AIState.db_browser_collection == "research_cache",
                            "solid", "soft",
                        ),
                        color_scheme="orange",
                        cursor="pointer",
                        flex_shrink="0",
                    ),
                    rx.button(
                        rx.icon("file-text", size=14),
                        " ", t("db_documents"),
                        on_click=AIState.select_db_collection("aifred_documents"),
                        size="2",
                        variant=rx.cond(
                            AIState.db_browser_collection == "aifred_documents",
                            "solid", "soft",
                        ),
                        color_scheme="orange",
                        cursor="pointer",
                        flex_shrink="0",
                    ),
                    rx.spacer(),
                    # Entry count badge
                    rx.cond(
                        AIState.db_browser_collection != "",
                        rx.badge(
                            AIState.db_browser_entries.length(),  # type: ignore[union-attr]
                            variant="soft",
                            color_scheme="orange",
                        ),
                    ),
                    # Clear all button (with confirmation)
                    rx.cond(
                        AIState.db_browser_entries.length() > 0,  # type: ignore[union-attr]
                        rx.cond(
                            AIState.db_clear_confirm,
                            # Confirmation: two buttons
                            rx.hstack(
                                rx.button(
                                    t("db_really_delete"),
                                    on_click=AIState.clear_db_collection,
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

                # Orphan section — only meaningful for the documents collection
                rx.cond(
                    AIState.db_browser_collection == "aifred_documents",
                    _db_orphan_section(),
                ),

                # Entries list
                rx.cond(
                    AIState.db_browser_collection == "",
                    rx.text(
                        t("db_select_hint"),
                        color="#888",
                        font_size="13px",
                        padding_top="20px",
                        text_align="center",
                    ),
                    rx.cond(
                        AIState.db_browser_entries.length() > 0,  # type: ignore[union-attr]
                        rx.vstack(
                            rx.foreach(
                                AIState.db_browser_entries,
                                _db_entry_row,
                            ),
                            spacing="2",
                            width="100%",
                        ),
                        rx.text(
                            t("db_no_entries"),
                            color="#888",
                            font_size="13px",
                        ),
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


def _db_orphan_row(orphan: rx.Var) -> rx.Component:
    """Single orphaned-document row in the cleanup section."""
    return rx.hstack(
        rx.icon("file-x-2", size=14, color="#d29922"),
        rx.vstack(
            rx.text(orphan["filename"], font_size="12px", color="white"),
            rx.text(
                orphan["total_chunks"].to(str) + rx.cond(
                    AIState.ui_language == "de", " Chunks", " chunks"),
                font_size="10px", color="#888",
            ),
            spacing="0", align="start", flex="1",
        ),
        rx.tooltip(
            rx.icon_button(
                rx.icon("trash-2", size=12), size="1",
                variant="ghost", color_scheme="red",
                on_click=AIState.db_delete_orphan(orphan["filename"]),
                cursor="pointer",
            ),
            content=rx.cond(
                AIState.ui_language == "de",
                "Aus Index loeschen",
                "Delete from index",
            ),
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
                rx.cond(
                    AIState.ui_language == "de",
                    "Verwaiste Index-Eintraege",
                    "Orphaned index entries",
                ),
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
                    rx.cond(
                        AIState.ui_language == "de",
                        "Alle loeschen",
                        "Delete all",
                    ),
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
                    rx.foreach(AIState.db_orphans, _db_orphan_row),
                    spacing="0", width="100%",
                    margin_top="4px",
                    background="#161616",
                    border="1px solid #2a2a2a",
                    border_radius="6px",
                    max_height="240px",
                    overflow_y="auto",
                ),
                rx.text(
                    rx.cond(
                        AIState.ui_language == "de",
                        "Keine verwaisten Eintraege.",
                        "No orphaned entries.",
                    ),
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


def _db_entry_row(entry: rx.Var) -> rx.Component:
    """Render a single database entry — same style as memory entries."""
    return rx.box(
        rx.hstack(
            rx.badge(
                entry["type"],
                variant="soft",
                color_scheme=rx.cond(
                    entry["type"] == "cache", "orange",
                    rx.cond(entry["type"] == "document", "blue", "gray"),
                ),
                font_size="10px",
            ),
            rx.text(entry["date"], font_size="11px", color="#888"),
            rx.spacer(),
            rx.icon_button(
                rx.icon("trash-2", size=12),
                on_click=AIState.delete_db_entry(entry["id"]),
                size="1",
                variant="ghost",
                color_scheme="red",
                cursor="pointer",
            ),
            width="100%",
            align="center",
        ),
        rx.text(
            entry["summary"],
            font_size="15px",
            color="#ddd",
            font_weight="500",
            padding_top="4px",
        ),
        rx.cond(
            entry["content"] != entry["summary"],
            rx.text(
                entry["content"],
                font_size="14px",
                color="#aaa",
                padding_top="4px",
                style={"white_space": "pre-wrap", "max_height": "150px", "overflow_y": "auto"},
            ),
        ),
        padding="10px 12px",
        background="rgba(255,255,255,0.03)",
        border_radius="6px",
        border="1px solid #333",
        width="100%",
    )
