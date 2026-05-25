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
    """One row in the source list — the per-cam manager.

    Layout: visibility-switch + editable alias-input + (hardware-name as
    placeholder/tooltip) + resolution-dropdown. The alias is what the
    user types ("Türkamera"); empty → falls back to hardware name.
    On blur the new alias gets persisted into vision_store via
    ``set_vision_preview_alias`` — tools then pick it up immediately
    (vision_list_sources returns the alias, the chat-bubble image
    uses it as the markdown alt-text).
    """
    sid = source["id"]
    return rx.hstack(
        rx.switch(
            checked=AIState.vision_preview_visible_sources.contains(sid),
            on_change=lambda _checked: AIState.toggle_vision_preview_source(sid),
            size="1",
        ),
        rx.input(
            default_value=source["alias"].to(str),
            placeholder=source["hardware_name"].to(str),
            on_blur=lambda v: AIState.set_vision_preview_alias(sid, v),
            size="2",
            style={"flex": "1", "min_width": "0"},
        ),
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

    Layout: a relative-positioned flex column. The image fills the
    remaining vertical space (``object-fit: contain`` keeps the aspect
    ratio without scrollbars). The camera label sits ON TOP of the
    image as a semi-transparent overlay — client-side only, the JPEG
    bytes stay clean so the VLM doesn't accidentally describe the
    text in the picture.
    """
    return rx.box(
        rx.image(
            src=entry["image_url"],
            alt=entry["label"],
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
        # Label overlay — sits at top-left of the image. pointer-events:
        # none so it doesn't intercept clicks if anything below it is
        # interactive later. Semi-transparent black background with
        # white text reads on any image content.
        rx.box(
            entry["label"],
            style={
                "position": "absolute",
                "top": "0.5em",
                "left": "0.5em",
                "padding": "2px 8px",
                "background_color": "rgba(0, 0, 0, 0.65)",
                "color": "#fff",
                "font_size": "0.85em",
                "font_weight": "bold",
                "border_radius": "4px",
                "pointer_events": "none",
                "max_width": "calc(100% - 1em)",
                "overflow": "hidden",
                "text_overflow": "ellipsis",
                "white_space": "nowrap",
            },
        ),
        style={
            "position": "relative",
            "display": "flex",
            "flex_direction": "column",
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
