"""
AIfred Intelligence - Reflex Edition

Full Gradio-Style UI with Single Column Layout
"""

import reflex as rx
from .state import AIState
from .theme import COLORS


def t(key: str) -> rx.Var:
    """
    Translation helper that returns German or English text based on state
    """
    # Define translation pairs directly since Reflex needs static values
    de_text = {
        "voice_input": "🎙️ Spracheingabe",
        "audio_input_placeholder": "Audio-Eingabe (Microphone Recording)",
        "stt_not_ported": "⚠️ STT/TTS noch nicht portiert - Coming Soon!",
        "tip_automatic_transcription": "💡 Tipp: Nach dem Stoppen läuft automatisch die Transkription",
        "text_input": "⌨️ Texteingabe",
        "enter_your_question": "Oder schreibe hier deine Frage...",
        "research_mode": "🎯 Recherche-Modus",
        "research_mode_auto": "🤖 Automatik (KI entscheidet)",
        "research_mode_none": "🧠 Eigenes Wissen (schnell)",
        "research_mode_quick": "⚡ Web-Suche Schnell (3 beste)",
        "research_mode_deep": "🔍 Web-Suche Ausführlich (7 beste)",
        "choose_research_mode": "Wähle, wie der Assistant Fragen beantwortet",
        "send_text": "💬 Text senden",
        "clear_chat": "🗑️ Chat löschen",
        "llm_parameters": "⚙️ LLM-Parameter (Erweitert)",
        "temperature": "🌡️ Temperature",
        "current": "Aktuell:",
        "temperature_info": "0.0 = deterministisch, 0.2 = fakten, 0.8 = ausgewogen, 1.5+ = kreativ",
        "context_window": "📦 Context Window (num_ctx)",
        "context_window_info": "Auto-berechnet basierend auf Message-Größe",
        "automatic_decision": "Automatik-Entscheidung ...",
        "web_scraping": "Web-Scraping",
        "compressing_context": "Komprimiere Kontext ...",
        "generating_answer": "Generiere Antwort ...",
        "websites_unreachable": "Website nicht erreichbar",
        "websites_unreachable_plural": "Websites nicht erreichbar",
        "input": "Eingabe:",
        "ai_response": "AI Antwort:",
        "tts_output": "🔊 Sprachausgabe (AI-Antwort)",
        "tts_enabled": "Sprachausgabe aktiviert",
        "tts_regenerate": "🔄 Neu generieren",
        "tts_not_ported": "⚠️ TTS noch nicht portiert - Coming Soon!",
        "chat_history": "💬 Chat Verlauf",
        "messages_count": "messages",
        "debug_console": "🐛 Debug Console",
        "live_debug_output": "Live Debug-Output: LLM-Starts, Entscheidungen, Statistiken",
        "auto_scroll": "Auto-Scroll",
        "settings": "⚙️ Einstellungen",
        "ui_language": "🌐 UI Sprache:",
        "backend": "Backend:",
        "main_llm": "Haupt-LLM:",
        "automatic_llm": "Automatik-LLM:",
        "system_control": "🔄 System-Steuerung",
        "restart_ollama": "🔄 Ollama Neustart",
        "restart_vllm": "🔄 vLLM Neustart",
        "restart_aifred": "🔄 AIfred neu starten",
        "ollama_restart_info": "ℹ️ Neustart: Stoppt laufende Generierungen, lädt Models neu",
        "vllm_restart_info": "ℹ️ Neustart: Stoppt vLLM Server, startet neu mit gewähltem Modell",
        "backend_restart_info": "ℹ️ Neustart: Startet Backend neu",
        "chat_preserved": "(Chats bleiben erhalten)",
        "aifred_restart_warning": "⚠️ AIfred-Neustart: Löscht ALLE Chats, Caches und Debug-Logs komplett!",
        "aifred_intelligence": "🎩 AIfred Intelligence",
        "subtitle": "AI at your service • Benannt nach Alfred (Großvater) und Wolfgang Alfred (Vater)",
    }
    
    en_text = {
        "voice_input": "🎙️ Voice Input",
        "audio_input_placeholder": "Audio Input (Microphone Recording)",
        "stt_not_ported": "⚠️ STT/TTS not yet ported - Coming Soon!",
        "tip_automatic_transcription": "💡 Tip: Automatic transcription runs after stopping",
        "text_input": "⌨️ Text Input",
        "enter_your_question": "Or write your question here...",
        "research_mode": "🎯 Research Mode",
        "research_mode_auto": "🤖 Automatic (AI decides)",
        "research_mode_none": "🧠 Own Knowledge (fast)",
        "research_mode_quick": "⚡ Web Search Quick (3 best)",
        "research_mode_deep": "🔍 Web Search Detailed (7 best)",
        "choose_research_mode": "Choose how the assistant answers questions",
        "send_text": "💬 Send Text",
        "clear_chat": "🗑️ Clear Chat",
        "llm_parameters": "⚙️ LLM Parameters (Advanced)",
        "temperature": "🌡️ Temperature",
        "current": "Current:",
        "temperature_info": "0.0 = deterministic, 0.2 = factual, 0.8 = balanced, 1.5+ = creative",
        "context_window": "📦 Context Window (num_ctx)",
        "context_window_info": "Auto-calculated based on message size",
        "automatic_decision": "Automatic decision ...",
        "web_scraping": "Web Scraping",
        "compressing_context": "Compressing Context ...",
        "generating_answer": "Generating Answer ...",
        "websites_unreachable": "Website unreachable",
        "websites_unreachable_plural": "Websites unreachable",
        "input": "Input:",
        "ai_response": "AI Response:",
        "tts_output": "🔊 Text-to-Speech (AI Answer)",
        "tts_enabled": "Text-to-Speech enabled",
        "tts_regenerate": "🔄 Regenerate",
        "tts_not_ported": "⚠️ TTS not yet ported - Coming Soon!",
        "chat_history": "💬 Chat History",
        "messages_count": "messages",
        "debug_console": "🐛 Debug Console",
        "live_debug_output": "Live Debug Output: LLM starts, decisions, statistics",
        "auto_scroll": "Auto-Scroll",
        "settings": "⚙️ Settings",
        "ui_language": "🌐 UI Language:",
        "backend": "Backend:",
        "main_llm": "Main LLM:",
        "automatic_llm": "Automatic LLM:",
        "system_control": "🔄 System Control",
        "restart_ollama": "🔄 Ollama Restart",
        "restart_vllm": "🔄 vLLM Restart",
        "restart_aifred": "🔄 Restart AIfred",
        "ollama_restart_info": "ℹ️ Restart: Stops ongoing generations, reloads models",
        "vllm_restart_info": "ℹ️ Restart: Stops vLLM server, restarts with selected model",
        "backend_restart_info": "ℹ️ Restart: Restarts backend",
        "chat_preserved": "(Chats are preserved)",
        "aifred_restart_warning": "⚠️ AIfred restart: Deletes ALL chats, caches and debug logs completely!",
        "aifred_intelligence": "🎩 AIfred Intelligence",
        "subtitle": "AI at your service • Named after Alfred (grandfather) and Wolfgang Alfred (father)",
    }
    
    return rx.cond(
        AIState.ui_language == "de",
        de_text.get(key, key),  # Fallback to key if not found
        en_text.get(key, key)   # Fallback to key if not found
    )


# ============================================================
# LEFT COLUMN: Input Controls
# ============================================================

def audio_input_section() -> rx.Component:
    """Audio input section (placeholder for STT)"""
    return rx.vstack(
        rx.heading(t("voice_input"), size="3"),
        rx.box(
            rx.text(
                t("audio_input_placeholder"),
                color=COLORS["text_muted"],
                font_size="12px",
            ),
            rx.text(
                t("stt_not_ported"),
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
            t("tip_automatic_transcription"),
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
        rx.heading(t("text_input"), size="2"),
        rx.text_area(
            placeholder=rx.cond(
                AIState.ui_language == "de",
                "Oder schreibe hier deine Frage...",
                "Or write your question here..."
            ),
            value=AIState.current_user_input,
            on_change=AIState.set_user_input,
            width="100%",
            rows="5",
            disabled=AIState.is_generating | AIState.is_compressing,
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
            rx.text(t("research_mode"), font_weight="bold", font_size="12px"),
            rx.radio(
                [
                    rx.cond(AIState.ui_language == "de", "🤖 Automatik (KI entscheidet)", "🤖 Automatic (AI decides)"),
                    rx.cond(AIState.ui_language == "de", "🧠 Eigenes Wissen (schnell)", "🧠 Own Knowledge (fast)"),
                    rx.cond(AIState.ui_language == "de", "⚡ Web-Suche Schnell (3 beste)", "⚡ Web Search Quick (3 best)"),
                    rx.cond(AIState.ui_language == "de", "🔍 Web-Suche Ausführlich (7 beste)", "🔍 Web Search Detailed (7 best)")
                ],
                value=AIState.research_mode_display,
                on_change=AIState.set_research_mode_display,
                spacing="2",
            ),
            rx.text(
                t("choose_research_mode"),
                font_size="12px",
                color=COLORS["text_secondary"],
            ),
            width="100%",
        ),

        # Processing Progress Banner (above the send button - always visible)
        processing_progress_banner(),

        # Action Buttons
        rx.hstack(
            rx.button(
                t("send_text"),
                on_click=AIState.send_message,
                size="2",
                variant="solid",  # Explizit solid, ohne color_scheme
                loading=AIState.is_generating | AIState.is_compressing,
                disabled=AIState.is_generating | AIState.is_compressing,
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
                t("clear_chat"),
                on_click=AIState.clear_chat,
                disabled=AIState.is_generating | AIState.is_compressing,  # Deaktiviert während Inferenz und Kompression
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

        # TTS Section (zwischen Senden-Button und LLM-Parametern platzieren)
        tts_section(),

        spacing="3",
        width="100%",
    )


def llm_parameters_accordion() -> rx.Component:
    """LLM Parameters in collapsible accordion - Kompakt"""
    return rx.accordion.root(
        rx.accordion.item(
            header=rx.box(
                rx.text(
                    rx.cond(
                        AIState.ui_language == "de",
                        "⚙️ LLM-Parameter (Erweitert)",
                        "⚙️ LLM Parameters (Advanced)"
                    ),
                    font_weight="500",
                    font_size="12px",
                    color=COLORS["text_primary"]
                ),
                padding_y="2",  # Weniger Padding oben/unten
            ),
            content=rx.vstack(
                # Temperature
                rx.vstack(
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "🌡️ Temperature",
                            "🌡️ Temperature"
                        ),
                        font_weight="bold",
                        font_size="12px"
                    ),
                    rx.slider(
                        default_value=AIState.temperature,
                        min=0.0,
                        max=2.0,
                        step=0.1,
                        on_change=AIState.set_temperature,
                        width="100%",
                    ),
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            f"Aktuell: {AIState.temperature}",
                            f"Current: {AIState.temperature}"
                        ),
                        font_size="11px",
                        color=COLORS["text_secondary"],
                    ),
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "0.0 = deterministisch, 0.2 = fakten, 0.8 = ausgewogen, 1.5+ = kreativ",
                            "0.0 = deterministic, 0.2 = factual, 0.8 = balanced, 1.5+ = creative"
                        ),
                        font_size="11px",
                        color=COLORS["text_muted"],
                    ),
                    width="100%",
                ),

                # Context Window
                rx.vstack(
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "📦 Context Window (num_ctx)",
                            "📦 Context Window (num_ctx)"
                        ),
                        font_weight="bold",
                        font_size="12px"
                    ),
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            f"Aktuell: {AIState.num_ctx} tokens",
                            f"Current: {AIState.num_ctx} tokens"
                        ),
                        font_size="12px",
                        color=COLORS["text_secondary"],
                    ),
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "Auto-berechnet basierend auf Message-Größe",
                            "Auto-calculated based on message size"
                        ),
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
# PROGRESS BANNER (wird unten eingefügt)
# ============================================================

def processing_progress_banner() -> rx.Component:
    """Progress banner for all processing phases (Automatik, Scraping, LLM) - Always visible"""

    # Berechne Fortschritt in Prozent (nur für Scraping relevant)
    progress_pct = rx.cond(
        AIState.progress_total > 0,
        (AIState.progress_current / AIState.progress_total) * 100,
        0
    )

    # Icon und Text basierend auf Phase
    phase_icon = rx.cond(
        AIState.progress_active,
        rx.cond(
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
        ),
        "💤"  # Idle icon - sleeping/waiting
    )

    phase_text = rx.cond(
        AIState.progress_active,
        rx.cond(
            AIState.progress_phase == "automatik",
            rx.cond(
                AIState.ui_language == "de",
                "Automatik-Entscheidung ...",
                "Automatic decision ..."
            ),
            rx.cond(
                AIState.progress_phase == "scraping",
                rx.cond(
                    AIState.ui_language == "de",
                    "Web-Scraping",
                    "Web Scraping"
                ),
                rx.cond(
                    AIState.progress_phase == "compress",
                    rx.cond(
                        AIState.ui_language == "de",
                        "Komprimiere Kontext ...",
                        "Compressing Context ..."
                    ),
                    rx.cond(
                        AIState.ui_language == "de",
                        "Generiere Antwort ...",
                        "Generating Answer ..."
                    )
                )
            )
        ),
        # Idle state text
        rx.cond(
            AIState.ui_language == "de",
            "Warte auf Eingabe ...",
            "Waiting for input ..."
        )
    )

    # Fortschrittsanzeige basierend auf Phase
    progress_content = rx.cond(
        # Progress-Bar nur für Scraping (wenn aktiv)
        (AIState.progress_active) & (AIState.progress_phase == "scraping"),
        rx.hstack(
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
                    rx.cond(
                        AIState.progress_failed == 1,
                        rx.cond(
                            AIState.ui_language == "de",
                            "(1 Website nicht erreichbar)",
                            "(1 Website unreachable)"
                        ),
                        rx.cond(
                            AIState.ui_language == "de",
                            f"({AIState.progress_failed} Websites nicht erreichbar)",
                            f"({AIState.progress_failed} Websites unreachable)"
                        )
                    ),
                    font_size="11px",
                    color=COLORS["accent_warning"],  # Orange statt Grau - besser sichtbar
                    font_weight="500",
                ),
                rx.box(),
            ),
            spacing="2",
            align="center",
        ),
        # Nur Text für Automatik, LLM und Idle (mit Pulsier-Animation nur wenn aktiv)
        rx.hstack(
            rx.text(
                phase_icon,
                font_size="14px",
                style=rx.cond(
                    AIState.progress_active,
                    {
                        "animation": "pulse 2s ease-in-out infinite",
                        "@keyframes pulse": {
                            "0%, 100%": {"opacity": "1"},
                            "50%": {"opacity": "0.5"},
                        }
                    },
                    {}  # No animation when idle
                )
            ),
            rx.text(
                phase_text,
                font_weight=rx.cond(AIState.progress_active, "bold", "500"),
                font_size="13px",
                color=COLORS["primary"],  # Always orange text
                style=rx.cond(
                    AIState.progress_active,
                    {
                        "animation": "pulse 2s ease-in-out infinite",
                        "@keyframes pulse": {
                            "0%, 100%": {"opacity": "1"},
                            "50%": {"opacity": "0.5"},
                        }
                    },
                    {}  # No animation when idle
                )
            ),
            spacing="2",
            align="center",
        )
    )

    # Always return the banner (no conditional rendering)
    return rx.box(
        progress_content,
        padding="2",
        background_color=COLORS["primary_bg"],  # Always orange/yellow background
        border_radius="6px",
        border=f"1px solid {COLORS['primary']}",  # Always orange border
        width="100%",
    )


def tts_section() -> rx.Component:
    """TTS controls section (placeholder)"""
    return rx.vstack(
        rx.heading(
            rx.cond(
                AIState.ui_language == "de",
                "🔊 Sprachausgabe (AI-Antwort)",
                "🔊 Text-to-Speech (AI Answer)"
            ),
            size="3"
        ),
        rx.hstack(
            rx.switch(
                checked=AIState.enable_tts,
                on_change=AIState.toggle_tts,
                color_scheme="orange",
                high_contrast=True,
            ),
            rx.text(
                rx.cond(
                    AIState.ui_language == "de",
                    "Sprachausgabe aktiviert",
                    "Text-to-Speech enabled"
                ),
                font_size="12px"
            ),
            rx.spacer(),
            rx.button(
                rx.cond(
                    AIState.ui_language == "de",
                    "🔄 Neu generieren",
                    "🔄 Regenerate"
                ),
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
                rx.cond(
                    AIState.ui_language == "de",
                    "⚠️ TTS noch nicht portiert - Coming Soon!",
                    "⚠️ TTS not yet ported - Coming Soon!"
                ),
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
        # Summary-Anzeige mit Collapsible (vereinfacht für Reflex)
        rx.box(
            rx.accordion.root(
                rx.accordion.item(
                    value="summary_main",
                    header=rx.box(
                        rx.hstack(
                            rx.text("📊", font_size="14px"),
                            rx.text(
                                "Komprimierte Messages",  # Fester Text statt StringVar-Operation
                                font_weight="bold",
                                font_size="13px",
                                color=COLORS["accent_warning"]
                            ),
                            spacing="2",
                            align="center",
                        ),
                        padding_y="2",
                        padding_x="3",
                        background_color=COLORS["card_bg"],
                        border_radius="6px",
                        cursor="pointer",
                        transition="background-color 0.2s ease",
                        _hover={
                            "background_color": COLORS["primary_bg"],
                        },
                    ),
                    content=rx.box(
                        rx.markdown(
                            msg[1],  # Zeige den kompletten Summary-Text
                            color=COLORS["text_primary"],
                            font_size="12px"
                        ),
                        padding="3",
                        background_color="rgba(255, 165, 0, 0.05)",  # Leichter Orange-Tint
                        border_radius="6px",
                        border=f"1px solid {COLORS['border']}",
                        width="100%",
                        max_height="600px",  # Scrollbar bei sehr langen Summaries
                        overflow_y="auto",
                    ),
                ),
                collapsible=True,
                variant="soft",
                width="100%",
            ),
            background_color="rgba(255, 165, 0, 0.1)",  # Orange Hintergrund für Container
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
                        rx.markdown(
                            msg[1],
                            color=COLORS["ai_text"], 
                            font_size="13px"
                        ),
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
    """Full chat history (like Gradio chatbot) - Collapsible"""
    chat_content = rx.cond(
        AIState.auto_refresh_enabled,
        # Auto-Scroll enabled: rx.auto_scroll scrollt automatisch
        rx.auto_scroll(
            rx.foreach(
                AIState.chat_history,
                render_chat_message  # Verwende separate Render-Funktion
            ),
            id="chat-history-box",
            width="100%",
            min_height="360px",  # Dreifache Höhe (120*3)
            max_height="2400px",  # Höheres Maximum (1200*2)
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
            min_height="360px",  # Dreifache Höhe (120*3)
            max_height="2400px",  # Höheres Maximum (1200*2)
            overflow_y="auto",
            padding="4",
            background_color=COLORS["readonly_bg"],
            border_radius="8px",
            border=f"1px solid {COLORS['border']}",
            style={
                "transition": "all 0.4s ease-out",
            },
        ),
    )
    
    return rx.accordion.root(
        rx.accordion.item(
            value="chat_history",  # Eindeutige ID für das Accordion Item
            header=rx.box(
                rx.hstack(
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "💬 Chat Verlauf",
                            "💬 Chat History"
                        ),
                        font_size="14px",
                        font_weight="500",
                        color=COLORS["text_primary"]
                    ),
                    rx.badge(
                        rx.cond(
                            AIState.ui_language == "de",
                            f"{AIState.chat_history.length()} messages",
                            f"{AIState.chat_history.length()} messages"
                        ),
                        color_scheme="orange",
                        size="1",
                    ),
                    spacing="3",
                    align="center",
                ),
                padding_y="2",  # Kompakter Header
                background_color=COLORS["card_bg"],  # Dunkles Grau
                _hover={
                    "background_color": COLORS["primary_bg"],  # Orange beim Hover (15% Opacity)
                },
                border_radius="6px",
                padding_x="3",
                cursor="pointer",
                transition="background-color 0.2s ease",
            ),
            content=chat_content,
        ),
        default_value="chat_history",  # Standardmäßig GEÖFFNET
        collapsible=True,
        width="100%",
        variant="soft",  # Soft statt ghost für besseres Styling
        color_scheme="gray",  # Grau statt Blau
    )


def right_column() -> rx.Component:
    """Right column removed completely"""
    # This function is now empty as the right column is completely removed
    return rx.vstack(
        spacing="4",
        width="100%",
    )


# ============================================================
# DEBUG CONSOLE (Bottom of Page)
# ============================================================

def debug_console() -> rx.Component:
    """Debug console with logs (25 lines like Gradio) - Matrix Terminal Style"""
    
    debug_content = rx.cond(
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
    )
    
    return rx.accordion.root(
        rx.accordion.item(
            value="debug",  # Eindeutige ID für das Accordion Item
            header=rx.box(
                rx.hstack(
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "🐛 Debug Console",
                            "🐛 Debug Console"
                        ),
                        font_size="12px",
                        font_weight="500",
                        color=COLORS["debug_accent"]
                    ),
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
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "Live Debug-Output: LLM-Starts, Entscheidungen, Statistiken",
                            "Live Debug Output: LLM starts, decisions, statistics"
                        ),
                        font_size="12px",
                        color=COLORS["text_secondary"]
                    ),
                    rx.spacer(),
                    rx.switch(
                        checked=AIState.auto_refresh_enabled,
                        on_change=AIState.toggle_auto_refresh,
                        color_scheme="orange",
                        high_contrast=True,
                    ),
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "Auto-Scroll",
                            "Auto-Scroll"
                        ),
                        font_size="12px",
                        color=COLORS["text_secondary"]
                    ),
                    spacing="3",
                    align="center",
                    width="100%",
                ),
                debug_content,
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
                rx.text(t("settings"), font_size="12px", font_weight="500", color=COLORS["text_primary"]),
                padding_y="2",  # Kompakter Header
            ),
            content=rx.vstack(
                # UI Language Selection
                rx.hstack(
                    rx.text(t("ui_language"), font_weight="bold", font_size="12px"),
                    rx.select(
                        ["de", "en"],
                        value=AIState.ui_language,
                        on_change=AIState.set_ui_language,
                        size="2",
                    ),
                    spacing="3",
                    align="center",
                ),

                # Backend Selection
                rx.hstack(
                    rx.text(t("backend"), font_weight="bold", font_size="12px"),
                    rx.select(
                        ["ollama", "vllm"],
                        value=AIState.backend_type,
                        on_change=AIState.switch_backend,
                        size="2",
                        disabled=AIState.backend_switching,  # Disable during backend switch
                    ),
                    rx.cond(
                        AIState.backend_switching,
                        rx.badge("Switching...", color_scheme="orange"),
                        rx.cond(
                            AIState.backend_healthy,
                            rx.badge(AIState.backend_info, color_scheme="green"),
                            rx.badge(AIState.backend_info, color_scheme="red"),
                        ),
                    ),
                    spacing="3",
                    align="center",
                ),

                # vLLM Single Model Warning
                rx.cond(
                    AIState.backend_type == "vllm",
                    rx.text(
                        "ℹ️ vLLM kann nur EIN Modell gleichzeitig laden (Haupt- und Automatik-LLM nutzen dasselbe Modell)",
                        font_size="11px",
                        color="#d4913d",  # Dunkles Orange - gut lesbar
                        line_height="1.5",
                        margin_top="8px",
                    ),
                ),

                # Model Selection
                rx.hstack(
                    rx.text(t("main_llm"), font_weight="bold", font_size="12px"),
                    rx.select(
                        AIState.available_models,
                        value=AIState.selected_model,
                        on_change=AIState.set_selected_model,
                        size="2",
                        disabled=AIState.backend_switching,  # Disable during backend switch
                    ),
                    spacing="3",
                    align="center",
                ),

                rx.hstack(
                    rx.text(t("automatic_llm"), font_weight="bold", font_size="12px"),
                    rx.select(
                        AIState.available_models,
                        value=AIState.automatik_model,
                        on_change=AIState.set_automatik_model,
                        size="2",
                        disabled=AIState.backend_switching,  # Disable during backend switch
                    ),
                    spacing="3",
                    align="center",
                ),

                # Qwen3 Thinking Mode Toggle (nur sichtbar bei Qwen3/QwQ-Modellen)
                rx.divider(),
                rx.cond(
                    AIState.selected_model.lower().contains("qwen3") | AIState.selected_model.lower().contains("qwq"),
                    rx.vstack(
                        rx.hstack(
                            rx.text("🧠 Thinking Mode:", font_weight="bold", font_size="12px"),
                            rx.switch(
                                checked=AIState.enable_thinking,
                                on_change=AIState.toggle_thinking_mode,
                                size="1",
                            ),
                            rx.text(
                                rx.cond(
                                    AIState.enable_thinking,
                                    "ON (temp=0.6, CoT)",
                                    "OFF (temp=0.7, direct)"
                                ),
                                font_size="11px",
                                color=rx.cond(
                                    AIState.enable_thinking,
                                    "#4CAF50",
                                    "#999"
                                ),
                            ),
                            spacing="2",
                            align="center",
                        ),
                        rx.text(
                            "ℹ️ Chain-of-Thought Reasoning für komplexe Aufgaben",
                            font_size="10px",
                            color="#999",
                            line_height="1.3",
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    rx.box(),  # Empty box when not Qwen3/QwQ
                ),

                # Restart Buttons
                rx.divider(),
                rx.text(t("system_control"), font_weight="bold", font_size="12px"),
                rx.hstack(
                    rx.button(
                        rx.cond(
                            AIState.backend_type == "ollama",
                            t("restart_ollama"),
                            rx.cond(
                                AIState.backend_type == "vllm",
                                t("restart_vllm"),
                                rx.text(f"🔄 {AIState.backend_type.upper()} Neustart")
                            )
                        ),
                        on_click=AIState.restart_backend,
                        size="2",
                        variant="soft",
                        color_scheme="blue",
                        disabled=AIState.backend_switching,  # Disable during backend switch
                    ),
                    rx.button(
                        t("restart_aifred"),
                        on_click=AIState.restart_aifred,
                        size="2",
                        variant="soft",
                        color_scheme="orange",
                        disabled=AIState.backend_switching,  # Disable during backend switch
                    ),
                    spacing="3",
                    width="100%",
                ),
                rx.vstack(
                    rx.text(
                        rx.cond(
                            AIState.backend_type == "ollama",
                            t("ollama_restart_info"),
                            rx.cond(
                                AIState.backend_type == "vllm",
                                t("vllm_restart_info"),
                                t("backend_restart_info")
                            )
                        ),
                        font_size="11px",
                        color="#d4913d",  # Dunkles Orange - gut lesbar
                        line_height="1.5",
                    ),
                    rx.text(
                        t("chat_preserved"),
                        font_size="10px",
                        color="#a67a30",  # Etwas dunkler für Zusatzinfo
                        line_height="1.3",
                        margin_top="-2px",  # Näher an die Zeile darüber
                        margin_left="16px",  # Eingerückt
                    ),
                    rx.text(
                        t("aifred_restart_warning"),
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
    """Main page with single column layout for mobile optimization"""
    return rx.box(
        rx.vstack(
            # Header
            rx.heading(t("aifred_intelligence"), size="6", margin_bottom="2"),
            rx.text(
                t("subtitle"),
                color=COLORS["text_secondary"],
                font_size="12px",
                font_style="italic",
                margin_bottom="4",
            ),

            # Single Column Layout - Input controls only
            rx.box(
                left_column(),
                padding="4",
                background_color=COLORS["card_bg"],
                border_radius="12px",
                border=f"1px solid {COLORS['border']}",
                width="100%",
            ),

            # Chat History (full width below input)
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
            max_width="1200px",  # Festgelegte maximale Breite
            margin="0 auto",  # Zentriert
            background_color=COLORS["page_bg"],  # Explizite Hintergrundfarbe
        ),

        width="100%",
        min_height="100vh",
        background_color=COLORS["page_bg"],
        display="flex",
        justify_content="center",
    )


# ==============================================================
# Note: Automatik-LLM Preloading moved to State.on_load()
# ==============================================================
# Model preloading now happens in state.py initialize_backend()
# when the user first opens the page. This ensures:
# 1. Models are loaded from State settings (not hardcoded)
# 2. Available models list is populated before preloading
# 3. Everything happens in one place (cleaner architecture)


# Create app
app = rx.App(
    stylesheets=["/custom.css"],  # Load custom CSS
)
