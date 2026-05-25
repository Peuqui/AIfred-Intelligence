"""Vision Live-Preview Modal — JPEG-Snapshot mit Source-Switch und Refresh-Button.

Stil analog zu vision_settings_modal und crop_modal: rx.cond +
absolute-positioned rx.box. Backdrop-Click und X-Icon schließen. Image
wird per ``<img>``-Tag aus dem Backend-Endpoint
``/api/vision/snapshot/{source_id}`` geladen, Cache-Buster im Query-
String erzwingt frischen Fetch bei jedem Refresh.

Phase 1: manueller Refresh-Button + Source-Switcher. Auto-Refresh kommt
in Phase 2 (rx.interval), genauso wie das verschiebbare Modal.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from .helpers import t


def _source_switcher() -> rx.Component:
    return rx.hstack(
        rx.select.root(
            rx.select.trigger(width="100%"),
            rx.select.content(
                rx.foreach(
                    AIState.vision_preview_sources,
                    lambda s: rx.select.item(s["label"], value=s["id"]),
                ),
            ),
            value=AIState.vision_preview_source,
            on_change=AIState.set_vision_preview_source,
        ),
        rx.icon_button(
            rx.icon("refresh-cw", size=14),
            on_click=AIState.refresh_vision_preview,
            size="2",
            variant="soft",
            color_scheme="gray",
            title=t("vision_preview_refresh_tooltip"),
        ),
        rx.icon_button(
            rx.icon("scan-search", size=14),
            on_click=AIState.rescan_vision_preview_sources,
            size="2",
            variant="soft",
            color_scheme="gray",
            title=t("vision_preview_rescan_tooltip"),
        ),
        spacing="2",
        align="center",
        width="100%",
    )


def _image_area() -> rx.Component:
    return rx.box(
        rx.cond(
            AIState.vision_preview_source != "",
            rx.image(
                src=AIState.vision_preview_image_url,
                alt="Webcam Live-Preview",
                width="100%",
                height="auto",
                max_height="60vh",
                object_fit="contain",
                border_radius="8px",
                background_color="#111",
            ),
            rx.box(
                rx.icon("camera-off", size=32, color="gray"),
                rx.text(
                    t("vision_preview_no_source"),
                    color="gray",
                    size="2",
                    margin_top="0.5em",
                ),
                display="flex",
                flex_direction="column",
                align_items="center",
                justify_content="center",
                width="100%",
                height="200px",
                background_color="#1a1a1a",
                border_radius="8px",
            ),
        ),
        width="100%",
    )


def vision_preview_modal() -> rx.Component:
    """Globally mounted in main + agent-editor layouts."""
    return rx.cond(
        AIState.vision_preview_open,
        rx.box(
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.6)",
                on_click=AIState.close_vision_preview,
            ),
            rx.box(
                rx.vstack(
                    rx.hstack(
                        rx.icon("video", size=20),
                        rx.text(
                            t("vision_preview_title"),
                            font_weight="bold",
                            size="4",
                        ),
                        rx.spacer(),
                        rx.icon_button(
                            rx.icon("x", size=16),
                            on_click=AIState.close_vision_preview,
                            size="1",
                            variant="ghost",
                            color_scheme="gray",
                        ),
                        spacing="2",
                        align="center",
                        width="100%",
                    ),
                    rx.text(
                        t("vision_preview_subtitle"),
                        color="gray",
                        size="2",
                    ),
                    rx.divider(),
                    _source_switcher(),
                    _image_area(),
                    rx.cond(
                        AIState.vision_preview_status != "",
                        rx.callout.root(
                            rx.callout.icon(rx.icon("info")),
                            rx.callout.text(AIState.vision_preview_status),
                            color_scheme="amber",
                        ),
                    ),
                    rx.button(
                        t("vision_preview_close"),
                        on_click=AIState.close_vision_preview,
                        variant="soft",
                        width="100%",
                    ),
                    align="stretch",
                    spacing="3",
                    width="100%",
                ),
                position="absolute",
                top="50%",
                left="50%",
                transform="translate(-50%, -50%)",
                background_color="var(--gray-2)",
                border="1px solid var(--gray-6)",
                border_radius="12px",
                padding="1.5em",
                width="min(720px, 94vw)",
                max_height="94vh",
                overflow_y="auto",
                box_shadow="0 20px 60px rgba(0,0,0,0.5)",
            ),
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index="9999",
        ),
    )
