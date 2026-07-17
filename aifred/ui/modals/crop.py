"""Bild-Zuschnitt-Modal (Crop-Overlay mit JS-Handles aus custom.js)."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t, overlay_scaffold


def crop_modal() -> rx.Component:
    """
    Fullscreen Overlay für Bild-Zuschnitt.
    Verwendet rx.cond statt rx.dialog für bessere Mobile-Kompatibilität.
    """
    return overlay_scaffold(
        # Modal Content - zentriert
        rx.vstack(
            # Header
            rx.hstack(
                rx.icon("crop", size=20, color="white"),
                rx.text(t("crop_modal_title"), color="white", font_weight="bold"),
                spacing="2",
                align="center",
            ),

            # Bild + Crop-Overlay Container
            rx.box(
                # Das Bild
                rx.image(
                    src=AIState.crop_preview_url,
                    id="crop-image",
                    max_width="90vw",
                    max_height="60vh",
                    object_fit="contain",
                    border_radius="8px",
                    display="block",
                ),
                # Crop-Overlay
                rx.box(
                    rx.box(
                        # 4 Ecken
                        rx.box(class_name="crop-handle crop-handle-nw", id="crop-nw"),
                        rx.box(class_name="crop-handle crop-handle-ne", id="crop-ne"),
                        rx.box(class_name="crop-handle crop-handle-sw", id="crop-sw"),
                        rx.box(class_name="crop-handle crop-handle-se", id="crop-se"),
                        # 4 Kanten
                        rx.box(class_name="crop-handle crop-handle-n", id="crop-n"),
                        rx.box(class_name="crop-handle crop-handle-s", id="crop-s"),
                        rx.box(class_name="crop-handle crop-handle-w", id="crop-w"),
                        rx.box(class_name="crop-handle crop-handle-e", id="crop-e"),
                        id="crop-box",
                        class_name="crop-box",
                    ),
                    id="crop-overlay",
                    class_name="crop-overlay",
                ),
                id="crop-container",
                position="relative",
                display="inline-block",  # Passt sich an Bildgröße an
            ),

            # Info-Text
            rx.text(
                t("crop_modal_hint"),
                font_size="12px",
                color="#888",
                text_align="center",
            ),

            # Buttons
            rx.hstack(
                rx.button(
                    t("crop_cancel"),
                    on_click=AIState.cancel_crop,
                    variant="soft",
                    color_scheme="gray",
                    size="3",
                ),
                rx.button(
                    rx.icon("rotate-ccw", size=16),
                    on_click=AIState.rotate_crop_image_left,
                    variant="soft",
                    color_scheme="blue",
                    size="3",
                    title="90° links",
                ),
                rx.button(
                    rx.icon("rotate-cw", size=16),
                    on_click=AIState.rotate_crop_image_right,
                    variant="soft",
                    color_scheme="blue",
                    size="3",
                    title="90° rechts",
                ),
                rx.button(
                    rx.hstack(
                        rx.icon("check", size=16),
                        rx.text(t("crop_apply")),
                        spacing="1",
                    ),
                    on_click=rx.call_script(
                        """
                            (() => {
                                const cropBox = document.getElementById('crop-box');
                                if (cropBox) {
                                    const left = parseFloat(cropBox.style.left) || 0;
                                    const top = parseFloat(cropBox.style.top) || 0;
                                    const width = parseFloat(cropBox.style.width) || 100;
                                    const height = parseFloat(cropBox.style.height) || 100;
                                    return JSON.stringify({ x: left, y: top, width: width, height: height });
                                }
                                return JSON.stringify({ x: 0, y: 0, width: 100, height: 100 });
                            })()
                            """,
                        callback=AIState.apply_crop_with_coords
                    ),
                    color_scheme="green",
                    size="3",
                ),
                spacing="3",
            ),

            spacing="4",
            align="center",
            padding="20px",
            background_color="#1a1a1a",
            border_radius="12px",
            max_width="95vw",
            max_height="90vh",
            position="relative",
            z_index="1001",
        ),
        open_var=AIState.crop_modal_open,
        backdrop_color="rgba(0, 0, 0, 0.85)",
        touch_action_none=True,  # Verhindert Browser-Scroll
    )
