"""Agent-Editor: Config-Tab-Sektionen — TTS, Tools, Prompt-Layer, Aktionen."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t
from .tool_pills import _tier_help_content, _tool_groups


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


def _tts_section(is_new: rx.Var, is_automatik: rx.Var, is_system: rx.Var) -> rx.Component:
    # ── TTS Settings (only existing agents, not Automatik or system) ─
    return rx.cond(
        ~is_new & ~is_automatik & ~is_system,
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
                    # 0.05 steps in the speech-natural range so
                    # there's a value between 0.8 and 0.9 (0.85);
                    # coarser at the fast extremes. All previous
                    # values (incl. 1.25x) stay on this grid.
                    [
                        "0.5x", "0.55x", "0.6x", "0.65x", "0.7x",
                        "0.75x", "0.8x", "0.85x", "0.9x", "0.95x",
                        "1.0x", "1.05x", "1.1x", "1.15x", "1.2x",
                        "1.25x", "1.3x", "1.35x", "1.4x", "1.45x",
                        "1.5x", "1.75x", "2.0x",
                    ],
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
            # Language override (Auto = detected language / UI fallback).
            # Greyed out for engines that ignore the language
            # setting — Fish-Speech auto-detects, Edge/Piper/eSpeak
            # encode the language in the voice itself.
            rx.hstack(
                rx.text("Sprache", font_size="11px", color="#aaa", width="55px"),
                rx.box(
                    rx.select(
                        AIState.tts_language_labels,
                        value=AIState.editor_agent_tts_language,
                        on_change=AIState.set_editor_agent_tts_language,
                        disabled=~AIState.editor_tts_supports_language,
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
            spacing="2",
            width="100%",
            padding="10px",
            background_color="#222",
            border_radius="8px",
            border="1px solid #333",
            overflow="hidden",
        ),
    )


def _tools_section(is_new: rx.Var, is_automatik: rx.Var, is_system: rx.Var) -> rx.Component:
    # ── Tool Whitelist (only existing agents, not Automatik or system) ──
    return rx.cond(
        ~is_new & ~is_automatik & ~is_system,
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
    )


def _prompts_section(is_new: rx.Var) -> rx.Component:
    # ── Prompt Layer Editor (only existing agents) ──
    return rx.cond(
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
    )


def _dirty_warning() -> rx.Component:
    # ── Unsaved Changes Warning ─────────────────────
    return rx.cond(
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
    )


def _action_buttons(is_new: rx.Var) -> rx.Component:
    # ── Action Buttons ──────────────────────────────
    return rx.hstack(
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
    )
