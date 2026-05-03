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


def _help_panel() -> rx.Component:
    """Toggleable help panel — explains the workflow + each button."""
    bullet = lambda label, text: rx.hstack(  # noqa: E731
        rx.text(label, font_weight="bold", min_width="120px", color="#ffd166"),
        rx.text(text, font_size="12px", color="#ddd", flex="1"),
        spacing="2",
        align="start",
        width="100%",
    )
    return rx.box(
        rx.vstack(
            rx.text(
                "💡 Workflow + Buttons im Überblick",
                font_weight="bold",
                font_size="13px",
                color="#ffd166",
            ),
            rx.divider(margin="4px 0"),
            rx.text(
                "Sources sind Ordner oder Symlinks unter ",
                rx.code("data/media/audio/"),
                ". Symlinks zeigen NAS-Inhalt transparent. "
                "HTTP-Streams werden in der settings.json verwaltet.",
                font_size="12px",
                color="#bbb",
                line_height="1.5",
            ),
            bullet(
                "Indexieren",
                "Liest neue/geänderte Dateien und ihre Tags. Schnell wenn nichts "
                "geändert (mtime-Check). Macht audio_search erst nutzbar.",
            ),
            bullet(
                "Force",
                "Wie Indexieren, aber liest ALLE Tags neu. Nutze nach Mass-Tag-"
                "Edit oder wenn der Index 'komisch' aussieht.",
            ),
            bullet(
                "Mülleimer 🗑️",
                "Löscht Index-Einträge dieser Source (die Audio-Dateien bleiben). "
                "Anschließend Indexieren bauf den Index neu auf.",
            ),
            bullet(
                "Rotes X",
                "Source komplett entfernen (Symlink/leerer Ordner weg + Index-"
                "Einträge weg). Symlink-Targets (z.B. /mnt/auto/...) bleiben "
                "unverändert — nur der Pointer hier wird gelöscht.",
            ),
            bullet(
                "Neue Source hinzufügen…",
                "Öffnet den Datei-Browser. Sandbox-Root ist konfigurierbar "
                "(siehe unten). Du wählst einen Ordner — wir legen einen Symlink "
                "unter data/media/audio/ an.",
            ),
            bullet(
                "Sandbox-Root",
                "Welcher Pfad-Bereich vom Picker erreichbar ist. Default /mnt "
                "(NAS-Mounts). Höher (z.B. /) wäre ein Sicherheits-Risiko, "
                "weil dann beliebige System-Pfade exposed werden könnten.",
            ),
            bullet(
                "Im Picker: Neuer Ordner / Symlink",
                "Du kannst innerhalb der Sandbox eigene Ordner anlegen oder "
                "Symlinks setzen — z.B. einen Ordner 'meine_hörbücher' mit "
                "Symlinks zu Subfoldern, um eine kuratierte Auswahl zu schaffen. "
                "Symlink-Targets MÜSSEN in der Sandbox bleiben (Schutz vor "
                "Sandbox-Escape).",
            ),
            bullet(
                "Tipp",
                "Bei großen NAS-Mounts: erst 'Indexieren' klicken (3–5 Min für "
                "~17k Files), dann ist audio_search sub-Sekunde. Background-"
                "Sync läuft alle 24h für Sources die schon Einträge haben.",
            ),
            bullet(
                "Überlappende Sources",
                "Wenn du sowohl einen Parent (z.B. 'nas_audio') als auch "
                "einzelne Sub-Folders (z.B. 'Hörbücher', 'Lustiges') als Sources "
                "hast UND beide indexierst, werden die Files doppelt im Index "
                "geführt — jede Source hat einen eigenen Namespace + eigenen "
                "Resume-State. audio_search liefert dann doppelte Treffer. "
                "Empfehlung: entweder nur die Sub-Folders ODER nur den Parent "
                "indexieren, nicht beides. Speicher-Overhead bei 3600 Files "
                "ist allerdings nur ~1 MB — kein echtes Problem.",
            ),
            spacing="2",
            width="100%",
        ),
        padding="12px 16px",
        background_color="rgba(255, 209, 102, 0.05)",
        border="1px solid rgba(255, 209, 102, 0.3)",
        border_radius="6px",
        width="100%",
    )


def _sandbox_root_editor() -> rx.Component:
    """Inline editor for picker.root in plugin settings.json."""
    return rx.hstack(
        rx.icon("shield", size=14, color="#888"),
        rx.text(
            "Sandbox-Root",
            font_size="12px",
            color="#aaa",
            min_width="100px",
        ),
        rx.input(
            value=AIState.audio_picker_root_input,
            on_change=AIState.audio_set_picker_root_input,
            placeholder="/mnt",
            size="1",
            flex="1",
            font_family="monospace",
        ),
        rx.button(
            rx.icon("save", size=14),
            "Speichern",
            size="1",
            variant="soft",
            on_click=AIState.audio_save_picker_root,
            cursor="pointer",
        ),
        spacing="2",
        align="center",
        width="100%",
    )


def _source_row(src: rx.Var) -> rx.Component:  # type: ignore[type-arg]
    """Single row per source — type icon, label, target, indexed-count, action buttons."""
    is_busy = (AIState.audio_settings_busy_source == src["label"]) & (
        AIState.audio_settings_busy != ""
    )

    # Fixed width for the action button group so all rows align flush right
    actions_width = "260px"

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
                flex_shrink="0",
            ),
            # Target (Pfad/URL) + indexed-count — takes remaining space
            rx.vstack(
                rx.text(
                    src["target"],
                    font_size="11px",
                    color="#888",
                    font_family="monospace",
                    overflow="hidden",
                    text_overflow="ellipsis",
                    white_space="nowrap",
                    width="100%",
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
                width="100%",
            ),
            # Action buttons — fixed width for alignment, hstack with
            # justify="end" pushes them flush right within that width
            rx.box(
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
                            title="Index-Einträge löschen (Source bleibt)",
                        ),
                        rx.button(
                            rx.icon("x", size=14),
                            size="1",
                            variant="soft",
                            color_scheme="red",
                            on_click=AIState.audio_remove_source(src["label"]),
                            cursor="pointer",
                            title="Source komplett entfernen (Symlink/Ordner weg + Index-Einträge weg)",
                        ),
                        spacing="2",
                        justify="end",
                        width="100%",
                    ),
                ),
                width=actions_width,
                flex_shrink="0",
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
        width="100%",
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
                background_color="rgba(0, 0, 0, 0.92)",
                # stop_propagation prevents the click from bubbling through
                # to any modal beneath us (e.g. settings modal would otherwise
                # also close, dropping the user back to the home page).
                on_click=AIState.close_audio_settings.stop_propagation,
            ),
            rx.vstack(
                # Header
                rx.hstack(
                    rx.icon("music", size=20, color="#4287f5"),
                    rx.text("Audio Player — Sources & Index", font_weight="bold", font_size="15px"),
                    rx.spacer(),
                    # Help / lightbulb
                    rx.button(
                        rx.icon("lightbulb", size=16),
                        size="1",
                        variant="ghost",
                        color_scheme="yellow",
                        on_click=AIState.toggle_audio_settings_help,
                        cursor="pointer",
                        title="Hilfe: was macht was?",
                    ),
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
                # Help panel (toggled via lightbulb)
                rx.cond(
                    AIState.audio_settings_help_open,
                    _help_panel(),
                    rx.fragment(),
                ),
                # Sandbox-Root editor (for the source picker)
                _sandbox_root_editor(),
                # Hint
                rx.text(
                    "Sources sind Ordner oder Symlinks unter ",
                    rx.code("data/media/audio/"),
                    ". Symlinks zeigen NAS-Inhalt transparent. "
                    "HTTP-Streams werden in der settings.json verwaltet.",
                    font_size="12px",
                    color="#888",
                    line_height="1.5",
                ),
                # Source list — flex=1 keeps the modal at a fixed size
                # regardless of how many sources the user has configured.
                rx.box(
                    rx.vstack(
                        rx.foreach(AIState.audio_sources_view, _source_row),
                        spacing="0",
                        width="100%",
                    ),
                    flex="1",
                    overflow_y="auto",
                    min_height="0",
                    width="100%",
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
                        "Neue Source hinzufügen…",
                        size="2",
                        variant="soft",
                        on_click=AIState.open_audio_source_picker,
                        cursor="pointer",
                    ),
                    rx.spacer(),
                    rx.button(
                        "Schließen",
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
                # Modal content — stop click bubbling so clicks inside don't
                # close the modal (only the backdrop click does)
                on_click=rx.stop_propagation,
                spacing="3",
                # Fixed responsive size — stays stable as sources are added
                width="min(1000px, 95vw)",
                height="min(680px, 90vh)",
                padding="20px",
                background_color="#1f1f1f",
                border_radius="8px",
                border="1px solid #444",
                box_shadow="0 8px 32px rgba(0, 0, 0, 0.5)",
                position="relative",
                z_index="1201",
            ),
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            display="flex",
            align_items="center",
            justify_content="center",
            z_index="1200",
        ),
        rx.fragment(),
    )
