"""Login/Registrierungs-Dialog (Fullscreen-Overlay)."""

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import agent_emoji, overlay_scaffold


def login_dialog() -> rx.Component:
    """
    Fullscreen Overlay für Login/Registrierung.
    Zeigt Login-Form oder Registrierungs-Form basierend auf login_mode.
    Enter-Taste im Passwort-Feld löst Login/Register aus.
    """
    return overlay_scaffold(
        # Modal Content - zentriert
        rx.vstack(
            # Logo/Header
            rx.vstack(
                agent_emoji("\U0001f3a9", size="48px"),
                rx.text("AIfred Intelligence", color="white", font_weight="bold", font_size="24px"),
                spacing="1",
                align="center",
            ),

            # Form Container - wrapped in <form> for browser password manager
            rx.el.form(
                rx.box(
                    rx.vstack(
                        # Mode Toggle
                        rx.hstack(
                            rx.button(
                                "Anmelden",
                                on_click=lambda: AIState.open_login_dialog("login"),  # type: ignore[arg-type]
                                variant=rx.cond(AIState.login_mode == "login", "solid", "ghost"),
                                color_scheme="orange",
                                size="2",
                                type="button",  # Prevent form submit
                            ),
                            rx.button(
                                "Registrieren",
                                on_click=lambda: AIState.open_login_dialog("register"),  # type: ignore[arg-type]
                                variant=rx.cond(AIState.login_mode == "register", "solid", "ghost"),
                                color_scheme="orange",
                                size="2",
                                type="button",  # Prevent form submit
                            ),
                            spacing="2",
                            justify="center",
                        ),

                        # Username Input (with autocomplete for browser password manager)
                        rx.input(
                            placeholder="Username",
                            value=AIState.login_username,
                            on_change=AIState.set_login_username,
                            name="username",
                            custom_attrs={"autocomplete": "username"},
                            width="100%",
                            size="3",
                        ),

                        # Password Input (Enter triggers login/register, autocomplete for password manager)
                        rx.input(
                            placeholder="Passwort",
                            type="password",
                            value=AIState.login_password,
                            on_change=AIState.set_login_password,
                            on_key_down=AIState.handle_login_key_down,
                            name="password",
                            custom_attrs={"autocomplete": "current-password"},
                            width="100%",
                            size="3",
                        ),

                        # Error Message
                        rx.cond(
                            AIState.login_error != "",
                            rx.text(AIState.login_error, color="red", font_size="14px"),
                        ),

                        # Submit Button
                        rx.cond(
                            AIState.login_mode == "login",
                            rx.button(
                                "Anmelden",
                                on_click=AIState.do_login,
                                color_scheme="orange",
                                width="100%",
                                size="3",
                                type="submit",
                            ),
                            rx.button(
                                "Account erstellen",
                                on_click=AIState.do_register,
                                color_scheme="green",
                                width="100%",
                                size="3",
                                type="submit",
                            ),
                        ),

                        spacing="4",
                        width="100%",
                    ),
                    background_color="#1a1a1a",
                    border_radius="12px",
                    padding="24px",
                    width="320px",
                    border="1px solid #333",
                ),
                on_submit=AIState.handle_login_submit,
                method="post",
            ),

            spacing="6",
            align="center",
            position="absolute",
            top="50%",
            left="50%",
            transform="translate(-50%, -50%)",
        ),
        open_var=AIState.login_dialog_open,
        # Backdrop nicht klickbar — Login ist erforderlich
        backdrop_color="rgba(0, 0, 0, 0.9)",
        backdrop_on_click=None,
        z_index="9999",
        flex_center=False,
    )
