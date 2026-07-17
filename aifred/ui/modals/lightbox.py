"""Lightbox: Chat-History-Bilder in Vollansicht."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import overlay_scaffold


def image_lightbox_modal() -> rx.Component:
    """
    Fullscreen Overlay for viewing chat history images full-size.
    Click anywhere to close.
    """
    return overlay_scaffold(
        # Close button (top-right corner)
        rx.box(
            rx.icon("x", size=28, color="white"),
            position="absolute",
            top="20px",
            right="20px",
            cursor="pointer",
            padding="8px",
            border_radius="50%",
            background_color="rgba(255, 255, 255, 0.1)",
            on_click=AIState.close_lightbox,
            z_index="1002",
            custom_attrs={"data-modal-close": "true"},
            style={
                "transition": "background-color 0.2s ease",
                "&:hover": {
                    "background_color": "rgba(255, 255, 255, 0.2)",
                },
            },
        ),

        # Image - centered, click to close
        rx.image(
            src=AIState.lightbox_image_url,
            max_width="90vw",
            max_height="85vh",
            object_fit="contain",
            border_radius="8px",
            on_click=AIState.close_lightbox,
            cursor="pointer",
            position="relative",
            z_index="1001",
        ),
        open_var=AIState.lightbox_open,
        backdrop_color="rgba(0, 0, 0, 0.92)",
        touch_action_none=True,  # Prevent browser scroll
    )
