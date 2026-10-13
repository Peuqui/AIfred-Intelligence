"""Collapsible status bar: loaded model, describer, speech recognition, speech output.

The header carries one icon per service (the hat for the language model, a camera for the
describer, the STT and TTS suite icons) with a status dot (green loaded, yellow loading, grey
nothing loaded, red not reachable); the open bar lists name and time left until each unloads itself.
Rendering is custom.js' job (browser push bus, kind ``service_status``): the elements below
are empty frames it fills, with the texts handed over as ``data-*`` attributes.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from ..theme import COLORS
from .helpers import t

DOTS_ID = "service-status-dots"
LIST_ID = "service-status-list"
REFRESH_ID = "service-status-refresh"


def service_status_bar() -> rx.Component:
    texts = {
        "data-lang": AIState.ui_language,
        "data-t-llm": t("service_status_llm"),
        "data-t-describer": t("service_status_describer"),
        "data-t-stt": t("service_status_stt"),
        "data-t-tts": t("service_status_tts"),
        "data-icon-llm": rx.asset("AIfred-Zylinder.png"),
        "data-icon-describer": rx.asset("status-icons/vision-describer-icon.svg"),
        "data-icon-stt": rx.asset("status-icons/stt-suite-icon.svg"),
        "data-icon-tts": rx.asset("status-icons/tts-suite-icon.svg"),
        "data-t-no-model": t("service_status_no_model"),
        "data-t-none-loaded": t("service_status_none_loaded"),
        "data-t-loading": t("service_status_loading"),
        "data-t-busy": t("service_status_busy"),
        "data-t-unreachable": t("service_status_unreachable"),
        "data-t-unloads-in": t("service_status_unloads_in"),
    }
    content = rx.hstack(
        rx.box(id=LIST_ID, class_name="service-status-list", flex="1"),
        rx.tooltip(
            rx.button(
                rx.icon("refresh-cw", size=14),
                id=REFRESH_ID,
                size="1",
                variant="soft",
                color_scheme="gray",
                cursor="pointer",
            ),
            content=t("service_status_refresh"),
        ),
        align="start",
        width="100%",
        padding="2",
    )
    return rx.accordion.root(
        rx.accordion.item(
            value="service_status",
            header=rx.box(
                rx.box(id=DOTS_ID, class_name="service-status-dots", custom_attrs=texts),
                padding_y="2",
                background_color=COLORS["card_bg"],
                _hover={"background_color": COLORS["primary_bg"]},
                border_radius="6px",
                padding_x="3",
                cursor="pointer",
                transition="background-color 0.2s ease",
            ),
            content=content,
        ),
        default_value="",
        collapsible=True,
        width="100%",
        variant="soft",
        color_scheme="gray",
    )
