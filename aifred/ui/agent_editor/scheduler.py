"""Agent-Editor: Scheduler-Tab — Jobs (cron/interval/once) verwalten."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t, clickable_tip
from .header import _editor_header


def _scheduler_job_row(job: rx.Var) -> rx.Component:
    """Render a single scheduler job as collapsible card with full details."""
    return rx.box(
        # Header row — always visible
        rx.hstack(
            rx.switch(
                checked=job["enabled"].to(bool),
                on_change=lambda _: AIState.toggle_scheduler_job(job["job_id"]),
                size="1",
            ),
            rx.text(
                job["name"],
                font_size="14px",
                font_weight="500",
                color=rx.cond(job["enabled"], "white", "#666"),
                flex="1",
                min_width="0",
                overflow="hidden",
                text_overflow="ellipsis",
                white_space="nowrap",
            ),
            rx.badge(job["type_display"], variant="soft", color_scheme="blue", font_size="10px"),
            rx.icon_button(
                rx.icon("pencil", size=12),
                on_click=AIState.edit_scheduler_job(job["job_id"]),
                size="1",
                variant="ghost",
                color_scheme="orange",
                cursor="pointer",
            ),
            rx.icon_button(
                rx.icon("trash-2", size=12),
                on_click=AIState.delete_scheduler_job(job["job_id"]),
                size="1",
                variant="ghost",
                color_scheme="red",
                cursor="pointer",
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        # Details — always shown
        rx.vstack(
            rx.hstack(
                rx.text(t("sched_label_schedule"), ":", font_size="12px", color="#888", min_width="90px"),
                rx.text(job["schedule_display"], font_size="12px", color="#ccc"),
                spacing="2",
            ),
            rx.hstack(
                rx.text("Agent:", font_size="12px", color="#888", min_width="90px"),
                rx.text(job["agent_display"], font_size="12px", color="#ccc"),
                spacing="2",
            ),
            rx.hstack(
                rx.text(t("sched_label_delivery"), ":", font_size="12px", color="#888", min_width="90px"),
                rx.text(job["delivery_display"], font_size="12px", color="#ccc"),
                spacing="2",
            ),
            rx.cond(
                job["webhook_url"] != "",
                rx.hstack(
                    rx.text("Webhook:", font_size="12px", color="#888", min_width="90px"),
                    rx.text(job["webhook_url"], font_size="12px", color="#ccc"),
                    spacing="2",
                ),
            ),
            rx.hstack(
                rx.text(t("sched_label_tier"), ":", font_size="12px", color="#888", min_width="90px"),
                rx.text(job["max_tier"], font_size="12px", color="#ccc"),
                spacing="2",
            ),
            rx.cond(
                job["next_run"] != "",
                rx.hstack(
                    rx.text(t("sched_next"), ":", font_size="12px", color="#888", min_width="90px"),
                    rx.text(job["next_run"], font_size="12px", color="#ccc"),
                    spacing="2",
                ),
            ),
            rx.cond(
                job["last_run"] != "",
                rx.hstack(
                    rx.text(t("sched_last"), ":", font_size="12px", color="#888", min_width="90px"),
                    rx.text(job["last_run"], font_size="12px", color="#ccc"),
                    spacing="2",
                ),
            ),
            rx.hstack(
                rx.text(t("sched_created"), ":", font_size="12px", color="#888", min_width="90px"),
                rx.text(job["created_at"], font_size="12px", color="#ccc"),
                spacing="2",
            ),
            rx.cond(
                job["retry_count"] != "0",
                rx.hstack(
                    rx.text(t("sched_retries"), ":", font_size="12px", color="#888", min_width="90px"),
                    rx.text(job["retry_count"], font_size="12px", color="#ff6600"),
                    spacing="2",
                ),
            ),
            rx.hstack(
                rx.text(t("sched_label_message"), ":", font_size="12px", color="#888", min_width="90px"),
                rx.text(
                    job["message"],
                    font_size="12px",
                    color="#aaa",
                    style={"white_space": "pre-wrap"},
                ),
                spacing="2",
                align="start",
            ),
            spacing="1",
            width="100%",
            padding_top="6px",
            padding_left="40px",
        ),
        padding="10px 12px",
        background="rgba(255,255,255,0.03)",
        border_radius="6px",
        border="1px solid #333",
        width="100%",
    )


def _cron_schedule_input() -> rx.Component:
    """Cron: structured fields + preset selector."""
    _small_input = {"size": "1", "width": "60px", "variant": "surface"}
    return rx.vstack(
        # Preset row
        rx.hstack(
            rx.text(t("sched_cron_preset"), font_size="11px", color="#888"),
            rx.select(
                AIState.sched_preset_options,
                placeholder="—",
                on_change=AIState.apply_cron_preset,
                size="1", width="160px",
            ),
            spacing="2", align="center",
        ),
        # Cron fields: Stunde, Minute, Tag, Monat (dropdown), Wochentag (dropdown)
        rx.hstack(
            rx.vstack(
                rx.text(t("sched_cron_hour"), font_size="10px", color="#666"),
                rx.input(value=AIState.scheduler_cron_hour, on_change=AIState.set_scheduler_cron_hour, **_small_input),
                spacing="0",
            ),
            rx.vstack(
                rx.text(t("sched_cron_minute"), font_size="10px", color="#666"),
                rx.input(value=AIState.scheduler_cron_min, on_change=AIState.set_scheduler_cron_min, **_small_input),
                spacing="0",
            ),
            rx.vstack(
                rx.text(t("sched_cron_dom"), font_size="10px", color="#666"),
                rx.input(value=AIState.scheduler_cron_dom, on_change=AIState.set_scheduler_cron_dom, **_small_input),
                spacing="0",
            ),
            rx.vstack(
                rx.text(t("sched_cron_month"), font_size="10px", color="#666"),
                rx.select(
                    AIState.sched_month_options,
                    value=AIState.sched_month_display,
                    on_change=AIState.set_scheduler_month_from_label,
                    size="1", width="120px",
                ),
                spacing="0",
            ),
            rx.vstack(
                rx.text(t("sched_cron_dow"), font_size="10px", color="#666"),
                rx.select(
                    AIState.sched_dow_options,
                    value=AIState.sched_dow_display,
                    on_change=AIState.set_scheduler_dow_from_label,
                    size="1", width="130px",
                ),
                spacing="0",
            ),
            spacing="2", align="end", flex_wrap="wrap",
        ),
        spacing="2", width="100%",
    )


def _interval_schedule_input() -> rx.Component:
    """Interval: number + unit dropdown."""
    return rx.hstack(
        rx.text(t("sched_interval_every"), font_size="12px", color="#999"),
        rx.input(
            value=AIState.scheduler_interval_value,
            on_change=AIState.set_scheduler_interval_value,
            type="number",
            size="1", width="80px", variant="surface",
            min_="1",
        ),
        rx.select(
            AIState.sched_interval_unit_options,
            value=AIState.sched_interval_unit_display,
            on_change=AIState.set_scheduler_interval_unit_from_label,
            size="1", width="120px",
        ),
        spacing="2", align="center",
    )


def _once_schedule_input() -> rx.Component:
    """Once: date + time pickers."""
    return rx.hstack(
        rx.vstack(
            rx.text(t("sched_once_date"), font_size="10px", color="#666"),
            rx.input(
                value=AIState.scheduler_once_date,
                on_change=AIState.set_scheduler_once_date,
                type="date",
                size="1", width="160px", variant="surface",
            ),
            spacing="0",
        ),
        rx.vstack(
            rx.text(t("sched_once_time"), font_size="10px", color="#666"),
            rx.input(
                value=AIState.scheduler_once_time,
                on_change=AIState.set_scheduler_once_time,
                type="time",
                size="1", width="120px", variant="surface",
            ),
            spacing="0",
        ),
        spacing="2", align="end",
    )


def _scheduler_edit_form() -> rx.Component:
    """Inline edit/create form for a scheduler job."""
    return rx.vstack(
        # Row 1: Name + Type
        rx.hstack(
            rx.vstack(
                rx.text("Name", font_size="11px", color="#888"),
                rx.input(
                    value=AIState.scheduler_edit_name,
                    on_change=AIState.set_scheduler_edit_name,
                    size="2", width="100%",
                    variant="surface",
                ),
                flex="1",
            ),
            rx.vstack(
                clickable_tip(
                    rx.hstack(rx.text(t("sched_label_type"), font_size="11px", color="#888"), rx.icon("lightbulb", size=12, color="#FFD700"), spacing="1", align="center", cursor="pointer"),
                    rx.vstack(
                        rx.text(t("sched_tip_type_cron"), font_size="12px", color="#ddd"),
                        rx.text(t("sched_tip_type_interval"), font_size="12px", color="#ddd"),
                        rx.text(t("sched_tip_type_once"), font_size="12px", color="#ddd"),
                        spacing="1",
                    ),
                ),
                rx.select(
                    AIState.sched_type_options,
                    value=AIState.sched_type_display,
                    on_change=AIState.set_scheduler_type_from_label,
                    size="2", width="140px",
                ),
            ),
            spacing="2", width="100%",
        ),
        # Row 2: Schedule input (type-dependent)
        rx.vstack(
            rx.text(t("sched_label_schedule"), font_size="11px", color="#888"),
            rx.cond(
                AIState.scheduler_edit_type == "cron",
                _cron_schedule_input(),
                rx.cond(
                    AIState.scheduler_edit_type == "interval",
                    _interval_schedule_input(),
                    _once_schedule_input(),
                ),
            ),
            width="100%",
        ),
        # Row 3: Agent + Delivery + Channel + Tier
        rx.hstack(
            rx.vstack(
                rx.text("Agent", font_size="11px", color="#888"),
                rx.select(
                    AIState.scheduler_agent_options,
                    value=AIState.scheduler_edit_agent_display,
                    on_change=AIState.set_scheduler_edit_agent_from_label,
                    size="2", width="100%",
                ),
                flex="1",
            ),
            rx.vstack(
                clickable_tip(
                    rx.hstack(rx.text(t("sched_label_delivery"), font_size="11px", color="#888"), rx.icon("lightbulb", size=12, color="#FFD700"), spacing="1", align="center", cursor="pointer"),
                    rx.vstack(
                        rx.text(t("sched_tip_delivery_review"), font_size="12px", color="#ddd"),
                        rx.text(t("sched_tip_delivery_announce"), font_size="12px", color="#ddd"),
                        rx.text(t("sched_tip_delivery_webhook"), font_size="12px", color="#ddd"),
                        spacing="1",
                    ),
                ),
                rx.select(
                    AIState.sched_delivery_options,
                    value=AIState.sched_delivery_display,
                    on_change=AIState.set_scheduler_delivery_from_label,
                    size="2", width="100%",
                ),
                flex="1",
            ),
            rx.cond(
                AIState.scheduler_edit_delivery == "announce",
                rx.vstack(
                    rx.text(t("sched_label_channel"), font_size="11px", color="#888"),
                    rx.select(
                        ["telegram", "discord", "email"],
                        value=AIState.scheduler_edit_channel,
                        on_change=AIState.set_scheduler_edit_channel,
                        size="2", width="100%",
                    ),
                    flex="1",
                ),
            ),
            rx.vstack(
                clickable_tip(
                    rx.hstack(rx.text(t("sched_label_tier"), font_size="11px", color="#888"), rx.icon("lightbulb", size=12, color="#FFD700"), spacing="1", align="center", cursor="pointer"),
                    rx.vstack(
                        rx.text(t("sched_tip_tier_0"), font_size="12px", color="#ddd"),
                        rx.text(t("sched_tip_tier_1"), font_size="12px", color="#ddd"),
                        rx.text(t("sched_tip_tier_2"), font_size="12px", color="#ddd"),
                        rx.text(t("sched_tip_tier_3"), font_size="12px", color="#ddd"),
                        rx.text(t("sched_tip_tier_4"), font_size="12px", color="#ddd"),
                        spacing="1",
                    ),
                ),
                rx.select(
                    ["0", "1", "2", "3", "4"],
                    value=AIState.scheduler_edit_tier,
                    on_change=AIState.set_scheduler_edit_tier,
                    size="2", width="70px",
                ),
            ),
            spacing="2", width="100%",
        ),
        # Recipient (only when delivery = announce)
        rx.cond(
            AIState.scheduler_edit_delivery == "announce",
            rx.vstack(
                rx.text(t("sched_label_recipient"), font_size="11px", color="#888"),
                rx.input(
                    value=AIState.scheduler_edit_recipient,
                    on_change=AIState.set_scheduler_edit_recipient,
                    size="2", width="100%",
                    variant="surface",
                    placeholder="Chat-ID / E-Mail / Channel-ID",
                ),
                width="100%",
            ),
        ),
        # Webhook URL (only when delivery = webhook)
        rx.cond(
            AIState.scheduler_edit_delivery == "webhook",
            rx.vstack(
                rx.text("Webhook URL", font_size="11px", color="#888"),
                rx.input(
                    value=AIState.scheduler_edit_webhook_url,
                    on_change=AIState.set_scheduler_edit_webhook_url,
                    size="2", width="100%",
                    variant="surface",
                    placeholder="https://example.com/webhook",
                ),
                width="100%",
            ),
        ),
        # Message
        rx.vstack(
            rx.text(t("sched_label_message"), font_size="11px", color="#888"),
            rx.text_area(
                value=AIState.scheduler_edit_message,
                on_change=AIState.set_scheduler_edit_message,
                width="100%",
                min_height="100px",
                font_size="13px",
            ),
            width="100%",
        ),
        # Buttons
        rx.hstack(
            rx.button(
                t("agent_editor_save"),
                on_click=AIState.save_scheduler_job,
                variant="soft",
                color_scheme="orange",
                size="2",
                cursor="pointer",
            ),
            rx.button(
                t("db_cancel"),
                on_click=AIState.cancel_scheduler_edit,
                variant="soft",
                color_scheme="gray",
                size="2",
                cursor="pointer",
            ),
            spacing="2",
        ),
        spacing="3",
        width="100%",
        padding="14px 12px",
        background="rgba(217, 128, 48, 0.1)",
        border="1px solid #d98030",
        border_radius="8px",
        overflow="visible",
    )


def _scheduler_view() -> rx.Component:
    """Scheduler tab: list all scheduled jobs with toggle, delete, edit, create."""
    return rx.vstack(
        _editor_header(),
        rx.box(
            rx.vstack(
                # New Job button
                rx.hstack(
                    rx.spacer(),
                    rx.button(
                        rx.icon("plus", size=14),
                        t("scheduler_new_job"),
                        on_click=AIState.new_scheduler_job,
                        size="2",
                        variant="soft",
                        color_scheme="green",
                        cursor="pointer",
                    ),
                    width="100%",
                ),

                # Edit/Create form (shown when editing)
                rx.cond(
                    AIState.scheduler_edit_id != "",
                    _scheduler_edit_form(),
                ),

                # Job list
                rx.cond(
                    AIState.scheduler_job_list.length() > 0,  # type: ignore[union-attr]
                    rx.vstack(
                        rx.foreach(
                            AIState.scheduler_job_list,
                            _scheduler_job_row,
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    rx.text(
                        t("scheduler_no_jobs"),
                        color="#888",
                        font_size="13px",
                        padding_top="20px",
                        text_align="center",
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
