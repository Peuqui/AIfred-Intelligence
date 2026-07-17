"""Agent-Editor: Tool-Pills, gruppiert nach Plugin (statisch gebaut)."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t


# Tier badge colors: 0=green, 1=blue, 2=orange, 3=red, 4=purple
_TIER_COLORS = {0: "#4CAF50", 1: "#2196F3", 2: "#FF9800", 3: "#f44336", 4: "#9C27B0"}

def _tier_help_content() -> rx.Component:
    """Shared popover content explaining security tiers with colored badges (i18n)."""
    from ...lib.security import TIER_I18N_KEYS
    return rx.vstack(
        rx.text(t("security_tiers_title"), font_weight="bold", font_size="12px", color="white"),
        *[
            rx.hstack(
                rx.text(f"T{tier}", color=_TIER_COLORS[tier], font_weight="bold", font_size="11px", min_width="22px"),
                rx.text(t(label_key), font_size="11px", color="white", font_weight="bold"),
                rx.text(t(desc_key), font_size="11px", color="#ccc"),
                spacing="2", align="center",
            )
            for tier, label_key, desc_key in TIER_I18N_KEYS
        ],
        spacing="1",
        padding="8px",
    )


def _build_tool_pill(tool_name: str, tier: int = 0) -> rx.Component:
    """Render a single tool as a clickable pill toggle with tier badge."""
    is_enabled = AIState.editor_tools[tool_name].to(bool)
    color = _TIER_COLORS.get(tier, "#666")
    return rx.hstack(
        rx.button(
            tool_name,
            on_click=AIState.toggle_editor_tool(tool_name),
            size="1",
            variant=rx.cond(is_enabled, "solid", "soft"),
            color_scheme=rx.cond(is_enabled, "orange", "gray"),
            cursor="pointer",
            font_size="11px",
            padding_x="10px",
            height="26px",
        ),
        rx.text(
            f"T{tier}",
            font_size="9px",
            color=color,
            font_weight="bold",
            min_width="18px",
        ),
        spacing="1",
        align="center",
    )


def _build_tool_groups() -> list[rx.Component]:
    """Build tool pill groups at build-time, grouped by plugin."""
    from ...lib.plugin_registry import discover_tools, all_channels
    from ...lib.plugin_base import PluginContext

    ctx = PluginContext(agent_id="__build__", lang="de", session_id="", llm_history=[])
    groups: list[rx.Component] = []

    from ...lib.security import TIER_WRITE_DATA

    # Memory (always first — tier 2 = write data)
    groups.append(
        rx.vstack(
            rx.text("Memory", font_size="10px", color="#888"),
            rx.flex(
                _build_tool_pill("store_memory", tier=TIER_WRITE_DATA),
                wrap="wrap", gap="4px",
            ),
            spacing="1", width="100%",
        )
    )

    # Tool plugins
    for plugin in discover_tools():
        if not plugin.is_available():
            continue
        tools = plugin.get_tools(ctx)
        if not tools:
            continue
        groups.append(
            rx.vstack(
                rx.text(plugin.display_name, font_size="10px", color="#888"),
                rx.flex(
                    *[_build_tool_pill(t.name, tier=t.tier) for t in tools],
                    wrap="wrap", gap="4px",
                ),
                spacing="1", width="100%",
            )
        )

    # Channel tools
    channel_pills: list[rx.Component] = []
    for ch in all_channels().values():
        if not ch.is_configured():
            continue
        for tool in ch.get_tools(ctx):
            channel_pills.append(_build_tool_pill(tool.name, tier=tool.tier))
    if channel_pills:
        groups.append(
            rx.vstack(
                rx.text("Channels", font_size="10px", color="#888"),
                rx.flex(*channel_pills, wrap="wrap", gap="4px"),
                spacing="1", width="100%",
            )
        )

    return groups


# Pre-build tool groups at import time
_tool_groups = _build_tool_groups()
