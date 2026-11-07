"""Agent-Editor: Storage-Tab — Agent-Dateiablage browsen."""

from __future__ import annotations

from typing import Any

import reflex as rx
from reflex.vars import ObjectVar
from reflex_components_lucide.icon import Icon

from ...state import AIState
from ...theme import COLORS
from ..helpers import bulk_delete_bar, t
from .header import _editor_header


def _storage_entry_row(entry: ObjectVar[dict[str, Any]]) -> rx.Component:
    """Eine Datei-Zeile im Speicher-Tab: Name + Typ-Badge + Datum/Größe,
    rechts die Aktionen (Öffnen / In Showcase übernehmen / Löschen mit
    zweistufiger Bestätigung)."""
    return rx.box(
        rx.hstack(
            rx.checkbox(
                checked=AIState.storage_selected.contains(entry["id"]),
                on_change=AIState.storage_toggle_select(entry["id"]),
                color_scheme="orange",
                flex_shrink="0",
            ),
            Icon.create(
                rx.cond(entry["kind"] == "export", "file-text", "code"),
                size=16, color=COLORS["primary"], flex_shrink="0",
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
                        color_scheme=rx.cond(entry["kind"] == "export", "amber", "gray"),
                        size="1",
                    ),
                    rx.text(entry["mtime"], font_size="11px", color=COLORS["text_secondary"]),
                    rx.text(entry["size_kb"].to_string() + " KB", font_size="11px", color=COLORS["text_secondary"]),
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
                            Icon.create("external-link", size=14),
                            on_click=AIState.storage_open_file(entry["url"]),
                            size="1", variant="soft", color_scheme="orange", cursor="pointer",
                        ),
                        content=t("storage_open"),
                    ),
                    rx.cond(
                        entry["is_html"] & (entry["kind"] == "export"),
                        rx.tooltip(
                            rx.icon_button(
                                Icon.create("tv", size=14),
                                on_click=AIState.storage_copy_to_showcase(entry["id"]),
                                size="1", variant="soft", color_scheme="orange", cursor="pointer",
                            ),
                            content=t("storage_to_showcase"),
                        ),
                    ),
                    rx.tooltip(
                        rx.icon_button(
                            Icon.create("trash-2", size=14),
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
        border="1px solid var(--aifred-border)",
        border_radius="8px",
        background_color=COLORS["card_bg"],
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
                        font_size="12px", color=COLORS["text_secondary"], flex_grow="1",
                    ),
                    rx.badge(
                        AIState.storage_files.length(),
                        variant="soft", color_scheme="orange",
                    ),
                    rx.tooltip(
                        rx.icon_button(
                            Icon.create("refresh-cw", size=14),
                            on_click=AIState.refresh_storage_files,
                            size="1", variant="soft", color_scheme="gray", cursor="pointer",
                        ),
                        content=t("storage_refresh"),
                    ),
                    spacing="2", width="100%", align="center",
                ),
                # Bulk-Aktionen: alle auswählen / ausgewählte löschen / alles löschen
                rx.cond(
                    AIState.storage_files.length() > 0,
                    bulk_delete_bar(
                        total_count=AIState.storage_files.length(),
                        selected_count=AIState.storage_selected.length(),
                        on_select_all=AIState.storage_select_all,
                        on_clear_selection=AIState.storage_clear_selection,
                        on_delete_selected=AIState.storage_delete_selected,
                        confirming=AIState.storage_confirm_clear,
                        on_request_delete_all=AIState.storage_request_clear,
                        on_confirm_delete_all=AIState.storage_clear_all,
                        on_cancel_delete_all=AIState.storage_cancel_clear,
                    ),
                ),
                rx.cond(
                    AIState.storage_files.length() > 0,
                    rx.vstack(
                        rx.foreach(AIState.storage_files, _storage_entry_row),
                        spacing="2", width="100%",
                    ),
                    rx.text(
                        t("storage_empty"),
                        color=COLORS["text_secondary"], font_size="13px",
                        padding_top="20px", text_align="center",
                    ),
                ),
                spacing="3", width="100%",
            ),
            overflow_y="auto", flex_grow="1", width="100%", padding_right="6px",
        ),
        spacing="3", width="100%", height="100%",
    )
