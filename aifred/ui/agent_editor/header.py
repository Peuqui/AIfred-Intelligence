"""Agent-Editor: gemeinsamer Header (Tab-Leiste + Close)."""

from __future__ import annotations

import reflex as rx
from reflex_components_lucide.icon import Icon

from ...state import AIState
from ...theme import COLORS
from ..helpers import t


def _editor_header() -> rx.Component:
    """Shared header with tab navigation for the settings modal."""
    return rx.vstack(
        rx.hstack(
            Icon.create("settings", size=24, color=COLORS["accent_gold"]),
            rx.text(
                t("agent_editor_title"),
                color="white",
                font_weight="bold",
                font_size="18px",
            ),
            rx.spacer(),
            rx.button(
                Icon.create("x", size=16),
                on_click=AIState.close_editor_with_dirty_check,
                size="1",
                variant="ghost",
                color_scheme="gray",
                cursor="pointer",
            ),
            width="100%",
            align="center",
        ),
        # Tab bar
        rx.hstack(
            rx.button(
                Icon.create("users", size=14),
                t("tab_agents"),
                on_click=AIState.set_agent_editor_tab("config"),
                size="2",
                variant=rx.cond(
                    AIState.agent_editor_mode == "config",
                    "solid", "soft",
                ),
                color_scheme=rx.cond(
                    AIState.agent_editor_mode == "config",
                    "orange", "gray",
                ),
                cursor="pointer",
            ),
            rx.button(
                Icon.create("brain", size=14),
                t("tab_memory"),
                on_click=AIState.set_agent_editor_tab("memory"),
                size="2",
                variant=rx.cond(
                    AIState.agent_editor_mode == "memory",
                    "solid", "soft",
                ),
                color_scheme=rx.cond(
                    AIState.agent_editor_mode == "memory",
                    "orange", "gray",
                ),
                cursor="pointer",
            ),
            rx.button(
                Icon.create("database", size=14),
                t("tab_database"),
                on_click=AIState.set_agent_editor_tab("database"),
                size="2",
                variant=rx.cond(
                    AIState.agent_editor_mode == "database",
                    "solid", "soft",
                ),
                color_scheme=rx.cond(
                    AIState.agent_editor_mode == "database",
                    "orange", "gray",
                ),
                cursor="pointer",
            ),
            rx.button(
                Icon.create("clock", size=14),
                t("tab_scheduler"),
                on_click=AIState.set_agent_editor_tab("scheduler"),
                size="2",
                variant=rx.cond(
                    AIState.agent_editor_mode == "scheduler",
                    "solid", "soft",
                ),
                color_scheme=rx.cond(
                    AIState.agent_editor_mode == "scheduler",
                    "orange", "gray",
                ),
                cursor="pointer",
            ),
            rx.button(
                Icon.create("shield-check", size=14),
                t("tab_audit"),
                on_click=AIState.set_agent_editor_tab("audit"),
                size="2",
                variant=rx.cond(
                    AIState.agent_editor_mode == "audit",
                    "solid", "soft",
                ),
                color_scheme=rx.cond(
                    AIState.agent_editor_mode == "audit",
                    "orange", "gray",
                ),
                cursor="pointer",
            ),
            rx.button(
                Icon.create("puzzle", size=14),
                t("tab_plugins"),
                on_click=AIState.set_agent_editor_tab("plugins"),
                size="2",
                variant=rx.cond(
                    AIState.agent_editor_mode == "plugins",
                    "solid", "soft",
                ),
                color_scheme=rx.cond(
                    AIState.agent_editor_mode == "plugins",
                    "orange", "gray",
                ),
                cursor="pointer",
            ),
            rx.button(
                Icon.create("hard-drive", size=14),
                t("tab_storage"),
                on_click=AIState.set_agent_editor_tab("storage"),
                size="2",
                variant=rx.cond(
                    AIState.agent_editor_mode == "storage",
                    "solid", "soft",
                ),
                color_scheme=rx.cond(
                    AIState.agent_editor_mode == "storage",
                    "orange", "gray",
                ),
                cursor="pointer",
            ),
            rx.button(
                Icon.create("volume-2", size=14),
                t("tab_tts"),
                on_click=AIState.set_agent_editor_tab("tts"),
                size="2",
                variant=rx.cond(
                    AIState.agent_editor_mode == "tts",
                    "solid", "soft",
                ),
                color_scheme=rx.cond(
                    AIState.agent_editor_mode == "tts",
                    "orange", "gray",
                ),
                cursor="pointer",
            ),
            rx.button(
                Icon.create("mic", size=14),
                t("tab_stt"),
                on_click=AIState.set_agent_editor_tab("stt"),
                size="2",
                variant=rx.cond(
                    AIState.agent_editor_mode == "stt",
                    "solid", "soft",
                ),
                color_scheme=rx.cond(
                    AIState.agent_editor_mode == "stt",
                    "orange", "gray",
                ),
                cursor="pointer",
            ),
            spacing="2",
            width="100%",
            flex_wrap="wrap",
        ),
        spacing="3",
        width="100%",
        flex_shrink="0",
        background_color=COLORS["card_bg"],
        z_index="10",
        padding_bottom="8px",
        border_bottom="1px solid var(--aifred-border)",
    )
