"""Agent-Editor: Audit-Tab — letzte Tool-Ausfuehrungen."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t
from .header import _editor_header


def _audit_entry_row(entry: rx.Var) -> rx.Component:
    """Render a single audit log entry."""
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


def _audit_view() -> rx.Component:
    """Audit tab: security audit log table."""
    return rx.vstack(
        _editor_header(),
        rx.box(
            rx.vstack(
                rx.text(t("audit_log_subtitle"), font_size="11px", color="gray"),
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
                        rx.foreach(AIState.audit_log_entries, _audit_entry_row),
                    ),
                    width="100%",
                    size="1",
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
