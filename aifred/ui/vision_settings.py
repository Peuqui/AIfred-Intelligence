"""Vision-Plugin Settings-Page — /vision-settings.

Minimaler Inhalt (Mode + Modell + Sync-Toggle) — Reflex-Page analog zur
audio_settings_page, aber ohne Source-Verwaltung weil Vision-Quellen
hardware-erkannt sind (siehe ``vision_rescan_sources`` Tool).
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState


def _mode_card() -> rx.Component:
    return rx.box(
        rx.heading("Modus", size="4", margin_bottom="0.3em"),
        rx.text(
            "off: Vision deaktiviert, alle Vision-Tools verschwinden aus der LLM-Sicht. "
            "on-demand: VLM wird bei Bedarf geladen (Default). "
            "live: VLM permanent im VRAM (für Türsteher / Always-On).",
            color="gray",
            size="2",
            margin_bottom="0.5em",
        ),
        rx.select.root(
            rx.select.trigger(),
            rx.select.content(
                rx.select.item("Aus", value="off"),
                rx.select.item("Bei Bedarf", value="on-demand"),
                rx.select.item("Permanent (live)", value="live"),
            ),
            value=AIState.vision_mode_value,
            on_change=AIState.set_vision_mode_value,
        ),
        margin_bottom="1.5em",
    )


def _model_card() -> rx.Component:
    return rx.box(
        rx.heading("VLM-Modell (Side-Channel)", size="4", margin_bottom="0.3em"),
        rx.text(
            "Wird für Webcam-Snapshots, Watch-Mode und für den Side-Channel "
            "des Chat-Bild-Uploads genutzt (vermeidet llama-swap-Modell-Swap). "
            "Aus Ollama-Discovery — neue Modelle erscheinen nach `ollama pull`.",
            color="gray",
            size="2",
            margin_bottom="0.5em",
        ),
        rx.hstack(
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
            rx.icon_button(
                rx.icon("refresh-cw", size=14),
                on_click=AIState.rescan_vision_models,
                size="2",
                variant="soft",
                color_scheme="gray",
                title="Ollama-Modelle neu scannen",
            ),
            spacing="2",
            align="center",
        ),
        margin_bottom="1.5em",
    )


def _sync_card() -> rx.Component:
    return rx.hstack(
        rx.switch(
            checked=AIState.vision_sync_value,
            on_change=AIState.set_vision_sync_value,
        ),
        rx.vstack(
            rx.text("Mit Vision-LLM aus Hauptsettings synchronisieren"),
            rx.text(
                "Wenn aktiv, übernimmt die Vision-Pipeline das Modell aus "
                "dem Vision-LLM-Dropdown der Hauptsettings statt obiger Auswahl.",
                color="gray",
                size="2",
            ),
            align="start",
            spacing="0",
        ),
        spacing="3",
        align="center",
        margin_bottom="1.5em",
    )


def _status_callout() -> rx.Component:
    return rx.cond(
        AIState.vision_settings_status != "",
        rx.callout.root(
            rx.callout.icon(rx.icon("info")),
            rx.callout.text(AIState.vision_settings_status),
            color_scheme="green",
            margin_bottom="1em",
        ),
    )


def vision_settings_page() -> rx.Component:
    """Page-Komponente für die Route ``/vision-settings``."""
    return rx.container(
        rx.heading("Bild & Video — Einstellungen", size="6"),
        rx.text(
            "Konfiguration der Vision-Pipeline: ob aktiv, welches VLM für "
            "Webcam und Side-Channel-Routing genutzt wird.",
            color="gray",
            margin_bottom="1.5em",
        ),
        rx.vstack(
            _mode_card(),
            _model_card(),
            _sync_card(),
            _status_callout(),
            rx.button(
                "Zurück",
                on_click=rx.redirect("/"),
                variant="soft",
                margin_top="1em",
            ),
            align="stretch",
            spacing="3",
            width="100%",
        ),
        padding="2em",
        max_width="640px",
    )
