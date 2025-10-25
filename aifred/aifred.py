"""
AIfred Intelligence - Reflex Edition

Minimal Chat UI with Multi-Backend Support
"""

import reflex as rx
from .state import AIState


def chat_message(is_user: bool, content: str) -> rx.Component:
    """Single chat message component"""
    return rx.box(
        rx.hstack(
            rx.cond(
                is_user,
                rx.text("👤", font_size="24px"),
                rx.text("🤖", font_size="24px"),
            ),
            rx.cond(
                is_user,
                rx.box(
                    rx.text(content, color="white", padding="3"),
                    background_color="#2563eb",
                    border_radius="3",
                    max_width="600px",
                ),
                rx.box(
                    rx.text(content, color="black", padding="3"),
                    background_color="#e5e7eb",
                    border_radius="3",
                    max_width="600px",
                ),
            ),
            spacing="3",
            align="start",
        ),
        width="100%",
        padding_y="2",
    )


def chat_area() -> rx.Component:
    """Main chat area"""
    return rx.vstack(
        # Chat History
        rx.box(
            rx.foreach(
                AIState.chat_history,
                lambda msg: rx.vstack(
                    chat_message(True, msg[0]),   # User message
                    chat_message(False, msg[1]),  # AI message
                    spacing="2",
                    width="100%",
                ),
            ),
            # Current AI response (streaming)
            rx.cond(
                AIState.current_ai_response != "",
                chat_message(False, AIState.current_ai_response),
            ),
            width="100%",
            max_height="600px",
            overflow_y="auto",
            padding="4",
            border="1px solid #e5e7eb",
            border_radius="8px",
        ),
        # Input Area
        rx.hstack(
            rx.input(
                placeholder="Type your message...",
                value=AIState.current_user_input,
                on_change=AIState.set_user_input,
                width="100%",
                size="3",
                disabled=AIState.is_generating,
            ),
            rx.button(
                "Send",
                on_click=AIState.send_message,
                size="3",
                loading=AIState.is_generating,
                color_scheme="blue",
            ),
            rx.button(
                "Clear",
                on_click=AIState.clear_chat,
                size="3",
                variant="outline",
            ),
            spacing="2",
            width="100%",
        ),
        spacing="4",
        width="100%",
    )


def backend_selector() -> rx.Component:
    """Backend selection dropdown"""
    return rx.hstack(
        rx.text("Backend:", font_weight="bold"),
        rx.select(
            ["ollama", "vllm"],
            value=AIState.backend_type,
            on_change=AIState.switch_backend,
            size="2",
        ),
        rx.cond(
            AIState.backend_healthy,
            rx.badge(AIState.backend_info, color_scheme="green"),
            rx.badge(AIState.backend_info, color_scheme="red"),
        ),
        spacing="3",
        align="center",
    )


def debug_console() -> rx.Component:
    """Debug console with logs"""
    return rx.vstack(
        rx.hstack(
            rx.heading("Debug Console", size="4"),
            rx.switch(
                checked=AIState.auto_refresh_enabled,
                on_change=AIState.toggle_auto_refresh,
            ),
            rx.text("Auto-refresh", font_size="14px"),
            spacing="3",
            align="center",
        ),
        rx.box(
            rx.foreach(
                AIState.debug_messages,
                lambda msg: rx.text(
                    msg,
                    font_family="monospace",
                    font_size="12px",
                    color="#333",
                ),
            ),
            width="100%",
            max_height="200px",
            overflow_y="auto",
            padding="3",
            background_color="#f5f5f5",
            border_radius="8px",
            border="1px solid #ddd",
        ),
        spacing="2",
        width="100%",
    )


def settings_panel() -> rx.Component:
    """Settings panel"""
    return rx.vstack(
        rx.heading("Settings", size="4"),
        backend_selector(),
        rx.hstack(
            rx.text("Model:", font_weight="bold"),
            rx.select(
                AIState.available_models,
                value=AIState.selected_model,
                on_change=AIState.set_selected_model,
                size="2",
            ),
            spacing="3",
            align="center",
        ),
        rx.hstack(
            rx.text(f"Temperature: {AIState.temperature}", font_weight="bold"),
            rx.slider(
                default_value=[AIState.temperature],
                min=0.0,
                max=1.0,
                step=0.1,
                on_change=lambda val: AIState.set_temperature(val[0]),
            ),
            spacing="3",
            align="center",
        ),
        spacing="4",
        padding="4",
        border="1px solid #e5e7eb",
        border_radius="8px",
    )


@rx.page(route="/", on_load=AIState.on_load)
def index() -> rx.Component:
    """Main page"""
    return rx.container(
        rx.vstack(
            # Header
            rx.heading("AIfred Intelligence - Reflex Edition", size="6"),
            rx.text(
                "Multi-Backend LLM Chat Assistant",
                color="#666",
                font_size="14px",
            ),
            # Main Content
            rx.grid(
                # Left: Chat
                rx.box(
                    chat_area(),
                    grid_column="span 8",
                ),
                # Right: Settings
                rx.box(
                    settings_panel(),
                    grid_column="span 4",
                ),
                columns="12",
                spacing="4",
                width="100%",
            ),
            # Debug Console (collapsible)
            debug_console(),
            spacing="6",
            padding="6",
            width="100%",
            max_width="1400px",
        ),
        center_content=True,
    )


# Create app
app = rx.App()
