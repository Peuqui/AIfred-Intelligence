"""Settings: Kalibrierungs-Button + TTS-Varianten-Picker (llama.cpp)."""

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
        rx.cond(
            AIState.is_calibrating,
            rx.button(
                rx.icon("circle-stop", size=14),
                on_click=AIState.cancel_calibration,
                size="1",
                variant="soft",
                color_scheme="red",
                title=t("calibration_cancel"),
            ),
        ),
        spacing="1",
        align="center",
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
                # Header row: empty corner + one label per TTS column.
                rx.hstack(
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
                ),
                rx.divider(margin_y="2px"),
                # One hstack per VLM row: row label + one checkbox cell
                # per TTS column.
                rx.foreach(
                    AIState.calibration_matrix_rows,
                    lambda row: rx.hstack(
                        rx.box(
                            rx.text(
                                row["label"],
                                font_size="10px",
                                font_weight="bold",
                            ),
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
                                    # Fixed-width slot for the status
                                    # dot: green = already calibrated,
                                    # red = tried but failed. Always
                                    # rendered so the checkbox column
                                    # alignment stays identical whether
                                    # a dot is visible or not.
                                    rx.box(
                                        rx.cond(
                                            cell["already_calibrated"].to(bool),
                                            rx.tooltip(
                                                rx.box(
                                                    width="6px",
                                                    height="6px",
                                                    border_radius="50%",
                                                    background="#22c55e",
                                                ),
                                                content=t("calibration_already_done"),
                                            ),
                                            rx.cond(
                                                cell["calibration_failed"].to(bool),
                                                rx.tooltip(
                                                    rx.box(
                                                        width="6px",
                                                        height="6px",
                                                        border_radius="50%",
                                                        background="#ef4444",
                                                    ),
                                                    content=t("calibration_previously_failed"),
                                                ),
                                            ),
                                        ),
                                        width="10px",
                                        height="10px",
                                        display="flex",
                                        align_items="center",
                                        justify_content="center",
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
                    ),
                ),
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
                # VRAM-Cache reset row — forces a fresh stress-burn-in
                # measurement on the next calibration. Useful after a
                # container update or when the user wants to re-validate.
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
                spacing="2",
                align="stretch",
            ),
            min_width="380px",
            padding="12px",
        ),
    )
