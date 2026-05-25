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
        rx.spacer(),
        # VLM analysis cooldown
        rx.text(t("vision_preview_cooldown_label"), size="2", color="gray"),
        rx.select.root(
            rx.select.trigger(),
            rx.select.content(
                rx.foreach(
                    AIState.vision_preview_cooldown_options,
                    lambda opt: rx.select.item(opt["label"], value=opt["value"]),
                ),
            ),
            value=AIState.vision_preview_vlm_cooldown_value,
            on_change=AIState.set_vision_preview_vlm_cooldown,
        ),
        # Teleprompter layout toggle — right side of the header
        rx.text(t("vision_preview_teleprompter_mode_label"), size="2", color="gray"),
        rx.select.root(
            rx.select.trigger(),
            rx.select.content(
                rx.select.item(
                    t("vision_preview_teleprompter_mode_overlay"), value="overlay",
                ),
                rx.select.item(
                    t("vision_preview_teleprompter_mode_below"), value="below",
                ),
            ),
            value=AIState.vision_preview_teleprompter_mode,
            on_change=AIState.set_vision_preview_teleprompter_mode,
        ),
        spacing="2",
        align="center",
        width="100%",
    )


def _source_row(source: rx.Var) -> rx.Component:
    """One row in the source list — the per-cam manager.

    Layout: visibility-switch + editable alias-input + watch-switch +
    resolution-dropdown (right-aligned). Watch ist ein normaler rx.switch
    — der Button-Variante (grün/rot wie Aufnahme) folgt in einem
    separaten Schritt, sobald wir sicher sind dass rx.cond mit zwei
    rx.button im Reflex-Compile stabil ist.
    """
    sid = source["id"]
    return rx.hstack(
        rx.switch(
            checked=AIState.vision_preview_visible_sources.contains(sid),
            on_change=lambda _checked: AIState.toggle_vision_preview_source(sid),
            size="1",
            color_scheme="orange",
        ),
        rx.input(
            default_value=source["alias"].to(str),
            placeholder=source["hardware_name"].to(str),
            on_blur=lambda v: AIState.set_vision_preview_alias(sid, v),
            size="2",
            style={"flex": "0 0 220px", "min_width": "0"},
        ),
        rx.text(t("vision_preview_watch_label"), size="1", color="gray"),
        rx.switch(
            checked=AIState.vision_preview_watching.contains(sid),
            on_change=lambda _checked: AIState.toggle_vision_preview_watch(sid),
            size="1",
            color_scheme="red",
        ),
        # Spacer schiebt die Auflösung nach rechts ans Zeilenende.
        rx.spacer(),
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
            style={"flex": "0 0 240px", "min_width": "200px"},
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


def _alias_overlay(entry: rx.Var) -> rx.Component:
    """Top-left semi-transparent label showing the camera alias."""
    return rx.box(
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
            "z_index": "2",
        },
    )


def _teleprompter_overlay(entry: rx.Var) -> rx.Component:
    """Subtitle-style overlay at the bottom of the image. The inner
    div has data-vlm-source so the page-level MutationObserver script
    can attach an EventSource as soon as the tile is rendered."""
    return rx.box(
        rx.box(
            t("vision_preview_teleprompter_idle"),
            class_name="vlm-event-target vlm-event-overlay",
            custom_attrs={"data-vlm-source": entry["id"]},
            style={
                "color": "rgba(255,255,255,0.85)",
                "font_style": "italic",
                "font_size": "0.85em",
                "white_space": "pre-wrap",
            },
        ),
        style={
            "position": "absolute",
            "bottom": "0.5em",
            "left": "0.5em",
            "right": "0.5em",
            "padding": "0.4em 0.6em",
            "background_color": "rgba(0, 0, 0, 0.55)",
            "border_radius": "6px",
            "max_height": "30%",
            "overflow_y": "auto",
            "pointer_events": "none",
            "z_index": "2",
        },
    )


def _teleprompter_below(entry: rx.Var) -> rx.Component:
    """Full-width block below the image. Same data-vlm-source hook
    as the overlay variant so the script attaches an EventSource
    regardless of which layout the user picked."""
    return rx.box(
        rx.text(
            t("vision_preview_teleprompter_label"),
            size="1", color="gray", weight="bold",
            style={"margin_bottom": "0.25em"},
        ),
        rx.box(
            t("vision_preview_teleprompter_idle"),
            class_name="vlm-event-target vlm-event-below",
            custom_attrs={"data-vlm-source": entry["id"]},
            style={
                "min_height": "80px",
                "max_height": "150px",
                "overflow_y": "auto",
                "padding": "0.5em",
                "background_color": "rgba(0, 0, 0, 0.3)",
                "border_radius": "6px",
                "border": "1px solid var(--gray-6)",
                "font_size": "0.85em",
                "color": "gray",
                "font_style": "italic",
                "white_space": "pre-wrap",
            },
        ),
        style={
            "width": "100%",
            "flex_shrink": "0",
        },
    )


def _image_tile(entry: rx.Var) -> rx.Component:
    """One full per-cam workspace in the grid.

    Layout: image (left) + briefing (right) side-by-side. The
    teleprompter is either an overlay ON the image (compact, default,
    needed when many cams share the screen) or a full-width block
    BELOW the image (longer text, single-cam mode). Toggle in the
    header.
    """
    sid = entry["id"]
    overlay_mode = AIState.vision_preview_teleprompter_mode == "overlay"
    return rx.box(
        # Row 1: image (with optional teleprompter overlay) + briefing
        rx.box(
            rx.box(
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
                    },
                ),
                _alias_overlay(entry),
                rx.cond(overlay_mode, _teleprompter_overlay(entry), rx.fragment()),
                style={
                    "position": "relative",
                    "flex": "2 1 0",
                    "min_width": "0",
                    "display": "flex",
                    "align_items": "center",
                    "justify_content": "center",
                },
            ),
            rx.box(
                # Briefing-Header (Watch-Toggle ist jetzt in der Source-Row).
                rx.text(
                    t("vision_preview_briefing_label"),
                    size="1", color="gray", weight="bold",
                    style={"margin_bottom": "0.25em"},
                ),
                rx.text_area(
                    # Controlled component: ``default_value`` würde den
                    # initialen (leeren) State festschreiben, das spätere
                    # _refresh_sources-Update käme im DOM nie an. Mit
                    # ``value=`` reagiert das Feld auf jeden State-Wechsel
                    # — also auch auf den nachgeladenen prompt_context.
                    value=entry["prompt_context"],
                    placeholder=t("vision_preview_briefing_placeholder"),
                    on_change=lambda v: AIState.set_vision_preview_briefing_text(sid, v),
                    on_blur=lambda v: AIState.set_vision_preview_prompt_context(sid, v),
                    size="2",
                    resize="vertical",
                    style={
                        "width": "100%",
                        "height": "100%",
                        "min_height": "120px",
                        "font_family": "var(--default-font-family)",
                    },
                ),
                style={
                    "flex": "1 1 0",
                    "min_width": "180px",
                    "display": "flex",
                    "flex_direction": "column",
                },
            ),
            style={
                "display": "flex",
                "flex_direction": "row",
                "gap": "0.5em",
                "width": "100%",
                "flex": "1 1 auto",
                "min_height": "0",
            },
        ),
        # Row 2: teleprompter as a below-block (only in 'below' mode)
        rx.cond(overlay_mode, rx.fragment(), _teleprompter_below(entry)),
        style={
            "display": "flex",
            "flex_direction": "column",
            "gap": "0.5em",
            "width": "100%",
            "flex": "1 1 auto",
            "min_height": "0",
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




_VLM_SSE_INLINE = r"""
console.log('[VLM-SSE-Inline] script tag executed');
(function () {
  function boot() {
    console.log('[VLM-SSE-Inline] boot() running');
    if (window.__aifredVLMSSEInit) { console.log('[VLM-SSE-Inline] already init'); return; }
    window.__aifredVLMSSEInit = true;
    var streams = {};
    var lines = {};
    var MAX_LINES = 8;
    function render(sid) {
      var items = lines[sid] || [];
      document.querySelectorAll('.vlm-event-target[data-vlm-source="' + sid + '"]').forEach(function (el) {
        if (items.length === 0) {
          if (el.textContent !== (el.dataset.idleText || '')) {
            el.textContent = el.dataset.idleText || '';
          }
          el.style.fontStyle = 'italic';
          return;
        }
        el.style.fontStyle = 'normal';
        var newText = items.join('\n');
        if (el.textContent !== newText) {
          el.textContent = newText;
          el.scrollTop = el.scrollHeight;
        }
      });
    }
    function openStream(sid) {
      if (streams[sid] && streams[sid].readyState !== 2) return;
      console.log('[VLM-SSE-Inline] opening EventSource for ' + sid);
      var es = new EventSource('/api/vision/events/' + sid);
      streams[sid] = es;
      lines[sid] = lines[sid] || [];
      es.onmessage = function (ev) {
        try {
          var data = JSON.parse(ev.data);
          var ts = (data.timestamp || '').split('T')[1] || '';
          var desc = (data.description || '').replace(/\s+/g, ' ').trim();
          lines[sid].push(ts + '  ' + desc);
          if (lines[sid].length > MAX_LINES) lines[sid].shift();
          render(sid);
        } catch (e) {}
      };
    }
    function scan() {
      var targets = document.querySelectorAll('.vlm-event-target[data-vlm-source]');
      var seen = new Set();
      targets.forEach(function (el) {
        var sid = el.dataset.vlmSource;
        if (!sid) return;
        if (!el.dataset.idleText) el.dataset.idleText = el.textContent;
        seen.add(sid);
        openStream(sid);
        render(sid);
      });
      Object.keys(streams).forEach(function (sid) {
        if (!seen.has(sid) && streams[sid]) { streams[sid].close(); delete streams[sid]; }
      });
    }
    // KEIN MutationObserver — der würde mit unseren eigenen
    // textContent-Updates eine Endlos-Schleife bilden und das Frontend
    // einfrieren. Stattdessen alle 2s neu scannen: deckt Toggle
    // visible/Watch und Resolution-Wechsel ab; reicht völlig weil der
    // EventSource selbst persistent ist.
    scan();
    setInterval(scan, 2000);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
"""


def vision_preview_page() -> rx.Component:
    """Page-Komponente für die Route ``/vision-preview-popup`` — Multi-
    Source Live-Preview, geöffnet als eigenständiges Browser-Fenster.

    Whole page is a 100vh flex column that hides overflow, so the
    image grid sizes itself to the remaining vertical space and the
    image proportionally shrinks with the window. No scrollbars.

    VLM-SSE-Manager wird hier inline geladen — die Hauptseite hat
    custom.js dafür, aber das Popup ist ein eigenes Lazy-Bundle.
    """
    return rx.box(
        rx.script(_VLM_SSE_INLINE),
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
