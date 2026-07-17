"""Agent-Bundle Export-/Import-Modals (ZIP)."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t, overlay_scaffold


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
    return overlay_scaffold(
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
                        custom_attrs={"data-modal-close": "true"},
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
        open_var=AIState.bundle_export_open,
        backdrop_color="rgba(0,0,0,0.5)",
        backdrop_fixed=True,
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
        on_drop=AIState.handle_bundle_upload(rx.upload_files(upload_id="agent-bundle-upload")),  # type: ignore[arg-type]
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

    return overlay_scaffold(
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
                        custom_attrs={"data-modal-close": "true"},
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
        open_var=AIState.bundle_import_open,
        backdrop_color="rgba(0,0,0,0.5)",
        backdrop_fixed=True,
    )
