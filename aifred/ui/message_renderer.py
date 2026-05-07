"""Message rendering components for AIfred chat display."""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from ..theme import COLORS
from .helpers import (
    MARKDOWN_COMPONENT_MAP,
    t,
)


# ============================================================
# IMAGE THUMBNAILS
# ============================================================

def render_history_thumbnail(img_data) -> rx.Component:
    """Render clickable thumbnail that opens lightbox on click.

    Args:
        img_data: Either a string URL or a dict with {"name": str, "url": str}
    """
    # Handle both string URLs and dict format
    img_url = rx.cond(
        img_data.is_string(),
        img_data,
        img_data["url"]
    )
    return rx.image(
        src=img_url,
        width="50px",
        height="50px",
        object_fit="cover",
        border_radius="4px",
        cursor="pointer",
        border=f"1px solid {COLORS['border']}",
        on_click=AIState.open_lightbox(img_url),  # type: ignore[call-arg, func-returns-value]
        style={
            "transition": "transform 0.2s ease, box-shadow 0.2s ease",
            # Mobile touch handling - prevent native context menu
            "touch_action": "manipulation",
            "user_select": "none",
            "-webkit_touch_callout": "none",
            "-webkit_user_select": "none",
            "&:hover": {
                "transform": "scale(1.05)",
                "box_shadow": "0 2px 8px rgba(0,0,0,0.3)",
            },
            # Mobile active state (visual feedback on tap)
            "&:active": {
                "transform": "scale(0.95)",
                "opacity": "0.8",
            },
        },
    )


def render_image_thumbnails(images) -> rx.Component:
    """Render row of clickable image thumbnails"""
    return rx.cond(
        images.length() > 0,
        rx.hstack(
            rx.foreach(images, render_history_thumbnail),
            spacing="2",
            margin_bottom="2",
            flex_wrap="wrap",
        ),
        rx.fragment(),  # Empty component when no images
    )


# ============================================================
# MESSAGE BUBBLES
# ============================================================

def render_user_message(msg: dict) -> rx.Component:
    """Render user message (right-aligned with emoji outside on the right)"""
    # NOTE: Since this is called from rx.cond, msg fields are already extracted as Vars
    # We can use them directly in rx components
    return rx.box(
        rx.hstack(
            rx.box(
                rx.text(
                    rx.cond(
                        AIState.user_name != "",
                        AIState.user_name,
                        "User"
                    ),
                    font_weight="bold",
                    font_size="12px",
                    color="#c06050",
                    margin_bottom="1",
                    text_align="right",
                ),
                # Content (text message)
                rx.markdown(msg["content"], color=COLORS["user_text"], font_size="13px", component_map=MARKDOWN_COMPONENT_MAP, overflow_x="auto"),
                padding="12px 16px 12px 12px",
                border_radius="6px",
                max_width="70%",
                background_color=COLORS["user_msg"],
            ),
            rx.text("\U0001f64b", font_size="13px"),
            spacing="2",
            align="start",
            justify="end",
            width="100%",
        ),
        padding="2",
        border_radius="8px",
        border="1px solid rgba(255, 255, 255, 0.1)",
        width="100%",
        margin_bottom="3",
    )


# ============================================================
# AUDIO BUTTONS
# ============================================================

def render_bubble_audio_buttons(msg: dict) -> rx.Component:
    """Render play and regenerate buttons for audio replay.

    KISS: Buttons werden IMMER gerendert mit display:none.
    JavaScript (initBubbleAudioButtons) zeigt sie und bindet click-handler.
    Das umgeht alle rx.cond Typ-Probleme mit Dict-Feldern.
    """
    return rx.hstack(
        # Play button - disabled during TTS regeneration
        rx.el.button(
            rx.icon("volume-2", size=18),  # type: ignore[operator]
            type="button",
            title=t("audio_play_tooltip"),
            disabled=AIState.tts_regenerating,
            **{"data-audio-urls": msg["audio_urls_json"]},
            class_name="bubble-audio-btn",
            style={
                "display": "none",  # JS zeigt Button wenn Audio vorhanden
                "opacity": rx.cond(AIState.tts_regenerating, "0.3", "0.6"),
                "cursor": rx.cond(AIState.tts_regenerating, "not-allowed", "pointer"),
                "padding": "6px 8px",
                "background": "transparent",
                "border": "none",
                "border_radius": "4px",
                "align_items": "center",
            },
        ),
        # (Re)Generate button — sichtbar sobald TTS global aktiv ist.
        # JS (initBubbleRegenerateButtons) wechselt den Tooltip je nachdem,
        # ob fuer diese Bubble bereits Audio existiert (regenerate) oder
        # noch nicht (initial generate).
        rx.button(
            rx.cond(
                AIState.tts_regenerating,
                rx.spinner(size="1"),
                rx.icon("refresh-cw", size=16),  # type: ignore[operator]
            ),
            on_click=lambda: AIState.resynthesize_bubble_tts(msg.get("timestamp", "")),  # type: ignore[call-arg]
            title=t("audio_generate_tooltip"),
            size="1",
            variant="ghost",
            color_scheme="gray",
            disabled=AIState.tts_regenerating,
            class_name="bubble-regenerate-btn",
            **{
                "data-audio-urls": msg["audio_urls_json"],
                "data-tooltip-generate": t("audio_generate_tooltip"),
                "data-tooltip-regenerate": t("audio_regenerate_tooltip"),
            },
            style={
                "display": rx.cond(AIState.enable_tts, "inline-flex", "none"),
                "opacity": rx.cond(AIState.tts_regenerating, "0.3", "0.5"),
                "cursor": rx.cond(AIState.tts_regenerating, "not-allowed", "pointer"),
                "padding": "6px 8px",
                "min_width": "auto",
            },
        ),
        spacing="1",
        align="center",
    )


# ============================================================
# ASSISTANT MESSAGE (with agent styling)
# ============================================================

def _agent_bubble(
    msg: dict,
    emoji: str,
    name: str,
    name_color: str,
    inner_bg: str,
    outer_bg: str,
    border_color: str,
) -> rx.Component:
    """Helper: Render a single agent message bubble with consistent layout."""
    return rx.box(
        rx.hstack(
            rx.text(emoji, font_size="13px"),
            rx.box(
                rx.hstack(
                    rx.text(name, font_weight="bold", font_size="12px", color=name_color),
                    render_bubble_audio_buttons(msg),
                    spacing="2",
                    align="center",
                    margin_bottom="1",
                ),
                rx.markdown(
                    msg["content"],
                    color=COLORS["ai_text"],
                    font_size="13px",
                    component_map=MARKDOWN_COMPONENT_MAP,
                    overflow_x="auto",
                ),
                background_color=inner_bg,
                padding="12px 16px 12px 12px",
                border_radius="6px",
                width="100%",
            ),
            spacing="2",
            align="start",
            justify="start",
            width="100%",
        ),
        background_color=outer_bg,
        padding="2",
        border_radius="8px",
        border=f"1px solid {border_color}",
        width="100%",
        margin_bottom="3",
    )


def render_assistant_message(msg: dict) -> rx.Component:
    """Render assistant message (left-aligned, styled per agent).

    Uses agent_display_name and agent_emoji from chat_history dict.
    Sokrates and Salomo get their signature colors, all others use primary.
    """
    return rx.cond(
        msg["agent"] == "sokrates",
        _agent_bubble(
            msg, msg["agent_emoji"], msg["agent_display_name"], "#cd7f32",
            "rgba(205, 127, 50, 0.08)", "rgba(205, 127, 50, 0.03)", "rgba(205, 127, 50, 0.3)",
        ),
        rx.cond(
            msg["agent"] == "salomo",
            _agent_bubble(
                msg, msg["agent_emoji"], msg["agent_display_name"], "#daa520",
                "rgba(218, 165, 32, 0.08)", "rgba(218, 165, 32, 0.03)", "rgba(218, 165, 32, 0.3)",
            ),
            _agent_bubble(
                msg, msg["agent_emoji"], msg["agent_display_name"],
                COLORS["primary"],
                COLORS["ai_msg"], "rgba(255, 255, 255, 0.03)", "rgba(255, 255, 255, 0.1)",
            ),
        ),
    )


# ============================================================
# SYSTEM MESSAGE
# ============================================================

def render_system_message(msg: dict) -> rx.Component:
    """Render system message (summary) as a centered, subtle bubble."""
    return rx.box(
        rx.markdown(
            msg["content"],
            color=COLORS["text_secondary"],
            font_size="12px",
            component_map=MARKDOWN_COMPONENT_MAP,
        ),
        background_color="rgba(125, 133, 144, 0.06)",
        border=f"1px solid {COLORS['border']}",
        border_radius="8px",
        padding="12px 16px 12px 12px",
        margin_y="2",
        width="90%",
        margin_x="auto",
    )


# ============================================================
# MESSAGE DISPATCHER
# ============================================================

def render_message_standalone(msg: dict) -> rx.Component:
    """
    Rendert eine einzelne Message aus chat_history (dict-based).

    Uses rx.cond to branch on message role since msg is a Reflex Var.
    content-visibility: auto skips rendering for off-screen messages entirely,
    so the browser only pays layout/paint cost for visible bubbles.
    """
    return rx.box(
        rx.cond(
            msg["role"] == "user",
            render_user_message(msg),
            rx.cond(
                msg["role"] == "assistant",
                render_assistant_message(msg),
                # system or unknown
                render_system_message(msg)
            )
        ),
        style={
            "content_visibility": "auto",
            "contain_intrinsic_size": "auto 150px",
        },
        width="100%",
    )
