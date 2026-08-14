"""Agent-Editor: STT-Tab — Whisper-Service konfigurieren.

Reines Frontend auf die Service-Config (SSOT im Whisper-Container,
siehe ``state/_stt_settings_mixin.py``).
"""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t
from .header import _editor_header


def _stt_row(label, control: rx.Component, hint=None) -> rx.Component:
    """Eine Einstellungs-Zeile: Label links, Control rechts, optionaler
    Hinweistext darunter.

    ``label``/``hint`` sind übersetzte Werte und damit Reflex-Vars — die
    dürfen nicht in ein bool gecastet werden (kein ``or``, kein ``if``).
    Ob die Hinweiszeile existiert, entscheidet daher Python beim Bauen
    des Trees, nicht ``rx.cond`` zur Laufzeit.
    """
    rows: list[rx.Component] = [
        rx.hstack(
            # flex_grow + min_width 0 lässt das Label schrumpfen statt die
            # Zeile breiter werden zu lassen — sonst schiebt die Summe aus
            # Label- und Control-Breite den Tab in einen H-Scrollbalken.
            rx.text(
                label, font_size="13px", color="#ccc",
                flex_grow="1", min_width="0",
            ),
            rx.box(control, flex_shrink="0"),
            width="100%",
            spacing="3",
            align="center",
        ),
    ]
    if hint is not None:
        rows.append(rx.text(hint, font_size="11px", color="#777", width="100%"))
    return rx.vstack(
        *rows,
        spacing="1",
        width="100%",
        min_width="0",
        padding_y="6px",
        border_bottom="1px solid #2a2a2a",
    )


def _stt_view() -> rx.Component:
    """STT-Tab: Whisper-Service-Einstellungen."""
    return rx.vstack(
        _editor_header(),
        rx.cond(
            ~AIState.stt_available,
            rx.callout(
                t("stt_unavailable"),
                icon="triangle-alert",
                color_scheme="red",
                width="100%",
            ),
            # Genau EIN Scroll-Container (wie im Speicher-Tab): zwei
            # verschachtelte erzeugten je einen eigenen Scrollbalken.
            rx.box(
                rx.vstack(
                    # Status: was läuft gerade wirklich (Degradierungskette!)
                    rx.hstack(
                        rx.badge(
                            rx.cond(
                                AIState.stt_gpu_model_loaded != "",
                                "GPU: " + AIState.stt_gpu_model_loaded,
                                "GPU: \u2014",
                            ),
                            color_scheme=rx.cond(
                                AIState.stt_gpu_model_loaded != "", "green", "gray",
                            ),
                            variant="soft",
                        ),
                        rx.badge(
                            rx.cond(
                                AIState.stt_cpu_loaded,
                                "CPU: " + AIState.stt_cpu_model,
                                "CPU: \u2014",
                            ),
                            color_scheme=rx.cond(AIState.stt_cpu_loaded, "green", "gray"),
                            variant="soft",
                        ),
                        rx.spacer(),
                        rx.icon_button(
                            rx.icon("refresh-cw", size=14),
                            on_click=AIState.refresh_stt_settings,
                            size="1", variant="ghost", color_scheme="gray",
                            cursor="pointer",
                        ),
                        width="100%",
                        align="center",
                    ),
                    _stt_row(
                        t("stt_gpu_model"),
                        rx.select(
                            AIState.stt_available_models,
                            value=AIState.stt_gpu_model,
                            on_change=AIState.stt_set_gpu_model,
                            size="1",
                        ),
                        t("stt_gpu_model_hint"),
                    ),
                    _stt_row(
                        t("stt_cpu_model"),
                        rx.select(
                            AIState.stt_available_models,
                            value=AIState.stt_cpu_model,
                            on_change=AIState.stt_set_cpu_model,
                            size="1",
                        ),
                    ),
                    _stt_row(
                        t("stt_num_speakers"),
                        rx.input(
                            value=AIState.stt_num_speakers.to_string(),
                            on_change=AIState.stt_set_num_speakers,
                            type="number", size="1", width="70px",
                            min="0", max="10",
                        ),
                        t("stt_num_speakers_hint"),
                    ),
                    _stt_row(
                        t("stt_initial_prompt"),
                        rx.input(
                            value=AIState.stt_initial_prompt,
                            on_change=AIState.stt_set_initial_prompt,
                            size="1", width="200px",
                        ),
                        t("stt_initial_prompt_hint"),
                    ),
                    _stt_row(
                        t("stt_beam_size"),
                        rx.input(
                            value=AIState.stt_beam_size.to_string(),
                            on_change=AIState.stt_set_beam_size,
                            type="number", size="1", width="70px",
                            min="1", max="20",
                        ),
                    ),
                    _stt_row(
                        t("stt_vad_filter"),
                        rx.switch(
                            checked=AIState.stt_vad_filter,
                            on_change=AIState.stt_set_vad_filter,
                            size="1", color_scheme="orange",
                        ),
                    ),
                    _stt_row(
                        t("stt_condition_on_previous"),
                        rx.switch(
                            checked=AIState.stt_condition_on_previous,
                            on_change=AIState.stt_set_condition_on_previous,
                            size="1", color_scheme="orange",
                        ),
                        t("stt_condition_on_previous_hint"),
                    ),
                    _stt_row(
                        t("stt_gpu_ttl"),
                        rx.input(
                            value=AIState.stt_gpu_ttl_minutes.to_string(),
                            on_change=AIState.stt_set_gpu_ttl,
                            type="number", size="1", width="70px",
                            min="0",
                        ),
                        t("stt_gpu_ttl_hint"),
                    ),
                    rx.hstack(
                        rx.button(
                            rx.icon("save", size=14),
                            t("stt_save"),
                            on_click=AIState.save_stt_settings,
                            size="2", color_scheme="orange",
                            cursor="pointer",
                            flex_shrink="0",
                        ),
                        rx.text(
                            AIState.stt_save_message,
                            font_size="12px", color="#8c8",
                            flex_grow="1", min_width="0",
                        ),
                        spacing="3",
                        align="center",
                        width="100%",
                        padding_top="10px",
                    ),
                    spacing="2",
                    width="100%",
                    min_width="0",
                ),
                overflow_y="auto",
                overflow_x="hidden",
                flex_grow="1",
                width="100%",
                min_width="0",
                padding_right="6px",
            ),
        ),
        spacing="3",
        width="100%",
        height="100%",
        min_width="0",
        overflow_x="hidden",
    )
