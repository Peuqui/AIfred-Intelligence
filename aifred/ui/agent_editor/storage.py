"""Agent-Editor: Storage-Tab — Agent-Dateiablage browsen."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t
from .header import _editor_header


def _storage_entry_row(entry: rx.Var) -> rx.Component:
    """Eine Datei-Zeile im Speicher-Tab: Name + Typ-Badge + Datum/Größe,
    rechts die Aktionen (Öffnen / In Showcase übernehmen / Löschen mit
    zweistufiger Bestätigung)."""
    return rx.box(
        rx.hstack(
            rx.checkbox(
                checked=AIState.storage_selected.contains(entry["id"]),  # type: ignore[union-attr]
                on_change=AIState.storage_toggle_select(entry["id"]),
                color_scheme="orange",
                flex_shrink="0",
            ),
            rx.icon(
                rx.cond(entry["kind"] == "export", "file-text", "code"),
                size=16, color="#d98030", flex_shrink="0",
            ),
            rx.vstack(
                rx.text(
                    entry["name"],
                    font_size="13px", color="white",
                    style={
                        "white_space": "nowrap", "overflow": "hidden",
                        "text_overflow": "ellipsis", "max_width": "100%",
                    },
                ),
                rx.hstack(
                    rx.badge(
                        rx.cond(
                            entry["kind"] == "export",
                            t("storage_kind_export"), t("storage_kind_sandbox"),
                        ),
                        variant="soft",
                        color_scheme=rx.cond(entry["kind"] == "export", "blue", "gray"),
                        size="1",
                    ),
                    rx.text(entry["mtime"], font_size="11px", color="#888"),
                    rx.text(entry["size_kb"].to_string() + " KB", font_size="11px", color="#888"),
                    spacing="2", align="center",
                ),
                spacing="1", align="start", flex_grow="1",
                style={"min_width": "0"},
            ),
            rx.spacer(),
            rx.cond(
                AIState.storage_confirm_delete_id == entry["id"],
                rx.hstack(
                    rx.button(
                        t("storage_delete_confirm"),
                        on_click=AIState.storage_confirm_delete(entry["id"]),
                        size="1", variant="solid", color_scheme="red", cursor="pointer",
                    ),
                    rx.button(
                        t("db_cancel"),
                        on_click=AIState.storage_cancel_delete,
                        size="1", variant="soft", color_scheme="gray", cursor="pointer",
                    ),
                    spacing="1",
                ),
                rx.hstack(
                    rx.tooltip(
                        rx.icon_button(
                            rx.icon("external-link", size=14),
                            on_click=AIState.storage_open_file(entry["url"]),
                            size="1", variant="soft", color_scheme="blue", cursor="pointer",
                        ),
                        content=t("storage_open"),
                    ),
                    rx.cond(
                        entry["is_html"] & (entry["kind"] == "export"),
                        rx.tooltip(
                            rx.icon_button(
                                rx.icon("tv", size=14),
                                on_click=AIState.storage_copy_to_showcase(entry["id"]),
                                size="1", variant="soft", color_scheme="orange", cursor="pointer",
                            ),
                            content=t("storage_to_showcase"),
                        ),
                    ),
                    rx.tooltip(
                        rx.icon_button(
                            rx.icon("trash-2", size=14),
                            on_click=AIState.storage_request_delete(entry["id"]),
                            size="1", variant="soft", color_scheme="red", cursor="pointer",
                        ),
                        content=t("storage_delete"),
                    ),
                    spacing="1",
                ),
            ),
            width="100%", align="center", spacing="2",
        ),
        padding="8px 10px",
        border="1px solid #333",
        border_radius="8px",
        background_color="#222",
        width="100%",
    )


def _storage_view() -> rx.Component:
    """Speicher-Tab: lokale, ungeräumte Datei-Stores (Chat-Exporte +
    Sandbox-Outputs) manuell verwalten — öffnen, in den GitHub-Showcase
    übernehmen, löschen. Kein TTL; bewusste Kuratierung."""
    return rx.vstack(
        _editor_header(),
        rx.box(
            rx.vstack(
                rx.hstack(
                    rx.text(
                        t("storage_intro"),
                        font_size="12px", color="#aaa", flex_grow="1",
                    ),
                    rx.badge(
                        AIState.storage_files.length(),  # type: ignore[union-attr]
                        variant="soft", color_scheme="orange",
                    ),
                    rx.tooltip(
                        rx.icon_button(
                            rx.icon("refresh-cw", size=14),
                            on_click=AIState.refresh_storage_files,
                            size="1", variant="soft", color_scheme="gray", cursor="pointer",
                        ),
                        content=t("storage_refresh"),
                    ),
                    spacing="2", width="100%", align="center",
                ),
                # Bulk-Aktionen: alle auswählen / ausgewählte löschen / alles löschen
                rx.cond(
                    AIState.storage_files.length() > 0,  # type: ignore[union-attr]
                    rx.hstack(
                        rx.button(
                            rx.icon("check-check", size=14),
                            t("storage_select_all"),
                            on_click=AIState.storage_select_all,
                            size="1", variant="soft", color_scheme="gray", cursor="pointer",
                        ),
                        rx.cond(
                            AIState.storage_selected.length() > 0,  # type: ignore[union-attr]
                            rx.button(
                                rx.icon("trash-2", size=14),
                                t("storage_delete_selected")
                                + " (" + AIState.storage_selected.length().to_string() + ")",  # type: ignore[union-attr]
                                on_click=AIState.storage_delete_selected,
                                size="1", variant="solid", color_scheme="red", cursor="pointer",
                            ),
                        ),
                        rx.spacer(),
                        rx.cond(
                            AIState.storage_confirm_clear,
                            rx.hstack(
                                rx.button(
                                    t("storage_clear_confirm"),
                                    on_click=AIState.storage_clear_all,
                                    size="1", variant="solid", color_scheme="red", cursor="pointer",
                                ),
                                rx.button(
                                    t("db_cancel"),
                                    on_click=AIState.storage_cancel_clear,
                                    size="1", variant="soft", color_scheme="gray", cursor="pointer",
                                ),
                                spacing="1",
                            ),
                            rx.tooltip(
                                rx.icon_button(
                                    rx.icon("eraser", size=16),
                                    on_click=AIState.storage_request_clear,
                                    size="1", variant="soft", color_scheme="red", cursor="pointer",
                                ),
                                content=t("storage_clear_all"),
                            ),
                        ),
                        spacing="2", width="100%", align="center",
                    ),
                ),
                rx.cond(
                    AIState.storage_files.length() > 0,  # type: ignore[union-attr]
                    rx.vstack(
                        rx.foreach(AIState.storage_files, _storage_entry_row),
                        spacing="2", width="100%",
                    ),
                    rx.text(
                        t("storage_empty"),
                        color="#888", font_size="13px",
                        padding_top="20px", text_align="center",
                    ),
                ),
                spacing="3", width="100%",
            ),
            overflow_y="auto", flex_grow="1", width="100%", padding_right="6px",
        ),
        spacing="3", width="100%", height="100%",
    )
