"""Vision-Plugin settings modal — opened from the Plugin-Tab gear icon.

Uses rx.cond + absolute-positioned rx.box (analog to crop_modal in
modals.py) instead of rx.dialog — Mobile-Kompatibilität und konsistent
mit dem Rest der UI. i18n über den t()-Helper aus ui/helpers.py.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from .helpers import t


def _mode_section() -> rx.Component:
    return rx.vstack(
        rx.text(t("vision_settings_mode_label"), font_weight="bold", size="3"),
        rx.text(t("vision_settings_mode_help"), color="gray", size="1"),
        rx.select.root(
            rx.select.trigger(width="100%"),
            rx.select.content(
                rx.select.item(t("vision_settings_mode_off"), value="off"),
                rx.select.item(t("vision_settings_mode_ondemand"), value="on-demand"),
                rx.select.item(t("vision_settings_mode_live"), value="live"),
            ),
            value=AIState.vision_mode_value,
            on_change=AIState.set_vision_mode_value,
        ),
        align="stretch",
        spacing="1",
        width="100%",
    )


def _model_section() -> rx.Component:
    return rx.vstack(
        rx.text(t("vision_settings_model_label"), font_weight="bold", size="3"),
        rx.text(t("vision_settings_model_help"), color="gray", size="1"),
        rx.hstack(
            # Box-Wrapper bekommt den flex-Grow — direkt am
            # rx.select.root wirkt das style-Prop in Reflex 0.8
            # nicht zuverlässig. ``min_width: 0`` lässt den Wrapper
            # schrumpfen, damit der Refresh-Button nicht rechts aus
            # dem Modal geschoben wird.
            rx.box(
                rx.select.root(
                    rx.select.trigger(width="100%"),
                    rx.select.content(
                        rx.foreach(
                            AIState.vision_available_models,
                            lambda m: rx.select.item(m, value=m),
                        ),
                    ),
                    value=AIState.vision_model_value,
                    on_change=AIState.set_vision_model_value,
                ),
                style={
                    "flex": "1 1 0",
                    "min_width": "0",
                    "overflow": "hidden",
                },
            ),
            rx.icon_button(
                rx.icon("refresh-cw", size=14),
                on_click=AIState.rescan_vision_models,
                size="2",
                variant="soft",
                color_scheme="gray",
                title=t("vision_settings_rescan_tooltip"),
                style={"flex_shrink": "0"},
            ),
            spacing="2",
            align="center",
            width="100%",
            style={"min_width": "0"},
        ),
        align="stretch",
        spacing="1",
        width="100%",
    )


def _face_recognition_section() -> rx.Component:
    """Toggle für Face-Recognition + Retention-Input. Beide schreiben
    in plugins/tools/vision/settings.json."""
    return rx.vstack(
        rx.hstack(
            rx.text(
                t("vision_settings_face_recognition_label"),
                font_weight="bold", size="3",
            ),
            rx.spacer(),
            rx.switch(
                checked=AIState.face_recognition_enabled,
                on_change=AIState.set_face_recognition_enabled,
                size="2",
                color_scheme="orange",
            ),
            align="center",
            width="100%",
        ),
        rx.text(
            t("vision_settings_face_recognition_help"),
            color="gray", size="1",
        ),
        # Retention-Input
        rx.hstack(
            rx.text(
                t("vision_settings_face_retention_label"),
                size="2", color="gray",
            ),
            rx.input(
                type="number",
                default_value=AIState.face_retention_days.to(str),
                on_blur=AIState.set_face_retention_days,
                size="2",
                min=1,
                max=3650,
                style={"width": "5em"},
            ),
            align="center",
            spacing="2",
            style={"margin_top": "0.5em"},
        ),
        rx.text(
            t("vision_settings_face_retention_help"),
            color="gray", size="1",
        ),
        align="stretch",
        spacing="1",
        width="100%",
    )


def vision_settings_modal() -> rx.Component:
    """Modal globally mounted in aifred.py. Visible only when
    ``AIState.vision_settings_open`` is True. Closes on backdrop click or
    on the close button (top right + bottom)."""
    return rx.cond(
        AIState.vision_settings_open,
        rx.box(
            # Backdrop (klickbar zum Schließen) — gleiches Pattern wie crop_modal
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.5)",
                on_click=AIState.close_vision_settings,
            ),
            # Modal content — zentriert, kompakte Box
            rx.box(
                rx.vstack(
                    # Header: Titel + X-Close
                    rx.hstack(
                        rx.icon("camera", size=20),
                        rx.text(
                            t("vision_settings_title"),
                            font_weight="bold",
                            size="4",
                        ),
                        rx.spacer(),
                        rx.icon_button(
                            rx.icon("x", size=16),
                            on_click=AIState.close_vision_settings,
                            size="1",
                            variant="ghost",
                            color_scheme="gray",
                        ),
                        spacing="2",
                        align="center",
                        width="100%",
                    ),
                    rx.text(
                        t("vision_settings_subtitle"),
                        color="gray",
                        size="2",
                        margin_bottom="0.5em",
                    ),
                    rx.divider(),
                    _mode_section(),
                    rx.divider(),
                    _model_section(),
                    rx.divider(),
                    _face_recognition_section(),
                    rx.divider(),
                    rx.button(
                        t("vision_settings_close"),
                        on_click=AIState.close_vision_settings,
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
                width="min(540px, 92vw)",
                max_height="92vh",
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
