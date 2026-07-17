"""Agent-Editor: Config-Tab — Dropdown, Metadata, TTS, Tools, Prompts."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t
from .header import _editor_header
from .config_sections import (
    _action_buttons,
    _dirty_warning,
    _prompts_section,
    _tools_section,
    _tts_section,
)


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
def _agent_selector_row(is_new: rx.Var, is_automatik: rx.Var, is_default: rx.Var) -> rx.Component:
    # ── Agent Selector Row ──────────────────────────
    return rx.hstack(
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
        # Export bundle (not during create, not Automatik)
        rx.cond(
            ~is_new & ~is_automatik,
            rx.tooltip(
                rx.icon_button(
                    rx.icon("package", size=16),
                    on_click=AIState.open_bundle_export,
                    size="2", variant="soft",
                    color_scheme="blue", cursor="pointer",
                ),
                content=t("agent_editor_export_tooltip"),
            ),
        ),
        # Import bundle (always visible — even mid-create)
        rx.cond(
            ~is_automatik,
            rx.tooltip(
                rx.icon_button(
                    rx.icon("package-open", size=16),
                    on_click=AIState.open_bundle_import,
                    size="2", variant="soft",
                    color_scheme="cyan", cursor="pointer",
                ),
                content=t("agent_editor_import_tooltip"),
            ),
        ),
        spacing="2",
        align="center",
        width="100%",
    )


def _metadata_row(is_new: rx.Var, is_automatik: rx.Var) -> rx.Component:
    # ── Metadata (hidden for Automatik) ────────────
    return rx.cond(~is_automatik, rx.hstack(
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
    ))  # end rx.cond(~is_automatik) for Metadata


def _system_model_section(is_system: rx.Var) -> rx.Component:
    # Cloud model selector + reasoning toggle — only for system
    # agents that drive Cloud-LLM workflows (e.g. calibration uses Qwen).
    return rx.cond(
        is_system,
        rx.vstack(
            # on_mount on the wrapper (fires reliably, unlike on a
            # Radix select) → fetch the live model list as soon as
            # the system-agent block is shown.
            rx.hstack(
                rx.text(t("agent_editor_cloud_provider"), color="#aaa", font_size="12px"),
                rx.select(
                    AIState.editor_cloud_provider_options,
                    value=AIState.editor_cloud_provider_label,
                    on_change=AIState.set_editor_cloud_provider,
                    size="2",
                    width="170px",
                ),
                rx.text(t("agent_editor_cloud_model"), color="#aaa", font_size="12px"),
                rx.select(
                    AIState.editor_cloud_model_options,
                    value=AIState.editor_model,
                    on_change=AIState.set_editor_model,
                    size="2",
                    width="280px",
                ),
                rx.tooltip(
                    rx.hstack(
                        rx.icon("brain", size=14, color=rx.cond(
                            AIState.editor_system_reasoning, "#FFD700", "#666",
                        )),
                        rx.switch(
                            checked=AIState.editor_system_reasoning,
                            on_change=AIState.toggle_editor_system_reasoning,
                            size="1",
                        ),
                        rx.text(t("agent_editor_reasoning_toggle"), font_size="12px", color="#aaa"),
                        spacing="2",
                        align="center",
                    ),
                    content=t("agent_editor_reasoning_tooltip"),
                ),
                spacing="3",
                align="center",
            ),
            rx.box(
                rx.text(
                    t("agent_editor_cloud_model_hint"),
                    color="#888",
                    font_size="11px",
                    line_height="1.5",
                ),
                padding="8px 10px",
                border_left="2px solid #444",
                background="rgba(255,255,255,0.02)",
                border_radius="0 4px 4px 0",
                width="100%",
            ),
            width="100%",
            spacing="2",
            align="start",
            on_mount=AIState.refresh_editor_cloud_models,
        ),
    )


def _role_description_row(is_automatik: rx.Var, is_system: rx.Var) -> rx.Component:
    # Role + Description (hidden for Automatik and system agents)
    return rx.cond(~is_automatik & ~is_system, rx.hstack(
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
    ))


def _config_view() -> rx.Component:
    """Config tab: agent dropdown at top, all settings below."""
    is_new = AIState.editor_agent_id == ""
    is_automatik = AIState.editor_agent_id == "automatik"
    # System-role agents (e.g. calibration) get a locked-down editor:
    # only metadata + model + prompts visible; no tools, TTS or sampling.
    is_system = AIState.editor_is_system_agent
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
                _agent_selector_row(is_new, is_automatik, is_default),
                _metadata_row(is_new, is_automatik),
                _system_model_section(is_system),
                _role_description_row(is_automatik, is_system),
                _tts_section(is_new, is_automatik, is_system),
                _tools_section(is_new, is_automatik, is_system),
                _prompts_section(is_new),
                _dirty_warning(),
                _action_buttons(is_new),
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
