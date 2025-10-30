"""
AIfred Intelligence - Reflex Edition

Full Gradio-Style UI with 2-Column Layout
"""

import reflex as rx
from .state import AIState


# ============================================================
# LEFT COLUMN: Input Controls
# ============================================================

def audio_input_section() -> rx.Component:
    """Audio input section (placeholder for STT)"""
    return rx.vstack(
        rx.heading("🎙️ Spracheingabe", size="4"),
        rx.box(
            rx.text(
                "Audio-Eingabe (Microphone Recording)",
                color="#666",
                font_size="14px",
            ),
            rx.text(
                "⚠️ STT/TTS noch nicht portiert - Coming Soon!",
                color="#f59e0b",
                font_weight="bold",
                font_size="12px",
            ),
            padding="4",
            background_color="#f9fafb",
            border_radius="8px",
            border="2px dashed #d1d5db",
            width="100%",
        ),
        rx.text(
            "💡 Tipp: Nach dem Stoppen läuft automatisch die Transkription",
            font_size="12px",
            color="#6b7280",
        ),
        rx.divider(margin_y="4"),
        spacing="3",
        width="100%",
    )


def text_input_section() -> rx.Component:
    """Text input section with research mode"""
    return rx.vstack(
        # Text Input
        rx.heading("⌨️ Texteingabe", size="4"),
        rx.text_area(
            placeholder="Oder schreibe hier deine Frage...",
            value=AIState.current_user_input,
            on_change=AIState.set_user_input,
            width="100%",
            rows="3",
            disabled=AIState.is_generating,
        ),

        # Research Mode Radio Buttons
        rx.vstack(
            rx.text("🎯 Recherche-Modus", font_weight="bold", font_size="14px"),
            rx.radio(
                [
                    "🧠 Eigenes Wissen (schnell)",
                    "⚡ Web-Suche Schnell (3 beste)",
                    "🔍 Web-Suche Ausführlich (7 beste)",
                    "🤖 Automatik (KI entscheidet)"
                ],
                value=AIState.research_mode_display,
                on_change=AIState.set_research_mode_display,
                spacing="2",
            ),
            rx.text(
                "Wähle, wie der Assistant Fragen beantwortet",
                font_size="12px",
                color="#6b7280",
            ),
            width="100%",
        ),

        # Action Buttons
        rx.hstack(
            rx.button(
                "Text senden",
                on_click=AIState.send_message,
                size="3",
                loading=AIState.is_generating,
                color_scheme="blue",
                width="100%",
            ),
            rx.button(
                "🗑️ Chat löschen",
                on_click=AIState.clear_chat,
                size="3",
                variant="outline",
                color_scheme="red",
            ),
            spacing="2",
            width="100%",
        ),

        spacing="3",
        width="100%",
    )


def llm_parameters_accordion() -> rx.Component:
    """LLM Parameters in collapsible accordion"""
    return rx.accordion.root(
        rx.accordion.item(
            header=rx.text("⚙️ LLM-Parameter (Erweitert)", font_weight="bold"),
            content=rx.vstack(
                # Temperature
                rx.vstack(
                    rx.text("🌡️ Temperature", font_weight="bold", font_size="14px"),
                    rx.slider(
                        default_value=AIState.temperature,
                        min=0.0,
                        max=2.0,
                        step=0.1,
                        on_change=AIState.set_temperature,
                        width="100%",
                    ),
                    rx.text(
                        f"Aktuell: {AIState.temperature}",
                        font_size="12px",
                        color="#6b7280",
                    ),
                    rx.text(
                        "0.0 = deterministisch, 0.2 = fakten, 0.8 = ausgewogen, 1.5+ = kreativ",
                        font_size="11px",
                        color="#9ca3af",
                    ),
                    width="100%",
                ),

                # Context Window
                rx.vstack(
                    rx.text("📦 Context Window (num_ctx)", font_weight="bold", font_size="14px"),
                    rx.text(
                        f"Aktuell: {AIState.num_ctx} tokens",
                        font_size="12px",
                        color="#6b7280",
                    ),
                    rx.text(
                        "Auto-berechnet basierend auf Message-Größe",
                        font_size="11px",
                        color="#9ca3af",
                    ),
                    width="100%",
                ),

                spacing="4",
                width="100%",
            ),
        ),
        collapsible=True,
        width="100%",
    )


def left_column() -> rx.Component:
    """Complete left column with all input controls"""
    return rx.vstack(
        audio_input_section(),
        text_input_section(),
        llm_parameters_accordion(),
        spacing="4",
        width="100%",
    )


# ============================================================
# RIGHT COLUMN: Output Display
# ============================================================

def chat_display() -> rx.Component:
    """Chat display area showing user input and AI response"""
    return rx.vstack(
        # User Input Display
        rx.vstack(
            rx.text("Eingabe:", font_weight="bold", font_size="14px"),
            rx.text_area(
                value=rx.cond(
                    AIState.chat_history.length() > 0,
                    AIState.chat_history[-1][0],
                    ""
                ),
                is_read_only=True,
                width="100%",
                rows="3",
                background_color="#f9fafb",
            ),
            width="100%",
        ),

        # AI Response Display
        rx.vstack(
            rx.text("AI Antwort:", font_weight="bold", font_size="14px"),
            rx.text_area(
                value=rx.cond(
                    AIState.current_ai_response != "",
                    AIState.current_ai_response,
                    rx.cond(
                        AIState.chat_history.length() > 0,
                        AIState.chat_history[-1][1],
                        ""
                    )
                ),
                is_read_only=True,
                width="100%",
                rows="8",
                background_color="#f9fafb",
            ),
            width="100%",
        ),

        spacing="4",
        width="100%",
    )


def tts_section() -> rx.Component:
    """TTS controls section (placeholder)"""
    return rx.vstack(
        rx.heading("🔊 Sprachausgabe (AI-Antwort)", size="4"),
        rx.hstack(
            rx.switch(
                checked=AIState.enable_tts,
                on_change=AIState.toggle_tts,
            ),
            rx.text("Sprachausgabe aktiviert", font_size="14px"),
            rx.spacer(),
            rx.button(
                "🔄 Neu generieren",
                variant="soft",
                size="2",
                disabled=True,  # Not yet implemented
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        rx.box(
            rx.text(
                "⚠️ TTS noch nicht portiert - Coming Soon!",
                color="#f59e0b",
                font_weight="bold",
                font_size="12px",
            ),
            padding="4",
            background_color="#fef3c7",
            border_radius="8px",
            border="1px solid #fbbf24",
            width="100%",
        ),
        spacing="3",
        width="100%",
    )


def chat_history_display() -> rx.Component:
    """Full chat history (like Gradio chatbot)"""
    return rx.vstack(
        rx.heading("💬 Chat Verlauf", size="4"),
        rx.box(
            rx.foreach(
                AIState.chat_history,
                lambda msg: rx.vstack(
                    # User message
                    rx.hstack(
                        rx.text("👤", font_size="20px"),
                        rx.box(
                            rx.text(msg[0], color="white"),
                            background_color="#2563eb",
                            padding="3",
                            border_radius="8px",
                            max_width="80%",
                        ),
                        spacing="3",
                        align="start",
                        justify="start",
                        width="100%",
                    ),
                    # AI message
                    rx.hstack(
                        rx.text("🤖", font_size="20px"),
                        rx.box(
                            rx.markdown(msg[1]),
                            background_color="#e5e7eb",
                            padding="3",
                            border_radius="8px",
                            max_width="80%",
                        ),
                        spacing="3",
                        align="start",
                        justify="start",
                        width="100%",
                    ),
                    spacing="2",
                    width="100%",
                ),
            ),
            width="100%",
            max_height="400px",
            overflow_y="auto",
            padding="4",
            background_color="#f9fafb",
            border_radius="8px",
            border="1px solid #e5e7eb",
        ),
        spacing="3",
        width="100%",
    )


def right_column() -> rx.Component:
    """Complete right column with output displays"""
    return rx.vstack(
        chat_display(),
        tts_section(),
        chat_history_display(),
        spacing="4",
        width="100%",
    )


# ============================================================
# DEBUG CONSOLE (Bottom of Page)
# ============================================================

def debug_console() -> rx.Component:
    """Debug console with logs (25 lines like Gradio)"""
    return rx.accordion.root(
        rx.accordion.item(
            header=rx.hstack(
                rx.text("🐛 Debug Console", font_weight="bold"),
                rx.badge(
                    f"{AIState.debug_messages.length()} messages",
                    color_scheme="blue",
                ),
                spacing="3",
            ),
            content=rx.vstack(
                rx.hstack(
                    rx.text("Live Debug-Output: LLM-Starts, Entscheidungen, Statistiken", font_size="12px", color="#6b7280"),
                    rx.spacer(),
                    rx.switch(
                        checked=AIState.auto_refresh_enabled,
                        on_change=AIState.toggle_auto_refresh,
                    ),
                    rx.text("Auto-Refresh", font_size="12px"),
                    spacing="3",
                    align="center",
                    width="100%",
                ),
                rx.box(
                    rx.foreach(
                        AIState.debug_messages,
                        lambda msg: rx.text(
                            msg,
                            font_family="monospace",
                            font_size="11px",
                            color="#1f2937",
                            white_space="pre",
                        ),
                    ),
                    width="100%",
                    height="400px",  # Fixed height for 25 lines
                    overflow_y="auto",
                    padding="3",
                    background_color="#f9fafb",
                    border_radius="8px",
                    border="1px solid #d1d5db",
                ),
                spacing="3",
                width="100%",
            ),
        ),
        collapsible=True,
        width="100%",
    )


# ============================================================
# SETTINGS ACCORDION (Bottom Settings)
# ============================================================

def settings_accordion() -> rx.Component:
    """Settings accordion at bottom"""
    return rx.accordion.root(
        rx.accordion.item(
            header=rx.text("⚙️ Einstellungen", font_weight="bold"),
            content=rx.vstack(
                # Backend Selection
                rx.hstack(
                    rx.text("Backend:", font_weight="bold", font_size="14px"),
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
                ),

                # Model Selection
                rx.hstack(
                    rx.text("Haupt-LLM:", font_weight="bold", font_size="14px"),
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
                    rx.text("Automatik-LLM:", font_weight="bold", font_size="14px"),
                    rx.select(
                        AIState.available_models,
                        value=AIState.automatik_model,
                        on_change=AIState.set_automatik_model,
                        size="2",
                    ),
                    spacing="3",
                    align="center",
                ),

                spacing="4",
                width="100%",
            ),
        ),
        collapsible=True,
        width="100%",
    )


# ============================================================
# MAIN PAGE
# ============================================================

@rx.page(route="/", on_load=AIState.on_load)
def index() -> rx.Component:
    """Main page with Gradio-style layout"""
    return rx.container(
        rx.vstack(
            # Header
            rx.heading("🎩 AIfred Intelligence", size="8", margin_bottom="2"),
            rx.text(
                "AI at your service • Benannt nach Alfred (Großvater) und Wolfgang Alfred (Vater)",
                color="#6b7280",
                font_size="14px",
                font_style="italic",
                margin_bottom="4",
            ),

            # Main 2-Column Layout (like Gradio)
            rx.grid(
                # Left Column
                rx.box(
                    left_column(),
                    padding="4",
                    background_color="white",
                    border_radius="12px",
                    border="1px solid #e5e7eb",
                ),
                # Right Column
                rx.box(
                    right_column(),
                    padding="4",
                    background_color="white",
                    border_radius="12px",
                    border="1px solid #e5e7eb",
                ),
                columns="2",
                spacing="4",
                width="100%",
            ),

            # Debug Console (bottom)
            debug_console(),

            # Settings Accordion (bottom)
            settings_accordion(),

            spacing="4",
            padding="6",
            width="100%",
            max_width="1600px",
        ),
        center_content=True,
        background_color="#f3f4f6",
    )


# Create app
app = rx.App()
