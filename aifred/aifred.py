"""
AIfred Intelligence - Reflex Edition

Full Gradio-Style UI with 2-Column Layout
"""

import reflex as rx
from .state import AIState
from .theme import COLORS


# ============================================================
# LEFT COLUMN: Input Controls
# ============================================================

def audio_input_section() -> rx.Component:
    """Audio input section (placeholder for STT)"""
    return rx.vstack(
        rx.heading("🎙️ Spracheingabe", size="3"),
        rx.box(
            rx.text(
                "Audio-Eingabe (Microphone Recording)",
                color=COLORS["text_muted"],
                font_size="12px",
            ),
            rx.text(
                "⚠️ STT/TTS noch nicht portiert - Coming Soon!",
                color=COLORS["accent_warning"],
                font_weight="bold",
                font_size="12px",
            ),
            padding="4",
            background_color=COLORS["warning_bg"],  # Dunkles Orange wie Text senden Button
            border_radius="8px",
            border=f"1px solid {COLORS['accent_warning']}",  # Orange Border
            width="100%",
        ),
        rx.text(
            "💡 Tipp: Nach dem Stoppen läuft automatisch die Transkription",
            font_size="12px",
            color=COLORS["text_secondary"],
        ),
        rx.divider(margin_y="4"),
        spacing="3",
        width="100%",
    )


def text_input_section() -> rx.Component:
    """Text input section with research mode"""
    return rx.vstack(
        # Text Input
        rx.heading("⌨️ Texteingabe", size="2"),
        rx.text_area(
            placeholder="Oder schreibe hier deine Frage...",
            value=AIState.current_user_input,
            on_change=AIState.set_user_input,
            width="100%",
            rows="5",
            disabled=AIState.is_generating,
            style={
                "border": f"1px solid {COLORS['border']}",
                "&:focus": {
                    "border": f"1px solid {COLORS['accent_blue']}",  # Blau beim Fokus (nicht orange)
                    "outline": "none",
                },
            },
        ),

        # Research Mode Radio Buttons
        rx.vstack(
            rx.text("🎯 Recherche-Modus", font_weight="bold", font_size="12px"),
            rx.radio(
                [
                    "🤖 Automatik (KI entscheidet)",
                    "🧠 Eigenes Wissen (schnell)",
                    "⚡ Web-Suche Schnell (3 beste)",
                    "🔍 Web-Suche Ausführlich (7 beste)"
                ],
                value=AIState.research_mode_display,
                on_change=AIState.set_research_mode_display,
                spacing="2",
            ),
            rx.text(
                "Wähle, wie der Assistant Fragen beantwortet",
                font_size="12px",
                color=COLORS["text_secondary"],
            ),
            width="100%",
        ),

        # Action Buttons
        rx.hstack(
            rx.button(
                "💬 Text senden",
                on_click=AIState.send_message,
                size="2",
                variant="solid",  # Explizit solid, ohne color_scheme
                loading=AIState.is_generating,
                disabled=AIState.is_generating,
                flex="1",  # Nimmt mehr Platz
                style={
                    "background": "#3d2a00 !important",  # Dunkles Orange (wichtig!)
                    "color": COLORS["accent_warning"] + " !important",  # Orange Text
                    "border": f"1px solid {COLORS['accent_warning']}",
                    "font_weight": "600",
                    "font_size": "14px",
                    "&:hover": {
                        "background": "#4d3500 !important",
                        "color": "#ffb84d !important",
                    },
                    "&:active": {
                        "background": "#331a00 !important",
                        "color": "#ff9500 !important",
                        "transform": "scale(0.98)",
                    },
                },
            ),
            rx.button(
                "🗑️ Chat löschen",
                on_click=AIState.clear_chat,
                disabled=AIState.is_generating,  # Deaktiviert während Inferenz
                size="2",
                variant="outline",
                color_scheme="red",
                style={
                    "min_width": "140px",  # Schmaler als Text senden
                },
            ),
            spacing="2",
            width="100%",
        ),

        # Processing Progress Banner (unterhalb der Buttons)
        processing_progress_banner(),

        spacing="3",
        width="100%",
    )


def llm_parameters_accordion() -> rx.Component:
    """LLM Parameters in collapsible accordion - Kompakt"""
    return rx.accordion.root(
        rx.accordion.item(
            header=rx.box(
                rx.text("⚙️ LLM-Parameter (Erweitert)", font_weight="500", font_size="12px", color=COLORS["text_primary"]),
                padding_y="2",  # Weniger Padding oben/unten
            ),
            content=rx.vstack(
                # Temperature
                rx.vstack(
                    rx.text("🌡️ Temperature", font_weight="bold", font_size="12px"),
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
                        font_size="11px",
                        color=COLORS["text_secondary"],
                    ),
                    rx.text(
                        "0.0 = deterministisch, 0.2 = fakten, 0.8 = ausgewogen, 1.5+ = kreativ",
                        font_size="11px",
                        color=COLORS["text_muted"],
                    ),
                    width="100%",
                ),

                # Context Window
                rx.vstack(
                    rx.text("📦 Context Window (num_ctx)", font_weight="bold", font_size="12px"),
                    rx.text(
                        f"Aktuell: {AIState.num_ctx} tokens",
                        font_size="12px",
                        color=COLORS["text_secondary"],
                    ),
                    rx.text(
                        "Auto-berechnet basierend auf Message-Größe",
                        font_size="11px",
                        color=COLORS["text_muted"],
                    ),
                    width="100%",
                ),

                spacing="4",
                width="100%",
            ),
        ),
        collapsible=True,  # WICHTIG: Macht Accordion schließbar!
        color_scheme="gray",
        variant="soft",
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

def processing_progress_banner() -> rx.Component:
    """Progress banner for all processing phases (Automatik, Scraping, LLM)"""

    # Berechne Fortschritt in Prozent (nur für Scraping relevant)
    progress_pct = rx.cond(
        AIState.progress_total > 0,
        (AIState.progress_current / AIState.progress_total) * 100,
        0
    )

    # Icon und Text basierend auf Phase
    phase_icon = rx.cond(
        AIState.progress_phase == "automatik",
        "🤖",
        rx.cond(
            AIState.progress_phase == "scraping",
            "🔍",
            rx.cond(
                AIState.progress_phase == "compress",
                "🗜️",
                "🧠"  # llm
            )
        )
    )

    phase_text = rx.cond(
        AIState.progress_phase == "automatik",
        "Automatik-Entscheidung ...",
        rx.cond(
            AIState.progress_phase == "scraping",
            "Web-Scraping",
            rx.cond(
                AIState.progress_phase == "compress",
                "Komprimiere Kontext ...",
                "Generiere Antwort ..."
            )
        )
    )

    # Progress-Bar nur für Scraping
    def scraping_bar():
        return rx.hstack(
            rx.text(phase_icon, font_size="14px"),
            rx.text(phase_text, font_weight="bold", font_size="13px", color=COLORS["primary"]),
            rx.box(
                rx.box(
                    width=f"{progress_pct}%",
                    height="100%",
                    background_color=COLORS["primary"],
                    border_radius="2px",
                    transition="width 0.3s ease",
                ),
                width="120px",
                height="14px",
                background_color="rgba(255, 255, 255, 0.1)",
                border_radius="4px",
                border=f"1px solid {COLORS['border']}",
                overflow="hidden",
            ),
            rx.text(
                f"{AIState.progress_current}/{AIState.progress_total}",
                font_size="12px",
                color=COLORS["primary"],  # Orange statt Grau - besser lesbar
                font_weight="600",
            ),
            rx.cond(
                AIState.progress_failed > 0,
                rx.text(
                    f"({AIState.progress_failed} Fehler)",
                    font_size="11px",
                    color=COLORS["accent_warning"],  # Orange statt Grau - besser sichtbar
                    font_weight="500",
                ),
                rx.box(),
            ),
            spacing="2",
            align="center",
        )

    # Nur Text für Automatik und LLM (mit Pulsier-Animation)
    def text_only():
        return rx.hstack(
            rx.text(
                phase_icon,
                font_size="14px",
                style={
                    "animation": "pulse 2s ease-in-out infinite",
                    "@keyframes pulse": {
                        "0%, 100%": {"opacity": "1"},
                        "50%": {"opacity": "0.5"},
                    }
                }
            ),
            rx.text(
                phase_text,
                font_weight="bold",
                font_size="13px",
                color=COLORS["primary"],
                style={
                    "animation": "pulse 2s ease-in-out infinite",
                    "@keyframes pulse": {
                        "0%, 100%": {"opacity": "1"},
                        "50%": {"opacity": "0.5"},
                    }
                }
            ),
            spacing="2",
            align="center",
        )

    # Entscheide welche Anzeige basierend auf Phase
    content = rx.cond(
        AIState.progress_phase == "scraping",
        scraping_bar(),
        text_only()
    )

    return rx.cond(
        AIState.progress_active,
        rx.box(
            content,
            padding="2",
            background_color=COLORS["primary_bg"],
            border_radius="6px",
            border=f"1px solid {COLORS['primary']}",
            width="100%",
        ),
        rx.box(),  # Empty box when not active
    )


def chat_display() -> rx.Component:
    """Chat display area showing user input and AI response"""
    return rx.vstack(
        # User Input Display
        rx.vstack(
            rx.text("Eingabe:", font_weight="bold", font_size="12px"),
            rx.box(
                rx.text(
                    rx.cond(
                        AIState.is_generating,
                        AIState.current_user_message,  # Zeige aktuell verarbeitete Nachricht
                        rx.cond(
                            AIState.chat_history.length() > 0,
                            AIState.chat_history[-1][0],  # Zeige letzte Chat-Nachricht
                            ""
                        )
                    ),
                    color=COLORS["text_primary"],
                    font_size="13px",
                    white_space="pre-wrap",
                    word_break="break-word",
                ),
                width="100%",
                height="120px",  # Feste Höhe für visuelle Stabilität
                padding="3",
                background_color=COLORS["readonly_bg"],
                border_radius="8px",
                border=f"1px solid {COLORS['border']}",
                overflow_y="auto",  # Scrollbar bei langen Texten
                style={
                    "cursor": "default",
                    "user-select": "text",
                },
            ),
            width="100%",
        ),

        # AI Response Display with Auto-Scroll Support
        rx.vstack(
            rx.text("AI Antwort:", font_weight="bold", font_size="12px"),
            rx.cond(
                AIState.auto_refresh_enabled,
                # Auto-Scroll enabled: Use rx.auto_scroll
                rx.auto_scroll(
                    rx.markdown(
                        rx.cond(
                            AIState.current_ai_response != "",
                            AIState.current_ai_response,
                            rx.cond(
                                AIState.chat_history.length() > 0,
                                AIState.chat_history[-1][1],
                                ""
                            )
                        ),
                        color=COLORS["text_primary"],
                        font_size="13px",
                    ),
                    id="ai-response-box",
                    width="100%",
                    height="400px",
                    padding="4",
                    background_color=COLORS["readonly_bg"],
                    border_radius="8px",
                    border=f"1px solid {COLORS['border']}",
                ),
                # Auto-Scroll disabled: Use normal box
                rx.box(
                    rx.markdown(
                        rx.cond(
                            AIState.current_ai_response != "",
                            AIState.current_ai_response,
                            rx.cond(
                                AIState.chat_history.length() > 0,
                                AIState.chat_history[-1][1],
                                ""
                            )
                        ),
                        color=COLORS["text_primary"],
                        font_size="13px",
                    ),
                    width="100%",
                    height="400px",
                    padding="4",
                    background_color=COLORS["readonly_bg"],
                    border_radius="8px",
                    border=f"1px solid {COLORS['border']}",
                    overflow_y="auto",
                ),
            ),
            width="100%",
        ),

        spacing="4",
        width="100%",
    )


def tts_section() -> rx.Component:
    """TTS controls section (placeholder)"""
    return rx.vstack(
        rx.heading("🔊 Sprachausgabe (AI-Antwort)", size="3"),
        rx.hstack(
            rx.switch(
                checked=AIState.enable_tts,
                on_change=AIState.toggle_tts,
                color_scheme="orange",
                high_contrast=True,
            ),
            rx.text("Sprachausgabe aktiviert", font_size="12px"),
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
                color=COLORS["accent_warning"],
                font_weight="bold",
                font_size="12px",
            ),
            padding="4",
            background_color=COLORS["warning_bg"],
            border_radius="8px",
            border=f"1px solid {COLORS['accent_warning']}",
            width="100%",
        ),
        spacing="3",
        width="100%",
    )


def render_chat_message(msg: tuple) -> rx.Component:
    """Rendert eine einzelne Chat-Message (User+AI oder Summary)"""
    # Check ob es eine Summary ist (leerer User-Teil + "[📊 Komprimiert" am Anfang)
    # Verwende Reflex bitwise operators: & statt 'and'
    is_summary = (msg[0] == "") & msg[1].startswith("[📊 Komprimiert")

    return rx.cond(
        is_summary,
        # Summary-Anzeige (Collapsible ganz oben)
        rx.box(
            rx.details(
                rx.summary(
                    rx.hstack(
                        rx.text("📊", font_size="14px"),
                        rx.text(
                            msg[1].split("]")[0] + "]",  # Nur "[📊 Komprimiert: X Messages]"
                            font_weight="bold",
                            font_size="13px",
                            color=COLORS["accent_warning"]
                        ),
                        spacing="2",
                    ),
                ),
                rx.box(
                    rx.markdown(
                        msg[1].split("\n", 1)[1] if "\n" in msg[1] else msg[1],  # Text nach dem Header
                        color=COLORS["text_primary"],
                        font_size="12px"
                    ),
                    padding="3",
                    margin_top="2",
                ),
                width="100%",
            ),
            background_color="rgba(255, 165, 0, 0.1)",  # Orange Tint
            padding="3",
            border_radius="8px",
            border=f"1px solid {COLORS['accent_warning']}",
            width="100%",
            margin_bottom="3",
        ),
        # Normale Message-Anzeige (User + AI)
        rx.vstack(
            # User message (rechts, max 70%) - mit hellgrauem Container
            rx.box(
                rx.hstack(
                    rx.spacer(),
                    rx.box(
                        rx.text(msg[0], color=COLORS["user_text"], font_size="13px"),
                        background_color=COLORS["user_msg"],
                        padding="3",
                        border_radius="6px",
                        max_width="70%",
                    ),
                    rx.text("👤", font_size="13px"),
                    spacing="2",
                    align="start",
                    justify="end",
                    width="100%",
                ),
                background_color="rgba(255, 255, 255, 0.03)",
                padding="2",
                border_radius="8px",
                width="100%",
            ),
            # AI message (links, bis 100% wenn nötig) - mit hellgrauem Container
            rx.box(
                rx.hstack(
                    rx.text("🤖", font_size="13px"),
                    rx.box(
                        rx.markdown(msg[1], color=COLORS["ai_text"], font_size="13px"),
                        background_color=COLORS["ai_msg"],
                        padding="3",
                        border_radius="6px",
                        width="100%",
                    ),
                    spacing="2",
                    align="start",
                    justify="start",
                    width="100%",
                ),
                background_color="rgba(255, 255, 255, 0.03)",
                padding="2",
                border_radius="8px",
                width="100%",
            ),
            spacing="3",
            width="100%",
        )
    )


def chat_history_display() -> rx.Component:
    """Full chat history (like Gradio chatbot)"""
    return rx.vstack(
        rx.heading("💬 Chat Verlauf", size="3"),
        rx.cond(
            AIState.auto_refresh_enabled,
            # Auto-Scroll enabled: rx.auto_scroll scrollt automatisch
            rx.auto_scroll(
                rx.foreach(
                    AIState.chat_history,
                    render_chat_message  # Verwende separate Render-Funktion
                ),
                id="chat-history-box",
                width="100%",
                min_height="120px",
                max_height="1200px",
                padding="4",
                background_color=COLORS["readonly_bg"],
                border_radius="8px",
                border=f"1px solid {COLORS['border']}",
                style={
                    "transition": "all 0.4s ease-out",
                },
            ),
            # Auto-Scroll disabled: normale rx.box (kein Scroll)
            rx.box(
                rx.foreach(
                    AIState.chat_history,
                    render_chat_message  # Verwende separate Render-Funktion
                ),
                id="chat-history-box",
                width="100%",
                min_height="120px",
                max_height="1200px",
                overflow_y="auto",
                padding="4",
                background_color=COLORS["readonly_bg"],
                border_radius="8px",
                border=f"1px solid {COLORS['border']}",
                style={
                    "transition": "all 0.4s ease-out",
                },
            ),
        ),
        spacing="3",
        width="100%",
        style={
            "animation": "fadeIn 0.4s ease-in",
            "transition": "all 0.4s ease-out",
            "@keyframes fadeIn": {
                "from": {"opacity": "0", "transform": "translateY(-10px)"},
                "to": {"opacity": "1", "transform": "translateY(0)"}
            }
        }
    )


def right_column() -> rx.Component:
    """Complete right column with output displays"""
    return rx.vstack(
        chat_display(),
        tts_section(),
        # Chat history moved to bottom (full width)
        spacing="4",
        width="100%",
    )


# ============================================================
# DEBUG CONSOLE (Bottom of Page)
# ============================================================

def debug_console() -> rx.Component:
    """Debug console with logs (25 lines like Gradio) - Matrix Terminal Style"""
    return rx.accordion.root(
        rx.accordion.item(
            value="debug",  # Eindeutige ID für das Accordion Item
            header=rx.box(
                rx.hstack(
                    rx.text("🐛 Debug Console", font_size="12px", font_weight="500", color=COLORS["debug_accent"]),
                    rx.badge(
                        f"{AIState.debug_messages.length()} messages",
                        color_scheme="green",
                        size="1",
                    ),
                    spacing="3",
                    align="center",
                ),
                padding_y="2",  # Kompakter Header
            ),
            content=rx.vstack(
                rx.hstack(
                    rx.text("Live Debug-Output: LLM-Starts, Entscheidungen, Statistiken", font_size="12px", color=COLORS["text_secondary"]),
                    rx.spacer(),
                    rx.switch(
                        checked=AIState.auto_refresh_enabled,
                        on_change=AIState.toggle_auto_refresh,
                        color_scheme="orange",
                        high_contrast=True,
                    ),
                    rx.text("Auto-Scroll", font_size="12px", color=COLORS["text_secondary"]),
                    spacing="3",
                    align="center",
                    width="100%",
                ),
                rx.cond(
                    AIState.auto_refresh_enabled,
                    # Auto-Scroll enabled: rx.auto_scroll scrollt automatisch
                    rx.auto_scroll(
                        rx.foreach(
                            AIState.debug_messages,
                            lambda msg: rx.text(
                                msg,
                                font_family="monospace",
                                font_size="11px",
                                color=COLORS["debug_text"],  # Matrix Grün
                                white_space="pre",
                            ),
                        ),
                        id="debug-console-box",
                        width="100%",
                        height="500px",
                        padding="3",
                        background_color=COLORS["debug_bg"],
                        border_radius="8px",
                        border=f"2px solid {COLORS['debug_border']}",
                        style={
                            "scroll-behavior": "smooth",
                        },
                    ),
                    # Auto-Scroll disabled: normale rx.box (kein Scroll)
                    rx.box(
                        rx.foreach(
                            AIState.debug_messages,
                            lambda msg: rx.text(
                                msg,
                                font_family="monospace",
                                font_size="11px",
                                color=COLORS["debug_text"],  # Matrix Grün
                                white_space="pre",
                            ),
                        ),
                        id="debug-console-box",
                        width="100%",
                        height="500px",
                        overflow_y="auto",
                        padding="3",
                        background_color=COLORS["debug_bg"],
                        border_radius="8px",
                        border=f"2px solid {COLORS['debug_border']}",
                        style={
                            "scroll-behavior": "smooth",
                        },
                    ),
                ),
                spacing="3",
                width="100%",
            ),
        ),
        collapsible=True,  # WICHTIG: Macht Accordion schließbar!
        default_value="debug",  # Standardmäßig geöffnet
        color_scheme="gray",
        variant="soft",
    )


# ============================================================
# SETTINGS ACCORDION (Bottom Settings)
# ============================================================

def settings_accordion() -> rx.Component:
    """Settings accordion at bottom - Kompakt"""
    return rx.accordion.root(
        rx.accordion.item(
            value="settings",  # Eindeutige ID für das Accordion Item
            header=rx.box(
                rx.text("⚙️ Einstellungen", font_size="12px", font_weight="500", color=COLORS["text_primary"]),
                padding_y="2",  # Kompakter Header
            ),
            content=rx.vstack(
                # Backend Selection
                rx.hstack(
                    rx.text("Backend:", font_weight="bold", font_size="12px"),
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
                    rx.text("Haupt-LLM:", font_weight="bold", font_size="12px"),
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
                    rx.text("Automatik-LLM:", font_weight="bold", font_size="12px"),
                    rx.select(
                        AIState.available_models,
                        value=AIState.automatik_model,
                        on_change=AIState.set_automatik_model,
                        size="2",
                    ),
                    spacing="3",
                    align="center",
                ),

                # Restart Buttons
                rx.divider(),
                rx.text("🔄 System-Steuerung", font_weight="bold", font_size="12px"),
                rx.hstack(
                    rx.button(
                        "🔄 Ollama neu starten",
                        on_click=AIState.restart_ollama,
                        size="2",
                        variant="soft",
                        color_scheme="blue",
                    ),
                    rx.button(
                        "🔄 AIfred neu starten",
                        on_click=AIState.restart_aifred,
                        size="2",
                        variant="soft",
                        color_scheme="orange",
                    ),
                    spacing="3",
                    width="100%",
                ),
                rx.vstack(
                    rx.text(
                        "ℹ️ Ollama-Neustart: Stoppt laufende Generierungen, lädt Models neu",
                        font_size="11px",
                        color="#d4913d",  # Dunkles Orange - gut lesbar
                        line_height="1.5",
                    ),
                    rx.text(
                        "(Chats bleiben erhalten)",
                        font_size="10px",
                        color="#a67a30",  # Etwas dunkler für Zusatzinfo
                        line_height="1.3",
                        margin_top="-2px",  # Näher an die Zeile darüber
                        margin_left="16px",  # Eingerückt
                    ),
                    rx.text(
                        "⚠️ AIfred-Neustart: Löscht ALLE Chats, Caches und Debug-Logs komplett!",
                        font_size="11px",
                        color="#d4913d",  # Dunkles Orange - gut lesbar
                        line_height="1.5",
                    ),
                    spacing="2",
                    width="100%",
                ),

                spacing="4",
                width="100%",
            ),
        ),
        collapsible=True,  # WICHTIG: Macht Accordion schließbar!
        default_value="settings",  # Standardmäßig geöffnet
        color_scheme="gray",
        variant="soft",
    )


# ============================================================
# MAIN PAGE
# ============================================================

@rx.page(route="/", on_load=AIState.on_load, title="AIfred Intelligence")
def index() -> rx.Component:
    """Main page with Gradio-style layout"""
    return rx.box(
        rx.vstack(
            # Header
            rx.heading("🎩 AIfred Intelligence", size="6", margin_bottom="2"),
            rx.text(
                "AI at your service • Benannt nach Alfred (Großvater) und Wolfgang Alfred (Vater)",
                color=COLORS["text_secondary"],
                font_size="12px",
                font_style="italic",
                margin_bottom="4",
            ),

            # Main 2-Column Layout (like Gradio)
            rx.grid(
                # Left Column
                rx.box(
                    left_column(),
                    padding="4",
                    background_color=COLORS["card_bg"],
                    border_radius="12px",
                    border=f"1px solid {COLORS['border']}",
                ),
                # Right Column
                rx.box(
                    right_column(),
                    padding="4",
                    background_color=COLORS["card_bg"],
                    border_radius="12px",
                    border=f"1px solid {COLORS['border']}",
                ),
                columns="2",
                spacing="4",  # Normaler Abstand zwischen Spalten
                width="100%",
                style={
                    "gap": "1rem",  # Explizites Gap ohne Hintergrundfarbe
                },
            ),

            # Chat History (full width below columns)
            chat_history_display(),

            # Debug Console & Settings side-by-side
            rx.grid(
                debug_console(),
                settings_accordion(),
                columns="11fr 9fr",  # Debug Console 55% (11fr), Settings 45% (9fr)
                spacing="4",
                width="100%",
            ),

            spacing="4",
            width="100%",
            padding="16",  # Padding rundherum (64px) - deutlich größer!
            max_width="96vw",  # 96% viewport width - weniger Breite für mehr Padding
            margin="0 auto",  # Zentriert
            background_color=COLORS["page_bg"],  # Explizite Hintergrundfarbe
        ),

        width="100%",
        min_height="100vh",
        background_color=COLORS["page_bg"],
        display="flex",
        justify_content="center",
    )


# Create app
app = rx.App(
    stylesheets=["/custom.css"],  # Load custom CSS
)
