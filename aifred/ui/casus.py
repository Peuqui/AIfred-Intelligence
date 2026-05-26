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
from .helpers import t


def _event_type_badge(event: rx.Var) -> rx.Component:
    """Farbiges Badge je nach event_type. Labels sind i18n-gerendert
    (DE: Bewegung/Bekannt/Unsicher/Unbekannt/Bildanalyse)."""
    et = event["event_type"]
    return rx.match(
        et,
        ("motion", rx.badge(rx.text(t("casus_badge_motion"), size="1"), color_scheme="blue", variant="soft")),
        ("face_known", rx.badge(rx.text(t("casus_badge_face_known"), size="1"), color_scheme="green", variant="soft")),
        ("face_unsure", rx.badge(rx.text(t("casus_badge_face_unsure"), size="1"), color_scheme="amber", variant="soft")),
        ("face_unknown", rx.badge(rx.text(t("casus_badge_face_unknown"), size="1"), color_scheme="gray", variant="soft")),
        ("vlm_analysis", rx.badge(rx.text(t("casus_badge_vlm"), size="1"), color_scheme="orange", variant="soft")),
        rx.badge(rx.text(et, size="1"), color_scheme="gray", variant="soft"),
    )


def _thumb(event: rx.Var) -> rx.Component:
    """Crop- oder Frame-Vorschau (40×40). Fallback: Icon."""
    return rx.cond(
        event["crop_url"] != "",
        rx.image(
            src=event["crop_url"],
            style={
                "width": "40px",
                "height": "40px",
                "border_radius": "4px",
                "object_fit": "cover",
                "flex_shrink": "0",
                "border": "1px solid var(--gray-7)",
            },
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
    )


def _person_cell(event: rx.Var) -> rx.Component:
    """Namen-Zelle: bei zugeordnetem Face den Namen, sonst Confidence-
    Band oder „—"."""
    return rx.cond(
        event["face_name"] != "",
        rx.text(event["face_name"], size="2"),
        rx.cond(
            event["matched_name"] != "",
            rx.text(event["matched_name"], size="2", color="gray", style={"font_style": "italic"}),
            rx.text("—", size="2", color="gray"),
        ),
    )


def _tag_controls(event: rx.Var) -> rx.Component:
    """Tag-Modus pro Zeile: bei aktivem Tag-Mode Dropdown + Save/Cancel,
    sonst „+ taggen"-Button (nur für face_unknown/face_unsure ohne face_id)."""
    eid = event["id"]
    is_tagging = AIState.casus_tag_event_id == eid
    # Kandidaten für „+ taggen": ein Event mit Face-Bezug, aber keiner
    # zugeordneten Identity.
    can_tag = (
        (event["event_type"] == "face_unknown")
        | (event["event_type"] == "face_unsure")
        | (event["event_type"] == "face_known")
    ) & (event["face_id"].is_none())
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


def _event_row(event: rx.Var) -> rx.Component:
    """Eine Zeile in der Casus-Tabelle."""
    return rx.hstack(
        # Zeit (kompakt: HH:MM:SS)
        rx.text(
            event["timestamp"].to(str).split("T")[1].to(str).split(".")[0],
            size="1",
            color="gray",
            style={"font_family": "monospace", "min_width": "5em", "flex_shrink": "0"},
        ),
        _thumb(event),
        _event_type_badge(event),
        rx.text(
            event["source_id"],
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


def casus_modal() -> rx.Component:
    """Casus-Modal: chronologische Ereignisliste mit Filter + Aktionen.
    Global gemountet in aifred.py, sichtbar wenn
    ``AIState.casus_open == True``."""
    return rx.cond(
        AIState.casus_open,
        rx.box(
            # Backdrop
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.5)",
                on_click=AIState.close_casus,
            ),
            # Modal-Box
            rx.box(
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
                        # VLM-Power-Toggle: für die spätere Bulk-Analyse
                        # braucht's das Modell im VRAM — der User kann
                        # es hier direkt vor der Aktion laden.
                        rx.tooltip(
                            rx.icon_button(
                                rx.icon("power", size=14),
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
                            content=rx.cond(
                                AIState.vlm_model_loaded,
                                t("vlm_unload_tooltip"),
                                t("vlm_load_tooltip"),
                            ),
                        ),
                        rx.icon_button(
                            rx.icon("x", size=16),
                            on_click=AIState.close_casus,
                            size="1",
                            variant="ghost",
                            color_scheme="gray",
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
                position="absolute",
                top="50%",
                left="50%",
                transform="translate(-50%, -50%)",
                background_color="var(--gray-2)",
                border="1px solid var(--gray-6)",
                border_radius="12px",
                padding="1.5em",
                width="min(900px, 95vw)",
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
