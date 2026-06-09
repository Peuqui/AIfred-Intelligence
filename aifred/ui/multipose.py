"""Multipose — geführtes Multi-Pose-Enrollment-Modal.

Workflow:
1. Name eingeben (oder vorausgefüllt bei „bestehende Person erweitern")
2. Cam-Quelle wählen (default: erste verfügbare)
3. Für jede Pose: Anweisung lesen, Live-Bild ansehen, „Aufnehmen"
   klicken — Snapshot wird detektiert + Embedding extrahiert
4. Am Ende: „Speichern" → alle Embeddings in den VisionStore

Global gemountet in aifred.py, sichtbar bei
``AIState.multipose_open == True``.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from .helpers import t


def _capture_thumb(cap: rx.Var) -> rx.Component:
    return rx.image(
        src=cap["preview_data_url"],
        style={
            "width": "56px",
            "height": "56px",
            "border_radius": "4px",
            "object_fit": "cover",
            "border": "1px solid var(--gray-7)",
            "flex_shrink": "0",
        },
    )


def _capture_strip() -> rx.Component:
    """Horizontale Bilder-Leiste mit allen bisher aufgenommenen Posen."""
    return rx.cond(
        AIState.multipose_captures.length() > 0,
        rx.box(
            rx.hstack(
                rx.foreach(AIState.multipose_captures, _capture_thumb),
                spacing="2",
                align="center",
            ),
            style={
                "width": "100%",
                "overflow_x": "auto",
                "padding": "0.5em 0",
            },
        ),
    )


def _name_input() -> rx.Component:
    """Bei neuer Person Eingabefeld, bei bestehender nur read-only Anzeige."""
    return rx.cond(
        AIState.multipose_is_existing,
        rx.hstack(
            rx.text(t("multipose_name_label"), size="2", color="gray"),
            rx.text(AIState.multipose_name, size="2", font_weight="bold"),
            spacing="2",
            align="center",
            width="100%",
        ),
        rx.vstack(
            rx.text(t("multipose_name_label"), size="2", color="gray"),
            rx.input(
                placeholder=t("multipose_name_placeholder"),
                value=AIState.multipose_name,
                on_change=AIState.multipose_set_name,
                size="2",
                width="100%",
            ),
            spacing="1",
            width="100%",
        ),
    )


def _source_picker() -> rx.Component:
    return rx.vstack(
        rx.text(t("multipose_source_label"), size="2", color="gray"),
        rx.select.root(
            rx.select.trigger(width="100%"),
            rx.select.content(
                rx.foreach(
                    AIState.multipose_source_options,
                    lambda opt: rx.select.item(opt["label"], value=opt["value"]),
                ),
            ),
            value=AIState.multipose_source_id,
            on_change=AIState.multipose_set_source,
        ),
        spacing="1",
        width="100%",
    )


def _live_preview() -> rx.Component:
    """Zentrale Live-Vorschau (data-URL aus Hub-Snapshot)."""
    return rx.cond(
        AIState.multipose_live_preview_url != "",
        rx.image(
            src=AIState.multipose_live_preview_url,
            style={
                "width": "100%",
                "max_height": "320px",
                "object_fit": "contain",
                "border_radius": "8px",
                "border": "1px solid var(--gray-6)",
                "background_color": "var(--gray-3)",
            },
        ),
        rx.box(
            rx.icon("camera-off", size=32, color="gray"),
            rx.text(
                t("multipose_no_preview"),
                color="gray",
                size="2",
                margin_top="0.5em",
            ),
            style={
                "width": "100%",
                "min_height": "200px",
                "display": "flex",
                "flex_direction": "column",
                "align_items": "center",
                "justify_content": "center",
                "background_color": "var(--gray-3)",
                "border": "1px dashed var(--gray-7)",
                "border_radius": "8px",
            },
        ),
    )


def _step_instruction() -> rx.Component:
    """Anweisungs-Text für die aktuelle Pose. Holt den i18n-Key aus
    ``multipose_current_pose_key`` und lokalisiert via dynamischem t()
    — der Key ist ein State-String, daher rx.match für die festen
    Pose-Keys."""
    pose_key = AIState.multipose_current_pose_key
    return rx.callout.root(
        rx.callout.icon(rx.icon("info")),
        rx.callout.text(
            rx.match(
                pose_key,
                ("multipose_pose_frontal", t("multipose_pose_frontal")),
                ("multipose_pose_left", t("multipose_pose_left")),
                ("multipose_pose_right", t("multipose_pose_right")),
                ("multipose_pose_up", t("multipose_pose_up")),
                ("multipose_pose_down", t("multipose_pose_down")),
                ("multipose_pose_done", t("multipose_pose_done")),
                t("multipose_pose_frontal"),
            ),
        ),
        color_scheme="orange",
        size="1",
    )


def _action_buttons() -> rx.Component:
    """Aufnehmen / Überspringen / Wiederholen / Speichern."""
    return rx.cond(
        AIState.multipose_finished,
        rx.hstack(
            rx.button(
                rx.icon("rotate-ccw", size=14),
                rx.text(t("multipose_retry")),
                on_click=AIState.multipose_retry_current,
                size="2",
                variant="soft",
                color_scheme="gray",
                disabled=AIState.multipose_captures.length() == 0,
            ),
            rx.spacer(),
            rx.button(
                rx.icon("check", size=14),
                rx.text(t("multipose_save")),
                on_click=AIState.multipose_finish,
                size="2",
                color_scheme="orange",
                disabled=~AIState.multipose_can_save,
            ),
            spacing="2",
            width="100%",
        ),
        rx.hstack(
            rx.button(
                rx.icon("rotate-ccw", size=14),
                rx.text(t("multipose_retry")),
                on_click=AIState.multipose_retry_current,
                size="2",
                variant="soft",
                color_scheme="gray",
                disabled=AIState.multipose_captures.length() == 0,
            ),
            rx.button(
                rx.icon("skip-forward", size=14),
                rx.text(t("multipose_skip")),
                on_click=AIState.multipose_skip_current,
                size="2",
                variant="soft",
                color_scheme="gray",
            ),
            rx.spacer(),
            rx.button(
                rx.icon("camera", size=14),
                rx.text(t("multipose_capture")),
                on_click=AIState.multipose_capture,
                size="2",
                color_scheme="orange",
                loading=AIState.multipose_busy,
            ),
            spacing="2",
            width="100%",
        ),
    )


def multipose_modal() -> rx.Component:
    return rx.cond(
        AIState.multipose_open,
        rx.box(
            # Backdrop
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.5)",
                on_click=AIState.close_multipose,
            ),
            rx.box(
                rx.vstack(
                    # Header
                    rx.hstack(
                        rx.icon("user-cog", size=20),
                        rx.text(
                            t("multipose_title"),
                            font_weight="bold",
                            size="4",
                        ),
                        rx.spacer(),
                        rx.text(
                            AIState.multipose_progress_label,
                            size="1",
                            color="gray",
                            style={"font_family": "monospace"},
                        ),
                        rx.icon_button(
                            rx.icon("x", size=16),
                            on_click=AIState.close_multipose,
                            size="1",
                            variant="ghost",
                            color_scheme="gray",
                            custom_attrs={"data-modal-close": "true"},
                        ),
                        spacing="2",
                        align="center",
                        width="100%",
                    ),
                    rx.text(
                        t("multipose_subtitle"),
                        color="gray",
                        size="2",
                        margin_bottom="0.25em",
                    ),
                    rx.divider(),
                    _name_input(),
                    _source_picker(),
                    rx.divider(),
                    _step_instruction(),
                    _live_preview(),
                    # Kontinuierliche Live-Preview: tickt nur solange das Modal
                    # offen ist (der Handler no-opt sonst) → frisches Bild zum
                    # Pose-Prüfen.
                    rx.moment(
                        interval=1500,
                        on_change=AIState.multipose_live_tick,
                        display="none",
                    ),
                    rx.text(
                        t("multipose_captures_label"),
                        size="1",
                        color="gray",
                    ),
                    _capture_strip(),
                    rx.cond(
                        AIState.multipose_status != "",
                        rx.callout.root(
                            rx.callout.icon(rx.icon("info")),
                            rx.callout.text(AIState.multipose_status),
                            color_scheme="amber",
                            size="1",
                        ),
                    ),
                    rx.divider(),
                    _action_buttons(),
                    align="stretch",
                    spacing="2",
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
                width="min(560px, 95vw)",
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
