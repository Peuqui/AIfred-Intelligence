"""Agent-Editor: Audit-Tab — letzte Tool-Ausfuehrungen."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t
from .header import _editor_header

# No cell wraps, so no column gets squeezed below its content; with the short
# timestamp and the "Stufe" header the table fits the 750 px window.
_CELL = {"white_space": "nowrap"}


def _cell(content: rx.Component) -> rx.Component:
    return rx.table.cell(content, style=_CELL)


def _header_cell(key: str) -> rx.Component:
    return rx.table.column_header_cell(rx.text(t(key), font_size="11px"), style=_CELL)


def _audit_entry_row(entry: rx.Var) -> rx.Component:
    """Render a single audit log entry."""
    return rx.table.row(
        _cell(rx.text(entry["timestamp"], font_size="11px")),
        _cell(rx.text(entry["agent_id"], font_size="11px")),
        _cell(
            rx.text(
                entry["session_short"], font_size="11px", font_family="monospace",
                custom_attrs={"title": entry["session_id"]},
            ),
        ),
        _cell(rx.text(entry["source"], font_size="11px")),
        # Hover shows the tool's arguments, like the full id on the session.
        _cell(
            rx.text(
                entry["tool_name"], font_size="11px", font_weight="500",
                cursor="help",
                custom_attrs={"title": entry["args"]},
            ),
        ),
        _cell(rx.text(entry["tool_tier"], font_size="11px")),
        _cell(
            rx.cond(
                entry["success"] == "OK",
                rx.text("OK", font_size="11px", color="green"),
                rx.text("FAIL", font_size="11px", color="red"),
            )
        ),
        _cell(rx.text(entry["duration"], font_size="11px")),
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
                            _header_cell("audit_col_time"),
                            _header_cell("audit_col_agent"),
                            _header_cell("audit_col_session"),
                            _header_cell("audit_col_source"),
                            _header_cell("audit_col_tool"),
                            _header_cell("audit_col_tier"),
                            _header_cell("audit_col_status"),
                            _header_cell("audit_col_duration"),
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
            # Narrow screens (the page is capped at 95vw): scroll sideways
            # instead of cutting off the right-hand columns.
            overflow_x="auto",
            width="100%",
        ),
        spacing="3",
        width="100%",
        flex="1",
        min_height="0",
    )
