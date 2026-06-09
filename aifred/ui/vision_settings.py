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


def _alert_type_check(
    sid: rx.Var, cam: rx.Var, atype: str, label_key: str
) -> rx.Component:
    """Checkbox für einen Alarm-Event-Typ einer Kamera (Phase 3)."""
    return rx.checkbox(
        t(label_key),
        checked=cam["alert_types"].to(list[str]).contains(atype),
        on_change=lambda v: AIState.set_vigilantia_alert_type(sid, atype, v),
        size="1",
        color_scheme="orange",
    )


def _source_card(cam: rx.Var) -> rx.Component:
    """Eine Karte pro Cam in der Quellen-Sektion. Zeigt Name +
    Hintergrund-Toggle + Auflösung.

    Bewegungs-Schwelle und Zonen-Maske werden im Zonen-Editor (Button)
    eingestellt — dort sieht man die Live-Bewegung blau überlagert und
    tunt die Schwelle direkt am Bild. Resolution + Alias bleiben im
    Vorschau-Popup. Hier nur die Hintergrund-relevante Konfig.
    """
    sid = cam["id"]
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.icon("camera", size=16, color="gray"),
                # Kameraname editierbar — SSoT für den Namen liegt jetzt hier
                # in den Einstellungen (die Live-Vorschau zeigt ihn nur an).
                rx.input(
                    default_value=cam["alias"].to(str),
                    placeholder=cam["hardware_name"].to(str),
                    on_blur=lambda v: AIState.set_vigilantia_source_alias(sid, v),
                    size="2",
                    style={"flex": "1", "min_width": "0"},
                ),
                rx.cond(
                    ~cam["available"].to(bool),
                    rx.badge("✗", color_scheme="red", size="1"),
                ),
                rx.switch(
                    checked=cam["auto_start"].to(bool),
                    on_change=lambda v: AIState.set_vigilantia_source_auto_start(sid, v),
                    size="2",
                    color_scheme="orange",
                ),
                align="center",
                width="100%",
                spacing="2",
            ),
            rx.text(
                t("vision_settings_source_background_help"),
                color="gray", size="1",
            ),
            # Pro-Kamera Push-Alerts an/aus (Anti-Spam). Aus = erkennt/speichert
            # weiter, schickt aber keine proaktiven Benachrichtigungen.
            rx.hstack(
                rx.icon("bell", size=14, color="gray"),
                rx.text(t("vision_settings_source_alerts_label"), size="2", color="gray"),
                rx.spacer(),
                rx.switch(
                    checked=cam["alerts_enabled"].to(bool),
                    on_change=lambda v: AIState.set_vigilantia_source_alerts(sid, v),
                    size="2",
                    color_scheme="orange",
                ),
                align="center",
                width="100%",
                spacing="2",
                style={"margin_top": "0.3em"},
            ),
            # Feinfilter (nur sinnvoll wenn Alerts an): welche Event-Typen
            # alarmieren + Ruhezeit. (Phase 3)
            rx.cond(
                cam["alerts_enabled"].to(bool),
                rx.vstack(
                    rx.hstack(
                        rx.text(
                            t("vision_settings_alert_types_label"),
                            size="1", color="gray",
                        ),
                        _alert_type_check(sid, cam, "person", "vision_settings_alert_type_person"),
                        _alert_type_check(sid, cam, "vehicle", "vision_settings_alert_type_vehicle"),
                        _alert_type_check(sid, cam, "animal", "vision_settings_alert_type_animal"),
                        _alert_type_check(sid, cam, "face", "vision_settings_alert_type_face"),
                        spacing="2", align="center", wrap="wrap",
                    ),
                    rx.hstack(
                        rx.icon("moon", size=13, color="gray"),
                        rx.text(t("vision_settings_quiet_label"), size="1", color="gray"),
                        rx.switch(
                            checked=cam["quiet_enabled"].to(bool),
                            on_change=lambda v: AIState.set_vigilantia_quiet(
                                sid, "quiet_enabled", v
                            ),
                            size="1", color_scheme="orange",
                        ),
                        rx.cond(
                            cam["quiet_enabled"].to(bool),
                            rx.hstack(
                                rx.input(
                                    value=cam["quiet_start"].to(str),
                                    on_change=lambda v: AIState.set_vigilantia_quiet(
                                        sid, "quiet_start", v
                                    ),
                                    type="number", size="1",
                                    style={"width": "3.5em"},
                                ),
                                rx.text("–", size="1", color="gray"),
                                rx.input(
                                    value=cam["quiet_end"].to(str),
                                    on_change=lambda v: AIState.set_vigilantia_quiet(
                                        sid, "quiet_end", v
                                    ),
                                    type="number", size="1",
                                    style={"width": "3.5em"},
                                ),
                                rx.text(
                                    t("vision_settings_quiet_oclock"),
                                    size="1", color="gray",
                                ),
                                align="center", spacing="1",
                            ),
                        ),
                        align="center", spacing="2", width="100%",
                    ),
                    spacing="1", align="start", width="100%",
                    style={"margin_top": "0.2em", "padding_left": "0.3em"},
                ),
            ),
            rx.hstack(
                rx.text(
                    t("vision_settings_source_resolution_label"),
                    size="2", color="gray",
                ),
                rx.text(cam["resolution"], size="1", color="gray"),
                rx.spacer(),
                spacing="2",
                align="center",
                width="100%",
                style={"margin_top": "0.3em"},
            ),
            rx.button(
                rx.icon("brush", size=12),
                rx.text(t("vision_settings_zone_mask_button"), size="1"),
                on_click=AIState.open_zone_editor(sid),
                size="1",
                variant="soft",
                color_scheme="orange",
                style={"margin_top": "0.4em", "align_self": "flex-start"},
            ),
            spacing="1",
            align="stretch",
            width="100%",
        ),
        style={
            "border": "1px solid var(--gray-6)",
            "border_radius": "8px",
            "padding": "0.7em",
            "width": "100%",
        },
    )


def _sources_section() -> rx.Component:
    """Quellen-Liste — pro Cam eine Karte mit Hintergrund-Toggle +
    Min-Bewegungsfläche. Ersetzt die zweite Zeile pro Source im
    Vorschau-Popup; alles, was zur Hintergrund-Konfig gehört, jetzt
    an einem Ort."""
    return rx.vstack(
        rx.hstack(
            rx.text(
                t("vision_settings_sources_label"),
                font_weight="bold", size="3",
            ),
            rx.spacer(),
            rx.icon_button(
                rx.icon("refresh-cw", size=12),
                on_click=AIState.open_vision_settings,
                size="1",
                variant="ghost",
                color_scheme="gray",
                title=t("vision_settings_sources_refresh"),
            ),
            align="center",
            width="100%",
        ),
        rx.text(
            t("vision_settings_sources_help"),
            color="gray", size="1",
        ),
        rx.cond(
            AIState.vigilantia_sources.length() > 0,
            rx.vstack(
                rx.foreach(AIState.vigilantia_sources, _source_card),
                spacing="2",
                align="stretch",
                width="100%",
            ),
            rx.box(
                rx.text(
                    t("vision_settings_sources_empty"),
                    color="gray", size="2", text_align="center",
                ),
                style={"padding": "1em"},
            ),
        ),
        align="stretch",
        spacing="1",
        width="100%",
    )


def _rtsp_field(
    label_key: str, field: str, placeholder: str = "", input_type: str = "text"
) -> rx.Component:
    """Ein beschriftetes Formularfeld, gebunden an AIState.rtsp_form[field]."""
    return rx.vstack(
        rx.text(t(label_key), size="1", color="gray"),
        rx.input(
            value=AIState.rtsp_form[field].to(str),
            placeholder=placeholder,
            type=input_type,
            on_change=lambda v: AIState.set_rtsp_form_field(field, v),
            size="2", width="100%",
        ),
        spacing="1", align="stretch", width="100%",
    )


def _rtsp_camera_row(cam: rx.Var) -> rx.Component:
    """Eine Zeile pro konfigurierter RTSP-Kamera (Name, Profil, Host) +
    Bearbeiten/Löschen."""
    return rx.hstack(
        rx.icon("video", size=14, color="gray"),
        rx.text(cam["name"], size="2", weight="medium"),
        rx.badge(cam["profile"], color_scheme="orange", size="1"),
        rx.text(cam["host"], size="1", color="gray"),
        rx.spacer(),
        rx.icon_button(
            rx.icon("pencil", size=12),
            on_click=lambda: AIState.open_rtsp_camera_edit(cam["name"]),
            size="1", variant="ghost", color_scheme="gray",
        ),
        rx.icon_button(
            rx.icon("trash-2", size=12),
            on_click=lambda: AIState.delete_rtsp_camera(cam["name"]),
            size="1", variant="ghost", color_scheme="red",
        ),
        align="center", width="100%", spacing="2",
    )


def _rtsp_camera_form() -> rx.Component:
    """Add/Edit-Formular für eine RTSP-Kamera."""
    return rx.box(
        rx.vstack(
            rx.hstack(
                _rtsp_field("vision_rtsp_field_name", "name", "Hauseingang"),
                _rtsp_field("vision_rtsp_field_host", "host", "192.168.0.251"),
                spacing="2", width="100%",
            ),
            rx.hstack(
                _rtsp_field("vision_rtsp_field_port", "port", "554"),
                _rtsp_field("vision_rtsp_field_path", "path", "h264Preview_01_sub"),
                spacing="2", width="100%",
            ),
            rx.hstack(
                rx.vstack(
                    rx.text(t("vision_rtsp_field_profile"), size="1", color="gray"),
                    rx.select(
                        ["webcam", "ai_camera"],
                        value=AIState.rtsp_form["profile"].to(str),
                        on_change=lambda v: AIState.set_rtsp_form_field("profile", v),
                        size="2", width="100%",
                    ),
                    spacing="1", align="stretch", width="100%",
                ),
                _rtsp_field("vision_rtsp_field_cred", "cred", "reolink"),
                spacing="2", width="100%",
            ),
            rx.hstack(
                rx.text(t("vision_rtsp_field_ptz"), size="2", color="gray"),
                rx.spacer(),
                rx.switch(
                    checked=AIState.rtsp_form["ptz"].to(bool),
                    on_change=lambda v: AIState.set_rtsp_form_field("ptz", v),
                    size="2", color_scheme="orange",
                ),
                align="center", width="100%",
                style={"margin_top": "0.3em"},
            ),
            rx.cond(
                AIState.rtsp_form["ptz"].to(bool),
                _rtsp_field("vision_rtsp_field_onvif", "onvif_port", "8000"),
            ),
            rx.cond(
                AIState.rtsp_form["profile"].to(str) == "ai_camera",
                rx.hstack(
                    _rtsp_field("vision_rtsp_field_apiport", "api_port", "443"),
                    _rtsp_field("vision_rtsp_field_facechannel", "face_channel", "1"),
                    spacing="2", width="100%",
                ),
            ),
            rx.hstack(
                _rtsp_field("vision_rtsp_field_user", "user", "admin"),
                _rtsp_field("vision_rtsp_field_password", "password", "", "password"),
                spacing="2", width="100%",
            ),
            rx.text(t("vision_rtsp_cred_hint"), size="1", color="gray"),
            rx.cond(
                AIState.rtsp_form_error != "",
                rx.text(AIState.rtsp_form_error, color="red", size="1"),
            ),
            rx.hstack(
                rx.button(
                    t("vision_rtsp_save"),
                    on_click=AIState.save_rtsp_camera,
                    size="2", color_scheme="orange",
                ),
                rx.button(
                    t("vision_rtsp_cancel"),
                    on_click=AIState.close_rtsp_camera_form,
                    size="2", variant="soft", color_scheme="gray",
                ),
                spacing="2",
            ),
            spacing="2", align="stretch", width="100%",
        ),
        style={
            "border": "1px solid var(--gray-6)",
            "border_radius": "8px",
            "padding": "0.8em",
            "margin_top": "0.5em",
            "width": "100%",
        },
    )


def _rtsp_cameras_section() -> rx.Component:
    """RTSP-Kamera-Verwaltung: Liste + Hinzufügen/Bearbeiten/Löschen."""
    return rx.vstack(
        rx.hstack(
            rx.text(t("vision_rtsp_section_title"), font_weight="bold", size="3"),
            rx.spacer(),
            rx.button(
                rx.icon("plus", size=12),
                rx.text(t("vision_rtsp_add"), size="1"),
                on_click=AIState.open_rtsp_camera_new,
                size="1", variant="soft", color_scheme="orange",
            ),
            align="center", width="100%",
        ),
        rx.cond(
            AIState.rtsp_cameras_list.length() > 0,
            rx.vstack(
                rx.foreach(AIState.rtsp_cameras_list, _rtsp_camera_row),
                spacing="1", align="stretch", width="100%",
            ),
            rx.text(t("vision_rtsp_empty"), color="gray", size="1"),
        ),
        rx.cond(AIState.rtsp_form_open, _rtsp_camera_form()),
        align="stretch", spacing="1", width="100%",
    )


def _feed_visibility_section() -> rx.Component:
    """Toggle für den Vigilantia-Live-Feed im Haupttab. Default off —
    User schaltet das ein, wenn er Hintergrund-Cams nutzt."""
    return rx.vstack(
        rx.hstack(
            rx.text(
                t("vision_settings_feed_visible_label"),
                font_weight="bold", size="3",
            ),
            rx.spacer(),
            rx.switch(
                checked=AIState.vigilantia_feed_visible,
                on_change=AIState.set_vigilantia_feed_visible,
                size="2",
                color_scheme="orange",
            ),
            align="center",
            width="100%",
        ),
        rx.text(
            t("vision_settings_feed_visible_help"),
            color="gray", size="1",
        ),
        align="stretch",
        spacing="1",
        width="100%",
    )


def _face_recognition_section() -> rx.Component:
    """Toggle für Face-Recognition + Continuous-Modus + Retention-Input.
    Alle schreiben in plugins/tools/vision/settings.json."""
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
        # Continuous-Toggle: kontinuierliche Detection vs. nur-bei-Motion
        rx.hstack(
            rx.text(
                t("vision_settings_face_continuous_label"),
                size="2",
            ),
            rx.spacer(),
            rx.switch(
                checked=AIState.face_recognition_continuous,
                on_change=AIState.set_face_recognition_continuous,
                size="2",
                color_scheme="orange",
            ),
            align="center",
            width="100%",
            style={"margin_top": "0.5em"},
        ),
        rx.text(
            t("vision_settings_face_continuous_help"),
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
                            custom_attrs={"data-modal-close": "true"},
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
                    _sources_section(),
                    rx.divider(),
                    _rtsp_cameras_section(),
                    rx.divider(),
                    _feed_visibility_section(),
                    rx.divider(),
                    # Sub-Modals (Personarium / Casus / Multi-Pose) öffnen
                    # OHNE das Vigilantia-Modal zu schließen — sie
                    # stapeln sich darüber. Beim Schließen des
                    # Sub-Modals ist Vigilantia wieder die Top-Layer,
                    # der User kommt nicht zu weit zurück.
                    rx.button(
                        rx.icon("users", size=16),
                        rx.text(t("vision_settings_open_personarium")),
                        on_click=AIState.open_personarium,
                        size="2",
                        variant="soft",
                        color_scheme="orange",
                        width="100%",
                    ),
                    rx.button(
                        rx.icon("scroll-text", size=16),
                        rx.text(t("vision_settings_open_casus")),
                        on_click=AIState.open_casus,
                        size="2",
                        variant="soft",
                        color_scheme="orange",
                        width="100%",
                    ),
                    rx.button(
                        rx.icon("user-cog", size=16),
                        rx.text(t("vision_settings_open_multipose")),
                        on_click=AIState.open_multipose(0, ""),
                        size="2",
                        variant="soft",
                        color_scheme="orange",
                        width="100%",
                    ),
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
