"""Agent-Editor: Memory-Tab — Eintraege mit Typ-Filter."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...lib.config import AGENT_MEMORY_SUMMARY_MAX_CHARS
from ...state import AIState
from ..helpers import confirm_delete_all, t
from .header import _editor_header


def _memory_entry_text(entry: rx.Var) -> rx.Component:
    """Summary and content of an entry, read-only."""
    return rx.fragment(
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
                style={"white_space": "pre-wrap"},
            ),
        ),
    )


def _memory_entry_editor() -> rx.Component:
    """Summary and content of the entry being edited, as input fields."""
    return rx.vstack(
        # One line: the summary is the entry's index line and search text.
        rx.input(
            value=AIState.memory_edit_summary,
            on_change=AIState.set_memory_edit_summary,
            max_length=AGENT_MEMORY_SUMMARY_MAX_CHARS,
            size="2",
            color_scheme="orange",
            width="100%",
        ),
        rx.text_area(
            value=AIState.memory_edit_content,
            on_change=AIState.set_memory_edit_content,
            rows="6",
            color_scheme="orange",
            width="100%",
        ),
        rx.hstack(
            rx.button(
                t("agent_editor_cancel"),
                on_click=AIState.cancel_memory_edit,
                size="1",
                variant="soft",
                color_scheme="gray",
                cursor="pointer",
            ),
            rx.button(
                t("agent_editor_save"),
                on_click=AIState.save_memory_edit,
                size="1",
                color_scheme="orange",
                cursor="pointer",
            ),
            spacing="2",
            justify="end",
            width="100%",
        ),
        spacing="2",
        padding_top="6px",
        width="100%",
    )


def _memory_entry_row(entry: rx.Var) -> rx.Component:
    """Render a single memory entry with expandable content."""
    return rx.box(
        rx.hstack(
            rx.badge(
                entry["type"],
                variant="soft",
                color_scheme=rx.cond(
                    entry["type"] == "session_summary", "amber",
                    rx.cond(entry["type"] == "sermon", "purple",
                    rx.cond(entry["type"] == "insight", "green", "gray")),
                ),
                font_size="10px",
            ),
            rx.text(entry["date"], font_size="11px", color="#888"),
            rx.spacer(),
            rx.icon_button(
                rx.icon("pencil", size=12),
                on_click=AIState.start_memory_edit(entry["id"]),
                size="1",
                variant="ghost",
                color_scheme="gray",
                cursor="pointer",
            ),
            rx.icon_button(
                rx.icon("trash-2", size=12),
                on_click=AIState.delete_memory_entry(entry["id"]),
                size="1",
                variant="ghost",
                color_scheme="red",
                cursor="pointer",
            ),
            width="100%",
            align="center",
        ),
        rx.cond(
            AIState.memory_edit_id == entry["id"],
            _memory_entry_editor(),
            _memory_entry_text(entry),
        ),
        padding="10px 12px",
        background="rgba(255,255,255,0.03)",
        border_radius="6px",
        border="1px solid #333",
        width="100%",
    )


def _memory_view() -> rx.Component:
    """Memory tab: agent dropdown + type filter + entries."""
    return rx.vstack(
        _editor_header(),

        # Scrollable content
        rx.box(
            rx.vstack(
                # Agent dropdown
                rx.hstack(
                    rx.select(
                        AIState.memory_agent_dropdown_options,
                        value=AIState.memory_browser_agent_display,
                        on_change=AIState.select_memory_agent,
                        placeholder=t("agent_editor_select_agent"),
                        size="2",
                        color_scheme="orange",
                        width="100%",
                    ),
                    spacing="2",
                    width="100%",
                    align="center",
                ),

                # Filter buttons (only when agent selected)
                rx.cond(
                    AIState.memory_browser_agent != "",
                    rx.hstack(
                        rx.button(
                            t("memory_filter_all"),
                            on_click=AIState.set_memory_filter("all"),
                            size="1",
                            variant=rx.cond(AIState.memory_browser_filter == "all", "solid", "soft"),
                            color_scheme=rx.cond(AIState.memory_browser_filter == "all", "orange", "gray"),
                            cursor="pointer",
                        ),
                        rx.button(
                            "Session",
                            on_click=AIState.set_memory_filter("session"),
                            size="1",
                            variant=rx.cond(AIState.memory_browser_filter == "session", "solid", "soft"),
                            color_scheme=rx.cond(AIState.memory_browser_filter == "session", "amber", "gray"),
                            cursor="pointer",
                        ),
                        rx.button(
                            "Agent",
                            on_click=AIState.set_memory_filter("agent"),
                            size="1",
                            variant=rx.cond(AIState.memory_browser_filter == "agent", "solid", "soft"),
                            color_scheme=rx.cond(AIState.memory_browser_filter == "agent", "green", "gray"),
                            cursor="pointer",
                        ),
                        rx.spacer(),
                        rx.badge(
                            AIState.filtered_memory_entries.length(),  # type: ignore[union-attr]
                            variant="soft",
                            color_scheme="orange",
                        ),
                        # All entries of the agent, whatever the filter shows
                        confirm_delete_all(
                            confirming=AIState.memory_confirm_clear,
                            on_request=AIState.request_clear_memory,
                            on_confirm=AIState.clear_browsed_memory,
                            on_cancel=AIState.cancel_clear_memory,
                            count=AIState.memory_browser_entries.length(),  # type: ignore[union-attr]
                        ),
                        spacing="2",
                        align="center",
                        width="100%",
                        flex_wrap="wrap",
                    ),
                ),

                # Entries
                rx.cond(
                    AIState.memory_browser_agent == "",
                    rx.text(
                        t("memory_select_hint"),
                        color="#888",
                        font_size="13px",
                        padding_top="20px",
                        text_align="center",
                    ),
                    rx.cond(
                        AIState.filtered_memory_entries.length() > 0,  # type: ignore[union-attr]
                        rx.vstack(
                            rx.foreach(
                                AIState.filtered_memory_entries,
                                _memory_entry_row,
                            ),
                            spacing="2",
                            width="100%",
                        ),
                        rx.text(
                            t("memory_no_entries"),
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
