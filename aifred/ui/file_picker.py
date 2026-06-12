"""Generic file/folder picker — Reflex modal component.

Used by callers that need a path selection. Uses the FilePickerMixin
state. Configured per-call via AIState.picker_open_for(...). On pick,
a callback event (registered by the caller) is dispatched with the
chosen relative path.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState


def _icon_for(entry_is_dir: rx.Var, entry_is_symlink: rx.Var) -> rx.Component:  # type: ignore[type-arg]
    """Folder/file icon, with a small overlay if symlink."""
    return rx.hstack(
        rx.cond(
            entry_is_dir,
            rx.icon("folder", size=18, color="#4287f5"),
            rx.icon("file", size=18, color="#888"),
        ),
        rx.cond(
            entry_is_symlink,
            rx.icon("link", size=11, color="#aaa", margin_left="-6px", margin_top="6px"),
            rx.fragment(),
        ),
        spacing="0",
        align="center",
    )


def _entry_row(entry: rx.Var) -> rx.Component:  # type: ignore[type-arg]
    """One row in the file list."""
    return rx.hstack(
        _icon_for(entry["is_dir"], entry["is_symlink"]),
        rx.text(
            entry["name"],
            font_size="13px",
            flex="1",
            overflow="hidden",
            text_overflow="ellipsis",
            white_space="nowrap",
        ),
        rx.cond(
            entry["error"] != "",
            rx.tooltip(
                rx.icon("triangle-alert", size=14, color="#f87171"),
                content=entry["error"],
            ),
            rx.fragment(),
        ),
        # Click handling is done in the state event itself (rx.cond can't
        # branch over EventSpecs in on_click — it's for Components).
        on_click=AIState.picker_entry_clicked(entry["rel_path"], entry["is_dir"]),
        spacing="2",
        align="center",
        padding="6px 10px",
        cursor="pointer",
        border_radius="4px",
        _hover={"background_color": "rgba(66, 135, 245, 0.1)"},
        width="100%",
    )


def _breadcrumbs() -> rx.Component:
    """Clickable path segments for navigation. Labels are pre-prefixed with
    '/' (in the state's computed var). Small gap between buttons so the
    leading slashes don't visually merge into '//'."""
    return rx.hstack(
        rx.foreach(
            AIState.picker_breadcrumbs,
            lambda crumb: rx.button(
                crumb["label"],
                size="1",
                variant="ghost",
                on_click=AIState.picker_navigate(crumb["rel_path"]),
                cursor="pointer",
            ),
        ),
        spacing="1",
        align="center",
        flex_wrap="wrap",
    )


def _toolbar() -> rx.Component:
    """Toolbar with capability-dependent buttons + sort selector."""
    return rx.hstack(
        rx.button(
            rx.icon("arrow-up", size=14),
            "Hoch",
            size="1",
            variant="soft",
            on_click=AIState.picker_navigate_up,
            disabled=AIState.picker_current == "",
            cursor="pointer",
        ),
        rx.cond(
            AIState.picker_caps.contains("can_create_folder"),
            rx.button(
                rx.icon("folder-plus", size=14),
                "Neuer Ordner",
                size="1",
                variant="soft",
                on_click=AIState.picker_create_folder_start,
                cursor="pointer",
                disabled=~AIState.picker_writable,  # type: ignore[arg-type]
                title=rx.cond(
                    AIState.picker_writable,
                    "Neuen Ordner anlegen",
                    "Kein Schreibrecht in diesem Ordner",
                ),
            ),
            rx.fragment(),
        ),
        rx.cond(
            AIState.picker_caps.contains("can_create_symlink"),
            rx.button(
                rx.icon("link", size=14),
                "Symlink",
                size="1",
                variant="soft",
                on_click=AIState.picker_create_symlink_start,
                cursor="pointer",
                disabled=~AIState.picker_writable,  # type: ignore[arg-type]
                title=rx.cond(
                    AIState.picker_writable,
                    "Symlink anlegen",
                    "Kein Schreibrecht in diesem Ordner",
                ),
            ),
            rx.fragment(),
        ),
        rx.spacer(),
        # Read-only indicator
        rx.cond(
            ~AIState.picker_writable,
            rx.hstack(
                rx.icon("lock", size=12, color="#888"),
                rx.text("read-only", font_size="11px", color="#888"),
                spacing="1",
                align="center",
            ),
            rx.fragment(),
        ),
        rx.select(
            ["name", "mtime", "size"],
            value=AIState.picker_sort_by,
            on_change=AIState.picker_set_sort,
            size="1",
        ),
        spacing="2",
        align="center",
        width="100%",
    )


def _path_input_bar() -> rx.Component:
    return rx.hstack(
        rx.input(
            value=AIState.picker_path_input,
            placeholder="Pfad eingeben (Enter zum Springen)…",
            on_change=AIState.picker_set_path_input,
            on_blur=AIState.picker_jump_to_path,
            size="1",
            flex="1",
        ),
        rx.button(
            "Gehe zu",
            size="1",
            variant="soft",
            on_click=AIState.picker_jump_to_path,
            cursor="pointer",
        ),
        spacing="2",
        width="100%",
    )


def _filter_bar() -> rx.Component:
    return rx.input(
        value=AIState.picker_filter_text,
        placeholder="Filter (live)…",
        on_change=AIState.picker_set_filter_text,
        size="1",
        width="100%",
    )


def _create_folder_form() -> rx.Component:
    return rx.cond(
        AIState.picker_creating_folder,
        rx.hstack(
            rx.input(
                value=AIState.picker_new_folder_name,
                placeholder="Ordnername",
                on_change=AIState.picker_set_new_folder_name,
                size="1",
                flex="1",
            ),
            rx.button("Anlegen", size="1", on_click=AIState.picker_create_folder_submit, cursor="pointer"),
            rx.button("Abbrechen", size="1", variant="soft", on_click=AIState.picker_create_folder_cancel, cursor="pointer"),
            spacing="2",
            width="100%",
        ),
        rx.fragment(),
    )


def _create_symlink_form() -> rx.Component:
    return rx.cond(
        AIState.picker_creating_symlink,
        rx.vstack(
            rx.text("Neuen Symlink anlegen", font_weight="bold", font_size="13px"),
            rx.hstack(
                rx.text("Name:", font_size="13px", width="80px"),
                rx.input(
                    value=AIState.picker_symlink_name,
                    placeholder="z.B. nas_klassik",
                    on_change=AIState.picker_set_symlink_name,
                    size="1",
                    flex="1",
                ),
                width="100%",
                align="center",
            ),
            rx.hstack(
                rx.text("Ziel:", font_size="13px", width="80px"),
                rx.input(
                    value=AIState.picker_symlink_target,
                    placeholder="absoluter Pfad, z.B. /mnt/auto/vuplus/MediaServ/Musik/Klassik",
                    on_change=AIState.picker_set_symlink_target,
                    size="1",
                    flex="1",
                ),
                width="100%",
                align="center",
            ),
            rx.hstack(
                rx.button("Anlegen", size="1", on_click=AIState.picker_create_symlink_submit, cursor="pointer"),
                rx.button("Abbrechen", size="1", variant="soft", on_click=AIState.picker_create_symlink_cancel, cursor="pointer"),
                spacing="2",
            ),
            spacing="2",
            padding="10px",
            background_color="rgba(66, 135, 245, 0.08)",
            border_radius="6px",
            width="100%",
        ),
        rx.fragment(),
    )


def file_picker_modal() -> rx.Component:
    """The actual modal — rx.cond for mobile compatibility (no rx.dialog)."""
    return rx.cond(
        AIState.picker_open,
        rx.box(
            # Backdrop
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.92)",
                # Backdrop fängt den Klick aber schließt das Modal nicht —
                # User schließt nur explizit via X / Abbrechen / Diesen Ordner wählen.
                on_click=rx.stop_propagation,
            ),
            # Modal content
            rx.vstack(
                # Header
                rx.hstack(
                    rx.icon("folder", size=20, color="#4287f5"),
                    rx.text(AIState.picker_title, font_weight="bold", font_size="15px"),
                    rx.spacer(),
                    rx.button(
                        rx.icon("x", size=16),
                        size="1",
                        variant="ghost",
                        on_click=AIState.picker_close,
                        cursor="pointer",
                        custom_attrs={"data-modal-close": "true"},
                    ),
                    spacing="2",
                    align="center",
                    width="100%",
                    padding_bottom="8px",
                    border_bottom="1px solid #333",
                ),
                # Sandbox-Hint (zeigt root)
                rx.text(
                    "Sandbox: ", AIState.picker_root,
                    font_size="11px",
                    color="#888",
                    font_family="monospace",
                ),
                # Breadcrumbs
                _breadcrumbs(),
                # Path input
                _path_input_bar(),
                # Toolbar (caps + sort)
                _toolbar(),
                # Inline forms (folder/symlink create)
                _create_folder_form(),
                _create_symlink_form(),
                # Live-Filter
                _filter_bar(),
                # Error
                rx.cond(
                    AIState.picker_error != "",
                    rx.callout(AIState.picker_error, color_scheme="red", size="1"),
                    rx.fragment(),
                ),
                # File list — flex=1 fills remaining modal height,
                # internal scroll keeps modal size stable.
                rx.box(
                    rx.cond(
                        AIState.picker_loading,
                        rx.center(rx.spinner(size="2"), padding="20px"),
                        rx.cond(
                            AIState.picker_filtered_entries.length() == 0,
                            rx.center(
                                rx.text("(Ordner leer oder kein Treffer)", color="#888", font_size="13px"),
                                padding="20px",
                            ),
                            rx.vstack(
                                rx.foreach(AIState.picker_filtered_entries, _entry_row),
                                spacing="0",
                                width="100%",
                            ),
                        ),
                    ),
                    flex="1",
                    overflow_y="auto",
                    min_height="0",  # required for flex children to shrink
                    width="100%",
                ),
                # Footer
                rx.hstack(
                    rx.spacer(),
                    rx.button(
                        "Abbrechen",
                        size="2",
                        variant="soft",
                        on_click=AIState.picker_close,
                        cursor="pointer",
                    ),
                    rx.cond(
                        AIState.picker_mode == "pick_folder",
                        rx.button(
                            "Diesen Ordner wählen",
                            size="2",
                            on_click=AIState.picker_pick_current,
                            cursor="pointer",
                            color_scheme="blue",
                        ),
                        rx.fragment(),
                    ),
                    spacing="2",
                    width="100%",
                    padding_top="10px",
                    border_top="1px solid #333",
                ),
                # Don't let inner clicks bubble to the backdrop
                on_click=rx.stop_propagation,
                spacing="2",
                # Fixed responsive size — modal stays stable regardless
                # of how many entries the listing returns
                width="min(900px, 95vw)",
                height="min(720px, 90vh)",
                padding="20px",
                background_color="#1f1f1f",
                border_radius="8px",
                border="1px solid #444",
                box_shadow="0 8px 32px rgba(0, 0, 0, 0.5)",
                position="relative",
                z_index="1301",
            ),
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            display="flex",
            align_items="center",
            justify_content="center",
            z_index="1300",
        ),
        rx.fragment(),
    )
