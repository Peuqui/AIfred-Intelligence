"""Settings Modal for AIfred UI.

Fullscreen overlay with three tabs:
- Agents: Agent dropdown → Metadata + TTS + Prompts (all on one page)
- Memory: Agent dropdown → Memory entries with type filter
- Database: System collections (Research Cache, Documents) browse + delete

All text inputs are pure DOM elements (no per-keystroke state updates).
Values are read from DOM only on save/tab-switch/lang-switch via call_script.
"""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ..state import AIState
from .helpers import t, clickable_tip

# Shared style for DOM-only input fields
_INPUT_STYLE = {
    "width": "100%",
    "color": "white",
    "background_color": "#333",
    "border": "1px solid #555",
    "border_radius": "6px",
    "padding": "6px 10px",
    "font_size": "14px",
}

_ID_INPUT_STYLE = {
    "width": "100%",
    "color": "#bbb",
    "background_color": "#2a2a2a",
    "border": "1px solid #444",
    "border_radius": "6px",
    "padding": "6px 10px",
    "font_size": "13px",
    "font_family": "monospace",
    "cursor": "not-allowed",
}


# ============================================================
# SHARED HEADER (tabs + close)
# ============================================================

def _editor_header() -> rx.Component:
    """Shared header with tab navigation for the settings modal."""
    return rx.vstack(
        rx.hstack(
            rx.icon("settings", size=24, color="#FFD700"),
            rx.text(
                t("agent_editor_title"),
                color="white",
                font_weight="bold",
                font_size="18px",
            ),
            rx.spacer(),
            rx.button(
                rx.icon("x", size=16),
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
                rx.icon("users", size=14),
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
                rx.icon("brain", size=14),
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
                rx.icon("database", size=14),
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
                rx.icon("clock", size=14),
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
                rx.icon("shield-check", size=14),
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
                rx.icon("puzzle", size=14),
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
            spacing="2",
            width="100%",
            flex_wrap="wrap",
        ),
        spacing="3",
        width="100%",
        flex_shrink="0",
        background_color="#1a1a1a",
        z_index="10",
        padding_bottom="8px",
        border_bottom="1px solid #333",
    )


# ============================================================
# TOOL PILLS — grouped by plugin (built statically)
# ============================================================

# Tier badge colors: 0=green, 1=blue, 2=orange, 3=red, 4=purple
_TIER_COLORS = {0: "#4CAF50", 1: "#2196F3", 2: "#FF9800", 3: "#f44336", 4: "#9C27B0"}

def _tier_help_content() -> rx.Component:
    """Shared popover content explaining security tiers with colored badges (i18n)."""
    from ..lib.security import TIER_I18N_KEYS
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
    from ..lib.plugin_registry import discover_tools, all_channels
    from ..lib.plugin_base import PluginContext

    ctx = PluginContext(agent_id="__build__", lang="de", session_id="", llm_history=[])
    groups: list[rx.Component] = []

    from ..lib.security import TIER_WRITE_DATA

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


# ============================================================
# PROMPT TAB BUTTON (for foreach)
# ============================================================

def _prompt_tab_button(key: rx.Var) -> rx.Component:
    """A single prompt layer tab button (rendered via foreach)."""
    return rx.button(
        key,
        on_click=AIState.set_editor_prompt_tab(key),
        size="1",
        variant=rx.cond(
            AIState.editor_prompt_tab == key,
            "solid",
            "soft",
        ),
        color_scheme=rx.cond(
            AIState.editor_prompt_tab == key,
            "blue",
            "gray",
        ),
        cursor="pointer",
    )


# JS to read all editor DOM fields as JSON
_READ_DOM_JS = (
    "JSON.stringify({"
    " name: (document.getElementById('editor-name')||{}).value||'',"
    " description: (document.getElementById('editor-description')||{}).value||'',"
    " prompt: (document.getElementById('editor-prompt-textarea')||{}).value||'',"
    " agent_id: (document.getElementById('editor-agent-id')||{}).value||''"
    "})"
)



# ============================================================
# CONFIG TAB — Dropdown + Metadata + TTS + Prompts
# ============================================================

def _config_view() -> rx.Component:
    """Config tab: agent dropdown at top, all settings below."""
    is_new = AIState.editor_agent_id == ""
    is_automatik = AIState.editor_agent_id == "automatik"
    is_default = (
        (AIState.editor_agent_id == "aifred")
        | (AIState.editor_agent_id == "sokrates")
        | (AIState.editor_agent_id == "salomo")
        | (AIState.editor_agent_id == "vision")
    )

    return rx.vstack(
        _editor_header(),

        # Scrollable content
        rx.box(
            rx.vstack(
                # ── Agent Selector Row ──────────────────────────
                rx.hstack(
                    # Agent dropdown
                    rx.cond(
                        ~is_new,
                        rx.box(
                            rx.select(
                                AIState.agent_dropdown_options,
                                value=AIState.editor_agent_dropdown_value,
                                on_change=AIState.select_editor_agent_with_dirty_check,
                                size="2",
                                width="100%",
                            ),
                            flex="1",
                            min_width="0",
                        ),
                        # New agent mode — show ID input instead
                        rx.box(
                            rx.el.input(
                                id="editor-agent-id",
                                placeholder=t("agent_editor_agent_id_placeholder"),
                                auto_complete="off",
                                spell_check=False,
                                **_INPUT_STYLE,
                            ),
                            flex="1",
                            min_width="0",
                        ),
                    ),
                    # New Agent button (not for Automatik)
                    rx.cond(
                        ~is_automatik,
                        rx.tooltip(
                            rx.icon_button(
                                rx.icon("plus", size=16),
                                on_click=AIState.start_new_agent,
                                size="2",
                                variant="soft",
                                color_scheme="green",
                                cursor="pointer",
                            ),
                            content=t("agent_editor_new"),
                        ),
                    ),
                    # Delete button (only custom agents, not during create, not Automatik)
                    rx.cond(
                        ~is_new & ~is_default & ~is_automatik,
                        rx.tooltip(
                            rx.icon_button(
                                rx.icon("trash-2", size=16),
                                on_click=AIState.delete_agent_editor(AIState.editor_agent_id),
                                size="2",
                                variant="soft",
                                color_scheme=rx.cond(
                                    AIState.editor_delete_confirm == AIState.editor_agent_id,
                                    "red", "gray",
                                ),
                                cursor="pointer",
                            ),
                            content=rx.cond(
                                AIState.editor_delete_confirm == AIState.editor_agent_id,
                                t("agent_editor_really_delete"),
                                t("agent_editor_delete_agent"),
                            ),
                        ),
                    ),
                    # Clear memory button (not during create, not Automatik)
                    rx.cond(
                        ~is_new & ~is_automatik,
                        rx.tooltip(
                            rx.icon_button(
                                rx.icon("eraser", size=16),
                                on_click=AIState.clear_agent_memory(AIState.editor_agent_id),
                                size="2",
                                variant="soft",
                                color_scheme=rx.cond(
                                    AIState.editor_memory_confirm == AIState.editor_agent_id,
                                    "red", "orange",
                                ),
                                cursor="pointer",
                            ),
                            content=rx.cond(
                                AIState.editor_memory_confirm == AIState.editor_agent_id,
                                t("agent_editor_really_forget"),
                                t("agent_editor_clear_memories"),
                            ),
                        ),
                    ),
                    spacing="2",
                    align="center",
                    width="100%",
                ),

                # ── Metadata (hidden for Automatik) ────────────
                rx.cond(~is_automatik, rx.hstack(
                    # Agent-ID (readonly, nur bei bestehenden Agenten)
                    rx.cond(
                        ~is_new,
                        rx.vstack(
                            rx.text(t("agent_editor_agent_id"), color="#aaa", font_size="12px"),
                            rx.el.input(
                                value=AIState.editor_agent_id,
                                read_only=True,
                                tab_index=-1,
                                **_ID_INPUT_STYLE,
                            ),
                            spacing="1",
                            width="140px",
                            flex_shrink="0",
                        ),
                    ),
                    # Name
                    rx.vstack(
                        rx.text(t("agent_editor_name"), color="#aaa", font_size="12px"),
                        rx.el.input(
                            id="editor-name",
                            auto_complete="off",
                            spell_check=False,
                            on_key_down=lambda _: AIState.mark_editor_dirty(),
                            **_INPUT_STYLE,
                        ),
                        spacing="1",
                        flex="1",
                    ),
                    # Emoji with picker
                    rx.vstack(
                        rx.text(t("agent_editor_emoji"), color="#aaa", font_size="12px"),
                        rx.box(
                            rx.button(
                                AIState.editor_emoji,
                                on_click=AIState.toggle_emoji_picker,
                                width="60px",
                                height="36px",
                                font_size="20px",
                                variant="outline",
                                color_scheme="gray",
                                cursor="pointer",
                                background_color="#333",
                            ),
                            rx.cond(
                                AIState.editor_emoji_picker_open,
                                rx.box(
                                    rx.flex(
                                        *[
                                            rx.button(
                                                e,
                                                on_click=AIState.set_editor_emoji(e),
                                                size="1",
                                                variant="ghost",
                                                cursor="pointer",
                                                font_size="18px",
                                                padding="4px",
                                                min_width="36px",
                                                height="36px",
                                            )
                                            for e in [
                                                "\U0001f3a9", "\U0001f3db\ufe0f", "\U0001f451",
                                                "\U0001f4f7", "\U0001f916", "\U0001f9e0",
                                                "\U0001f4a1", "\U0001f52c", "\U0001f3ad",
                                                "\U0001f98a", "\U0001f43a", "\U0001f989",
                                                "\U0001f409", "\U0001f9d9", "\U0001f468\u200d\u2695\ufe0f",
                                                "\U0001f468\u200d\U0001f52c", "\U0001f575\ufe0f",
                                                "\U0001f468\u200d\U0001f4bb", "\U0001f9d1\u200d\U0001f3eb",
                                                "\U0001f3af", "\u26a1", "\U0001f525",
                                                "\u2744\ufe0f", "\U0001f31f", "\U0001f480",
                                                "\U0001f47d", "\U0001f921", "\U0001f608",
                                                "\U0001f9be", "\U0001f9ec", "\u2699\ufe0f",
                                                "\U0001f3aa",
                                            ]
                                        ],
                                        wrap="wrap",
                                        gap="2px",
                                        max_width="300px",
                                    ),
                                    position="absolute",
                                    top="100%",
                                    left="0",
                                    z_index="100",
                                    background_color="#2a2a2a",
                                    border="1px solid #555",
                                    border_radius="8px",
                                    padding="8px",
                                    margin_top="4px",
                                ),
                            ),
                            position="relative",
                        ),
                        spacing="1",
                    ),
                    width="100%",
                    spacing="3",
                )),  # end rx.cond(~is_automatik) for Metadata

                # Role + Description (hidden for Automatik)
                rx.cond(~is_automatik, rx.hstack(
                    rx.vstack(
                        rx.text(t("agent_editor_role"), color="#aaa", font_size="12px"),
                        rx.select(
                            ["main", "critic", "judge", "custom"],
                            value=AIState.editor_role,
                            on_change=AIState.set_editor_role,
                            size="2",
                            width="120px",
                        ),
                        spacing="1",
                    ),
                    rx.vstack(
                        rx.text(t("agent_editor_description"), color="#aaa", font_size="12px"),
                        rx.el.input(
                            id="editor-description",
                            auto_complete="off",
                            spell_check=False,
                            on_key_down=lambda _: AIState.mark_editor_dirty(),
                            **_INPUT_STYLE,
                        ),
                        spacing="1",
                        flex="1",
                    ),
                    width="100%",
                    spacing="3",
                    align="end",
                )),

                # ── TTS Settings (only existing agents, not Automatik) ─
                rx.cond(
                    ~is_new & ~is_automatik,
                    rx.vstack(
                        # Header: Title + Enabled toggle
                        rx.hstack(
                            rx.text(
                                "\U0001f50a ", t("agent_editor_tts_title"),
                                color="#FFD700",
                                font_weight="bold",
                                font_size="14px",
                            ),
                            rx.spacer(),
                            width="100%",
                            align="center",
                        ),
                        # Backend + Voice
                        rx.hstack(
                            rx.text("Backend", font_size="11px", color="#aaa", flex_shrink="0"),
                            rx.box(
                                rx.select(
                                    AIState.tts_engines,
                                    value=AIState.editor_tts_engine_label,
                                    on_change=AIState.set_editor_tts_engine,
                                    size="1",
                                    width="100%",
                                ),
                                flex="1",
                                min_width="0",
                            ),
                            rx.text("Voice", font_size="11px", color="#aaa", flex_shrink="0"),
                            rx.box(
                                rx.select(
                                    AIState.editor_tts_available_voices,
                                    value=AIState.editor_agent_tts_voice,
                                    on_change=AIState.set_editor_agent_tts_voice,
                                    placeholder="",
                                    size="1",
                                    width="100%",
                                ),
                                flex="1",
                                min_width="0",
                            ),
                            spacing="2",
                            align="center",
                            width="100%",
                        ),
                        # Speed + Pitch
                        rx.hstack(
                            rx.text("Speed", font_size="11px", color="#aaa", width="55px"),
                            rx.select(
                                ["0.8x", "0.9x", "1.0x", "1.1x", "1.2x", "1.25x", "1.5x", "2.0x"],
                                value=AIState.editor_agent_tts_speed,
                                on_change=AIState.set_editor_agent_tts_speed,
                                size="1",
                                width="90px",
                            ),
                            rx.text("Pitch", font_size="11px", color="#aaa", width="40px"),
                            rx.select(
                                ["0.8", "0.85", "0.9", "0.95", "1.0", "1.05", "1.1", "1.15", "1.2"],
                                value=AIState.editor_agent_tts_pitch,
                                on_change=AIState.set_editor_agent_tts_pitch,
                                size="1",
                                width="90px",
                            ),
                            spacing="2",
                            align="center",
                            width="100%",
                        ),
                        spacing="2",
                        width="100%",
                        padding="10px",
                        background_color="#222",
                        border_radius="8px",
                        border="1px solid #333",
                        overflow="hidden",
                    ),
                ),

                # ── Tool Whitelist (only existing agents, not Automatik) ──
                rx.cond(
                    ~is_new & ~is_automatik,
                    rx.vstack(
                        rx.hstack(
                            rx.text(
                                "Tools",
                                color="#FFD700",
                                font_weight="bold",
                                font_size="14px",
                            ),
                            rx.spacer(),
                            rx.popover.root(
                                rx.popover.trigger(
                                    rx.icon("lightbulb", size=14, color="#FFD700", cursor="pointer"),
                                ),
                                rx.popover.content(
                                    _tier_help_content(),
                                    side="left",
                                    style={"background": "#2a2a3e", "border": "1px solid #555", "border-radius": "8px"},
                                ),
                            ),
                            rx.button(
                                t("tools_all_on"),
                                on_click=AIState.set_all_editor_tools(True),
                                size="1",
                                variant="soft",
                                color_scheme="green",
                                cursor="pointer",
                            ),
                            rx.button(
                                t("tools_all_off"),
                                on_click=AIState.set_all_editor_tools(False),
                                size="1",
                                variant="soft",
                                color_scheme="red",
                                cursor="pointer",
                            ),
                            width="100%",
                            align="center",
                        ),
                        rx.box(
                            *_tool_groups,
                            style={
                                "columns": ["1", "1", "2"],
                                "column-gap": "16px",
                                "& > *": {"break-inside": "avoid", "margin-bottom": "8px"},
                            },
                            width="100%",
                        ),
                        spacing="2",
                        width="100%",
                    ),
                ),

                # ── Prompt Layer Editor (only existing agents) ──
                rx.cond(
                    ~is_new,
                    rx.vstack(
                        rx.hstack(
                            rx.text(
                                t("agent_editor_prompts"),
                                color="#FFD700",
                                font_weight="bold",
                                font_size="14px",
                            ),
                            rx.spacer(),
                            rx.hstack(
                                rx.button(
                                    "DE",
                                    on_click=AIState.set_editor_prompt_lang("de"),
                                    size="1",
                                    variant=rx.cond(AIState.editor_prompt_lang == "de", "solid", "soft"),
                                    color_scheme=rx.cond(AIState.editor_prompt_lang == "de", "blue", "gray"),
                                    cursor="pointer",
                                ),
                                rx.button(
                                    "EN",
                                    on_click=AIState.set_editor_prompt_lang("en"),
                                    size="1",
                                    variant=rx.cond(AIState.editor_prompt_lang == "en", "solid", "soft"),
                                    color_scheme=rx.cond(AIState.editor_prompt_lang == "en", "blue", "gray"),
                                    cursor="pointer",
                                ),
                                spacing="1",
                            ),
                            width="100%",
                            align="center",
                        ),
                        rx.hstack(
                            rx.foreach(
                                AIState.editor_prompt_keys,
                                _prompt_tab_button,
                            ),
                            spacing="2",
                            flex_wrap="wrap",
                        ),
                        rx.el.textarea(
                            id="editor-prompt-textarea",
                            width="100%",
                            min_height="200px",
                            color="white",
                            background_color="#1a1a1a",
                            border="1px solid #444",
                            font_family="monospace",
                            font_size="13px",
                            padding="12px",
                            border_radius="6px",
                            auto_complete="off",
                            spell_check=False,
                            on_key_down=lambda _: AIState.mark_editor_dirty(),
                            style={"resize": "vertical"},
                        ),
                        spacing="2",
                        width="100%",
                    ),
                ),

                # ── Unsaved Changes Warning ─────────────────────
                rx.cond(
                    AIState.editor_dirty_confirm,
                    rx.vstack(
                        rx.hstack(
                            rx.icon("alert-triangle", size=16, color="#ff6600"),
                            rx.text(
                                t("agent_editor_unsaved_warning"),
                                font_size="13px",
                                color="#ff6600",
                            ),
                            spacing="2",
                            align="center",
                            justify="center",
                            width="100%",
                        ),
                        rx.button(
                            t("agent_editor_discard"),
                            on_click=AIState.confirm_discard_changes,
                            size="2",
                            variant="solid",
                            color_scheme="red",
                            cursor="pointer",
                            width="auto",
                        ),
                        spacing="2",
                        align="center",
                        width="100%",
                        padding="10px 12px",
                        background="rgba(255, 100, 0, 0.1)",
                        border="1px solid #ff6600",
                        border_radius="6px",
                    ),
                ),

                # ── Action Buttons ──────────────────────────────
                rx.hstack(
                    rx.button(
                        t("agent_editor_save"),
                        on_click=rx.call_script(
                            _READ_DOM_JS,
                            callback=AIState.save_agent_editor,
                        ),
                        variant="soft",
                        color_scheme="orange",
                        size="2",
                        cursor="pointer",
                    ),
                    rx.cond(
                        ~is_new,
                        rx.cond(
                            AIState.editor_reset_confirm,
                            rx.button(
                                t("agent_editor_really_delete"),
                                on_click=AIState.confirm_reset_editor_prompt,
                                variant="solid",
                                color_scheme="red",
                                size="2",
                                cursor="pointer",
                            ),
                            rx.button(
                                t("agent_editor_reset"),
                                on_click=AIState.request_reset_editor_prompt,
                                variant="soft",
                                color_scheme="gray",
                                size="2",
                                cursor="pointer",
                            ),
                        ),
                    ),
                    spacing="3",
                    width="100%",
                ),

                spacing="4",
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


# ============================================================
# MEMORY TAB — Dropdown + Filter + Entries
# ============================================================

def _memory_entry_row(entry: rx.Var) -> rx.Component:
    """Render a single memory entry with expandable content."""
    return rx.box(
        rx.hstack(
            rx.badge(
                entry["type"],
                variant="soft",
                color_scheme=rx.cond(
                    entry["type"] == "session_summary", "blue",
                    rx.cond(entry["type"] == "sermon", "purple",
                    rx.cond(entry["type"] == "insight", "green", "gray")),
                ),
                font_size="10px",
            ),
            rx.text(entry["date"], font_size="11px", color="#888"),
            rx.spacer(),
            rx.icon_button(
                rx.icon("trash-2", size=12),
                on_click=AIState.delete_memory_entry(entry["id"]),
                size="1",
                variant="ghost",
                color_scheme="red",
                cursor="pointer",
            ),
            width="100%",
            align="center",
        ),
        rx.text(
            entry["summary"],
            font_size="15px",
            color="#ddd",
            font_weight="500",
            padding_top="4px",
        ),
        rx.cond(
            entry["content"] != entry["summary"],
            rx.text(
                entry["content"],
                font_size="14px",
                color="#aaa",
                padding_top="4px",
                style={"white_space": "pre-wrap"},
            ),
        ),
        rx.cond(
            entry["sources"] != "",
            rx.vstack(
                rx.text(t("memory_sources"), font_size="12px", color="#888", font_weight="600", padding_top="8px"),
                rx.foreach(
                    entry["sources"].split("\n"),  # type: ignore[union-attr]
                    _source_link,
                ),
                spacing="1",
                width="100%",
            ),
        ),
        padding="10px 12px",
        background="rgba(255,255,255,0.03)",
        border_radius="6px",
        border="1px solid #333",
        width="100%",
    )


def _source_link(url: rx.Var) -> rx.Component:
    """Render a clickable source URL."""
    return rx.link(
        rx.hstack(
            rx.icon("external-link", size=12, color="#4da6ff"),
            rx.text(url, font_size="12px", color="#4da6ff"),
            spacing="1",
            align="center",
        ),
        href=url,
        is_external=True,
        style={
            "text_decoration": "none",
            "&:hover": {"text_decoration": "underline"},
        },
    )


def _memory_view() -> rx.Component:
    """Memory tab: agent dropdown + type filter + entries."""
    return rx.vstack(
        _editor_header(),

        # Scrollable content
        rx.box(
            rx.vstack(
                # Agent dropdown
                rx.hstack(
                    rx.select(
                        AIState.memory_agent_dropdown_options,
                        value=AIState.memory_browser_agent_display,
                        on_change=AIState.select_memory_agent,
                        placeholder=t("agent_editor_select_agent"),
                        size="2",
                        width="100%",
                    ),
                    spacing="2",
                    width="100%",
                    align="center",
                ),

                # Filter buttons (only when agent selected)
                rx.cond(
                    AIState.memory_browser_agent != "",
                    rx.hstack(
                        rx.button(
                            t("memory_filter_all"),
                            on_click=AIState.set_memory_filter("all"),
                            size="1",
                            variant=rx.cond(AIState.memory_browser_filter == "all", "solid", "soft"),
                            color_scheme=rx.cond(AIState.memory_browser_filter == "all", "orange", "gray"),
                            cursor="pointer",
                        ),
                        rx.button(
                            "Session",
                            on_click=AIState.set_memory_filter("session"),
                            size="1",
                            variant=rx.cond(AIState.memory_browser_filter == "session", "solid", "soft"),
                            color_scheme=rx.cond(AIState.memory_browser_filter == "session", "blue", "gray"),
                            cursor="pointer",
                        ),
                        rx.button(
                            "Agent",
                            on_click=AIState.set_memory_filter("agent"),
                            size="1",
                            variant=rx.cond(AIState.memory_browser_filter == "agent", "solid", "soft"),
                            color_scheme=rx.cond(AIState.memory_browser_filter == "agent", "green", "gray"),
                            cursor="pointer",
                        ),
                        rx.spacer(),
                        rx.badge(
                            AIState.filtered_memory_entries.length(),  # type: ignore[union-attr]
                            variant="soft",
                            color_scheme="orange",
                        ),
                        spacing="2",
                        align="center",
                        width="100%",
                    ),
                ),

                # Entries
                rx.cond(
                    AIState.memory_browser_agent == "",
                    rx.text(
                        t("memory_select_hint"),
                        color="#888",
                        font_size="13px",
                        padding_top="20px",
                        text_align="center",
                    ),
                    rx.cond(
                        AIState.filtered_memory_entries.length() > 0,  # type: ignore[union-attr]
                        rx.vstack(
                            rx.foreach(
                                AIState.filtered_memory_entries,
                                _memory_entry_row,
                            ),
                            spacing="2",
                            width="100%",
                        ),
                        rx.text(
                            t("memory_no_entries"),
                            color="#888",
                            font_size="13px",
                        ),
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


# ============================================================
# DATABASE VIEW (ChromaDB Management — System Collections)
# ============================================================

def _database_view() -> rx.Component:
    """Database tab: Research Cache + Documents with same browse/delete UI as Memory."""
    return rx.vstack(
        _editor_header(),

        # Scrollable content
        rx.box(
            rx.vstack(
                # Collection selector buttons + clear-all
                rx.hstack(
                    rx.button(
                        rx.icon("search", size=14),
                        " ", t("db_research_cache"),
                        on_click=AIState.select_db_collection("research_cache"),
                        size="2",
                        variant=rx.cond(
                            AIState.db_browser_collection == "research_cache",
                            "solid", "soft",
                        ),
                        color_scheme="orange",
                        cursor="pointer",
                        flex_shrink="0",
                    ),
                    rx.button(
                        rx.icon("file-text", size=14),
                        " ", t("db_documents"),
                        on_click=AIState.select_db_collection("aifred_documents"),
                        size="2",
                        variant=rx.cond(
                            AIState.db_browser_collection == "aifred_documents",
                            "solid", "soft",
                        ),
                        color_scheme="orange",
                        cursor="pointer",
                        flex_shrink="0",
                    ),
                    rx.spacer(),
                    # Entry count badge
                    rx.cond(
                        AIState.db_browser_collection != "",
                        rx.badge(
                            AIState.db_browser_entries.length(),  # type: ignore[union-attr]
                            variant="soft",
                            color_scheme="orange",
                        ),
                    ),
                    # Clear all button (with confirmation)
                    rx.cond(
                        AIState.db_browser_entries.length() > 0,  # type: ignore[union-attr]
                        rx.cond(
                            AIState.db_clear_confirm,
                            # Confirmation: two buttons
                            rx.hstack(
                                rx.button(
                                    t("db_really_delete"),
                                    on_click=AIState.clear_db_collection,
                                    size="1",
                                    variant="solid",
                                    color_scheme="red",
                                    cursor="pointer",
                                ),
                                rx.button(
                                    t("db_cancel"),
                                    on_click=AIState.confirm_clear_db,
                                    size="1",
                                    variant="soft",
                                    color_scheme="gray",
                                    cursor="pointer",
                                ),
                                spacing="1",
                            ),
                            # Normal: eraser icon
                            rx.tooltip(
                                rx.icon_button(
                                    rx.icon("eraser", size=16),
                                    on_click=AIState.confirm_clear_db,
                                    size="2",
                                    variant="soft",
                                    color_scheme="red",
                                    cursor="pointer",
                                ),
                                content=t("db_clear_all"),
                            ),
                        ),
                    ),
                    spacing="2",
                    width="100%",
                    align="center",
                ),

                # Entries list
                rx.cond(
                    AIState.db_browser_collection == "",
                    rx.text(
                        t("db_select_hint"),
                        color="#888",
                        font_size="13px",
                        padding_top="20px",
                        text_align="center",
                    ),
                    rx.cond(
                        AIState.db_browser_entries.length() > 0,  # type: ignore[union-attr]
                        rx.vstack(
                            rx.foreach(
                                AIState.db_browser_entries,
                                _db_entry_row,
                            ),
                            spacing="2",
                            width="100%",
                        ),
                        rx.text(
                            t("db_no_entries"),
                            color="#888",
                            font_size="13px",
                        ),
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


def _db_entry_row(entry: rx.Var) -> rx.Component:
    """Render a single database entry — same style as memory entries."""
    return rx.box(
        rx.hstack(
            rx.badge(
                entry["type"],
                variant="soft",
                color_scheme=rx.cond(
                    entry["type"] == "cache", "orange",
                    rx.cond(entry["type"] == "document", "blue", "gray"),
                ),
                font_size="10px",
            ),
            rx.text(entry["date"], font_size="11px", color="#888"),
            rx.spacer(),
            rx.icon_button(
                rx.icon("trash-2", size=12),
                on_click=AIState.delete_db_entry(entry["id"]),
                size="1",
                variant="ghost",
                color_scheme="red",
                cursor="pointer",
            ),
            width="100%",
            align="center",
        ),
        rx.text(
            entry["summary"],
            font_size="15px",
            color="#ddd",
            font_weight="500",
            padding_top="4px",
        ),
        rx.cond(
            entry["content"] != entry["summary"],
            rx.text(
                entry["content"],
                font_size="14px",
                color="#aaa",
                padding_top="4px",
                style={"white_space": "pre-wrap", "max_height": "150px", "overflow_y": "auto"},
            ),
        ),
        padding="10px 12px",
        background="rgba(255,255,255,0.03)",
        border_radius="6px",
        border="1px solid #333",
        width="100%",
    )


# ============================================================
# PLUGINS VIEW (Channel & Tool Plugin Management)
# ============================================================

def _plugin_description_popover(description: str) -> rx.Component:
    """Lightbulb icon + popover showing the plugin description.

    Empty descriptions render an empty box (so column alignment stays
    consistent across the whole list).
    """
    if not description:
        return rx.box(width="14px", flex_shrink="0")
    return rx.popover.root(
        rx.popover.trigger(
            rx.icon("lightbulb", size=14, color="#FFD700", cursor="pointer"),
        ),
        rx.popover.content(
            rx.text(description, font_size="11px", color="white", padding="8px", max_width="320px"),
            side="right",
            style={"background": "#2a2a3e", "border": "1px solid #555", "border-radius": "8px"},
        ),
    )


def _plugins_view() -> rx.Component:
    """Plugins tab: channel and tool plugin management."""
    from ..lib.plugin_registry import all_channels, discover_tools
    from ..lib.security import TIER_I18N_KEYS
    from ..lib.i18n import TranslationManager as _TM
    # Build tier dropdown options per language (select needs static lists)
    _tier_opts = {
        lang: [f"T{tier} — {_TM.get_text(lk, lang)}" for tier, lk, _dk in TIER_I18N_KEYS]
        for lang in ("de", "en")
    }
    tier_options = rx.cond(AIState.ui_language == "de", _tier_opts["de"], _tier_opts["en"])

    # ── Build tool plugin rows at build time (static, like channels) ──
    tool_rows: list[rx.Component] = []
    for plugin in discover_tools():
        name = plugin.name
        enabled_var = AIState.tool_plugin_toggles[name].to(str) == "1"
        has_creds = bool(getattr(plugin, "credential_fields", None))
        description = getattr(plugin, "description", "") or ""

        row_children: list[rx.Component] = [
            # Name column — fixed width keeps lightbulb column aligned across rows
            rx.hstack(
                rx.icon("puzzle", size=14, color=rx.cond(enabled_var, "#4CAF50", "#666")),
                rx.text(plugin.display_name, font_size="14px", color=rx.cond(enabled_var, "white", "#999")),
                spacing="2", align="center", min_width="220px",
            ),
            _plugin_description_popover(description),
            rx.spacer(),
        ]

        if has_creds:
            row_children.append(
                rx.icon_button(
                    rx.icon("settings", size=14),
                    on_click=AIState.open_channel_credentials(name),
                    size="1",
                    variant="ghost",
                    color_scheme="gray",
                    cursor="pointer",
                ),
            )

        row_children.extend([
            rx.switch(
                checked=enabled_var,
                on_change=lambda _val, n=name: AIState.toggle_tool_plugin(n),
                size="1",
            ),
            rx.text(
                rx.cond(enabled_var, "ON", "OFF"),
                font_size="11px",
                color=rx.cond(enabled_var, "#4CAF50", "#999"),
                min_width="24px",
            ),
        ])

        tool_rows.append(
            rx.hstack(*row_children, spacing="2", align="center", width="100%"),
        )

    # ── Build channel rows at build time (static) ──
    channel_rows: list[rx.Component] = []
    for name, plugin in all_channels().items():
        enabled_var = AIState.channel_toggles[name]["monitor"].to(bool)

        # Build tier dropdown for middle column (if not browser)
        _tier_col: list[rx.Component] = []
        if name != "browser":
            _tier_match_de = rx.match(
                AIState.channel_security_tiers[name],
                *[(tier, f"T{tier} — {_TM.get_text(lk, 'de')}") for tier, lk, _dk in TIER_I18N_KEYS],
                f"T1 — {_TM.get_text('tier_1_label', 'de')}",
            )
            _tier_match_en = rx.match(
                AIState.channel_security_tiers[name],
                *[(tier, f"T{tier} — {_TM.get_text(lk, 'en')}") for tier, lk, _dk in TIER_I18N_KEYS],
                f"T1 — {_TM.get_text('tier_1_label', 'en')}",
            )
            _tier_match = rx.cond(AIState.ui_language == "de", _tier_match_de, _tier_match_en)
            _tier_col = [
                rx.cond(
                    enabled_var,
                    rx.select(
                        tier_options,
                        value=_tier_match,
                        on_change=lambda val, ch=name: AIState.set_channel_security_tier([ch, val]),
                        size="1",
                        width="180px",
                    ),
                ),
            ]

        ch_description = getattr(plugin, "description", "") or ""
        header = rx.hstack(
            # Col 1: Icon + Name (fixed width for alignment)
            rx.hstack(
                rx.icon(plugin.icon, size=14, color=rx.cond(enabled_var, "#4CAF50", "#666")),
                rx.text(plugin.display_name, font_size="14px", color=rx.cond(enabled_var, "white", "#999")),
                spacing="2", align="center", min_width="220px",
            ),
            # Col 1b: lightbulb description popover (aligned across rows)
            _plugin_description_popover(ch_description),
            # Col 2: Tier dropdown (fixed position)
            rx.box(*_tier_col, width="190px", flex_shrink="0") if _tier_col else rx.box(width="190px", flex_shrink="0"),
            rx.spacer(),
            # Col 3: Gear + Switch + ON/OFF
            rx.icon_button(
                rx.icon("settings", size=14),
                on_click=AIState.open_channel_credentials(name),
                size="1",
                variant="ghost",
                color_scheme="gray",
                cursor="pointer",
            ),
            rx.switch(
                checked=enabled_var,
                on_change=lambda val, ch=name: AIState.toggle_channel_monitor([ch, val]),
                size="1",
            ),
            rx.text(
                rx.cond(enabled_var, "ON", "OFF"),
                font_size="11px",
                color=rx.cond(enabled_var, "#4CAF50", "#999"),
                min_width="24px",
            ),
            spacing="2",
            align="center",
            width="100%",
        )

        children: list[rx.Component] = [header]

        if not plugin.always_reply:
            monitor_var = AIState.channel_toggles[name]["listener"].to(bool)
            auto_reply_var = AIState.channel_toggles[name]["auto_reply"].to(bool)

            children.append(
                rx.cond(
                    enabled_var,
                    rx.hstack(
                        rx.box(width="14px"),
                        rx.text("Monitor", font_size="11px", color="#999"),
                        rx.spacer(),
                        rx.switch(
                            checked=monitor_var,
                            on_change=lambda val, ch=name: AIState.toggle_channel_listener([ch, val]),
                            size="1",
                        ),
                        rx.text(rx.cond(monitor_var, "ON", "OFF"), font_size="11px", color=rx.cond(monitor_var, "#4CAF50", "#999"), min_width="24px"),
                        spacing="2", align="center", width="100%",
                    ),
                )
            )
            children.append(
                rx.cond(
                    enabled_var & monitor_var,
                    rx.hstack(
                        rx.box(width="14px"),
                        rx.text(t("auto_reply"), font_size="11px", color="#999"),
                        rx.spacer(),
                        rx.switch(
                            checked=auto_reply_var,
                            on_change=lambda val, ch=name: AIState.toggle_channel_auto_reply([ch, val]),
                            size="1",
                        ),
                        rx.text(rx.cond(auto_reply_var, "ON", "OFF"), font_size="11px", color=rx.cond(auto_reply_var, "#4CAF50", "#999"), min_width="24px"),
                        spacing="2", align="center", width="100%",
                    ),
                )
            )

        # Allowlist row (separate from tier)
        ch_plugin = all_channels().get(name)
        if ch_plugin and ch_plugin.has_allowlist:
            children.append(
                rx.cond(
                    enabled_var,
                    rx.hstack(
                        rx.box(width="14px"),
                        rx.icon("shield", size=12, color="#666"),
                        rx.text(
                            AIState.channel_allowlists[name],
                            font_size="10px", color="#888",
                            overflow="hidden", text_overflow="ellipsis",
                            white_space="nowrap",
                        ),
                        spacing="1", align="center", width="100%",
                    ),
                )
            )

        channel_rows.append(rx.vstack(*children, spacing="1", width="100%"))

    return rx.vstack(
        _editor_header(),
        rx.box(
            rx.vstack(
                # Channels
                rx.hstack(
                    rx.text(t("plugin_channels"), font_size="14px", font_weight="bold", color="#999", min_width="220px"),
                    rx.hstack(
                        rx.text(t("security_tiers_title"), font_size="11px", color="#666"),
                        rx.popover.root(
                            rx.popover.trigger(
                                rx.icon("lightbulb", size=14, color="#FFD700", cursor="pointer"),
                            ),
                            rx.popover.content(
                                _tier_help_content(),
                                side="right",
                                style={"background": "#2a2a3e", "border": "1px solid #555", "border-radius": "8px"},
                            ),
                        ),
                        spacing="1", align="center", width="190px",
                    ),
                    rx.spacer(),
                    align="center",
                    width="100%",
                ),
                rx.vstack(*channel_rows, spacing="2", width="100%"),
                rx.divider(),
                # Tool Plugins
                rx.text(t("plugin_tools"), font_size="14px", font_weight="bold", color="#999"),
                rx.vstack(
                    *tool_rows,
                    spacing="2",
                    width="100%",
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


# ============================================================
# SCHEDULER VIEW (Job Management)
# ============================================================

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


# ============================================================
# AUDIT VIEW (Security Audit Log)
# ============================================================

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
                            rx.table.column_header_cell(rx.text("Time", font_size="11px")),
                            rx.table.column_header_cell(rx.text("Source", font_size="11px")),
                            rx.table.column_header_cell(rx.text("Tool", font_size="11px")),
                            rx.table.column_header_cell(rx.text("Tier", font_size="11px")),
                            rx.table.column_header_cell(rx.text("Status", font_size="11px")),
                            rx.table.column_header_cell(rx.text("Duration", font_size="11px")),
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


# ============================================================
# MAIN MODAL
# ============================================================

def agent_editor_modal() -> rx.Component:
    """Settings modal fullscreen overlay."""
    return rx.cond(
        AIState.agent_editor_open,
        rx.box(
            # Backdrop
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.85)",
                on_click=AIState.close_editor_with_dirty_check,
            ),
            # Modal content — switches between tabs
            rx.box(
                rx.match(
                    AIState.agent_editor_mode,
                    ("memory", _memory_view()),
                    ("database", _database_view()),
                    ("plugins", _plugins_view()),
                    ("scheduler", _scheduler_view()),
                    ("audit", _audit_view()),
                    _config_view(),  # default
                ),
                padding="25px",
                background_color="#1a1a1a",
                border_radius="12px",
                max_width="95vw",
                width="750px",
                height="90vh",
                max_height="95vh",
                overflow_x="hidden",
                overflow_y="hidden",
                display="flex",
                flex_direction="column",
                position="relative",
                z_index="1001",
                color="white",
            ),
            # Fullscreen container
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index="1000",
            display="flex",
            justify_content="center",
            align_items="center",
        ),
    )
