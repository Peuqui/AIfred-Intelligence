"""Personarium — Identitäten-Verwaltungs-Modal.

Tabelle aller im ``vision_store`` registrierten Personen mit Avatar
(letzter Crop), Anzahl Embeddings und letzter Sichtung. Inline-Edit
für den Namen, Löschen-Button pro Zeile. Modal global gemountet in
aifred.py, aufrufbar via ``AIState.open_personarium``.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from .helpers import t


def _face_row(face: rx.Var) -> rx.Component:
    """Eine Zeile in der Identitäten-Tabelle."""
    fid = face["id"]
    is_editing = AIState.personarium_edit_face_id == fid
    return rx.vstack(
        rx.hstack(
        # Avatar
        rx.cond(
            face["crop_url"] != "",
            rx.image(
                src=face["crop_url"],
                style={
                    "width": "48px",
                    "height": "48px",
                    "border_radius": "4px",
                    "object_fit": "cover",
                    "flex_shrink": "0",
                    "border": "1px solid var(--gray-7)",
                },
            ),
            rx.box(
                rx.icon("user", size=24, color="gray"),
                style={
                    "width": "48px",
                    "height": "48px",
                    "border_radius": "4px",
                    "background_color": "var(--gray-3)",
                    "border": "1px solid var(--gray-7)",
                    "display": "flex",
                    "align_items": "center",
                    "justify_content": "center",
                    "flex_shrink": "0",
                },
            ),
        ),
        # Name + Metadaten — Inline-Edit oder Read-Only.
        rx.cond(
            is_editing,
            rx.hstack(
                rx.input(
                    value=AIState.personarium_edit_name,
                    on_change=AIState.personarium_set_edit_name,
                    size="2",
                    style={"flex": "1 1 auto", "min_width": "0"},
                ),
                rx.icon_button(
                    rx.icon("check", size=14),
                    on_click=AIState.personarium_save_rename,
                    size="1",
                    variant="soft",
                    color_scheme="green",
                    title=t("personarium_save"),
                ),
                rx.icon_button(
                    rx.icon("x", size=14),
                    on_click=AIState.personarium_cancel_rename,
                    size="1",
                    variant="soft",
                    color_scheme="gray",
                    title=t("personarium_cancel"),
                ),
                spacing="1",
                align="center",
                style={"flex": "1 1 auto", "min_width": "0"},
            ),
            rx.vstack(
                rx.text(face["name"], font_weight="bold", size="2"),
                rx.hstack(
                    rx.text(face["embedding_count"].to(str), size="1", color="gray"),
                    rx.text(t("personarium_embeddings_suffix"), size="1", color="gray"),
                    rx.text("·", size="1", color="gray"),
                    rx.text(face["last_seen"], size="1", color="gray"),
                    spacing="1",
                    align="center",
                ),
                spacing="0",
                align="start",
                style={"flex": "1 1 auto", "min_width": "0"},
            ),
        ),
        # Aktionen
        rx.cond(
            is_editing,
            rx.fragment(),
            rx.hstack(
                rx.icon_button(
                    rx.icon("user-cog", size=14),
                    on_click=AIState.open_multipose(fid, face["name"]),
                    size="1",
                    variant="soft",
                    color_scheme="orange",
                    title=t("personarium_multipose_add"),
                ),
                rx.icon_button(
                    rx.icon("layers", size=14),
                    on_click=AIState.personarium_open_embeddings(fid),
                    size="1",
                    variant="soft",
                    color_scheme="gray",
                    title=t("personarium_manage_embeddings"),
                ),
                rx.icon_button(
                    rx.icon("pencil", size=14),
                    on_click=AIState.personarium_start_rename(fid, face["name"]),
                    size="1",
                    variant="soft",
                    color_scheme="gray",
                    title=t("personarium_rename"),
                ),
                rx.icon_button(
                    rx.icon("trash-2", size=14),
                    on_click=AIState.personarium_delete_face(fid),
                    size="1",
                    variant="soft",
                    color_scheme="red",
                    title=t("personarium_delete"),
                ),
                spacing="1",
                align="center",
                style={"flex_shrink": "0"},
            ),
        ),
        spacing="3",
        align="center",
        width="100%",
        style={
            "padding": "0.5em 0",
            "border_bottom": "1px solid var(--gray-6)",
        },
        ),
        # Embedding-Manager: inline-Grid unter der Zeile, wenn diese Identität
        # aufgeklappt ist (über den „layers"-Button).
        rx.cond(
            AIState.personarium_manage_face_id == fid,
            _embedding_grid(),
        ),
        spacing="1",
        align="stretch",
        width="100%",
    )


def _embedding_grid() -> rx.Component:
    """Grid der Embeddings der gerade verwalteten Identität: Crop (oder
    Platzhalter) + Quality + Einzel-Löschen."""
    return rx.box(
        rx.cond(
            AIState.personarium_embeddings.length() > 0,
            rx.flex(
                rx.foreach(AIState.personarium_embeddings, _embedding_cell),
                wrap="wrap", gap="2",
            ),
            rx.text(t("personarium_no_embeddings"), size="1", color="gray"),
        ),
        style={
            "padding": "0.5em 0.5em 0.7em",
            "border_bottom": "1px solid var(--gray-6)",
            "width": "100%",
        },
    )


def _embedding_cell(emb: rx.Var) -> rx.Component:
    """Eine Embedding-Kachel: Crop-Thumbnail (oder Platzhalter), Quality-Badge
    und ein Löschen-Overlay."""
    return rx.box(
        rx.cond(
            emb["crop_url"] != "",
            rx.image(
                src=emb["crop_url"],
                style={
                    "width": "64px", "height": "64px", "object_fit": "cover",
                    "border_radius": "4px", "border": "1px solid var(--gray-7)",
                },
            ),
            rx.box(
                rx.icon("image-off", size=20, color="gray"),
                style={
                    "width": "64px", "height": "64px", "border_radius": "4px",
                    "background_color": "var(--gray-3)", "border": "1px solid var(--gray-7)",
                    "display": "flex", "align_items": "center", "justify_content": "center",
                },
            ),
        ),
        rx.text(
            emb["quality"].to(str),
            size="1", color="gray", style={"text_align": "center"},
        ),
        rx.icon_button(
            rx.icon("trash-2", size=12),
            on_click=AIState.personarium_delete_embedding(emb["id"]),
            size="1", variant="soft", color_scheme="red",
            style={"position": "absolute", "top": "2px", "right": "2px"},
            title=t("personarium_delete_embedding"),
        ),
        style={"position": "relative", "width": "64px"},
    )


def personarium_modal() -> rx.Component:
    """Personarium-Modal: Liste aller Identitäten mit
    Verwaltungs-Aktionen. Global gemountet in aifred.py, sichtbar
    wenn ``AIState.personarium_open == True``."""
    return rx.cond(
        AIState.personarium_open,
        rx.box(
            # Backdrop — Klick schließt
            rx.box(
                position="absolute",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.5)",
                on_click=AIState.close_personarium,
            ),
            # Modal-Box
            rx.box(
                rx.vstack(
                    # Header
                    rx.hstack(
                        rx.icon("users", size=20),
                        rx.text(
                            t("personarium_title"),
                            font_weight="bold",
                            size="4",
                        ),
                        rx.spacer(),
                        rx.icon_button(
                            rx.icon("x", size=16),
                            on_click=AIState.close_personarium,
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
                        t("personarium_subtitle"),
                        color="gray",
                        size="2",
                        margin_bottom="0.5em",
                    ),
                    rx.divider(),
                    # Liste oder Leer-Zustand
                    rx.cond(
                        AIState.personarium_faces.length() > 0,
                        rx.box(
                            rx.foreach(AIState.personarium_faces, _face_row),
                            style={
                                "width": "100%",
                                "max_height": "60vh",
                                "overflow_y": "auto",
                            },
                        ),
                        rx.box(
                            rx.icon("users", size=32, color="gray"),
                            rx.text(
                                t("personarium_empty"),
                                color="gray",
                                size="2",
                                margin_top="0.5em",
                                text_align="center",
                            ),
                            style={
                                "display": "flex",
                                "flex_direction": "column",
                                "align_items": "center",
                                "justify_content": "center",
                                "padding": "2em",
                            },
                        ),
                    ),
                    rx.cond(
                        AIState.personarium_status != "",
                        rx.callout.root(
                            rx.callout.icon(rx.icon("info")),
                            rx.callout.text(AIState.personarium_status),
                            color_scheme="amber",
                            size="1",
                        ),
                    ),
                    rx.divider(),
                    rx.button(
                        t("vision_settings_close"),
                        on_click=AIState.close_personarium,
                        size="2",
                        width="100%",
                    ),
                    align="stretch",
                    spacing="2",
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
                width="min(640px, 92vw)",
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
