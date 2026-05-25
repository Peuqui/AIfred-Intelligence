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
                    source["resolution_options"].to(list[dict[str, str]]),
                    lambda opt: rx.select.item(opt["label"], value=opt["value"]),
                ),
            ),
            value=source["resolution"].to(str),
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
    """One image in the grid — ``entry`` is {id, label, image_url}.

    Layout-wise the tile is a flex column where the image fills the
    remaining vertical space and shrinks proportionally with ``object-
    fit: contain`` so neither dimension creates a scrollbar in the
    popup window.
    """
    return rx.box(
        rx.text(
            entry["label"], size="1", color="gray", weight="bold",
            style={"flex_shrink": "0"},
        ),
        rx.image(
            src=entry["image_url"],
            alt="Live preview",
            border_radius="8px",
            background_color="#111",
            style={
                "max_width": "100%",
                "max_height": "100%",
                "width": "auto",
                "height": "auto",
                "object_fit": "contain",
                "min_height": "0",
                "flex": "1 1 auto",
            },
        ),
        style={
            "display": "flex",
            "flex_direction": "column",
            "gap": "0.25em",
            "min_height": "0",
            "flex": "1 1 auto",
            "width": "100%",
            "align_items": "center",
        },
    )


def _image_grid() -> rx.Component:
    """Grid of all visible sources. Single-source case ↔ wide single tile.
    Multi-source case ↔ 2-column responsive grid.

    The grid is flex-1 inside the popup column layout so it takes
    whatever vertical space remains after the header + source-list,
    and its children shrink to fit (``min_height: 0`` is the magic
    that lets flex children actually shrink instead of overflowing).
    """
    return rx.cond(
        AIState.vision_preview_has_visible,
        rx.box(
            rx.foreach(AIState.vision_preview_visible_entries, _image_tile),
            style={
                "display": "grid",
                "grid_template_columns":
                    "repeat(auto-fit, minmax(280px, 1fr))",
                "gap": "0.75em",
                "width": "100%",
                "flex": "1 1 auto",
                "min_height": "0",
                "overflow": "hidden",
            },
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
            style={
                "display": "flex",
                "flex_direction": "column",
                "align_items": "center",
                "justify_content": "center",
                "width": "100%",
                "flex": "1 1 auto",
                "min_height": "0",
                "background_color": "#1a1a1a",
                "border_radius": "8px",
            },
        ),
    )


def vision_preview_page() -> rx.Component:
    """Page-Komponente für die Route ``/vision-preview-popup`` — Multi-
    Source Live-Preview, geöffnet als eigenständiges Browser-Fenster.

    Whole page is a 100vh flex column that hides overflow, so the
    image grid sizes itself to the remaining vertical space and the
    image proportionally shrinks with the window. No scrollbars.
    """
    return rx.box(
        rx.box(
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
            style={
                "display": "flex",
                "flex_direction": "column",
                "gap": "0.5em",
                "flex_shrink": "0",
            },
        ),
        _image_grid(),
        rx.cond(
            AIState.vision_preview_status != "",
            rx.callout.root(
                rx.callout.icon(rx.icon("info")),
                rx.callout.text(AIState.vision_preview_status),
                color_scheme="amber",
            ),
        ),
        style={
            "display": "flex",
            "flex_direction": "column",
            "gap": "0.5em",
            "padding": "1em",
            "height": "100vh",
            "width": "100%",
            "overflow": "hidden",
            "box_sizing": "border-box",
        },
    )
