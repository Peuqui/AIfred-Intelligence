"""Shared UI helper components for AIfred."""

from __future__ import annotations

from typing import Any

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
# FULLSCREEN OVERLAY MODAL SCAFFOLD
# ============================================================

def _overlay_backdrop(
    color: str,
    on_click: Any = None,
    *,
    fixed: bool = False,
    z_index: str | None = None,
) -> rx.Component:
    """Abdunkelnder Fullscreen-Backdrop hinter einem Modal.

    Standard: absolut positioniert, füllt den fixed Container.
    ``fixed=True`` rendert die Variante der Audit-/Bundle-Modals
    (position fixed, 100vw/100vh, eigener z-index).
    """
    props: dict[str, Any] = {}
    if on_click is not None:
        props["on_click"] = on_click
    if fixed:
        return rx.box(
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            background_color=color,
            z_index=z_index,
            **props,
        )
    return rx.box(
        position="absolute",
        top="0",
        left="0",
        width="100%",
        height="100%",
        background_color=color,
        **props,
    )


def overlay_modal(
    open_var: rx.Var | bool,
    body: rx.Component,
    *extra: rx.Component,
    on_close: Any,
    width: str,
    z_index: str = "9999",
) -> rx.Component:
    """Fullscreen-Modal, Familie A (Vigilantia/Casus/Personarium/…).

    Struktur: rx.cond(open) → fixed Fullscreen-Container → Backdrop
    (rgba 0.5, Klick schließt) → Content-Box mit Standard-Styling,
    per translate(-50%, -50%) zentriert. ``extra`` hängt weitere
    Geschwister hinter die Content-Box (z.B. Sub-Overlays).
    """
    return rx.cond(
        open_var,
        rx.box(
            _overlay_backdrop("rgba(0, 0, 0, 0.5)", on_close),
            rx.box(
                body,
                position="absolute",
                top="50%",
                left="50%",
                transform="translate(-50%, -50%)",
                background_color="var(--gray-2)",
                border="1px solid var(--gray-6)",
                border_radius="12px",
                padding="1.5em",
                width=width,
                max_height="92vh",
                overflow_y="auto",
                box_shadow="0 20px 60px rgba(0,0,0,0.5)",
            ),
            *extra,
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index=z_index,
        ),
    )


def overlay_scaffold(
    *children: rx.Component,
    open_var: rx.Var | bool | None = None,
    backdrop_color: str,
    backdrop_on_click: Any = rx.stop_propagation,
    z_index: str = "1000",
    backdrop_fixed: bool = False,
    flex_center: bool = True,
    touch_action_none: bool = False,
) -> rx.Component:
    """Fullscreen-Overlay-Gerüst, Familie B (Help-Modals/Pages/Lightbox).

    Fixed Fullscreen-Container mit Backdrop; die ``children`` behalten
    ihr eigenes Styling (inkl. z-index über dem Backdrop).
    ``flex_center`` zentriert per Flexbox; ohne ``open_var`` wird das
    Overlay unbedingt gerendert (Vollbild-Pages mit eigener Route).
    """
    outer: dict[str, Any] = dict(
        position="fixed",
        top="0",
        left="0",
        width="100vw",
        height="100vh",
        z_index=z_index,
    )
    if flex_center:
        outer.update(display="flex", justify_content="center", align_items="center")
    if touch_action_none:
        outer["style"] = {"touch_action": "none"}
    backdrop = _overlay_backdrop(
        backdrop_color,
        backdrop_on_click,
        fixed=backdrop_fixed,
        z_index=z_index if backdrop_fixed else None,
    )
    overlay = rx.box(backdrop, *children, **outer)
    if open_var is None:
        return overlay
    return rx.cond(open_var, overlay)


# ============================================================
# MOBILE NATIVE SELECT HELPERS
# ============================================================

# Gemeinsames Dark-Theme-Styling der nativen <select>-Elemente (Mobile).
_NATIVE_SELECT_STYLE: dict[str, str] = {
    "width": "100%",
    "padding": "8px 12px",
    "font_size": "12px",
    "color": COLORS["text_primary"],
    "background": COLORS["input_bg"],
    "border": f"1px solid {COLORS['border']}",
    "border_radius": "6px",
    "min_height": "48px",  # Touch-friendly
    "cursor": "pointer",
    "white_space": "nowrap",  # Don't wrap long entries
    "overflow": "hidden",  # Hide overflow
    "text_overflow": "ellipsis",  # Show ... if text is too long
}

# Kompakt-Variante für die Pill-Button-Reihe (feste 32px statt Touch-Höhe).
_NATIVE_SELECT_STYLE_COMPACT: dict[str, str] = {
    **{k: v for k, v in _NATIVE_SELECT_STYLE.items() if k != "min_height"},
    "width": "auto",
    "padding": "4px 10px",
    "height": "32px",
}

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
            **_NATIVE_SELECT_STYLE,
            "min_width": "120px",  # Ensure minimum width for text
            "flex": "1",  # Take available space in hstack
        },
    )


def native_select_model(value_var, on_change_handler, disabled_condition=False, model_list=None, rich_list=None) -> rx.Component:
    """Native HTML <select> for Model Selection (Mobile)

    Uses simple list of display names (like before).
    The value must match one of the options exactly.

    Args:
        value_var: State variable for current value (display name with size)
        on_change_handler: Event handler function
        disabled_condition: Boolean condition to disable the select (default: False)
        model_list: List of model display names (default: AIState.available_models)
        rich_list: Optional list of ``{name, badge, ...}`` dicts. When given,
            the option text becomes ``name + " " + badge`` (e.g. the Vision
            dropdown's ``⚡ No Swap`` / ``🔄 Swap`` marker) while the option
            *value* stays the plain ``name`` so selection resolves cleanly.
            A native <select> can't colour the badge — the emoji carries it.
    """
    if rich_list is not None:
        options = rx.foreach(
            rich_list,
            lambda row: rx.el.option(
                rx.cond(row["badge"] != "", row["name"] + "  " + row["badge"], row["name"]),
                value=row["name"],
            ),
        )
    else:
        # Default to available_models if no list specified
        models = model_list if model_list is not None else AIState.available_models
        options = rx.foreach(
            models,
            lambda model: rx.el.option(
                model,  # Display: "qwen3:8b (2.3 GB)"
                value=model,  # Value: "qwen3:8b (2.3 GB)" (same!)
            ),
        )

    return rx.el.select(
        options,
        value=value_var,
        on_change=on_change_handler,
        disabled=disabled_condition,
        # Dark theme styling for native select - Model names visible
        style=_NATIVE_SELECT_STYLE,
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
        style={**_NATIVE_SELECT_STYLE, "flex": "1"},
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
        style={**_NATIVE_SELECT_STYLE, "flex": "1"},
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
        style=_NATIVE_SELECT_STYLE_COMPACT,
    )


# ============================================================
# ACTION BUTTON STYLES
# ============================================================

def blue_action_button_style(press_effect: bool = True) -> dict[str, Any]:
    """Blauer Outline-Action-Button-Style (Audio-Upload/Share/Download).

    ``press_effect=True`` ergänzt Hover-/Active-Transform (scale) und den
    Active-Hintergrund; ``False`` liefert die reine Hover-Variante des
    Audio-Upload-Buttons.
    """
    style: dict[str, Any] = {
        "background": "rgba(0, 50, 100, 0.4)",
        "color": "#58a6ff",
        "border_color": "#58a6ff",
        "&:hover:not([disabled])": {
            "background": "rgba(0, 80, 150, 0.6) !important",
        },
        "&[disabled]": {"opacity": "0.45"},
    }
    if press_effect:
        style["&:hover:not([disabled])"] = {
            "background": "rgba(0, 80, 150, 0.6) !important",
            "transform": "scale(1.02)",
        }
        style["&:active:not([disabled])"] = {
            "background": "rgba(0, 40, 80, 0.7) !important",
            "transform": "scale(0.98)",
        }
    return style


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
