"""Vigilantia-Live-Feed — Akkordeon im AIfred-Haupttab.

Zeigt die letzten Vision-Events der Hintergrund-Watcher als kompakte
Liste. Nur sichtbar wenn ``AIState.vigilantia_feed_visible == True``
(ein-/ausblendbar über das Burger-Menü).

Layout angelehnt an „Gespeicherte Chats" — Radix-Accordion mit
Header-Badge (Events-Count letzte 10 Min) und ausklappbarer Liste.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from .helpers import t


def _feed_thumb(event: rx.Var) -> rx.Component:
    """Vorschaubild (32×32): Face-Crop wenn vorhanden, sonst verkleinertes
    Vollbild über den /api/vision/frame-Endpoint (w=64). Nur wenn weder
    Crop noch Frame da sind, bleibt das Activity-Icon."""
    img_style = {
        "width": "32px",
        "height": "32px",
        "border_radius": "3px",
        "object_fit": "cover",
        "flex_shrink": "0",
        "border": "1px solid var(--gray-7)",
    }
    return rx.cond(
        event["crop_url"] != "",
        rx.image(src=event["crop_url"], style=img_style),
        rx.cond(
            event["frame_path"] != "",
            rx.image(
                src="/api/vision/frame?id=" + event["id"].to(str) + "&w=64",
                style=img_style,
            ),
            rx.box(
                rx.icon("activity", size=14, color="gray"),
                style={
                    "width": "32px",
                    "height": "32px",
                    "border_radius": "3px",
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


def _feed_badge(event: rx.Var) -> rx.Component:
    et = event["event_type"]
    return rx.match(
        et,
        ("motion", rx.badge(t("casus_badge_motion"), color_scheme="blue", variant="soft", size="1")),
        ("person", rx.badge(t("casus_badge_person"), color_scheme="purple", variant="soft", size="1")),
        ("face_known", rx.badge(t("casus_badge_face_known"), color_scheme="green", variant="soft", size="1")),
        ("face_unsure", rx.badge(t("casus_badge_face_unsure"), color_scheme="amber", variant="soft", size="1")),
        ("face_unknown", rx.badge(t("casus_badge_face_unknown"), color_scheme="gray", variant="soft", size="1")),
        ("vlm_analysis", rx.badge(t("casus_badge_vlm"), color_scheme="orange", variant="soft", size="1")),
        rx.badge(et, color_scheme="gray", variant="soft", size="1"),
    )


def _feed_row(event: rx.Var) -> rx.Component:
    return rx.hstack(
        # Datum + Zeit in einer Zeile — im Popover-Feed wird die Liste
        # mehrere Tage abdecken; ohne Datum sind Sprünge zwischen Tagen
        # nicht erkennbar.
        rx.text(
            event["date_display"].to(str) + " " + event["time_display"].to(str),
            size="1",
            color="gray",
            style={"font_family": "monospace", "flex_shrink": "0"},
        ),
        _feed_thumb(event),
        _feed_badge(event),
        rx.text(
            event["source_name"],
            size="1",
            color="gray",
            style={"flex_shrink": "0"},
        ),
        rx.cond(
            event["face_name"] != "",
            rx.text(event["face_name"], size="1", style={"flex": "1 1 auto", "min_width": "0"}),
            rx.cond(
                event["description"] != "",
                rx.text(
                    event["description"],
                    size="1",
                    color="gray",
                    style={
                        "flex": "1 1 auto",
                        "min_width": "0",
                        "overflow": "hidden",
                        "text_overflow": "ellipsis",
                        "white_space": "nowrap",
                    },
                ),
                rx.text("—", size="1", color="gray"),
            ),
        ),
        spacing="2",
        align="center",
        width="100%",
        style={"padding": "0.2em 0", "border_bottom": "1px solid var(--gray-5)"},
    )


def _latest_event_card(event) -> rx.Component:
    """Inline-Karte mit dem jüngsten Event. Sitzt als Popover-Trigger
    rechtsbündig in der Research-Modi-Zeile — der User sieht sofort,
    was die Hintergrund-Cams gerade gemeldet haben, ohne klicken zu
    müssen. Den Status (scharf/deaktiviert) liefert das Auge-Icon
    links daneben — hier nur die Event-Daten."""
    return rx.hstack(
        # Mini-Thumb (24×24) falls vorhanden, sonst Activity-Icon
        rx.cond(
            event["crop_url"] != "",
            rx.image(
                src=event["crop_url"],
                style={
                    "width": "24px",
                    "height": "24px",
                    "border_radius": "3px",
                    "object_fit": "cover",
                    "flex_shrink": "0",
                    "border": "1px solid var(--gray-7)",
                },
            ),
        ),
        # Inline-Karte (rechte Modus-Zeile): Datum + Zeit kombiniert,
        # gleich wie im Popover-Feed.
        rx.text(
            event["date_display"].to(str) + " " + event["time_display"].to(str),
            size="1",
            color="gray",
            style={"font_family": "monospace", "flex_shrink": "0"},
        ),
        _feed_badge(event),
        rx.cond(
            event["face_name"] != "",
            rx.text(
                event["face_name"],
                size="1",
                style={
                    "flex": "1 1 auto",
                    "min_width": "0",
                    "overflow": "hidden",
                    "text_overflow": "ellipsis",
                    "white_space": "nowrap",
                },
            ),
            rx.cond(
                event["description"] != "",
                rx.text(
                    event["description"],
                    size="1",
                    color="gray",
                    style={
                        "flex": "1 1 auto",
                        "min_width": "0",
                        "overflow": "hidden",
                        "text_overflow": "ellipsis",
                        "white_space": "nowrap",
                    },
                ),
                rx.text(
                    event["source_name"],
                    size="1",
                    color="gray",
                    style={
                        "flex": "1 1 auto",
                        "min_width": "0",
                        "overflow": "hidden",
                        "text_overflow": "ellipsis",
                        "white_space": "nowrap",
                    },
                ),
            ),
        ),
        # +N Indikator wenn mehrere Events da sind. Tooltip stellt klar,
        # dass es ein gleitendes 10-Minuten-Fenster ist (zählt runter, wenn
        # es ruhig wird) — sonst irritiert das Rückwärtszählen.
        rx.cond(
            AIState.vigilantia_feed_recent_count > 1,
            rx.tooltip(
                rx.badge(
                    "+" + (AIState.vigilantia_feed_recent_count - 1).to(str),
                    color_scheme="orange",
                    size="1",
                ),
                content=t("vigilantia_badge_window_tooltip"),
            ),
        ),
        spacing="2",
        align="center",
        style={
            "padding": "4px 10px",
            "background_color": "var(--gray-3)",
            "border": "1px solid var(--orange-9)",
            "border_radius": "6px",
            "cursor": "pointer",
            # No min_width: keeps the card from forcing a horizontal
            # page scroll on very narrow windows. The white-space:nowrap
            # + ellipsis on the description column means the card stays
            # legible even when shrunk to ~150px.
            "min_width": "0",
            # Fixed cap (not vw-relative): when the window shrinks, the
            # rx.spacer in front of us (flex-basis 0) eats all the
            # leftover space first; only once the spacer is at 0 does
            # the card itself start to shrink. A vw-relative cap would
            # shrink the card immediately on every pixel of resize.
            "max_width": "800px",
            "flex_shrink": "1",
        },
    )


def _idle_card() -> rx.Component:
    """Karte für den Zustand ohne Events.

    Drei Sub-Zustände:

    * ``armed=False``        → „Deaktiviert" (kein Watcher läuft)
    * ``armed=True`` ohne     → „Keine Cams scharfgeschaltet" — der User
      ``auto_start``-Source     hat das Master-Auge eingeschaltet aber
                                noch keine Cam für den Hintergrund
                                konfiguriert; Klick führt zum Settings-
                                Modal mit der Quellen-Sektion.
    * ``armed=True`` + Cams  → „Ruhig" (Watcher läuft, keine Events)
    """
    return rx.hstack(
        rx.cond(
            AIState.vigilantia_armed,
            rx.cond(
                AIState.vigilantia_has_armed_source,
                rx.text(
                    t("vigilantia_feed_idle"),
                    size="1", color="gray",
                ),
                rx.text(
                    t("vigilantia_feed_no_sources"),
                    size="1",
                    color="var(--amber-11)",
                    style={"font_weight": "500"},
                ),
            ),
            rx.text(
                t("vigilantia_feed_disarmed"),
                size="1", color="gray",
            ),
        ),
        spacing="2",
        align="center",
        style={
            "padding": "4px 10px",
            "background_color": "var(--gray-2)",
            "border": "1px solid var(--orange-9)",
            "border_radius": "6px",
            "cursor": "pointer",
            "flex_shrink": "0",
        },
    )


def _help_row(icon: str, color: str, key_label: str, key_body: str) -> rx.Component:
    """Eine Zeile im Help-Modal — Icon · Titel · Erklärungstext."""
    return rx.hstack(
        rx.icon(icon, size=18, color=color, style={"flex_shrink": "0"}),
        rx.vstack(
            rx.text(t(key_label), font_weight="bold", size="2"),
            rx.text(t(key_body), size="1", color="gray"),
            spacing="1",
            align="stretch",
            style={"flex": "1 1 auto", "min_width": "0"},
        ),
        spacing="3",
        align="start",
        width="100%",
        style={"padding": "0.5em 0"},
    )


def vigilantia_help_modal() -> rx.Component:
    """Übersichts-Modal, das die drei Ebenen (Master / Quellen / Live-
    Steuerung) und die zugehörigen Zustände erklärt. Globally mounted
    in aifred.py, sichtbar wenn ``AIState.vigilantia_help_open``."""
    return rx.cond(
        AIState.vigilantia_help_open,
        rx.box(
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.5)",
                on_click=AIState.close_vigilantia_help,
            ),
            rx.box(
                rx.vstack(
                    rx.hstack(
                        rx.icon("lightbulb", size=20, color="#FFA500"),
                        rx.text(
                            t("vigilantia_help_title"),
                            font_weight="bold", size="4",
                        ),
                        rx.spacer(),
                        rx.icon_button(
                            rx.icon("x", size=16),
                            on_click=AIState.close_vigilantia_help,
                            size="1", variant="ghost", color_scheme="gray",
                            custom_attrs={"data-modal-close": "true"},
                        ),
                        spacing="2",
                        align="center",
                        width="100%",
                    ),
                    rx.text(
                        t("vigilantia_help_intro"),
                        color="gray", size="2",
                        margin_bottom="0.5em",
                    ),
                    rx.divider(),
                    # Ebene 1: Master
                    rx.text(
                        t("vigilantia_help_section_master"),
                        font_weight="bold", size="3",
                        style={"margin_top": "0.5em"},
                    ),
                    _help_row("eye", "#FFA500",
                              "vigilantia_help_eye_open_label",
                              "vigilantia_help_eye_open_body"),
                    _help_row("eye-off", "var(--gray-9)",
                              "vigilantia_help_eye_closed_label",
                              "vigilantia_help_eye_closed_body"),
                    rx.divider(),
                    # Ebene 2: Quellen
                    rx.text(
                        t("vigilantia_help_section_sources"),
                        font_weight="bold", size="3",
                        style={"margin_top": "0.5em"},
                    ),
                    _help_row("camera", "#FFA500",
                              "vigilantia_help_sources_label",
                              "vigilantia_help_sources_body"),
                    _help_row("users", "#FFA500",
                              "vigilantia_help_face_label",
                              "vigilantia_help_face_body"),
                    _help_row("activity", "var(--blue-9)",
                              "vigilantia_help_motion_label",
                              "vigilantia_help_motion_body"),
                    _help_row("person-standing", "var(--purple-9)",
                              "vigilantia_help_person_label",
                              "vigilantia_help_person_body"),
                    rx.divider(),
                    # Ebene 3: Vorschau
                    rx.text(
                        t("vigilantia_help_section_preview"),
                        font_weight="bold", size="3",
                        style={"margin_top": "0.5em"},
                    ),
                    _help_row("monitor", "#FFA500",
                              "vigilantia_help_preview_label",
                              "vigilantia_help_preview_body"),
                    rx.divider(),
                    # Verwandte Modals
                    rx.text(
                        t("vigilantia_help_section_related"),
                        font_weight="bold", size="3",
                        style={"margin_top": "0.5em"},
                    ),
                    _help_row("users", "#FFA500",
                              "vigilantia_help_personarium_label",
                              "vigilantia_help_personarium_body"),
                    _help_row("scroll-text", "#FFA500",
                              "vigilantia_help_casus_label",
                              "vigilantia_help_casus_body"),
                    _help_row("user-cog", "#FFA500",
                              "vigilantia_help_multipose_label",
                              "vigilantia_help_multipose_body"),
                    rx.divider(),
                    rx.text(
                        t("vigilantia_help_lifecycle_link"),
                        size="2",
                        color="#FFA500",
                        cursor="pointer",
                        on_click=[
                            AIState.close_vigilantia_help,
                            AIState.open_model_lifecycle_help,
                        ],
                        style={"text_decoration": "underline"},
                    ),
                    rx.button(
                        t("vision_settings_close"),
                        on_click=AIState.close_vigilantia_help,
                        size="2", variant="soft", width="100%",
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
                width="min(620px, 95vw)",
                max_height="92vh",
                overflow_y="auto",
                box_shadow="0 20px 60px rgba(0,0,0,0.5)",
            ),
            position="fixed",
            top="0", left="0",
            width="100vw", height="100vh",
            z_index="9999",
        ),
    )


def vigilantia_feed_popover() -> rx.Component:
    """Inline-Live-Karte (Popover-Trigger) für die Vigilantia-Chronik.

    Sitzt rechtsbündig in der Research-Modi-Zeile — zeigt permanent
    das jüngste Hintergrund-Event in Mini-Card-Optik (Zeit, Badge,
    Quelle/Person). Klick öffnet einen Popover mit der vollen Liste.
    Versteckt unless ``vigilantia_feed_visible`` im Vigilantia-
    Settings eingeschaltet ist.
    """
    # Aktive Event-Liste (gibt's wenn mind. ein Event in den letzten
    # Cycles aufgelaufen ist) ODER ein zustandsabhängiger Hinweis.
    events_block = rx.vstack(
        rx.box(
            rx.foreach(AIState.vigilantia_feed_events, _feed_row),
            style={
                "max_height": "40vh",
                "overflow_y": "auto",
                "width": "100%",
            },
        ),
        rx.hstack(
            rx.button(
                rx.icon("refresh-cw", size=12),
                rx.text(t("vigilantia_feed_refresh"), size="1"),
                on_click=AIState.refresh_vigilantia_feed,
                size="1",
                variant="soft",
                color_scheme="gray",
            ),
            rx.spacer(),
            # Casus-Sprung schliesst den Popover mit — sonst ueberdeckt
            # der Popover (Radix-Standard-Stacking) das Casus-Modal.
            # Controlled popover: rx.popover.close greift nicht mehr,
            # also explicit close + open_casus in einer Handler-Kette.
            rx.button(
                rx.icon("scroll-text", size=12),
                rx.text(t("vigilantia_feed_open_casus"), size="1"),
                on_click=[
                    AIState.close_vigilantia_feed_popover,
                    AIState.open_casus,
                ],
                size="1",
                variant="soft",
                color_scheme="orange",
            ),
            spacing="2",
            align="center",
            width="100%",
            style={"padding_top": "0.4em"},
        ),
        spacing="1",
        align="stretch",
        width="100%",
    )
    disarmed_block = rx.box(
        rx.icon("eye-off", size=20, color="gray"),
        rx.text(
            t("vigilantia_feed_disarmed_hint"),
            size="1", color="gray",
            text_align="center", margin_top="0.5em",
        ),
        rx.button(
            rx.icon("eye", size=12),
            rx.text(t("vigilantia_arm_button"), size="1"),
            on_click=AIState.toggle_vigilantia_armed,
            size="1",
            color_scheme="orange",
            style={"margin_top": "0.7em"},
        ),
        style={
            "display": "flex",
            "flex_direction": "column",
            "align_items": "center",
            "justify_content": "center",
            "padding": "1.5em 1em",
        },
    )
    no_sources_block = rx.box(
        rx.icon("triangle-alert", size=20, color="var(--amber-11)"),
        rx.text(
            t("vigilantia_feed_no_sources_hint"),
            size="1",
            text_align="center",
            margin_top="0.5em",
            style={"color": "var(--amber-11)"},
        ),
        rx.popover.close(
            rx.button(
                rx.icon("settings", size=12),
                rx.text(t("vigilantia_open_settings"), size="1"),
                on_click=AIState.open_vision_settings,
                size="1",
                color_scheme="orange",
                style={"margin_top": "0.7em"},
            ),
        ),
        style={
            "display": "flex",
            "flex_direction": "column",
            "align_items": "center",
            "justify_content": "center",
            "padding": "1.5em 1em",
        },
    )
    empty_armed_block = rx.box(
        rx.icon("eye", size=20, color="var(--orange-9)"),
        rx.text(
            t("vigilantia_feed_empty"),
            size="1", color="gray",
            text_align="center", margin_top="0.5em",
        ),
        rx.button(
            rx.icon("refresh-cw", size=12),
            rx.text(t("vigilantia_feed_refresh"), size="1"),
            on_click=AIState.refresh_vigilantia_feed,
            size="1",
            variant="soft",
            color_scheme="gray",
            style={"margin_top": "0.7em"},
        ),
        style={
            "display": "flex",
            "flex_direction": "column",
            "align_items": "center",
            "justify_content": "center",
            "padding": "1.5em 1em",
        },
    )
    feed_content = rx.cond(
        AIState.vigilantia_feed_events.length() > 0,
        events_block,
        rx.cond(
            AIState.vigilantia_armed,
            rx.cond(
                AIState.vigilantia_has_armed_source,
                empty_armed_block,
                no_sources_block,
            ),
            disarmed_block,
        ),
    )
    return rx.cond(
        AIState.vigilantia_feed_visible,
        rx.hstack(
            rx.popover.root(
                rx.popover.trigger(
                    rx.box(
                        rx.cond(
                            AIState.vigilantia_feed_events.length() > 0,
                            _latest_event_card(AIState.vigilantia_feed_events[0]),
                            _idle_card(),
                        ),
                        on_click=AIState.open_vigilantia_feed_popover,
                        # min_width:0 propagates the shrinkability of
                        # the outer hstack down to the card itself.
                        # Without this, the box keeps its 800px content
                        # basis and the page horizontal-scrolls on
                        # narrow windows.
                        style={"min_width": "0", "max_width": "100%"},
                    ),
                    style={"min_width": "0", "max_width": "100%"},
                ),
                rx.popover.content(
                    rx.vstack(
                        rx.hstack(
                            rx.text(
                                t("vigilantia_feed_title"),
                                font_weight="bold",
                                size="2",
                            ),
                            rx.spacer(),
                            # Explicit close — not rx.popover.close, because
                            # the popover is controlled. rx.popover.close
                            # only manages the Radix-internal state, which
                            # we override via open=... / on_open_change.
                            rx.icon_button(
                                rx.icon("x", size=12),
                                size="1",
                                variant="ghost",
                                color_scheme="gray",
                                on_click=AIState.close_vigilantia_feed_popover,
                                custom_attrs={"data-modal-close": "true"},
                            ),
                            align="center",
                            width="100%",
                        ),
                        rx.divider(),
                        feed_content,
                        spacing="2",
                        align="stretch",
                        width="100%",
                    ),
                    width="min(560px, 92vw)",
                    style={"padding": "0.8em"},
                    # ESC stays a convenient close shortcut — wire it
                    # directly to the explicit close handler. Without
                    # this, the open-only filter in
                    # handle_vigilantia_feed_popover_change would
                    # swallow the close event coming from ESC too.
                    on_escape_key_down=AIState.close_vigilantia_feed_popover,
                ),
                # Controlled popover — Radix would close on outside-click
                # otherwise. The state mixin ignores `False` events from
                # on_open_change (see handle_vigilantia_feed_popover_change),
                # so only the explicit X button (or ESC, above) closes it.
                open=AIState.vigilantia_feed_popover_open,
                on_open_change=AIState.handle_vigilantia_feed_popover_change,
            ),
            # Glühbirne + Auge sitzen RECHTS von der Karte und damit am
            # rechten Rand der Zeile. Der äußere rx.spacer ankert das Trio
            # rechtsbündig; die Karte (erstes Element) wächst nach links in
            # den freigewordenen Platz. So bleiben die beiden Klick-Targets
            # ortsfest, egal wie die Erkennungs-Karte ihre Breite ändert —
            # kein Springen mehr, und der Bildschirmrand ist ein leichtes Ziel.
            # Glühbirne — öffnet das Hilfe-Modal mit der Übersicht
            # über Master / Quellen / Vorschau und der Auge-Logik.
            rx.tooltip(
                rx.icon(
                    "lightbulb",
                    size=14,
                    color="#FFA500",
                    cursor="pointer",
                    on_click=AIState.open_vigilantia_help,
                    style={
                        "transition": "transform 0.2s ease",
                        "&:hover": {"transform": "scale(1.15)"},
                    },
                ),
                content=t("vigilantia_help_tooltip"),
            ),
            # Auge-Icon — Master-Toggle für vigilantia_armed.
            # Offenes Auge (eye) = scharf, geschlossenes Auge (eye-off)
            # = deaktiviert. Klick toggled. Farbe macht den Zustand
            # auch peripheriesicher erkennbar.
            rx.tooltip(
                rx.icon_button(
                    rx.cond(
                        AIState.vigilantia_armed,
                        rx.icon("eye", size=14),
                        rx.icon("eye-off", size=14),
                    ),
                    on_click=AIState.toggle_vigilantia_armed,
                    size="1",
                    variant=rx.cond(
                        AIState.vigilantia_armed, "solid", "soft",
                    ),
                    color_scheme=rx.cond(
                        AIState.vigilantia_armed, "orange", "gray",
                    ),
                    # Greyed out + unclickable when the Vision plugin is off —
                    # the Watcher can't run without it (enable it in the menu).
                    disabled=~AIState.vision_plugin_enabled,
                ),
                content=rx.cond(
                    AIState.vision_plugin_enabled,
                    rx.cond(
                        AIState.vigilantia_armed,
                        t("vigilantia_disarm_tooltip"),
                        t("vigilantia_arm_tooltip"),
                    ),
                    t("vigilantia_plugin_disabled_tooltip"),
                ),
            ),
            spacing="2",
            align="center",
            # min_width: 0 + max_width: 100% lets the trio (card + lightbulb
            # + eye) shrink with its row. Without max_width: 100% the hstack
            # would insist on its content width (~860px) even when wrapped
            # onto its own row in a narrower window — pushing horizontal
            # page scroll.
            style={"min_width": "0", "max_width": "100%"},
        ),
    )
