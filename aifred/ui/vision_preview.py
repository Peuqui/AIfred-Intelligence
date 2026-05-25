"""Vision Live-Preview popup page — multi-source grid with per-cam controls.

Geöffnet als eigenes Browser-Fenster (siehe ``open_vision_preview`` in
``_vision_preview_mixin.py``). Layout:

  [Header: globale FPS-Dropdown + Refresh + Rescan-Buttons]
  ─────────────────────────────────────────────────────────
  [Source-Liste mit Per-Source-Controls — Toggle + Resolution]
  ─────────────────────────────────────────────────────────
  [Image-Grid — rx.foreach über visible_sources]

Multi-Source: jede sichtbare Source bekommt ein eigenes ``<img>`` mit
MJPEG-Stream-URL und passenden Query-Params (fps, width/height,
cache-buster). Single-Source ist der degenerierte Fall mit nur einem
Element im Grid.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from .helpers import t


def _header_row() -> rx.Component:
    """Global FPS dropdown + refresh + rescan buttons."""
    return rx.hstack(
        rx.text(t("vision_preview_fps_label"), size="2", color="gray"),
        rx.select.root(
            rx.select.trigger(),
            rx.select.content(
                rx.foreach(
                    AIState.vision_preview_fps_options,
                    lambda opt: rx.select.item(opt["label"], value=opt["value"]),
                ),
            ),
            value=AIState.vision_preview_fps_value,
            on_change=AIState.set_vision_preview_fps,
        ),
        rx.cond(
            AIState.vision_preview_is_manual_mode,
            rx.icon_button(
                rx.icon("refresh-cw", size=14),
                on_click=AIState.refresh_vision_preview,
                size="2",
                variant="soft",
                color_scheme="gray",
                title=t("vision_preview_refresh_tooltip"),
            ),
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


def _source_row(source: rx.Var) -> rx.Component:
    """One row in the source list: toggle + name + per-source resolution.

    ``source`` is a foreach item — dict Var with keys ``id``, ``label``,
    ``available``, ``resolution`` (pre-computed in _refresh_sources).
    """
    sid = source["id"]
    return rx.hstack(
        rx.switch(
            checked=AIState.vision_preview_visible_sources.contains(sid),
            on_change=lambda _checked: AIState.toggle_vision_preview_source(sid),
            size="1",
        ),
        rx.text(source["label"], size="2", style={"flex": "1", "min_width": "0"}),
        rx.select.root(
            rx.select.trigger(),
            rx.select.content(
                rx.foreach(
                    AIState.vision_preview_resolution_options,
                    lambda opt: rx.select.item(opt["label"], value=opt["value"]),
                ),
            ),
            value=source["resolution"],
            on_change=lambda v: AIState.set_vision_preview_resolution(sid, v),
        ),
        spacing="3",
        align="center",
        width="100%",
    )


def _source_list() -> rx.Component:
    return rx.vstack(
        rx.foreach(AIState.vision_preview_sources, _source_row),
        align="stretch",
        spacing="2",
        width="100%",
    )


def _image_tile(entry: rx.Var) -> rx.Component:
    """One image in the grid — ``entry`` is {id, label, image_url}."""
    return rx.vstack(
        rx.text(entry["label"], size="1", color="gray", weight="bold"),
        rx.image(
            src=entry["image_url"],
            alt="Live preview",
            width="100%",
            height="auto",
            object_fit="contain",
            border_radius="8px",
            background_color="#111",
        ),
        spacing="1",
        align="stretch",
        width="100%",
    )


def _image_grid() -> rx.Component:
    """Grid of all visible sources. Single-source case ↔ wide single tile.
    Multi-source case ↔ 2-column responsive grid."""
    return rx.cond(
        AIState.vision_preview_has_visible,
        rx.box(
            rx.foreach(AIState.vision_preview_visible_entries, _image_tile),
            display="grid",
            grid_template_columns=[
                "1fr",                  # mobile: 1 col always
                "1fr",
                "repeat(auto-fit, minmax(360px, 1fr))",  # desktop: as many as fit
            ],
            gap="0.75em",
            width="100%",
        ),
        rx.box(
            rx.icon("camera-off", size=32, color="gray"),
            rx.text(
                t("vision_preview_no_source"),
                color="gray",
                size="2",
                margin_top="0.5em",
                text_align="center",
            ),
            display="flex",
            flex_direction="column",
            align_items="center",
            justify_content="center",
            width="100%",
            height="40vh",
            background_color="#1a1a1a",
            border_radius="8px",
        ),
    )


def vision_preview_page() -> rx.Component:
    """Page-Komponente für die Route ``/vision-preview-popup`` — Multi-
    Source Live-Preview, geöffnet als eigenständiges Browser-Fenster."""
    return rx.container(
        rx.vstack(
            # Title row
            rx.hstack(
                rx.icon("video", size=20),
                rx.text(
                    t("vision_preview_title"),
                    font_weight="bold",
                    size="4",
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
            _header_row(),
            rx.divider(),
            rx.text(t("vision_preview_sources_label"), size="2", weight="bold"),
            _source_list(),
            rx.divider(),
            _image_grid(),
            rx.cond(
                AIState.vision_preview_status != "",
                rx.callout.root(
                    rx.callout.icon(rx.icon("info")),
                    rx.callout.text(AIState.vision_preview_status),
                    color_scheme="amber",
                ),
            ),
            align="stretch",
            spacing="3",
            width="100%",
        ),
        padding="1em",
        width="100%",
        max_width="100%",
        overflow_x="hidden",
    )
