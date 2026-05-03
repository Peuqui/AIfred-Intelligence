"""Audio Player Plugin Settings Modal.

Lists all audio sources (folders + symlinks under data/media/audio/, plus
HTTP streams from settings.json). Per-source actions:

- Indexieren (incremental, mtime-based)
- Komplett (force=True, ignores mtime)
- Index löschen
- Source entfernen (symlink/folder unlink)

Plus a "Neue Source" button that triggers the generic file_picker.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState


def _source_row(src: rx.Var) -> rx.Component:  # type: ignore[type-arg]
    """Single row per source — type icon, label, target, indexed-count, action buttons."""
    is_busy = (AIState.audio_settings_busy_source == src["label"]) & (
        AIState.audio_settings_busy != ""
    )

    return rx.box(
        rx.hstack(
            # Type icon
            rx.cond(
                src["is_stream"],
                rx.icon("radio", size=16, color="#a78bfa"),
                rx.cond(
                    src["is_symlink"],
                    rx.icon("link", size=16, color="#4287f5"),
                    rx.icon("folder", size=16, color="#4287f5"),
                ),
            ),
            # Label
            rx.text(
                src["label"],
                font_weight="bold",
                font_size="14px",
                min_width="120px",
            ),
            # Target (Pfad/URL) + indexed-count
            rx.vstack(
                rx.text(
                    src["target"],
                    font_size="11px",
                    color="#888",
                    font_family="monospace",
                    overflow="hidden",
                    text_overflow="ellipsis",
                    white_space="nowrap",
                ),
                rx.cond(
                    src["is_stream"],
                    rx.text("Stream — kein Index", font_size="10px", color="#666"),
                    rx.text(
                        src["indexed"].to(str), " Dateien indexiert",
                        font_size="10px",
                        color="#666",
                    ),
                ),
                spacing="0",
                align="start",
                flex="1",
                min_width="0",
            ),
            rx.spacer(),
            # Action buttons (only for local_folder sources)
            rx.cond(
                src["is_stream"],
                rx.fragment(),
                rx.hstack(
                    rx.button(
                        rx.cond(
                            is_busy,
                            rx.spinner(size="1"),
                            rx.icon("refresh-cw", size=14),
                        ),
                        "Indexieren",
                        size="1",
                        variant="soft",
                        on_click=AIState.audio_index_rebuild_source(src["label"], False),
                        cursor="pointer",
                        disabled=AIState.audio_settings_busy != "",
                    ),
                    rx.button(
                        rx.icon("zap", size=14),
                        "Force",
                        size="1",
                        variant="soft",
                        color_scheme="orange",
                        on_click=AIState.audio_index_rebuild_source(src["label"], True),
                        cursor="pointer",
                        disabled=AIState.audio_settings_busy != "",
                        title="Komplett neu indexieren (ignoriert mtime, liest alle Tags neu)",
                    ),
                    rx.button(
                        rx.icon("trash", size=14),
                        size="1",
                        variant="soft",
                        color_scheme="gray",
                        on_click=AIState.audio_index_clear_source(src["label"]),
                        cursor="pointer",
                        title="Index-Eintraege loeschen (Source bleibt)",
                    ),
                    rx.button(
                        rx.icon("x", size=14),
                        size="1",
                        variant="soft",
                        color_scheme="red",
                        on_click=AIState.audio_remove_source(src["label"]),
                        cursor="pointer",
                        title="Source komplett entfernen (Symlink/Folder weg + Index-Eintraege weg)",
                    ),
                    spacing="2",
                ),
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        padding="10px 12px",
        background_color="rgba(40, 40, 40, 0.6)",
        border="1px solid #333",
        border_radius="6px",
        margin_bottom="6px",
    )


def audio_settings_modal() -> rx.Component:
    """The audio plugin settings modal — opened via gear icon in plugin tab."""
    return rx.cond(
        AIState.audio_settings_open,
        rx.box(
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.6)",
                on_click=AIState.close_audio_settings,
            ),
            rx.vstack(
                # Header
                rx.hstack(
                    rx.icon("music", size=20, color="#4287f5"),
                    rx.text("Audio Player — Sources & Index", font_weight="bold", font_size="15px"),
                    rx.spacer(),
                    rx.button(
                        rx.icon("x", size=16),
                        size="1",
                        variant="ghost",
                        on_click=AIState.close_audio_settings,
                        cursor="pointer",
                    ),
                    spacing="2",
                    align="center",
                    width="100%",
                    padding_bottom="8px",
                    border_bottom="1px solid #333",
                ),
                # Hint
                rx.text(
                    "Sources sind Folders oder Symlinks unter ",
                    rx.code("data/media/audio/"),
                    ". Symlinks zeigen NAS-Inhalt transparent. "
                    "HTTP-Streams werden in der settings.json verwaltet.",
                    font_size="12px",
                    color="#888",
                    line_height="1.5",
                ),
                # Source list
                rx.vstack(
                    rx.foreach(AIState.audio_sources_view, _source_row),
                    spacing="0",
                    width="100%",
                    max_height="50vh",
                    overflow_y="auto",
                ),
                # Status line
                rx.cond(
                    AIState.audio_settings_status != "",
                    rx.text(
                        AIState.audio_settings_status,
                        font_size="12px",
                        color="#aaa",
                        font_family="monospace",
                        padding="6px 10px",
                        background_color="rgba(0, 0, 0, 0.3)",
                        border_radius="4px",
                    ),
                    rx.fragment(),
                ),
                # Footer with add-source button
                rx.hstack(
                    rx.button(
                        rx.icon("folder-plus", size=14),
                        "Neue Source hinzufuegen…",
                        size="2",
                        variant="soft",
                        on_click=AIState.open_audio_source_picker,
                        cursor="pointer",
                    ),
                    rx.spacer(),
                    rx.button(
                        "Schliessen",
                        size="2",
                        variant="soft",
                        on_click=AIState.close_audio_settings,
                        cursor="pointer",
                    ),
                    spacing="2",
                    width="100%",
                    padding_top="10px",
                    border_top="1px solid #333",
                ),
                spacing="3",
                width="min(800px, 95vw)",
                max_height="90vh",
                overflow_y="auto",
                padding="20px",
                background_color="#1f1f1f",
                border_radius="8px",
                border="1px solid #444",
                box_shadow="0 8px 32px rgba(0, 0, 0, 0.5)",
                position="relative",
                z_index="1000",
            ),
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            display="flex",
            align_items="center",
            justify_content="center",
            z_index="999",
        ),
        rx.fragment(),
    )
