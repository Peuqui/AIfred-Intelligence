"""Settings: Kalibrierungs-Buttons — TTS-Varianten-Picker (llama.cpp)
und Betriebspunkt-Popover (vLLM)."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t


def _calibration_picker_button() -> rx.Component:
    """Calibrate button for llama.cpp — opens a popover with a 2D matrix
    picker. Rows are VLM choices (no VLM, Vigilantia 4B, Vigilantia 8B),
    columns are TTS choices (no TTS plus each installed engine). Every
    ticked cell becomes one calibrated llama-swap profile.

    No hidden defaults: open the popover, every cell is empty, the user
    ticks exactly the combinations they want. A green dot on a cell
    means that profile already has a real (non-preliminary) entry in
    the VRAM cache.

    While a calibration is running the picker button turns into a
    spinner and a red stop button appears next to it — calibration is a
    background task now, so the cancel event gets through immediately
    (the run ends cleanly at its next step instead of requiring a
    service restart).
    """
    return rx.hstack(
        _calibration_picker_popover(),
        _cancel_button(),
        spacing="1",
        align="center",
    )


def _matrix_header() -> rx.Component:
    """Kopfzeile der VLM x TTS-Matrix — leere Ecke plus eine Spalte je
    TTS-Engine. Von beiden Backends genutzt: llama.cpp waehlt hier
    Kalibrierziele, vLLM Burn-in-Paare, aber die Spalten sind dieselben."""
    return rx.hstack(
        rx.foreach(
            AIState.calibration_matrix_header,
            lambda lbl: rx.box(
                rx.text(
                    lbl,
                    font_size="10px",
                    font_weight="bold",
                    text_align="center",
                ),
                flex="1",
                min_width="60px",
            ),
        ),
        spacing="1",
        align="center",
        width="100%",
    )


def _matrix_row(row) -> rx.Component:
    """Eine Matrixzeile: Zeilenkopf plus je Spalte eine Zelle aus Checkbox
    und Statuspunkt (gruen = fertig, rot = fehlgeschlagen)."""
    return rx.hstack(
        rx.box(
            rx.text(row["label"], font_size="10px", font_weight="bold"),
            flex="1",
            min_width="60px",
        ),
        rx.foreach(
            row["cells"],
            lambda cell: rx.box(
                rx.hstack(
                    rx.checkbox(
                        checked=cell["checked"].to(bool),
                        on_change=lambda val, k=cell["key"]: AIState.set_calibration_matrix_cell([k, val]),  # type: ignore[arg-type]
                        size="1",
                        color_scheme="orange",
                        variant="soft",
                    ),
                    rx.box(
                        width="6px",
                        height="6px",
                        border_radius="50%",
                        background=rx.cond(
                            cell["calibration_failed"].to(bool),
                            "#ef4444",
                            rx.cond(
                                cell["already_calibrated"].to(bool),
                                "#22c55e",
                                "transparent",
                            ),
                        ),
                        flex_shrink="0",
                    ),
                    spacing="2",
                    align="center",
                    justify="center",
                ),
                flex="1",
                min_width="60px",
                display="flex",
                align_items="center",
                justify_content="center",
            ),
        ),
        spacing="1",
        align="center",
        width="100%",
    )


def _cache_reset_row() -> rx.Component:
    """VRAM-Cache-Reset — erzwingt eine frische Messung beim naechsten
    Lauf. Beide Backends lesen dieselben Caches (llama.cpp fuer die
    Reserve, vLLM fuer den Side-Channel-Burn-in)."""
    return rx.fragment(
        rx.divider(margin_y="2px"),
        rx.hstack(
            rx.tooltip(
                rx.button(
                    rx.icon("trash-2", size=11),
                    rx.text(t("calibration_reset_vlm_cache_button"), font_size="10px"),
                    on_click=AIState.reset_vlm_vram_cache,
                    size="1",
                    variant="outline",
                    color_scheme="gray",
                    flex="1",
                ),
                content=t("calibration_reset_vlm_cache_tooltip"),
            ),
            rx.tooltip(
                rx.button(
                    rx.icon("trash-2", size=11),
                    rx.text(t("calibration_reset_tts_cache_button"), font_size="10px"),
                    on_click=AIState.reset_tts_vram_cache,
                    size="1",
                    variant="outline",
                    color_scheme="gray",
                    flex="1",
                ),
                content=t("calibration_reset_tts_cache_tooltip"),
            ),
            spacing="2",
            width="100%",
        ),
    )


def _cancel_button() -> rx.Component:
    """Roter Stopp-Knopf neben dem Trigger, solange ein Lauf laeuft."""
    return rx.cond(
        AIState.is_calibrating,
        rx.button(
            rx.icon("circle-stop", size=14),
            on_click=AIState.cancel_calibration,
            size="1",
            variant="soft",
            color_scheme="red",
            title=t("calibration_cancel"),
        ),
    )


def _calibration_picker_popover() -> rx.Component:
    return rx.popover.root(
        rx.popover.trigger(
            rx.button(
                rx.cond(
                    AIState.is_calibrating,
                    rx.hstack(
                        rx.spinner(size="1"),
                        rx.text(t("calibrating"), font_size="11px"),
                        spacing="2",
                        align="center",
                    ),
                    rx.hstack(
                        rx.icon("gauge", size=14),
                        rx.text(t("calibrate_context"), font_size="11px"),
                        spacing="2",
                        align="center",
                    ),
                ),
                on_click=AIState.open_calibration_picker,
                disabled=AIState.is_calibrating | AIState.backend_switching,
                size="1",
                variant="outline",
                color_scheme="orange",
            ),
        ),
        rx.popover.content(
            rx.vstack(
                rx.text(
                    t("calibration_pick_engines"),
                    font_size="12px",
                    font_weight="bold",
                ),
                _matrix_header(),
                rx.divider(margin_y="2px"),
                rx.foreach(AIState.calibration_matrix_rows, _matrix_row),
                # Start button — closes popover and kicks off calibration.
                rx.popover.close(
                    rx.button(
                        rx.icon("play", size=12),
                        rx.text(t("calibration_start"), font_size="11px"),
                        on_click=AIState.calibrate_context,
                        size="1",
                        variant="solid",
                        color_scheme="orange",
                        width="100%",
                    ),
                ),
                _cache_reset_row(),
                spacing="2",
                align="stretch",
            ),
            min_width="380px",
            padding="12px",
        ),
    )


def _vllm_calibration_popover() -> rx.Component:
    """Calibrate popover for vLLM — same layout as the llama.cpp picker,
    different meaning per cell.

    llama.cpp: one cell = one llama-swap profile to calibrate. vLLM:
    ``side_channel_uuids()`` keeps the VLM/TTS card out of the topology
    ladder, so calibrating per combination would measure the identical
    thing nine times at ~80 minutes each. A cell here is therefore a
    BURN-IN PAIR — VLM and TTS share that one card, and the question is
    whether they fit on it together under load. Green = measured and it
    fits, red = measured and it does not.

    The popover also serves as a confirmation step: a stray click used to
    start an hours-long run that seizes every GPU, while llama.cpp muscle
    memory expects a dialog first.
    """
    return rx.popover.root(
        rx.popover.trigger(
            rx.button(
                rx.cond(
                    AIState.is_calibrating,
                    rx.hstack(
                        rx.spinner(size="1"),
                        rx.text(t("calibrating"), font_size="11px"),
                        spacing="2",
                        align="center",
                    ),
                    rx.hstack(
                        rx.box(
                            width="8px",
                            height="8px",
                            border_radius="50%",
                            background=rx.cond(
                                AIState.vllm_model_calibrated,
                                "#22c55e",
                                "#4b5563",
                            ),
                            flex_shrink="0",
                        ),
                        rx.icon("gauge", size=14),
                        rx.text(t("calibrate_context"), font_size="11px"),
                        spacing="2",
                        align="center",
                    ),
                ),
                disabled=AIState.is_calibrating | AIState.backend_switching,
                size="1",
                variant="outline",
                color_scheme="orange",
                title=rx.cond(
                    AIState.vllm_model_calibrated,
                    t("calibration_dot_done"),
                    t("calibration_dot_missing"),
                ),
            ),
        ),
        rx.popover.content(
            rx.vstack(
                rx.text(
                    t("calibration_vllm_title"),
                    font_size="12px",
                    font_weight="bold",
                ),
                rx.hstack(
                    rx.box(
                        width="8px",
                        height="8px",
                        border_radius="50%",
                        background=rx.cond(
                            AIState.vllm_model_calibrated,
                            "#22c55e",
                            "#4b5563",
                        ),
                        flex_shrink="0",
                    ),
                    rx.text(
                        rx.cond(
                            AIState.vllm_model_calibrated,
                            t("calibration_dot_done"),
                            t("calibration_dot_missing"),
                        ),
                        font_size="10px",
                    ),
                    spacing="2",
                    align="center",
                ),
                rx.divider(margin_y="2px"),
                rx.text(
                    t("calibration_vllm_burnin_title"),
                    font_size="11px",
                    font_weight="bold",
                ),
                _matrix_header(),
                rx.foreach(AIState.vllm_burnin_matrix_rows, _matrix_row),
                rx.divider(margin_y="2px"),
                rx.text(
                    t("calibration_vllm_hint"),
                    font_size="9px",
                    color="gray",
                ),
                rx.popover.close(
                    rx.button(
                        rx.icon("play", size=12),
                        rx.text(t("calibration_start"), font_size="11px"),
                        on_click=AIState.calibrate_context,
                        size="1",
                        variant="solid",
                        color_scheme="orange",
                        width="100%",
                    ),
                ),
                _cache_reset_row(),
                spacing="2",
                align="stretch",
            ),
            min_width="380px",
            padding="12px",
        ),
    )


def vllm_calibration_button() -> rx.Component:
    """Public entry: vLLM calibrate popover + cancel button while running."""
    return rx.hstack(
        _vllm_calibration_popover(),
        _cancel_button(),
        spacing="1",
        align="center",
    )
