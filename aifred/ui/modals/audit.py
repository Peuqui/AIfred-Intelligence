"""Audit-Log-Modal: letzte Tool-Ausfuehrungen."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t, overlay_scaffold


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

    return overlay_scaffold(
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
                        custom_attrs={"data-modal-close": "true"},
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
        open_var=AIState.audit_log_open,
        backdrop_color="rgba(0,0,0,0.5)",
        backdrop_fixed=True,
    )
