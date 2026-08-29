"""Settings: Haupt-Accordion (Skelett — Sektionen in den Nachbar-Modulen)."""

from __future__ import annotations

import reflex as rx

from ...theme import COLORS
from ..helpers import t
from .agent_model_rows import (
    _aifred_model_row,
    _aifred_toggles_row,
    _automatik_model_row,
    _automatik_rope_row,
    _secondary_agent_rows,
    _vision_model_row,
    _vision_rope_row,
    _vision_toggles_row,
)
from .audio_system_section import (
    _restart_buttons,
    _restart_info,
    _stt_section,
    _tts_section,
)
from .backend_section import (
    _backend_row,
    _calibration_row,
    _cloud_provider_row,
    _language_user_row,
)


def settings_accordion() -> rx.Component:
    """Settings accordion at bottom - Kompakt"""
    return rx.accordion.root(
        rx.accordion.item(
            value="settings",  # Eindeutige ID für das Accordion Item
            header=rx.box(
                rx.text(t("settings"), font_size="12px", font_weight="500", color=COLORS["text_primary"]),
                padding_y="2",  # Kompakter Header
            ),
            content=rx.vstack(
                _language_user_row(),
                _backend_row(),
                _calibration_row(),
                _cloud_provider_row(),
                _aifred_model_row(),
                _aifred_toggles_row(),
                # Sokrates/Salomo/Custom-Agenten — generisch via foreach
                _secondary_agent_rows(),
                _automatik_model_row(),
                _automatik_rope_row(),
                _vision_model_row(),
                _vision_rope_row(),
                _vision_toggles_row(),

                # NOTE: Global "Thinking Mode" toggle removed in v2.23.0
                # Reasoning is now controlled per-agent via aifred_reasoning, sokrates_reasoning, salomo_reasoning
                # which control BOTH the reasoning prompt AND the enable_thinking flag

                # NOTE: _yarn_section() nicht mehr gerendert — YaRN gehörte
                # zum alten Direkt-vLLM-Pfad; Bewertung verschoben
                # (Backend-Trennungs-Paket 2026-08-29).

                # TTS/STT Settings
                rx.divider(margin_top="12px", margin_bottom="12px"),

                # TTS (Text-to-Speech) Section
                _tts_section(),

                # STT (Speech-to-Text) Section
                rx.divider(margin_top="12px", margin_bottom="12px"),
                _stt_section(),

                # Restart Buttons
                rx.divider(),
                rx.text(t("system_control"), font_weight="bold", font_size="12px"),
                _restart_buttons(),
                # Neustart-Info Texte
                _restart_info(),

                spacing="4",
                width="100%",
            ),
        ),
        id="settings-accordion",  # ID for JavaScript height sync
        collapsible=True,  # WICHTIG: Macht Accordion schließbar!
        default_value="settings",  # Standardmäßig geöffnet
        color_scheme="gray",
        variant="soft",
    )
