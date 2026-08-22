"""Casus — Ereignis-Verwaltungs-Modal.

Tabelle aller Vision-Events (motion / face_* / vlm_analysis) mit
Filter-Bar (Quelle, Typ, Identity), Pagination und Aktionen pro Zeile
(Event löschen, Unknown nachträglich taggen).

Erreichbar vom Vigilantia-Settings-Modal aus über den Button
"Casus öffnen". Global gemountet in aifred.py, sichtbar wenn
``AIState.casus_open == True``.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from .helpers import t, overlay_modal


def _event_type_badge(event: rx.Var) -> rx.Component:
    """Farbiges Badge je nach event_type. Labels sind i18n-gerendert
    (DE: Bewegung/Bekannt/Unsicher/Unbekannt/Bildanalyse)."""
    et = event["event_type"]
    return rx.match(
        et,
        ("motion", rx.badge(rx.text(t("casus_badge_motion"), size="1"), color_scheme="blue", variant="soft")),
        ("person", rx.badge(rx.text(t("casus_badge_person"), size="1"), color_scheme="purple", variant="soft")),
        ("face_known", rx.badge(rx.text(t("casus_badge_face_known"), size="1"), color_scheme="green", variant="soft")),
        ("face_unsure", rx.badge(rx.text(t("casus_badge_face_unsure"), size="1"), color_scheme="amber", variant="soft")),
        ("face_unknown", rx.badge(rx.text(t("casus_badge_face_unknown"), size="1"), color_scheme="gray", variant="soft")),
        ("vlm_analysis", rx.badge(rx.text(t("casus_badge_vlm"), size="1"), color_scheme="orange", variant="soft")),
        rx.badge(rx.text(et, size="1"), color_scheme="gray", variant="soft"),
    )


def _thumb(event: rx.Var, index: rx.Var) -> rx.Component:
    """Vorschaubild (40×40), klickbar → Bild-Modal mit dem Vollbild.

    Face-Events zeigen den Gesichts-Crop, reine Motion-Events ein
    verkleinertes Vollbild über den /api/vision/frame-Endpoint (w=80).
    Klick öffnet das Modal an diesem Index (von dort per Pfeil blätterbar).
    Nur wenn weder Crop noch Frame existieren, bleibt das Activity-Icon."""
    full_url = "/api/vision/frame?id=" + event["id"].to(str)
    thumb_style = {
        "width": "40px",
        "height": "40px",
        "border_radius": "4px",
        "object_fit": "cover",
        "flex_shrink": "0",
        "border": "1px solid var(--gray-7)",
        "cursor": "pointer",
    }
    return rx.cond(
        event["crop_url"] != "",
        rx.image(
            src=event["crop_url"],
            on_click=AIState.casus_show_image_at(index),
            style=thumb_style,
        ),
        rx.cond(
            event["frame_path"] != "",
            rx.image(
                src=full_url + "&w=80",
                on_click=AIState.casus_show_image_at(index),
                style=thumb_style,
            ),
            rx.box(
                rx.icon("activity", size=18, color="gray"),
                style={
                    "width": "40px",
                    "height": "40px",
                    "border_radius": "4px",
                    "background_color": "var(--gray-3)",
                    "border": "1px solid var(--gray-7)",
                    "display": "flex",
                    "align_items": "center",
                    "justify_content": "center",
                    "flex_shrink": "0",
                },
            ),
        ),
    )


def _person_cell(event: rx.Var) -> rx.Component:
    """Namen-Zelle: bei zugeordnetem Face den Namen, sonst Confidence-
    Band oder „—". Wenn eine VLM-Beschreibung vorliegt, wird sie als
    zweite Zeile dezenter angehängt."""
    desc_base = {"font_style": "italic", "cursor": "pointer"}
    return rx.vstack(
        rx.cond(
            event["face_name"] != "",
            rx.text(event["face_name"], size="2"),
            rx.cond(
                event["matched_name"] != "",
                rx.text(event["matched_name"], size="2", color="gray", style={"font_style": "italic"}),
                # Kein "—"-Platzhalter mehr: bei Motion-Events ohne Person
                # bleibt die Zeile leer, die Beschreibung rückt nach oben.
                rx.fragment(),
            ),
        ),
        # VLM-Beschreibung: per Default auf 2 Zeilen geklemmt; Klick toggelt
        # den Volltext (white-space:pre-wrap, keine Klemmung) — so kann man
        # längere VLM-Analysen in der Liste vollständig lesen.
        rx.cond(
            event["description"] != "",
            rx.tooltip(
                rx.cond(
                    AIState.casus_expanded_event_id == event["id"],
                    rx.text(
                        event["description"],
                        size="1",
                        color="gray",
                        on_click=AIState.casus_toggle_expand(event["id"]),
                        style={**desc_base, "white_space": "pre-wrap"},
                    ),
                    rx.text(
                        event["description"],
                        size="1",
                        color="gray",
                        on_click=AIState.casus_toggle_expand(event["id"]),
                        style={
                            **desc_base,
                            "overflow": "hidden",
                            "text_overflow": "ellipsis",
                            "display": "-webkit-box",
                            "-webkit-line-clamp": "2",
                            "-webkit-box-orient": "vertical",
                        },
                    ),
                ),
                content=t("casus_expand_hint"),
            ),
        ),
        spacing="0",
        align="start",
        width="100%",
    )


def _tag_controls(event: rx.Var) -> rx.Component:
    """Tag-Modus pro Zeile: bei aktivem Tag-Mode Dropdown + Save/Cancel,
    sonst „+ taggen"-Button (nur für face_unknown/face_unsure ohne face_id)."""
    eid = event["id"]
    is_tagging = AIState.casus_tag_event_id == eid
    # Tag-Knopf für JEDES Event mit Face-Bezug — auch bereits zugeordnete:
    # sonst wäre eine Zuordnung per UI nie korrigier-/lösbar („Zuordnung
    # lösen" lebt im Tag-Dropdown).
    can_tag = (
        (event["event_type"] == "face_unknown")
        | (event["event_type"] == "face_unsure")
        | (event["event_type"] == "face_known")
    )
    return rx.cond(
        is_tagging,
        rx.hstack(
            rx.select.root(
                rx.select.trigger(placeholder=t("casus_tag_placeholder"), width="11em"),
                rx.select.content(
                    rx.select.item(t("casus_tag_clear"), value="__clear__"),
                    rx.foreach(
                        AIState.casus_face_options,
                        lambda opt: rx.cond(
                            (opt["value"] != "all") & (opt["value"] != "unknown"),
                            rx.select.item(opt["label"], value=opt["value"]),
                        ),
                    ),
                ),
                value=AIState.casus_tag_face_id,
                on_change=AIState.casus_set_tag_face,
            ),
            rx.icon_button(
                rx.icon("check", size=14),
                on_click=AIState.casus_save_tag,
                size="1",
                variant="soft",
                color_scheme="green",
                title=t("casus_tag_save"),
            ),
            rx.icon_button(
                rx.icon("x", size=14),
                on_click=AIState.casus_cancel_tag,
                size="1",
                variant="soft",
                color_scheme="gray",
                title=t("casus_tag_cancel"),
            ),
            spacing="1",
            align="center",
        ),
        rx.hstack(
            rx.cond(
                can_tag,
                rx.icon_button(
                    rx.icon("user-plus", size=14),
                    on_click=AIState.casus_start_tag(eid),
                    size="1",
                    variant="soft",
                    color_scheme="orange",
                    title=t("casus_tag_button"),
                ),
            ),
            # VLM-Analyse-Button: nur sinnvoll wenn Frame-Pfad da ist
            # und VLM-Modell geladen. Während Analyse: Spinner-Variante
            # via loading=. Bei vorhandener Beschreibung: lila statt
            # orange als „schon analysiert"-Hinweis.
            rx.icon_button(
                rx.icon("sparkles", size=14),
                on_click=AIState.casus_analyze_event(eid),
                size="1",
                variant=rx.cond(event["description"] != "", "soft", "soft"),
                color_scheme=rx.cond(event["description"] != "", "purple", "orange"),
                loading=AIState.casus_analyzing_event_id == eid,
                disabled=(AIState.casus_analyzing_event_id != 0) & (AIState.casus_analyzing_event_id != eid),
                title=rx.cond(
                    event["description"] != "",
                    t("casus_analyze_again"),
                    t("casus_analyze"),
                ),
            ),
            # Film: Serie des Vorkommnisses (Burst-Frames) als Slideshow.
            rx.cond(
                event["cluster_id"] != "",
                rx.icon_button(
                    rx.icon("film", size=14),
                    on_click=AIState.casus_open_film(eid),
                    size="1",
                    variant="soft",
                    color_scheme="blue",
                    title=t("casus_film_tooltip"),
                ),
                rx.fragment(),
            ),
            rx.icon_button(
                rx.icon("trash-2", size=14),
                on_click=AIState.casus_delete_event(eid),
                size="1",
                variant="soft",
                color_scheme="red",
                title=t("casus_delete"),
            ),
            spacing="1",
            align="center",
            style={"flex_shrink": "0"},
        ),
    )


def _event_row(event: rx.Var, index: rx.Var) -> rx.Component:
    """Eine Zeile in der Casus-Tabelle. ``index`` = Position in
    casus_events, fürs Bild-Modal/Slideshow."""
    return rx.hstack(
        # Datum + Zeit gestapelt — im Casus ist genug Platz für beides,
        # und ohne Datum sind chronologische Sprünge schwer einzuordnen.
        rx.vstack(
            rx.text(
                event["date_display"],
                size="1",
                color="gray",
                style={"font_family": "monospace", "line_height": "1.1"},
            ),
            rx.text(
                event["time_display"],
                size="1",
                color="gray",
                style={"font_family": "monospace", "line_height": "1.1"},
            ),
            spacing="0",
            align="start",
            style={"min_width": "6em", "flex_shrink": "0"},
        ),
        _thumb(event, index),
        _event_type_badge(event),
        # Cluster-Member-Badge — nur sichtbar im Cluster-Mode wenn der
        # Cluster mehr als 1 Mitglied hat. Reflex Var[Any] kann nicht
        # direkt mit int verglichen werden → .to(int) Cast.
        rx.cond(
            event["cluster_member_count"].to(int) > 1,
            rx.badge(
                "+" + (event["cluster_member_count"].to(int) - 1).to(str),
                color_scheme="purple",
                variant="soft",
                size="1",
            ),
        ),
        rx.text(
            event["source_name"],
            size="1",
            color="gray",
            style={
                "min_width": "6em",
                "max_width": "10em",
                "overflow": "hidden",
                "text_overflow": "ellipsis",
                "white_space": "nowrap",
                "flex_shrink": "0",
            },
        ),
        rx.box(
            _person_cell(event),
            style={"flex": "1 1 auto", "min_width": "0"},
        ),
        _tag_controls(event),
        spacing="2",
        align="center",
        width="100%",
        style={
            "padding": "0.4em 0",
            "border_bottom": "1px solid var(--gray-6)",
        },
    )


def _bulk_bar() -> rx.Component:
    """Bulk-VLM-Analyse-Bedienleiste mit Start-Button, Progress und
    Status-Text. Sichtbar wenn aktuell ein Bulk-Run läuft, sonst
    nur der Start-Button."""
    progress_pct = rx.cond(
        AIState.casus_bulk_total > 0,
        AIState.casus_bulk_progress * 100 / AIState.casus_bulk_total,
        0,
    )
    return rx.cond(
        AIState.casus_bulk_running,
        rx.box(
            rx.hstack(
                rx.icon("sparkles", size=14, color="var(--orange-9)"),
                rx.text(AIState.casus_bulk_message, size="1", color="gray"),
                rx.spacer(),
                rx.text(
                    AIState.casus_bulk_progress.to(str) + " / " +
                    AIState.casus_bulk_total.to(str),
                    size="1", color="gray",
                    style={"font_family": "monospace"},
                ),
                rx.button(
                    rx.icon("x", size=12),
                    rx.text(t("casus_bulk_cancel"), size="1"),
                    on_click=AIState.casus_bulk_cancel_run,
                    size="1",
                    variant="soft",
                    color_scheme="red",
                ),
                spacing="2",
                align="center",
                width="100%",
            ),
            rx.progress(
                value=progress_pct,
                size="3",
                color_scheme="orange",
                high_contrast=True,
                style={
                    "margin_top": "0.5em",
                    # Track heller, damit der Restweg sichtbar bleibt.
                    "--progress-track-color": "var(--gray-7)",
                    "height": "10px",
                },
            ),
            style={
                "padding": "0.5em 0.7em",
                "background_color": "var(--gray-2)",
                "border": "1px solid var(--orange-9)",
                "border_radius": "6px",
                "width": "100%",
            },
        ),
        rx.hstack(
            rx.cond(
                AIState.casus_bulk_message != "",
                rx.text(
                    AIState.casus_bulk_message,
                    size="1", color="gray",
                ),
            ),
            rx.spacer(),
            # Glühbirne — öffnet das Casus-Hilfe-Modal (erklärt
            # „Alle analysieren" + „Gruppiert"-Toggle).
            rx.tooltip(
                rx.icon(
                    "lightbulb",
                    size=14,
                    color="#FFD700",
                    cursor="pointer",
                    on_click=AIState.open_casus_help,
                    style={
                        "transition": "transform 0.2s ease",
                        "&:hover": {"transform": "scale(1.15)"},
                    },
                ),
                content=t("casus_help_tooltip"),
            ),
            rx.tooltip(
                rx.button(
                    rx.icon("users", size=14),
                    rx.text(t("casus_open_personarium"), size="1"),
                    on_click=AIState.open_personarium,
                    size="1",
                    variant="soft",
                    color_scheme="orange",
                ),
                content=t("casus_open_personarium_tooltip"),
            ),
            rx.tooltip(
                rx.button(
                    rx.icon("sparkles", size=14),
                    rx.text(t("casus_bulk_start"), size="1"),
                    on_click=AIState.casus_bulk_start,
                    size="1",
                    variant="soft",
                    color_scheme="orange",
                ),
                content=t("casus_bulk_start_tooltip"),
            ),
            spacing="2",
            align="center",
            width="100%",
        ),
    )


def casus_help_modal() -> rx.Component:
    """Modal mit Erklärung der Casus-Aktionen — „Alle analysieren"
    (Bulk-VLM mit pHash-Dedup) und „Gruppiert" (Cluster-Anzeige).
    Global gemountet in aifred.py."""
    return overlay_modal(
        AIState.casus_help_open,
        rx.vstack(
            rx.hstack(
                rx.icon("lightbulb", size=20, color="#FFD700"),
                rx.text(
                    t("casus_help_title"),
                    font_weight="bold", size="4",
                ),
                rx.spacer(),
                rx.icon_button(
                    rx.icon("x", size=16),
                    on_click=AIState.close_casus_help,
                    size="1", variant="ghost", color_scheme="gray",
                    custom_attrs={"data-modal-close": "true"},
                ),
                spacing="2", align="center", width="100%",
            ),
            rx.text(
                t("casus_help_intro"),
                color="gray", size="2",
                margin_bottom="0.5em",
            ),
            rx.divider(),
            # Sparkles per single event
            rx.hstack(
                rx.icon("sparkles", size=18, color="var(--orange-9)",
                        style={"flex_shrink": "0"}),
                rx.vstack(
                    rx.text(
                        t("casus_help_sparkles_label"),
                        font_weight="bold", size="2",
                    ),
                    rx.text(
                        t("casus_help_sparkles_body"),
                        size="1", color="gray",
                    ),
                    spacing="1", align="stretch",
                    style={"flex": "1 1 auto", "min_width": "0"},
                ),
                spacing="3", align="start", width="100%",
                style={"padding": "0.5em 0"},
            ),
            rx.divider(),
            # Alle analysieren
            rx.hstack(
                rx.icon("sparkles", size=18, color="var(--orange-9)",
                        style={"flex_shrink": "0"}),
                rx.vstack(
                    rx.text(
                        t("casus_help_bulk_label"),
                        font_weight="bold", size="2",
                    ),
                    rx.text(
                        t("casus_help_bulk_body"),
                        size="1", color="gray",
                    ),
                    spacing="1", align="stretch",
                    style={"flex": "1 1 auto", "min_width": "0"},
                ),
                spacing="3", align="start", width="100%",
                style={"padding": "0.5em 0"},
            ),
            rx.divider(),
            # Gruppiert
            rx.hstack(
                rx.icon("layers", size=18, color="var(--purple-9)",
                        style={"flex_shrink": "0"}),
                rx.vstack(
                    rx.text(
                        t("casus_help_grouped_label"),
                        font_weight="bold", size="2",
                    ),
                    rx.text(
                        t("casus_help_grouped_body"),
                        size="1", color="gray",
                    ),
                    spacing="1", align="stretch",
                    style={"flex": "1 1 auto", "min_width": "0"},
                ),
                spacing="3", align="start", width="100%",
                style={"padding": "0.5em 0"},
            ),
            rx.divider(),
            rx.button(
                t("vision_settings_close"),
                on_click=AIState.close_casus_help,
                size="2", variant="soft", width="100%",
            ),
            align="stretch", spacing="2", width="100%",
        ),
        on_close=AIState.close_casus_help,
        width="min(580px, 95vw)",
        z_index="10000",  # über Casus selbst (9999)
    )


def _filter_bar() -> rx.Component:
    """Zeile mit Source-/Typ-/Identity-Filter + Clear-Button."""
    return rx.hstack(
        # Source-Filter
        rx.select.root(
            rx.select.trigger(width="11em"),
            rx.select.content(
                rx.foreach(
                    AIState.casus_source_options,
                    lambda opt: rx.select.item(opt["label"], value=opt["value"]),
                ),
            ),
            value=AIState.casus_filter_source,
            on_change=AIState.casus_set_filter_source,
        ),
        # Typ-Filter — Labels via rx.match aus t(), damit der Filter
        # sprachreaktiv ist (Mixin liefert nur die value-Strings).
        rx.select.root(
            rx.select.trigger(width="10em"),
            rx.select.content(
                rx.foreach(
                    AIState.casus_type_values,
                    lambda v: rx.select.item(
                        rx.match(
                            v,
                            ("all", t("casus_type_all")),
                            ("motion", t("casus_type_motion")),
                            ("face", t("casus_type_face_any")),
                            ("face_known", t("casus_type_face_known")),
                            ("face_unsure", t("casus_type_face_unsure")),
                            ("face_unknown", t("casus_type_face_unknown")),
                            ("vlm", t("casus_type_vlm")),
                            v,
                        ),
                        value=v,
                    ),
                ),
            ),
            value=AIState.casus_filter_type,
            on_change=AIState.casus_set_filter_type,
        ),
        # Identity-Filter
        rx.select.root(
            rx.select.trigger(width="11em"),
            rx.select.content(
                rx.foreach(
                    AIState.casus_face_options,
                    lambda opt: rx.select.item(opt["label"], value=opt["value"]),
                ),
            ),
            value=AIState.casus_filter_face,
            on_change=AIState.casus_set_filter_face,
        ),
        rx.spacer(),
        rx.tooltip(
            rx.icon_button(
                rx.icon("refresh-cw", size=14),
                on_click=AIState.casus_refresh,
                size="1",
                variant="soft",
                color_scheme="gray",
            ),
            content=t("casus_refresh"),
        ),
        # Cluster-Mode-Toggle — eine Zeile pro pHash-Cluster statt aller
        # Events einzeln. Nützlich nach Bulk-Analyse, wenn man die
        # 1000 identischen „Person am Schreibtisch"-Events bündeln will.
        rx.hstack(
            rx.text(
                t("casus_cluster_mode_label"),
                size="1", color="gray",
            ),
            rx.switch(
                checked=AIState.casus_cluster_mode,
                on_change=AIState.casus_toggle_cluster_mode,
                size="1",
                color_scheme="orange",
            ),
            spacing="1",
            align="center",
        ),
        rx.button(
            t("casus_filter_clear"),
            on_click=AIState.casus_clear_filters,
            size="1",
            variant="soft",
            color_scheme="gray",
        ),
        # Bulk-Delete — zweistufig: erst „Alle löschen", dann
        # „Wirklich löschen?" + „Abbrechen". Im Confirm-Modus wird
        # die total_count im Label angezeigt, damit der User weiß,
        # wie viele Events gleich verschwinden.
        rx.cond(
            AIState.casus_confirm_delete_all,
            rx.hstack(
                rx.button(
                    rx.icon("trash-2", size=14),
                    rx.text(
                        t("casus_delete_all_confirm")
                        + " ("
                        + AIState.casus_total_count.to(str)
                        + ")"
                    ),
                    on_click=AIState.casus_confirm_delete_all_now,
                    size="1",
                    color_scheme="red",
                ),
                rx.button(
                    t("casus_delete_all_cancel"),
                    on_click=AIState.casus_cancel_delete_all,
                    size="1",
                    variant="soft",
                    color_scheme="gray",
                ),
                spacing="1",
                align="center",
            ),
            rx.button(
                rx.icon("trash-2", size=14),
                rx.text(t("casus_delete_all")),
                on_click=AIState.casus_request_delete_all,
                size="1",
                variant="soft",
                color_scheme="red",
                disabled=AIState.casus_total_count == 0,
            ),
        ),
        spacing="2",
        align="center",
        width="100%",
        style={"flex_wrap": "wrap"},
    )


def _pagination_bar() -> rx.Component:
    return rx.hstack(
        rx.icon_button(
            rx.icon("chevron-left", size=14),
            on_click=AIState.casus_prev_page,
            size="1",
            variant="soft",
            color_scheme="gray",
            disabled=~AIState.casus_has_prev,
        ),
        rx.text(AIState.casus_page_label, size="1", color="gray"),
        rx.icon_button(
            rx.icon("chevron-right", size=14),
            on_click=AIState.casus_next_page,
            size="1",
            variant="soft",
            color_scheme="gray",
            disabled=~AIState.casus_has_next,
        ),
        spacing="2",
        align="center",
        justify="center",
        width="100%",
    )


def _image_overlay() -> rx.Component:
    """Bild-Modal mit Slideshow: zeigt den Event-Frame groß über dem Casus-
    Modal. Pfeil-Buttons (und Pfeiltasten via data-image-nav in custom.js)
    blättern vor/zurück durch die Events. Backdrop-Klick + X schließen;
    der X-Button trägt ``data-modal-close`` für ESC."""
    return rx.cond(
        AIState.casus_image_open,
        rx.box(
            # Backdrop — Klick schließt
            rx.box(
                position="absolute", top="0", left="0",
                width="100%", height="100%",
                background_color="rgba(0, 0, 0, 0.85)",
                on_click=AIState.casus_close_image,
            ),
            # Pfeil ◂ · Bild · Pfeil ▸ — zentriert. Klick aufs Bild schließt
            # NICHT (eigene Container über dem Backdrop).
            rx.hstack(
                rx.icon_button(
                    rx.icon("chevron-left", size=30),
                    on_click=AIState.casus_image_older,
                    size="3", variant="soft", color_scheme="gray",
                    custom_attrs={"data-image-nav": "older"},
                    style={"flex_shrink": "0", "opacity": "0.85"},
                ),
                rx.box(
                    rx.image(
                        src=AIState.casus_image_src,
                        style={
                            "max_width": "80vw",
                            "max_height": "82vh",
                            "object_fit": "contain",
                            "border_radius": "8px",
                            "border": "1px solid var(--gray-6)",
                            "box_shadow": "0 20px 60px rgba(0,0,0,0.6)",
                            "display": "block",
                        },
                    ),
                    rx.icon_button(
                        rx.icon("x", size=18),
                        on_click=AIState.casus_close_image,
                        size="2", variant="solid", color_scheme="gray",
                        custom_attrs={"data-modal-close": "true"},
                        style={"position": "absolute", "top": "-14px", "right": "-14px"},
                    ),
                    rx.badge(
                        AIState.casus_image_counter,
                        color_scheme="gray", variant="solid",
                        style={
                            "position": "absolute", "bottom": "8px",
                            "left": "50%", "transform": "translateX(-50%)",
                            "opacity": "0.9",
                        },
                    ),
                    position="relative",
                    style={"flex_shrink": "1", "min_width": "0"},
                ),
                rx.icon_button(
                    rx.icon("chevron-right", size=30),
                    on_click=AIState.casus_image_newer,
                    size="3", variant="soft", color_scheme="gray",
                    custom_attrs={"data-image-nav": "newer"},
                    style={"flex_shrink": "0", "opacity": "0.85"},
                ),
                spacing="3",
                align="center",
                position="absolute",
                top="50%", left="50%",
                transform="translate(-50%, -50%)",
            ),
            position="fixed", top="0", left="0",
            width="100vw", height="100vh",
            z_index="10001",
        ),
    )


def casus_modal() -> rx.Component:
    """Casus-Modal: chronologische Ereignisliste mit Filter + Aktionen.
    Global gemountet in aifred.py, sichtbar wenn
    ``AIState.casus_open == True``."""
    return overlay_modal(
        AIState.casus_open,
        rx.vstack(
            # Header
            rx.hstack(
                rx.icon("scroll-text", size=20),
                rx.text(
                    t("casus_title"),
                    font_weight="bold",
                    size="4",
                ),
                rx.spacer(),
                # VLM-Power-Button: für die Bulk-Analyse braucht's
                # das Modell im VRAM. Voller Button mit Beschriftung
                # damit klar ist was passiert — der kleine Power-
                # Icon-Button war zu kryptisch.
                rx.button(
                    rx.icon("power", size=14),
                    rx.text(
                        rx.cond(
                            AIState.vlm_model_loaded,
                            t("vlm_unload_button"),
                            t("vlm_load_button"),
                        ),
                        size="1",
                    ),
                    on_click=AIState.toggle_vlm_model_loaded,
                    size="1",
                    variant=rx.cond(
                        AIState.vlm_model_loaded, "solid", "soft",
                    ),
                    color_scheme=rx.cond(
                        AIState.vlm_model_loaded, "orange", "gray",
                    ),
                    loading=AIState.vlm_model_busy,
                ),
                rx.icon_button(
                    rx.icon("x", size=16),
                    on_click=AIState.close_casus,
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
                t("casus_subtitle"),
                color="gray",
                size="2",
                margin_bottom="0.5em",
            ),
            rx.divider(),
            _bulk_bar(),
            _filter_bar(),
            rx.divider(),
            # Event-Liste oder Leer-Zustand
            rx.cond(
                AIState.casus_events.length() > 0,
                rx.box(
                    rx.foreach(AIState.casus_events, _event_row),
                    style={
                        "width": "100%",
                        "max_height": "55vh",
                        "overflow_y": "auto",
                    },
                ),
                rx.box(
                    rx.icon("scroll-text", size=32, color="gray"),
                    rx.text(
                        t("casus_empty"),
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
                        "padding": "2em",
                    },
                ),
            ),
            rx.cond(
                AIState.casus_status != "",
                rx.callout.root(
                    rx.callout.icon(rx.icon("info")),
                    rx.callout.text(AIState.casus_status),
                    color_scheme="amber",
                    size="1",
                ),
            ),
            rx.divider(),
            _pagination_bar(),
            rx.divider(),
            rx.button(
                t("vision_settings_close"),
                on_click=AIState.close_casus,
                size="2",
                width="100%",
            ),
            align="stretch",
            spacing="2",
            width="100%",
        ),
        _image_overlay(),
        on_close=AIState.close_casus,
        width="min(900px, 95vw)",
    )
