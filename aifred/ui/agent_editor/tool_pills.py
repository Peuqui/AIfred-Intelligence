"""Agent-Editor: Tool-Pills, gruppiert nach Plugin. Sie kommen zur Laufzeit aus dem
State: Plugins werden im laufenden Dienst an- und abgeschaltet."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

from typing import Any

import reflex as rx

from ...state import AIState
from ...theme import COLORS
from ..helpers import t

# Tier badge colors: 0=green, 1=blue, 2=orange, 3=red, 4=purple
_TIER_COLORS = {0: COLORS["accent_success"], 1: COLORS["accent_blue"], 2: COLORS["accent_amber"], 3: COLORS["danger"], 4: COLORS["accent_purple"]}

def _tier_help_content() -> rx.Component:
    """Shared popover content explaining security tiers with colored badges (i18n)."""
    from ...lib.security import TIER_I18N_KEYS
    return rx.vstack(
        rx.text(t("security_tiers_title"), font_weight="bold", font_size="12px", color="white"),
        *[
            rx.hstack(
                rx.text(f"T{tier}", color=_TIER_COLORS[tier], font_weight="bold", font_size="11px", min_width="22px"),
                rx.text(t(label_key), font_size="11px", color="white", font_weight="bold"),
                rx.text(t(desc_key), font_size="11px", color=COLORS["text_soft"]),
                spacing="2", align="center",
            )
            for tier, label_key, desc_key in TIER_I18N_KEYS
        ],
        spacing="1",
        padding="8px",
    )


def _tool_pill(tool: rx.Var) -> rx.Component:
    """Render a single tool as a clickable pill toggle with tier badge."""
    name = tool["name"].to(str)
    tier = tool["tier"].to(int)
    is_enabled = AIState.editor_tools[name].to(bool)
    return rx.hstack(
        rx.button(
            name,
            on_click=AIState.toggle_editor_tool(name),
            size="1",
            variant=rx.cond(is_enabled, "solid", "soft"),
            color_scheme=rx.cond(is_enabled, "orange", "gray"),
            cursor="pointer",
            font_size="11px",
            padding_x="10px",
            height="26px",
        ),
        rx.text(
            "T" + tier.to(str),
            font_size="9px",
            color=rx.match(tier, *_TIER_COLORS.items(), COLORS["text_muted"]),
            font_weight="bold",
            min_width="18px",
        ),
        spacing="1",
        align="center",
    )


def _tool_group(group: rx.Var) -> rx.Component:
    """One plugin's pills under a heading with a switch that toggles every tool of
    the group at once.

    The switch is ON only when *all* tools of the group are granted; flipping it
    grants/revokes the whole group (a partial selection reads as OFF, so one flip
    grants all). Saves the per-tool click-orgy for big groups like Google Suite.
    """
    names = group["names"].to(list[str])
    all_on = rx.Var(f"{names}.every((n) => {AIState.editor_tools}[n])").to(bool)
    return rx.vstack(
        rx.hstack(
            rx.text(
                rx.cond(AIState.ui_language == "de", group["name_de"].to(str), group["name_en"].to(str)),
                font_size="10px", color=COLORS["text_secondary"],
            ),
            rx.switch(
                checked=all_on,
                on_change=lambda v: AIState.set_editor_tool_group(names, v),  # type: ignore[arg-type]
                color_scheme="orange",
                size="1",
                transform="scale(0.85)",
            ),
            spacing="2",
            align="center",
        ),
        rx.flex(
            rx.foreach(group["tools"].to(list[dict[str, Any]]), _tool_pill),
            wrap="wrap", gap="4px",
        ),
        spacing="1", width="100%",
    )
