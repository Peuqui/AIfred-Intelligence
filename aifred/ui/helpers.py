"""Shared UI helper components for AIfred."""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from ..theme import COLORS
from ..lib.i18n import TranslationManager


# ============================================================
# MARKDOWN CONFIGURATION
# ============================================================

# Custom component map for rx.markdown - opens links in new tab
MARKDOWN_COMPONENT_MAP = {
    # Links open in new tab with rel="noopener noreferrer" for security
    "a": lambda text, **props: rx.link(text, **props, is_external=True),
    # Tighter paragraph spacing (browser default is 1em top+bottom = too much)
    "p": lambda text, **props: rx.el.p(text, **props, style={"margin_top": "0.5em", "margin_bottom": "0.5em", "line_height": "1.4"}),
}

# Extended component map with list styling (used in some UI areas)
MARKDOWN_COMPONENT_MAP_WITH_LISTS = {
    **MARKDOWN_COMPONENT_MAP,
    "ul": lambda children: rx.el.ul(children, style={"margin_left": "16px", "list_style_type": "disc"}),
    "li": lambda children: rx.el.li(children, style={"margin_bottom": "4px"}),
}


# ============================================================
# TRANSLATION HELPER
# ============================================================

def t(key: str) -> rx.Var:
    """
    Translation helper that returns German or English text based on state.

    Uses centralized translations from i18n.py.
    Returns rx.cond() for Reflex frontend conditional rendering.
    """
    # Get translations from centralized TranslationManager
    de_text = TranslationManager._translations.get("de", {})
    en_text = TranslationManager._translations.get("en", {})

    return rx.cond(
        AIState.ui_language == "de",
        de_text.get(key, key),  # Fallback to key if not found
        en_text.get(key, key)   # Fallback to key if not found
    )


# ============================================================
# AGENT EMOJI HELPER
# ============================================================

# Custom image replaces the Unicode 🎩 for AIfred with the designed top hat
_CUSTOM_EMOJI_MAP: dict[str, str] = {
    "\U0001f3a9": "/AIfred-Zylinder.svg",
}


def agent_emoji(emoji: str, size: str = "1.2em") -> rx.Component:
    """Render an agent emoji — custom image for AIfred's top hat, text for others."""
    if emoji in _CUSTOM_EMOJI_MAP:
        return rx.image(
            src=_CUSTOM_EMOJI_MAP[emoji],
            width=size,
            height=size,
            display="inline-block",
            vertical_align="middle",
            flex_shrink="0",
        )
    return rx.text(emoji, font_size=size, line_height="1", flex_shrink="0")


# ============================================================
# MOBILE NATIVE SELECT HELPERS
# ============================================================

def native_select_backend(value_var, on_change_handler, disabled_condition, backend_list) -> rx.Component:
    """Native HTML <select> for Backend Selection (Mobile)

    Simple approach: value and text are the same (display name like "Ollama").
    Identical pattern to native_select_model.

    Args:
        value_var: State variable for current backend display name (AIState.backend_label)
        on_change_handler: Event handler function (AIState.switch_backend_by_label)
        disabled_condition: Boolean condition to disable the select
        backend_list: List of backend display names (e.g., ["Ollama", "llama.cpp"])
    """
    return rx.el.select(
        # Simple foreach - value and text are the same
        rx.foreach(
            backend_list,
            lambda backend: rx.el.option(
                backend,  # Display: "Ollama"
                value=backend,  # Value: "Ollama" (same!)
            ),
        ),
        value=value_var,
        on_change=on_change_handler,
        disabled=disabled_condition,
        # Dark theme styling for native select (same as native_select_model)
        style={
            "width": "100%",
            "min_width": "120px",  # Ensure minimum width for text
            "flex": "1",  # Take available space in hstack
            "padding": "8px 12px",
            "font_size": "12px",
            "color": COLORS["text_primary"],
            "background": COLORS["input_bg"],
            "border": f"1px solid {COLORS['border']}",
            "border_radius": "6px",
            "min_height": "48px",
            "cursor": "pointer",
            "white_space": "nowrap",
            "overflow": "hidden",
            "text_overflow": "ellipsis",
        },
    )


def native_select_model(value_var, on_change_handler, disabled_condition=False, model_list=None) -> rx.Component:
    """Native HTML <select> for Model Selection (Mobile)

    Uses simple list of display names (like before).
    The value must match one of the options exactly.

    Args:
        value_var: State variable for current value (display name with size)
        on_change_handler: Event handler function
        disabled_condition: Boolean condition to disable the select (default: False)
        model_list: List of model display names (default: AIState.available_models)
    """
    # Default to available_models if no list specified
    models = model_list if model_list is not None else AIState.available_models

    return rx.el.select(
        # Generate options - value and text are the same (display name)
        rx.foreach(
            models,
            lambda model: rx.el.option(
                model,  # Display: "qwen3:8b (2.3 GB)"
                value=model,  # Value: "qwen3:8b (2.3 GB)" (same!)
            ),
        ),
        value=value_var,
        on_change=on_change_handler,
        disabled=disabled_condition,
        # Dark theme styling for native select - Model names visible
        style={
            "width": "100%",
            "padding": "8px 12px",
            "font_size": "12px",  # Smaller font to fit more text
            "color": COLORS["text_primary"],
            "background": COLORS["input_bg"],
            "border": f"1px solid {COLORS['border']}",
            "border_radius": "6px",
            "min_height": "48px",  # Touch-friendly
            "cursor": "pointer",
            "white_space": "nowrap",  # Don't wrap long model names
            "overflow": "hidden",  # Hide overflow
            "text_overflow": "ellipsis",  # Show ... if text is too long
        },
    )


def native_select_tts(value_var, on_change_handler, options_list) -> rx.Component:
    """Native HTML <select> for TTS Settings (Mobile)

    ``options_list`` is a list of ``{label, disabled}`` dicts — GPU-TTS
    engines without a calibrated profile for the current model render
    as disabled <option>s. Same styling as backend/model selects.
    """
    return rx.el.select(
        rx.foreach(
            options_list,
            lambda option: rx.el.option(
                option["label"],
                value=option["label"].to(str),
                disabled=option["disabled"].to(bool),
            ),
        ),
        value=value_var,
        on_change=on_change_handler,
        style={
            "width": "100%",
            "flex": "1",
            "padding": "8px 12px",
            "font_size": "12px",
            "color": COLORS["text_primary"],
            "background": COLORS["input_bg"],
            "border": f"1px solid {COLORS['border']}",
            "border_radius": "6px",
            "min_height": "48px",
            "cursor": "pointer",
            "white_space": "nowrap",
            "overflow": "hidden",
            "text_overflow": "ellipsis",
        },
    )


def native_select_stt(value_var, on_change_handler, options_list) -> rx.Component:
    """Native HTML <select> for STT Settings (Mobile)

    Same styling as backend/model selects for consistent mobile experience.
    """
    return rx.el.select(
        rx.foreach(
            options_list,
            lambda option: rx.el.option(option, value=option),
        ),
        value=value_var,
        on_change=on_change_handler,
        style={
            "width": "100%",
            "flex": "1",
            "padding": "8px 12px",
            "font_size": "12px",
            "color": COLORS["text_primary"],
            "background": COLORS["input_bg"],
            "border": f"1px solid {COLORS['border']}",
            "border_radius": "6px",
            "min_height": "48px",
            "cursor": "pointer",
            "white_space": "nowrap",
            "overflow": "hidden",
            "text_overflow": "ellipsis",
        },
    )


def native_select_generic(value_var, on_change_handler, options_pairs) -> rx.Component:
    """Native HTML <select> for generic key/value options (Mobile)

    Args:
        value_var: State variable for current value (the key, e.g., "auto_consensus")
        on_change_handler: Handler for value changes
        options_pairs: List of [key, display_label] pairs, e.g., [["standard", "Standard"], ["auto_consensus", "Auto-Konsens"]]

    Compact styling to match pill buttons in the control row.
    """
    return rx.el.select(
        rx.foreach(
            options_pairs,
            lambda pair: rx.el.option(pair[1], value=pair[0]),  # pair[0]=key, pair[1]=label
        ),
        value=value_var,
        on_change=on_change_handler,
        style={
            "width": "auto",
            "padding": "4px 10px",
            "font_size": "12px",
            "color": COLORS["text_primary"],
            "background": COLORS["input_bg"],
            "border": f"1px solid {COLORS['border']}",
            "border_radius": "6px",
            "height": "32px",
            "cursor": "pointer",
            "white_space": "nowrap",
            "overflow": "hidden",
            "text_overflow": "ellipsis",
        },
    )


# ============================================================
# CLICKABLE TOOLTIP (replaces rx.tooltip for mobile support)
# ============================================================

def clickable_tip(trigger: rx.Component, content: str | rx.Component) -> rx.Component:
    """A tooltip that opens on click/tap instead of hover.

    Works on both desktop and mobile. Replaces rx.tooltip and rx.hover_card.

    Args:
        trigger: The component that triggers the tooltip (e.g., icon button)
        content: Text string or rx.Component to show in the popup
    """
    body: rx.Component
    if isinstance(content, str):
        body = rx.text(content, font_size="12px", color="#ddd")
    else:
        body = content

    return rx.popover.root(
        rx.popover.trigger(trigger),
        rx.popover.content(
            body,
            style={
                "background": "#222",
                "border": "1px solid #555",
                "border_radius": "8px",
                "padding": "8px 12px",
                "max_width": "300px",
                "z_index": "9999",
            },
        ),
    )


# ============================================================
# SIMPLE COMPONENT HELPERS
# ============================================================

def left_column() -> rx.Component:
    """Complete left column with all input controls"""
    from .input_sections import text_input_section

    return rx.vstack(
        text_input_section(),
        spacing="4",
        width="100%",
    )
