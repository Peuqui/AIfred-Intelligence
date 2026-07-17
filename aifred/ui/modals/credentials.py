"""Channel-Credentials Vollbild-Page (dynamische Felder + OAuth-Connect)."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t


def _cred_field_input(field: rx.Var) -> rx.Component:
    """Render a single credential field from a field descriptor dict.

    The field var is a dict with keys: env_key, label_key, placeholder, is_password, group, width_ratio.
    """
    env_key = field["env_key"]
    value = AIState.channel_credential_values[env_key].to(str)

    # Password field with eye toggle
    password_input = rx.vstack(
        rx.text(field["label_key"].to(str), font_size="11px", color="#999"),
        rx.box(
            rx.cond(
                AIState.channel_cred_show_password,
                rx.input(
                    value=value,
                    on_change=lambda val: AIState.update_channel_credential([env_key, val]),  # type: ignore[arg-type]
                    placeholder="••••••••",
                    size="2",
                    width="100%",
                ),
                rx.input(
                    value=value,
                    on_change=lambda val: AIState.update_channel_credential([env_key, val]),  # type: ignore[arg-type]
                    type="password",
                    placeholder="••••••••",
                    size="2",
                    width="100%",
                ),
            ),
            rx.icon_button(
                rx.cond(
                    AIState.channel_cred_show_password,
                    rx.icon("eye-off", size=14),
                    rx.icon("eye", size=14),
                ),
                on_click=AIState.toggle_channel_cred_show_password,
                size="1",
                variant="ghost",
                color_scheme="gray",
                position="absolute",
                right="6px",
                top="50%",
                transform="translateY(-50%)",
                cursor="pointer",
            ),
            position="relative",
            width="100%",
        ),
        spacing="1",
        width="100%",
    )

    # Label with optional tooltip (convention: label_key + "_tooltip")
    tooltip_key = field["label_key"].to(str) + "_tooltip"
    label_with_tooltip = rx.tooltip(
        rx.text(
            field["label_key"].to(str),
            font_size="11px", color="#999", cursor="help",
        ),
        content=t(tooltip_key),
    )
    # Dropdown input (when options are provided)
    # UI shows labels from channel_credential_values (pre-mapped in State)
    # State handler maps label→value on save
    dropdown_input = rx.vstack(
        label_with_tooltip,
        rx.select(
            field["option_labels"].to(str).split(","),
            value=value,
            on_change=lambda val: AIState.update_channel_credential([env_key, val]),  # type: ignore[arg-type]
            size="2",
            width="100%",
        ),
        spacing="1",
        width="100%",
    )

    # Normal text input
    text_input = rx.vstack(
        label_with_tooltip,
        rx.input(
            value=value,
            on_change=lambda val: AIState.update_channel_credential([env_key, val]),  # type: ignore[arg-type]
            placeholder=field["placeholder"].to(str),
            size="2",
            width="100%",
        ),
        spacing="1",
        width="100%",
    )

    return rx.cond(
        field["is_password"].to(str) == "1",
        password_input,
        rx.cond(
            field["options"].to(str) != "",
            dropdown_input,
            text_input,
        ),
    )


def channel_credentials_page() -> rx.Component:
    """Channel-Credentials Vollbild-Page (vormals channel_credentials_modal).

    Lebt seit dem Multi-Route-Split auf der Route ``/credentials`` —
    automatisches Code-Splitting durch Reflex+React-Router-7. Felder
    kommen dynamisch aus ``AIState.channel_credential_fields`` (vorher
    in open_channel_credentials gefuellt).
    """
    return rx.box(
            # Backdrop
            rx.box(
                on_click=rx.stop_propagation,
                position="fixed",
                top="0",
                left="0",
                width="100%",
                height="100%",
                background_color="rgba(0, 0, 0, 0.92)",
            ),
            # Modal content
            rx.vstack(
                # Title: channel name + "Credentials"
                rx.text(
                    AIState.channel_credentials_display_name,
                    font_weight="bold",
                    font_size="16px",
                    color="white",
                ),

                # Dynamic fields
                rx.foreach(
                    AIState.channel_credential_fields,
                    _cred_field_input,
                ),

                # OAuth Connect-Button (nur wenn Plugin oauth_provider gesetzt hat)
                rx.cond(
                    AIState.oauth_connect_provider != "",
                    rx.vstack(
                        rx.divider(),
                        rx.match(
                            AIState.oauth_connect_status,
                            (
                                "connecting",
                                rx.button(
                                    rx.hstack(
                                        rx.spinner(size="1"),
                                        rx.text(t("oauth_connect_connecting")),
                                        spacing="2", align="center",
                                    ),
                                    variant="solid",
                                    color_scheme="amber",
                                    size="2",
                                    width="100%",
                                    disabled=True,
                                ),
                            ),
                            (
                                "connected",
                                rx.hstack(
                                    rx.button(
                                        rx.hstack(
                                            rx.icon("check-circle", size=14),
                                            rx.text(t("oauth_connect_connected")),
                                            spacing="2", align="center",
                                        ),
                                        variant="solid",
                                        color_scheme="green",
                                        size="2",
                                        flex="1",
                                        disabled=True,
                                    ),
                                    rx.button(
                                        t("oauth_connect_disconnect"),
                                        on_click=AIState.disconnect_oauth,
                                        variant="soft",
                                        color_scheme="red",
                                        size="2",
                                    ),
                                    spacing="2",
                                    width="100%",
                                ),
                            ),
                            (
                                "error",
                                rx.cond(
                                    AIState.oauth_auth_url != "",
                                    rx.link(
                                        rx.button(
                                            rx.hstack(
                                                rx.icon("circle-alert", size=14),
                                                rx.text(t("oauth_connect_error")),
                                                spacing="2", align="center",
                                            ),
                                            variant="solid",
                                            color_scheme="red",
                                            size="2",
                                            width="100%",
                                        ),
                                        href=AIState.oauth_auth_url,
                                        target="_blank",
                                        is_external=True,
                                        on_click=AIState.start_oauth_connection,
                                        width="100%",
                                        underline="none",
                                    ),
                                    rx.button(
                                        rx.hstack(
                                            rx.icon("circle-alert", size=14),
                                            rx.text(t("oauth_connect_no_credentials")),
                                            spacing="2", align="center",
                                        ),
                                        variant="soft",
                                        color_scheme="gray",
                                        size="2",
                                        width="100%",
                                        disabled=True,
                                    ),
                                ),
                            ),
                            # Default = "idle"
                            rx.cond(
                                AIState.oauth_auth_url != "",
                                rx.link(
                                    rx.button(
                                        rx.hstack(
                                            rx.icon("link", size=14),
                                            rx.text(t("oauth_connect_idle")),
                                            spacing="2", align="center",
                                        ),
                                        variant="solid",
                                        color_scheme="blue",
                                        size="2",
                                        width="100%",
                                    ),
                                    href=AIState.oauth_auth_url,
                                    target="_blank",
                                    is_external=True,
                                    on_click=AIState.start_oauth_connection,
                                    width="100%",
                                    underline="none",
                                ),
                                rx.button(
                                    rx.hstack(
                                        rx.icon("link", size=14),
                                        rx.text(t("oauth_connect_no_credentials")),
                                        spacing="2", align="center",
                                    ),
                                    variant="soft",
                                    color_scheme="gray",
                                    size="2",
                                    width="100%",
                                    disabled=True,
                                ),
                            ),
                        ),
                        rx.text(
                            t("oauth_connect_hint"),
                            font_size="10px",
                            color="#888",
                        ),
                        spacing="2",
                        width="100%",
                    ),
                ),

                # Buttons
                rx.hstack(
                    rx.button(
                        t("cred_cancel"),
                        on_click=AIState.close_channel_credentials,
                        variant="soft",
                        color_scheme="gray",
                        size="1",
                        flex="1",
                        custom_attrs={"data-modal-close": "true"},
                    ),
                    rx.button(
                        t("cred_save"),
                        on_click=AIState.save_channel_credentials,
                        variant="solid",
                        color_scheme="blue",
                        size="1",
                        flex="1",
                    ),
                    spacing="2",
                    width="100%",
                ),

                spacing="3",
                padding="24px",
                background="#1a1a2e",
                border_radius="12px",
                border="1px solid var(--gray-a6)",
                width="500px",
                max_width="90vw",
                position="relative",
                z_index="1101",
            ),
            position="fixed",
            top="0",
            left="0",
            width="100vw",
            height="100vh",
            z_index="1100",
            display="flex",
            justify_content="center",
            align_items="center",
    )
