"""Agent-Editor: Tool-Pills, gruppiert nach Plugin (statisch gebaut)."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import json

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


def _group_header(name: str, tool_names: list[str]) -> rx.Component:
    """Group heading with a switch that toggles every tool in the group at once.

    The switch is ON only when *all* tools of the group are granted; flipping it
    grants/revokes the whole group (a partial selection reads as OFF, so one flip
    grants all). Saves the per-tool click-orgy for big groups like Google Suite.
    """
    # Chaining the per-tool Vars with `&` (functools.reduce) blows up
    # exponentially: Reflex' boolean `&` repeats its left operand in the
    # generated JS, so every extra tool roughly doubles the expression tree.
    # Measured: 12 tools 0.01s, 20 tools 0.98s, 26 tools (Google Suite) 58s —
    # paid at import time, which is what pushed AIfred's startup to 3.5 min.
    # Referencing the dict Var ONCE and testing the keys in JS is O(n):
    # 0.001s, a 5x smaller expression, same state binding (so it stays
    # reactive).
    all_on = rx.Var(
        f"{json.dumps(tool_names)}.every((n) => {AIState.editor_tools}[n])"
    ).to(bool)
    return rx.hstack(
        rx.text(name, font_size="10px", color="#888"),
        rx.switch(
            checked=all_on,
            on_change=lambda v: AIState.set_editor_tool_group(tool_names, v),  # type: ignore[arg-type]
            color_scheme="orange",
            size="1",
            transform="scale(0.85)",
        ),
        spacing="2",
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
            _group_header("Memory", ["store_memory", "update_memory", "delete_memory"]),
            rx.flex(
                _build_tool_pill("store_memory", tier=TIER_WRITE_DATA),
                _build_tool_pill("update_memory", tier=TIER_WRITE_DATA),
                _build_tool_pill("delete_memory", tier=TIER_WRITE_DATA),
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
                _group_header(plugin.display_name, [t.name for t in tools]),
                rx.flex(
                    *[_build_tool_pill(t.name, tier=t.tier) for t in tools],
                    wrap="wrap", gap="4px",
                ),
                spacing="1", width="100%",
            )
        )

    # Channel tools
    channel_pills: list[rx.Component] = []
    channel_names: list[str] = []
    for ch in all_channels().values():
        if not ch.is_configured():
            continue
        for tool in ch.get_tools(ctx):
            channel_pills.append(_build_tool_pill(tool.name, tier=tool.tier))
            channel_names.append(tool.name)
    if channel_pills:
        groups.append(
            rx.vstack(
                _group_header("Channels", channel_names),
                rx.flex(*channel_pills, wrap="wrap", gap="4px"),
                spacing="1", width="100%",
            )
        )

    return groups


# Pre-build tool groups at import time
_tool_groups = _build_tool_groups()
