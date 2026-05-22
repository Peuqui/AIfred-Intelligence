"""Chat display components for AIfred UI.

Contains the main chat history display, session list, and progress banner.
"""

from __future__ import annotations

import reflex as rx

from ..state import AIState, StreamingState, ChatHistoryState
from ..theme import COLORS
from .message_renderer import render_message_standalone
from .streaming_text import streaming_text

# Pulse animation style (reused for progress icon and text)
_PULSE_STYLE: dict = {
    "animation": "pulse 2s ease-in-out infinite",
    "@keyframes pulse": {
        "0%, 100%": {"opacity": "1"},
        "50%": {"opacity": "0.5"},
    },
}


# ============================================================
# PROCESSING PROGRESS BANNER
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
        AIState.is_uploading_image,
        "\U0001f4e4",  # Upload icon
        rx.cond(
            AIState.progress_active,
            rx.cond(
                AIState.progress_phase == "automatik",
                "\U0001f916",
                rx.cond(
                    AIState.progress_phase == "scraping",
                    "\U0001f50d",
                    rx.cond(
                        AIState.progress_phase == "compress",
                        "\U0001f5dc\ufe0f",
                        "\U0001f9e0"  # llm
                    )
                )
            ),
            rx.cond(
                AIState.tool_status != "",
                "\U0001f527",  # 🔧 Tool icon
                "\U0001f4a4"  # Idle icon - sleeping/waiting
            )
        )
    )

    phase_text = rx.cond(
        AIState.is_uploading_image,
        # Upload state - highest priority
        rx.cond(
            AIState.ui_language == "de",
            "Bild wird hochgeladen ...",
            "Uploading image ..."
        ),
        rx.cond(
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
            # Tool status or generating
            rx.cond(
                AIState.tool_status != "",
                AIState.tool_status,
                rx.cond(
                    AIState.is_generating,
                    rx.cond(
                        AIState.ui_language == "de",
                        "Generiere Antwort ...",
                        "Generating Answer ..."
                    ),
                    # Wirklich idle
                    rx.cond(
                        AIState.ui_language == "de",
                        "Warte auf Eingabe ...",
                        "Waiting for input ..."
                    )
                )
            )
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
                    AIState.progress_active | AIState.is_generating,
                    _PULSE_STYLE,
                    {}
                )
            ),
            rx.text(
                phase_text,
                font_weight=rx.cond(AIState.progress_active | AIState.is_generating, "bold", "500"),
                font_size="13px",
                color=COLORS["primary"],
                style=rx.cond(
                    AIState.progress_active | AIState.is_generating,
                    _PULSE_STYLE,
                    {}
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


# ============================================================
# SESSION LIST DISPLAY
# ============================================================

def session_list_display() -> rx.Component:
    """Session picker - Collapsible list of saved chats above chat history."""

    def render_session_item(session) -> rx.Component:
        """Render a single session item in the list."""
        # Format date from ISO string
        # session has: session_id, title, last_seen, created_at, message_count
        return rx.box(
            rx.hstack(
                # Session title or placeholder — rendered as rx.el.span so
                # the data-session-id attribute makes it into the DOM. The
                # Radix rx.text component filters unknown props out (both
                # `custom_attrs=…` and `**`-unpacking did NOT render the
                # attribute, confirmed via DevTools); rx.el.* are raw-HTML
                # elements that pass kwargs through. The class + data-attr
                # let custom.js patch the title live when the Browser Push
                # Bus delivers a "session_title" event — same pattern as
                # data-audio-urls on rx.el.button (message_renderer.py).
                rx.el.span(
                    rx.cond(
                        session["title"],
                        session["title"],
                        rx.cond(
                            AIState.ui_language == "de",
                            "Unbenannter Chat",
                            "Untitled Chat",
                        ),
                    ),
                    class_name="session-title-text",
                    **{"data-session-id": session["session_id"]},
                    style={
                        "fontSize": "13px",
                        "fontWeight": rx.cond(
                            session["session_id"] == AIState.session_id,
                            "600",  # Bold for current session
                            "400",
                        ),
                        "color": rx.cond(
                            session["session_id"] == AIState.session_id,
                            COLORS["primary"],  # Orange for current
                            COLORS["text_primary"],
                        ),
                        "flex": "1",
                        "wordBreak": "break-word",
                    },
                ),
                # Message count badge
                rx.badge(
                    f"{session['message_count']}",
                    color_scheme=rx.cond(
                        session["session_id"] == AIState.session_id,
                        "orange",
                        "gray",
                    ),
                    size="1",
                ),
                # Delete button (only for non-current sessions)
                rx.cond(
                    session["session_id"] != AIState.session_id,
                    rx.icon_button(
                        rx.icon("trash-2", size=12),
                        size="1",
                        variant="ghost",
                        color_scheme="red",
                        on_click=AIState.delete_session(session["session_id"]),  # type: ignore[call-arg]
                        cursor="pointer",
                    ),
                    rx.fragment(),
                ),
                spacing="2",
                align="center",
                width="100%",
            ),
            padding="2",
            padding_x="3",
            border_radius="4px",
            background_color=rx.cond(
                session["session_id"] == AIState.session_id,
                COLORS["primary_bg"],  # Highlight current session
                "transparent",
            ),
            _hover={
                "background_color": COLORS["card_bg"],
            },
            cursor="pointer",
            on_click=AIState.switch_session(session["session_id"]),  # type: ignore[call-arg]
            width="100%",
        )

    session_list_content = rx.vstack(
        # New Chat + Refresh buttons
        rx.hstack(
            rx.button(
                rx.hstack(
                    rx.icon("plus", size=14),
                    rx.cond(
                        AIState.ui_language == "de",
                        rx.text("Neuer Chat", font_size="12px"),
                        rx.text("New Chat", font_size="12px"),
                    ),
                    spacing="2",
                    align="center",
                ),
                size="1",
                variant="solid",
                on_click=AIState.new_session,
                flex="1",
                style={
                    "background": "#3d2a00",
                    "color": COLORS["accent_warning"],
                    "border": f"1px solid {COLORS['accent_warning']}",
                    "font_weight": "600",
                    "&:hover": {
                        "background": "#4d3500",
                        "color": "#ffb84d",
                    },
                },
            ),
            rx.button(
                rx.icon("refresh-cw", size=14),
                size="1",
                variant="solid",
                on_click=AIState.refresh_session_list,
                padding_x="4",
                style={
                    "background": "rgba(180, 60, 60, 0.3)",
                    "color": COLORS["accent_warning"],
                    "border": "1px solid rgba(220, 80, 80, 0.6)",
                    "minWidth": "42px",
                    "&:hover": {
                        "background": "rgba(200, 70, 70, 0.4)",
                        "color": "#ffb84d",
                    },
                },
            ),
            spacing="2",
            width="100%",
            margin_bottom="2",
        ),
        # Session list (scrollable area separate from button)
        rx.cond(
            AIState.available_sessions.length() > 0,
            rx.box(
                rx.vstack(
                    rx.foreach(
                        AIState.available_sessions,
                        render_session_item,
                    ),
                    spacing="1",
                    width="100%",
                ),
                style={
                    "minHeight": "70px",  # Fits 3 sessions without scroll
                    "maxHeight": "168px",
                    "overflowY": "auto",
                    "overflowX": "hidden",
                    "scrollbarGutter": "stable",  # Prevent UI jump when scrollbar appears/disappears
                },
                width="100%",
            ),
            rx.text(
                rx.cond(
                    AIState.ui_language == "de",
                    "Keine gespeicherten Chats",
                    "No saved chats",
                ),
                font_size="12px",
                color=COLORS["text_muted"],
                text_align="center",
                padding="3",
            ),
        ),
        spacing="1",
        width="100%",
        padding="2",
    )

    return rx.accordion.root(
        rx.accordion.item(
            value="session_list",
            header=rx.box(
                rx.hstack(
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "\U0001f4c1 Gespeicherte Chats",
                            "\U0001f4c1 Saved Chats"
                        ),
                        font_size="14px",
                        font_weight="500",
                        color=COLORS["text_primary"]
                    ),
                    rx.badge(
                        f"{AIState.available_sessions.length()}",
                        color_scheme="gray",
                        size="1",
                    ),
                    spacing="3",
                    align="center",
                ),
                padding_y="2",
                background_color=COLORS["card_bg"],
                _hover={
                    "background_color": COLORS["primary_bg"],
                },
                border_radius="6px",
                padding_x="3",
                cursor="pointer",
                transition="background-color 0.2s ease",
            ),
            content=session_list_content,
        ),
        default_value="",  # Collapsed by default
        collapsible=True,
        width="100%",
        variant="soft",
        color_scheme="gray",
    )


# ============================================================
# CHAT HISTORY DISPLAY
# ============================================================

def chat_history_display() -> rx.Component:
    """Full chat history (like Gradio chatbot) - Collapsible"""
    # Loading spinner während Initialisierung, Backend-Wechsel oder Bild-Upload
    loading_spinner = rx.vstack(
        rx.spinner(size="3", color="orange"),
        rx.cond(
            AIState.is_uploading_image,
            rx.text(
                rx.cond(
                    AIState.ui_language == "de",
                    "Bild wird hochgeladen...",
                    "Uploading image...",
                ),
                font_size="14px",
                color=COLORS["text_secondary"],
                margin_top="3",
            ),
            rx.cond(
                AIState.backend_initializing,
                rx.text(
                    "AIfred wird initialisiert...",
                    font_size="14px",
                    color=COLORS["text_secondary"],
                    margin_top="3",
                ),
                rx.cond(
                    AIState.vllm_restarting,
                    rx.text(
                        "vLLM wird neu gestartet...",
                        font_size="14px",
                        color=COLORS["text_secondary"],
                        margin_top="3",
                    ),
                    rx.text(
                        "Backend wird gewechselt...",
                        font_size="14px",
                        color=COLORS["text_secondary"],
                        margin_top="3",
                    ),
                ),
            ),
        ),
        rx.cond(
            AIState.is_uploading_image,
            rx.fragment(),  # No subtitle for image upload
            rx.text(
                "Bitte warten, Backend startet (~40-70 Sekunden)",
                font_size="11px",
                color=COLORS["text_muted"],
                font_style="italic",
                margin_top="1",
            ),
        ),
        align="center",
        justify="center",
        width="100%",
        min_height="360px",
        padding="4",
        background_color=COLORS["readonly_bg"],
        border_radius="8px",
        border=f"1px solid {COLORS['border']}",
    )

    # Chat History Box - JavaScript-basiertes Autoscroll (nicht rx.auto_scroll)
    # rx.auto_scroll ignoriert den Toggle während der Inferenz, daher JavaScript-Lösung
    # StreamingText uses useEffect + DOM append for O(1) updates per state delta.

    _stream_text_style: dict = {"white_space": "pre-wrap", "word_break": "break-word", "color": COLORS["ai_text"], "font_size": "13px", "line_height": "1.35"}

    # Single streaming box — NO rx.cond switching between different streaming_text instances.
    # This prevents React "removeChild" crashes when current_agent changes mid-stream.
    # Agent name, emoji, and colors update via state vars without DOM node replacement.
    streaming_box = rx.cond(
        AIState.is_generating,
        rx.box(
            rx.box(
                rx.hstack(
                    rx.cond(
                        AIState.current_agent != "",
                        rx.text(AIState.current_agent_emoji, font_size="13px"),
                    ),
                    rx.box(
                        rx.cond(
                            AIState.current_agent != "",
                            rx.hstack(
                                rx.text(
                                    AIState.current_agent_display_name,
                                    font_weight="bold", font_size="12px",
                                    color=COLORS["primary"],
                                ),
                                rx.text("\u258c", font_size="14px", color=COLORS["primary"], animation="blink 1s infinite"),
                                spacing="1", margin_bottom="1",
                            ),
                            # Neutral: just blinking cursor
                            rx.hstack(
                                rx.text("\u258c", font_size="14px", color=COLORS["primary"], animation="blink 1s infinite"),
                                spacing="1", margin_bottom="1",
                            ),
                        ),
                        streaming_text(text=StreamingState.current_ai_response, id="st-main", style=_stream_text_style),
                        background_color=COLORS["ai_msg"], padding="3", border_radius="6px", width="100%",
                    ),
                    spacing="2", align="start", width="100%",
                ),
                background_color="rgba(255, 255, 255, 0.03)", padding="2", border_radius="8px",
                border=f"1px solid {COLORS['primary']}", width="100%",
            ),
            id="streaming-box", width="100%",
        ),
    )

    # NOTE: Web sources collapsible is now embedded directly in the AI response HTML
    # (like the Denkprozess collapsible), so we don't need a separate component here.

    chat_history_box = rx.box(
        rx.vstack(
            # All chat messages including inline summaries
            rx.foreach(
                ChatHistoryState.chat_history,
                render_message_standalone
            ),
            # Unified streaming element at the end (adapts to current_agent)
            streaming_box,
            spacing="3",
            width="100%",
        ),
        id="chat-history-box",
        width="100%",
        min_height="360px",
        max_height="2400px",
        overflow_y="auto",
        padding="4",
        background_color=COLORS["readonly_bg"],
        border_radius="8px",
        border=f"1px solid {COLORS['border']}",
        style={
            "transition": "all 0.4s ease-out",
        },
    )

    chat_content = rx.cond(
        AIState.backend_initializing | AIState.backend_switching | AIState.vllm_restarting | AIState.is_uploading_image,
        loading_spinner,  # Show spinner during initialization, backend switch, vLLM restart, or image upload
        chat_history_box,
    )

    return rx.accordion.root(
        rx.accordion.item(
            value="chat_history",  # Eindeutige ID für das Accordion Item
            header=rx.box(
                rx.hstack(
                    rx.text(
                        rx.cond(
                            AIState.ui_language == "de",
                            "\U0001f4ac Chat Verlauf",
                            "\U0001f4ac Chat History"
                        ),
                        font_size="14px",
                        font_weight="500",
                        color=COLORS["text_primary"]
                    ),
                    rx.badge(
                        rx.cond(
                            AIState.ui_language == "de",
                            f"{ChatHistoryState.chat_history.length()} Nachrichten",
                            f"{ChatHistoryState.chat_history.length()} messages"
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
