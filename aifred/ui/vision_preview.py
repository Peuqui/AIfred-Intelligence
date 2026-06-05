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


def _header_group(
    label: rx.Var | str,
    *controls: rx.Component,
    wrap_label: bool = False,
) -> rx.Component:
    """Eine Header-Gruppe: Label oben, Controls unten. Wird in
    ``_header_row`` mehrfach genutzt, damit das Header bei Bedarf
    in zwei Zeilen umbricht (flex-wrap) statt rechts aus dem Modal
    zu laufen.

    Bei ``wrap_label=True`` darf das Label selbst umbrechen — sinnvoll
    für lange deutsche Wörter wie „Gesichtserkennung", die sonst die
    Spalte weit machen.
    """
    label_style = {"line_height": "1.1"}
    if not wrap_label:
        label_style["white_space"] = "nowrap"
    else:
        # Erzwungener Umbruch am Bindestrich + Fallback ``overflow-wrap``
        # für lange Wörter ohne natürlichen Trennpunkt.
        label_style["overflow_wrap"] = "anywhere"
    return rx.vstack(
        rx.text(label, size="1", color="gray", style=label_style),
        rx.hstack(*controls, spacing="1", align="center"),
        spacing="1",
        align="start",
        style={
            "flex_shrink": "0",
            "max_width": "6em" if wrap_label else "none",
        },
    )


def _header_row() -> rx.Component:
    """Header mit Gruppen (Label oben, Control unten). ``flex-wrap``
    auf dem äußeren Container lässt das Header bei schmalen Fenstern
    automatisch in zwei Reihen umbrechen, statt rechts überzulaufen.
    """
    return rx.box(
        _header_group(
            t("vision_preview_fps_label"),
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
        ),
        _header_group(
            t("vision_preview_vlm_model_label"),
            rx.select.root(
                rx.select.trigger(),
                rx.select.content(
                    rx.foreach(
                        AIState.vision_available_models,
                        lambda m: rx.select.item(m, value=m),
                    ),
                ),
                value=AIState.vision_model_value,
                on_change=AIState.set_vision_model_value,
            ),
            # Power-Toggle: VLM-Modell in/aus Ollama-VRAM laden.
            # Solid + orange = geladen, soft + gray = entladen.
            rx.tooltip(
                rx.icon_button(
                    rx.icon("power", size=14),
                    on_click=AIState.toggle_vlm_model_loaded,
                    size="2",
                    variant=rx.cond(
                        AIState.vlm_model_loaded, "solid", "soft",
                    ),
                    color_scheme=rx.cond(
                        AIState.vlm_model_loaded, "orange", "gray",
                    ),
                    loading=AIState.vlm_model_busy,
                ),
                content=rx.cond(
                    AIState.vlm_model_loaded,
                    t("vlm_unload_tooltip"),
                    t("vlm_load_tooltip"),
                ),
            ),
        ),
        _header_group(
            t("vision_preview_cooldown_label"),
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
        ),
        _header_group(
            t("vision_preview_face_throttle_label"),
            rx.select.root(
                rx.select.trigger(),
                rx.select.content(
                    rx.foreach(
                        AIState.vision_preview_face_throttle_options,
                        lambda opt: rx.select.item(opt["label"], value=opt["value"]),
                    ),
                ),
                value=AIState.vision_preview_face_throttle_value,
                on_change=AIState.set_vision_preview_face_throttle,
            ),
        ),
        _header_group(
            t("vision_preview_teleprompter_mode_label"),
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
        ),
        style={
            "display": "flex",
            "flex_direction": "row",
            "flex_wrap": "wrap",
            "gap": "1em",
            "align_items": "flex-end",
            "width": "100%",
        },
    )


# Stil-Spiegel zum Aufnahme-Button im Haupt-Input (input_sections.py).
# Idle: outline-grün. Aktiv: gefüllt-rot (Stop-Signal). Beide Varianten
# als separate rx.button via rx.cond — Reflex 0.8 propagiert rx.cond
# auf einzelnen Properties nicht zuverlässig.
_WATCH_BTN_IDLE_STYLE = {
    "background": "rgba(0, 80, 30, 0.4)",
    "color": "#3fb950",
    "border_color": "#3fb950",
    "cursor": "pointer",
}
_WATCH_BTN_ACTIVE_STYLE = {
    "background": "#dc2626",
    "color": "white",
    "border_color": "#dc2626",
    "cursor": "pointer",
}


def _toggle_button(
    sid: rx.Var,
    is_active: rx.Var,
    label_key: str,
    on_click_event,
) -> rx.Component:
    """Generischer Toggle-Button mit konstantem Label und wechselndem
    Icon/Style. Label bleibt sichtbar in beiden Zuständen — Icon und
    Hintergrund signalisieren ob's gerade läuft."""
    return rx.cond(
        is_active,
        rx.button(
            rx.icon("circle-stop", size=14),
            rx.text(t(label_key), font_size="13px"),
            on_click=on_click_event,
            size="2",
            variant="outline",
            style=_WATCH_BTN_ACTIVE_STYLE,
        ),
        rx.button(
            rx.icon("play", size=14),
            rx.text(t(label_key), font_size="13px"),
            on_click=on_click_event,
            size="2",
            variant="outline",
            style=_WATCH_BTN_IDLE_STYLE,
        ),
    )


def _watch_button(sid: rx.Var) -> rx.Component:
    """Bild-Analyse-Toggle (continuous-VLM)."""
    return _toggle_button(
        sid,
        AIState.vision_preview_watching.contains(sid),
        "vision_preview_watch_start",
        AIState.toggle_vision_preview_watch(sid),
    )


def _face_recognition_button(sid: rx.Var) -> rx.Component:
    """Gesichtserkennungs-Toggle (continuous face-detection)."""
    return _toggle_button(
        sid,
        AIState.vision_preview_face_active.contains(sid),
        "vision_preview_face_button",
        AIState.toggle_vision_preview_face_recognition(sid),
    )


def _source_row(source: rx.Var) -> rx.Component:
    """One row in the source list — the per-cam manager.

    Pure Live-Steuerung: Sichtbarkeit, Alias, Watch/Face-Buttons
    (Sofort-Aktionen für „jetzt ansehen"), Resolution.

    Hintergrund-Toggle und Min-Bewegungsfläche leben jetzt im
    Vigilantia-Settings-Modal (Quellen-Sektion) — dort sind die
    Daueraufgaben besser aufgehoben.
    """
    sid = source["id"]
    return rx.hstack(
        rx.switch(
            checked=AIState.vision_preview_visible_sources.contains(sid),
            on_change=lambda _checked: AIState.toggle_vision_preview_source(sid),
            size="1",
            color_scheme="orange",
        ),
        # Read-only Namens-Schild — editiert wird der Name in den
        # Vigilantia-Einstellungen (Quellen-Sektion), nicht mehr hier.
        rx.box(
            rx.text(
                source["label"],
                size="2",
                weight="medium",
                style={
                    "overflow": "hidden",
                    "text_overflow": "ellipsis",
                    "white_space": "nowrap",
                },
            ),
            style={
                "flex": "0 0 220px",
                "min_width": "0",
                "padding": "0.3em 0.7em",
                "border": "1px solid var(--gray-6)",
                "border_radius": "6px",
                "background": "var(--gray-2)",
            },
        ),
        _watch_button(sid),
        _face_recognition_button(sid),
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
    .vlm-event-target div both carries the SSE-Hook and is the
    scroll-container — vlm_sse_manager.js setzt ``el.scrollTop =
    el.scrollHeight`` direkt auf dieses Element, der äußere Container
    darf nicht scrollen, sonst rennt der Auto-Scroll ins Leere.

    ``pointer_events: auto`` nur am Scroll-Element + Clear-Button, der
    transparente Wrapper drumherum bleibt durchklick-freundlich für
    das darunter liegende Bild.
    """
    sid = entry["id"]
    return rx.box(
        rx.box(
            t("vision_preview_teleprompter_idle"),
            class_name="vlm-event-target vlm-event-overlay",
            custom_attrs={"data-vlm-source": sid},
            style={
                "color": "rgba(255, 255, 255, 0.88)",
                "font_style": "italic",
                "font_size": "0.85em",
                "white_space": "pre-wrap",
                # Fix statt max — Container soll nicht wachsen.
                "height": "15em",
                "overflow_y": "auto",
                "padding": "0.4em 0.6em",
                "background_color": "rgba(0, 0, 0, 0.55)",
                "border_radius": "6px",
                "pointer_events": "auto",
                "box_sizing": "border-box",
            },
        ),
        # Clear-Button oben rechts auf dem Overlay — analog zum
        # ``_teleprompter_below``-Header, nur dezent als floating
        # Icon. ``pointer_events: auto`` damit er klickbar ist trotz
        # ``pointer_events: none`` am Wrapper.
        rx.icon_button(
            # AIfred-Orange — gleiches Pattern wie im below-Header.
            rx.icon("eraser", size=12, color="#e67700"),
            on_click=AIState.clear_vlm_teleprompter(sid),
            size="1",
            variant="soft",
            color_scheme="gray",
            title=t("vision_preview_teleprompter_clear"),
            style={
                "position": "absolute",
                "top": "0.25em",
                "right": "0.25em",
                "pointer_events": "auto",
                "background_color": "rgba(0, 0, 0, 0.6)",
                "opacity": "0.85",
            },
        ),
        style={
            "position": "absolute",
            "bottom": "0.5em",
            "left": "0.5em",
            "right": "0.5em",
            "pointer_events": "none",
            "z_index": "2",
        },
    )


def _teleprompter_below(entry: rx.Var) -> rx.Component:
    """Full-width block below the image. Same data-vlm-source hook
    as the overlay variant so the script attaches an EventSource
    regardless of which layout the user picked."""
    sid = entry["id"]
    return rx.box(
        # Header-Zeile: Label + Clear-Button (rechts).
        rx.hstack(
            rx.text(
                t("vision_preview_teleprompter_label"),
                size="1", color="gray", weight="bold",
            ),
            rx.spacer(),
            rx.icon_button(
                # AIfred-Orange aus theme.py (primary #e67700) — der
                # User-Brand-Akzent zieht den Klick-Punkt ins Auge,
                # ohne den Button selbst voll-orange einzufärben.
                rx.icon("eraser", size=12, color="#e67700"),
                on_click=AIState.clear_vlm_teleprompter(sid),
                size="1",
                variant="ghost",
                color_scheme="gray",
                title=t("vision_preview_teleprompter_clear"),
            ),
            spacing="2",
            align="center",
            width="100%",
            style={"margin_bottom": "0.25em"},
        ),
        rx.box(
            t("vision_preview_teleprompter_idle"),
            class_name="vlm-event-target vlm-event-below",
            custom_attrs={"data-vlm-source": entry["id"]},
            style={
                # Fixe Höhe statt min/max — sonst dehnt sich der
                # Container, sobald die Antworten reinkommen, und
                # drückt das Bild zusammen. ~10 Zeilen bei 0.85em
                # font-size und line-height 1.4 ≈ 200 px + Padding.
                "height": "220px",
                "overflow_y": "auto",
                "padding": "0.5em",
                "background_color": "rgba(0, 0, 0, 0.3)",
                "border_radius": "6px",
                "border": "1px solid var(--gray-6)",
                "font_size": "0.85em",
                "color": "rgba(255, 255, 255, 0.88)",
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
                    # Immer auf Container-Größe skalieren — sowohl 320×240
                    # als auch 4K bekommen die volle verfügbare Fläche.
                    # ``object_fit: contain`` erhält das Seitenverhältnis,
                    # damit nichts verzerrt wird.
                    style={
                        "width": "100%",
                        "height": "100%",
                        "object_fit": "contain",
                    },
                ),
                _alias_overlay(entry),
                rx.cond(overlay_mode, _teleprompter_overlay(entry), rx.fragment()),
                # ``data-vlm-image-slot`` markiert diesen Container als
                # Ziel für das „Light-Table"-Overlay vom SSE-Manager:
                # Klick auf ein Crop-Thumb legt das Bild hier groß über
                # das Live-Video, weitere Klicks wechseln das Bild im
                # selben Slot.
                custom_attrs={"data-vlm-image-slot": entry["id"]},
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
                    # Reflex 0.8 propagiert weder ``value=`` noch
                    # ``default_value=`` aus rx.foreach zuverlässig auf
                    # die unterliegende Radix-TextArea. Der initiale
                    # Wert wird daher nach Page-Load via JavaScript
                    # aus on_load_vision_preview gesetzt (sucht das
                    # textarea per data-vlm-briefing-source). on_blur
                    # persistiert wie gehabt.
                    placeholder=t("vision_preview_briefing_placeholder"),
                    on_blur=lambda v: AIState.set_vision_preview_prompt_context(sid, v),
                    custom_attrs={"data-vlm-briefing-source": sid},
                    size="2",
                    resize="vertical",
                    style={
                        # 1/4-Anteil an der rechten Spalte — Briefing
                        # ist meist nur ein, zwei Sätze. ``resize:
                        # vertical`` lässt den User bei längeren
                        # Texten manuell vergrößern.
                        "width": "100%",
                        "flex": "1 1 0",
                        "min_height": "60px",
                        "font_family": "var(--default-font-family)",
                    },
                ),
                # Erkannte Personen — Live-Liste der Face-Events.
                # Wird vom vlm_sse_manager.js befüllt (face_known /
                # face_unsure / face_unknown mit farbigen Dots).
                rx.text(
                    t("vision_preview_faces_section_label"),
                    size="1", color="gray", weight="bold",
                    style={"margin_top": "0.5em", "margin_bottom": "0.25em"},
                ),
                rx.box(
                    t("vision_preview_faces_idle"),
                    class_name="vlm-face-target",
                    custom_attrs={"data-vlm-face-source": sid},
                    style={
                        # 3/4-Anteil an der rechten Spalte — hier
                        # sammeln sich alle erkannten Personen-Events
                        # mit Mini-Thumbs, das braucht deutlich mehr
                        # vertikalen Platz als das Briefing.
                        "flex": "3 1 0",
                        "min_height": "180px",
                        "overflow_y": "auto",
                        "padding": "0.4em 0.6em",
                        "background_color": "rgba(0, 0, 0, 0.3)",
                        "border_radius": "6px",
                        "border": "1px solid var(--gray-6)",
                        "font_size": "0.85em",
                        "color": "rgba(255, 255, 255, 0.88)",
                        "font_style": "italic",
                        "white_space": "pre-wrap",
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




def vision_preview_page() -> rx.Component:
    """Page-Komponente für die Route ``/vision-preview-popup`` — Multi-
    Source Live-Preview, geöffnet als eigenständiges Browser-Fenster.

    Whole page is a 100vh flex column that hides overflow, so the
    image grid sizes itself to the remaining vertical space and the
    image proportionally shrinks with the window. No scrollbars.

    VLM-SSE-Manager wird via ``rx.call_script`` aus
    :meth:`on_load_vision_preview` dynamisch injiziert — siehe
    Begründung dort.
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
        # ESC closes the popup window. This page is opened via
        # window.open() so it doesn't share the host page's
        # data-modal-close listener — wire ESC directly to
        # window.close() instead.
        rx.script(
            "document.addEventListener('keydown', function(e){"
            "if(e.key==='Escape'){e.preventDefault();window.close();}});"
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
