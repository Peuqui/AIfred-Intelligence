"""Input section components for AIfred UI: upload, text input, debug console."""

from __future__ import annotations

import reflex as rx

from ..state import AIState
from ..theme import COLORS
from .helpers import t, clickable_tip, blue_action_button_style

from .helpers import native_select_generic
from .settings_accordion import llm_parameters_accordion
from .chat_display import processing_progress_banner
from .vigilantia_feed import vigilantia_feed_popover


def _busy_disabled() -> rx.Var:
    """Gemeinsamer Disabled-Ausdruck der Eingabe-/Action-Buttons:
    aktiv während Generierung, History-Kompression oder laufendem
    Bild-/Dokument-Upload."""
    return (
        AIState.is_generating
        | AIState.is_compressing
        | AIState.is_uploading_image
        | AIState.is_uploading_document
    )


def _agent_toggle_button(agent: rx.Var) -> rx.Component:
    """Render a single agent toggle button for the active-agent row."""
    # Core agent IDs that are locked in fixed modes
    is_core = (agent["id"] == "aifred") | (agent["id"] == "sokrates") | (agent["id"] == "salomo")
    is_fixed = AIState.is_fixed_agent_mode

    # In fixed modes: core agents are always active, others always inactive
    # In Symposion mode: multi-select via symposion_agents list
    # In Standard mode: single active_agent
    is_symposion = AIState.multi_agent_mode == "symposion"
    is_selected_symposion = AIState.symposion_agents.contains(agent["id"])
    is_active_standard = AIState.active_agent == agent["id"]
    is_active = rx.cond(
        is_fixed,
        is_core,
        rx.cond(is_symposion, is_selected_symposion, is_active_standard),
    )

    return rx.button(
        rx.hstack(
            rx.cond(
                agent["emoji"] == "\U0001f3a9",
                rx.image(src="/AIfred-Zylinder.png", width="16px", height="16px", display="inline-block"),
                rx.text(agent["emoji"], font_size="13px"),
            ),
            rx.text(agent["display_name"], font_size="12px"),
            spacing="1",
            align="center",
        ),
        on_click=AIState.set_active_agent(agent["id"]),
        size="2",
        variant=rx.cond(is_active, "solid", "soft"),
        color_scheme=rx.cond(is_active, "orange", "gray"),
        cursor=rx.cond(is_fixed, "default", "pointer"),
        opacity=rx.cond(is_fixed & ~is_core, "0.35", rx.cond(is_fixed & is_core, "0.7", "1")),
        padding_x="10px",
        height="32px",
    )


def _research_pill(mode: str, label_de: str, label_en: str) -> rx.Component:
    """Render a single research mode pill button."""
    is_active = AIState.research_mode == mode
    return rx.button(
        rx.cond(AIState.ui_language == "de", label_de, label_en),
        on_click=AIState.set_research_mode(mode),  # type: ignore[arg-type]
        size="2",
        variant=rx.cond(is_active, "solid", "soft"),
        color_scheme=rx.cond(is_active, "orange", "gray"),
        cursor="pointer",
        font_size="12px",
        padding_x="12px",
        height="32px",
    )


# ============================================================
# IMAGE / AUDIO UPLOAD SECTION
# ============================================================


def image_upload_section() -> rx.Component:
    """Image and Audio upload components with preview"""
    return rx.vstack(
        # Warning box (if model doesn't support vision)
        rx.cond(
            AIState.image_upload_warning != "",
            rx.box(
                rx.text(AIState.image_upload_warning),
                background_color="rgba(255, 152, 0, 0.15)",
                border="2px solid rgba(255, 152, 0, 0.5)",
                padding="8px 12px",
                border_radius="6px",
                margin_bottom="8px",
            )
        ),
        # Document upload status
        rx.cond(
            AIState.document_upload_status != "",
            rx.box(
                rx.text(AIState.document_upload_status, font_size="12px"),
                background_color="rgba(68, 204, 102, 0.15)",
                border="1px solid rgba(68, 204, 102, 0.3)",
                padding="4px 10px",
                border_radius="4px",
                margin_bottom="4px",
            )
        ),

        # Row 1: Buttons (Aufnahme, Camera, Upload, Audio, Clear) - responsive
        rx.hstack(
            # Live Audio Recording Button (LEFTMOST - MediaRecorder)
            rx.button(
                rx.icon("mic", size=16),
                rx.text(t("recording"), font_size="14px", display=["none", "none", "inline"]),  # Hide text on mobile
                id="recording-button",
                size="2",
                variant="outline",
                color_scheme="green",
                min_width=["auto", "auto", "160px"],  # Fixed on desktop
                flex=["2 1 0%", "2 1 0%", "0 0 auto"],  # Mobile: 2x share, Desktop: auto
                width=["100%", "100%", "auto"],
                on_click=AIState.toggle_audio_recording,
                disabled=AIState.is_generating | AIState.is_uploading_image | AIState.is_uploading_document,
                style={
                    "background": "rgba(0, 80, 30, 0.4)",
                    "color": "#3fb950",
                    "border_color": "#3fb950",
                    "&:hover:not([disabled]):not(.recording)": {
                        "background": "rgba(0, 120, 50, 0.6) !important",
                    },
                    "&.recording": {
                        "background": "#dc2626 !important",
                        "color": "white !important",
                        "border_color": "#dc2626 !important",
                    },
                    "&.recording:hover": {
                        "background": "#ef4444 !important",
                    },
                    "&[disabled]": {"opacity": "0.45"},
                },
            ),

            # Camera button (only visible if browser supports camera) - with drag & drop tooltip
            rx.cond(
                AIState.camera_available,
                rx.upload(
                    rx.tooltip(
                        rx.button(
                            rx.icon("camera", size=16),
                            rx.text(t("camera"), font_size="14px", display=["none", "none", "inline"]),
                            size="2",
                            variant="outline",
                            color_scheme="orange",
                            width="100%",
                            disabled=AIState.is_generating | AIState.is_uploading_image | AIState.is_uploading_document | (AIState.pending_images.length() >= AIState.max_images_per_message),
                            on_click=AIState.on_camera_click,
                            style={
                                "background": "rgba(100, 10, 0, 0.4)",
                                "color": "#d98030",
                                "border_color": "#d98030",
                                "&:hover:not([disabled])": {
                                    "background": "rgba(150, 15, 0, 0.6) !important",
                                },
                                "&[disabled]": {"opacity": "0.45"},
                            },
                        ),
                        content=t("image_hint"),
                    ),
                    id="camera-upload",
                    accept={"image/*": []},
                    max_files=1,
                    on_drop=AIState.handle_camera_upload,
                    multiple=False,
                    border="none",
                    padding="0",
                    flex=["1 1 0%", "1 1 0%", "0 0 auto"],  # Mobile: 1x share, Desktop: auto
                ),
            ),

            # Upload button with drag & drop - with tooltip
            rx.upload(
                rx.tooltip(
                    rx.button(
                        rx.icon("image", size=16),
                        rx.text(t("upload_image"), font_size="14px", display=["none", "none", "inline"]),
                        size="2",
                        variant="outline",
                        color_scheme="orange",
                        width="100%",
                        disabled=AIState.is_generating | AIState.is_uploading_image | AIState.is_uploading_document | (AIState.pending_images.length() >= AIState.max_images_per_message),
                        on_click=AIState.on_file_picker_click,
                        style={
                            "background": "rgba(100, 10, 0, 0.4)",
                            "color": "#d98030",
                            "border_color": "#d98030",
                            "&:hover:not([disabled])": {
                                "background": "rgba(150, 15, 0, 0.6) !important",
                            },
                            "&[disabled]": {"opacity": "0.45"},
                        },
                    ),
                    content=t("image_hint"),
                ),
                id="image-upload",
                accept={"image/*": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]},
                max_files=AIState.max_images_per_message,
                on_drop=AIState.handle_image_upload,
                multiple=True,
                border="none",
                padding="0",
                flex=["1 1 0%", "1 1 0%", "0 0 auto"],  # Mobile: 1x share, Desktop: auto
            ),

            # Vision Live-Preview button — opens a popup showing whatever
            # USB / IP camera is connected to the server-side V4L2 stack
            # (independent of the browser's getUserMedia camera).
            rx.tooltip(
                rx.button(
                    rx.icon("video", size=16),
                    rx.text(
                        t("vision_preview_button"),
                        font_size="14px",
                        display=["none", "none", "inline"],
                    ),
                    size="2",
                    variant="outline",
                    color_scheme="orange",
                    on_click=AIState.open_vision_preview,
                    disabled=AIState.is_generating,
                    style={
                        "background": "rgba(100, 10, 0, 0.4)",
                        "color": "#d98030",
                        "border_color": "#d98030",
                        "&:hover:not([disabled])": {
                            "background": "rgba(150, 15, 0, 0.6) !important",
                        },
                        "&[disabled]": {"opacity": "0.45"},
                    },
                    flex=["1 1 0%", "1 1 0%", "0 0 auto"],
                ),
                content=t("vision_preview_button_tooltip"),
            ),

            # Document Manager Button (click = open modal, drop = upload)
            rx.tooltip(
                rx.button(
                    rx.icon("file-text", size=16),
                    rx.text(t("upload_document"), font_size="14px", display=["none", "none", "inline"]),
                    size="2",
                    variant="outline",
                    color_scheme="yellow",
                    on_click=AIState.open_document_manager,
                    disabled=AIState.is_generating | AIState.is_uploading_image | AIState.is_uploading_document,
                    style={
                        "background": "rgba(100, 70, 0, 0.4)",
                        "color": "#d29922",
                        "border_color": "#d29922",
                        "&:hover:not([disabled])": {
                            "background": "rgba(130, 90, 0, 0.6) !important",
                        },
                        "&[disabled]": {"opacity": "0.45"},
                    },
                ),
                content=t("doc_hint"),
            ),

            # Audio File Upload Button
            rx.upload(
                rx.tooltip(
                    rx.button(
                        rx.icon("disc-3", size=16),
                        rx.text(t("audio"), font_size="14px", display=["none", "none", "inline"]),
                        size="2",
                        variant="outline",
                        color_scheme="blue",
                        width="100%",
                        disabled=AIState.is_generating | AIState.is_uploading_image | AIState.is_uploading_document,
                        style=blue_action_button_style(press_effect=False),
                    ),
                    content=t("audio_hint"),
                ),
                id="audio-upload",
                accept={"audio/*": [".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"]},
                max_files=1,
                on_drop=AIState.handle_audio_upload,
                multiple=False,
                border="none",
                padding="0",
                flex=["1 1 0%", "1 1 0%", "0 0 auto"],  # Mobile: 1x share, Desktop: auto
            ),

            # Help button (lightbulb) — explains all input buttons
            clickable_tip(
                rx.icon(
                    "lightbulb",
                    size=16,
                    color="#FFD700",
                    cursor="pointer",
                    style={
                        "transition": "transform 0.2s ease",
                        "&:hover": {"transform": "scale(1.15)"},
                    },
                ),
                rx.vstack(
                    rx.text("Eingabe-Optionen", font_size="13px", font_weight="bold", color="#FFD700"),
                    rx.text("Aufnahme — Spracheingabe per Mikrofon", font_size="11px", color="#ccc"),
                    rx.text("Kamera — Foto aufnehmen (Handy/Webcam)", font_size="11px", color="#ccc"),
                    rx.text("Bild hochladen — Bilder per Drag & Drop", font_size="11px", color="#ccc"),
                    rx.text("Dokument — Dateien verwalten, hochladen, indexieren", font_size="11px", color="#ccc"),
                    rx.text("Audio — Audiodatei hochladen (WAV, MP3)", font_size="11px", color="#ccc"),
                    spacing="1",
                ),
            ),

            # Hidden upload for MediaRecorder (JavaScript will populate this)
            rx.upload(
                rx.box(display="none"),
                id="audio-recording-upload",
                accept={"audio/*": [".webm"]},
                max_files=1,
                on_drop=AIState.handle_audio_upload,
                multiple=False,
                border="none",
                padding="0",
            ),

            # Clear button (only show if images present)
            rx.cond(
                AIState.pending_images.length() > 0,
                rx.button(
                    rx.icon("trash-2", size=20),
                    rx.text(t("clear_all"), font_size="16px", display=["none", "none", "inline"]),
                    size="4",
                    variant="soft",
                    color_scheme="red",
                    padding_y="24px",
                    on_click=AIState.clear_pending_images,
                )
            ),

            spacing="2",
            width="100%",
            align="center",
            justify_content="flex-start",
            flex_wrap="wrap",  # Wrap to next line on very narrow screens
        ),

        # Row 2: Transkription-Toggle + Enter-sends-Toggle (zwei kleine Switches)
        rx.hstack(
            # Transkription bearbeiten ──────────────────────
            rx.text(t("transcription_edit"), font_size="11px", font_weight="500"),
            rx.switch(
                checked=AIState.show_transcription,
                on_change=AIState.toggle_show_transcription,
                size="1",
            ),
            rx.text(
                rx.cond(
                    AIState.show_transcription,
                    t("text_edit"),
                    t("text_direct")
                ),
                font_size="10px",
                color=rx.cond(
                    AIState.show_transcription,
                    "#4CAF50",
                    "#999"
                ),
            ),
            # Trenner
            rx.text("·", font_size="11px", color="#666", margin_x="6px"),
            # Enter-sendet ─────────────────────────────────
            rx.switch(
                checked=AIState.enter_sends_message,
                on_change=AIState.toggle_enter_sends_message,
                size="1",
            ),
            rx.text(
                t("enter_sends"),
                font_size="10px",
                color=rx.cond(AIState.enter_sends_message, "#4CAF50", "#999"),
            ),
            rx.text(
                rx.cond(AIState.enter_sends_message, t("enter_sends_hint"), ""),
                font_size="10px",
                color="#777",
            ),
            spacing="2",
            align="center",
            margin_top="8px",
            margin_bottom="4px",
            flex_wrap="wrap",
        ),

        # Row 3: Image previews (only if images present) - linksbündig
        rx.cond(
            AIState.pending_images.length() > 0,
            rx.hstack(
                rx.foreach(
                    AIState.pending_images,
                    lambda img, idx: rx.box(
                        # Thumbnail - 80px für bessere Sichtbarkeit
                        rx.image(
                            src=img["url"],
                            width="80px",
                            height="80px",
                            object_fit="cover",
                            border_radius="4px",
                            border=f"1px solid {COLORS['border']}",
                        ),
                        # Crop-Button (links oben) - größer für Touch
                        rx.icon_button(
                            rx.icon("crop", size=16),
                            size="2",  # Größer für bessere Touch-Bedienung
                            color_scheme="green",
                            variant="solid",
                            position="absolute",
                            top="4px",
                            left="4px",
                            on_click=AIState.open_crop_modal(idx),  # type: ignore[call-arg, func-returns-value]
                            title=t("crop_tooltip"),
                        ),
                        # Remove-Button (rechts oben) - größer für Touch
                        rx.icon_button(
                            rx.icon("x", size=16),
                            size="2",  # Größer für bessere Touch-Bedienung
                            color_scheme="red",
                            variant="solid",
                            position="absolute",
                            top="4px",
                            right="4px",
                            on_click=AIState.remove_pending_image(idx),  # type: ignore[call-arg, func-returns-value]
                        ),
                        position="relative",
                    )
                ),
                spacing="3",
                wrap="wrap",
                justify_content="flex-start",  # Thumbnails linksbündig
                width="100%",
                margin_top="8px",
                margin_bottom="8px",
            ),
        ),

        width="100%",
        spacing="2"
    )


# ============================================================
# TEXT INPUT SECTION
# ============================================================


def text_input_section() -> rx.Component:
    """Text input section with research mode"""
    return rx.vstack(
        # Image Upload Section (NEW)
        image_upload_section(),

        # Text Input (no heading — placeholder is self-explanatory)
        rx.text_area(
            placeholder=t("text_input_placeholder"),
            id="user-text-input",
            width="100%",
            rows="3",
            spell_check=False,
            disabled=_busy_disabled(),
            style={
                "field_sizing": "content",
                "min_height": "4.5em",
                "max_height": "22.5em",
                "overflow_y": "auto",
                "border": f"1px solid {COLORS['border']}",
                "&:focus-within": {
                    "border": f"1px solid {COLORS['accent_blue']}",
                    "outline": "none",
                },
                "& textarea": {
                    "field_sizing": "content",
                    "height": "100%",
                },
            },
        ),

        # Row 1: Research Mode Pills + Info Icon + Vigilantia-Live-Popover
        rx.hstack(
            _research_pill("automatik", "✨ Automatik", "✨ Auto"),
            _research_pill("none", "💡 Wissen", "💡 Knowledge"),
            _research_pill("quick", "⚡ Web Quick", "⚡ Web Quick"),
            _research_pill("deep", "🌍 Web Deep", "🌍 Web Deep"),
            # Research mode help lightbulb (opens modal on click)
            rx.tooltip(
                rx.icon(
                    "lightbulb",
                    size=16,
                    color="#FFD700",
                    cursor="pointer",
                    on_click=AIState.open_research_help,
                    style={
                        "transition": "transform 0.2s ease",
                        "&:hover": {"transform": "scale(1.15)"},
                    },
                ),
                content=t("choose_research_mode"),
            ),
            spacing="2",
            align="center",
            flex_wrap="wrap",
            width="100%",
        ),

        # Row 2: Discussion Mode + Agent Toggles + LLM Parameters
        rx.hstack(
            # Discussion Mode Dropdown
            rx.cond(
                AIState.is_mobile,
                # MOBILE: Native HTML <select>
                native_select_generic(
                    AIState.multi_agent_mode,
                    AIState.set_multi_agent_mode,
                    AIState.multi_agent_mode_options,
                ),
                # DESKTOP: Radix UI Select (dynamic from same options as mobile)
                rx.select.root(
                    rx.select.trigger(height="32px"),
                    rx.select.content(
                        rx.foreach(
                            AIState.multi_agent_mode_options,
                            lambda opt: rx.select.item(opt[1], value=opt[0]),
                        ),
                    ),
                    value=AIState.multi_agent_mode,
                    on_change=AIState.set_multi_agent_mode,
                    size="1",
                ),
            ),
            # Help icon
            rx.tooltip(
                rx.icon(
                    "lightbulb",
                    size=16,
                    color="#FFD700",
                    cursor="pointer",
                    on_click=AIState.open_multi_agent_help,
                    style={
                        "transition": "transform 0.2s ease",
                        "&:hover": {"transform": "scale(1.15)"},
                    },
                ),
                content=t("discussion_mode_tooltip"),
            ),
            # Consensus Type Toggle (nur bei auto_consensus)
            rx.cond(
                AIState.multi_agent_mode == "auto_consensus",
                rx.hstack(
                    rx.text(
                        rx.cond(AIState.is_unanimous_consensus, "3/3", "2/3"),
                        font_size="11px",
                        color=rx.cond(AIState.is_unanimous_consensus, COLORS["primary"], "var(--gray-10)"),
                        font_weight="600",
                        min_width="24px",
                        text_align="right",
                    ),
                    rx.switch(
                        checked=AIState.is_unanimous_consensus,
                        on_change=AIState.toggle_consensus_type,
                        size="1",
                    ),
                    spacing="1",
                    align="center",
                    title=AIState.consensus_toggle_tooltip,
                ),
            ),
            # Incognito toggle (always visible)
            rx.tooltip(
                rx.box(
                    rx.cond(
                        AIState.agent_memory_enabled,
                        rx.icon("lock-open", size=18, cursor="pointer", color="#4CAF50"),
                        rx.icon("lock", size=18, cursor="pointer", color="#888", opacity=0.7),
                    ),
                    on_click=AIState.toggle_agent_memory,
                ),
                content=rx.cond(
                    AIState.agent_memory_enabled,
                    "Gedächtnis aktiv",
                    "Inkognito-Modus (kein Gedächtnis)",
                ),
            ),
            # Vigilantia-Live: rechtsbündig auf der Standard/Lock-Zeile
            # (mehr horizontaler Platz als in Row 1 mit den Pills). Der
            # Spacer schiebt nach rechts; die Card selbst wächst bis zu
            # ihrem max_width (50vw, gedeckelt bei 800px).
            rx.spacer(),
            vigilantia_feed_popover(),
            # Agent toggle buttons (always visible in all modes)
            rx.hstack(
                rx.box(
                    width="1px",
                    height="20px",
                    background=COLORS["border"],
                ),
                rx.foreach(
                    AIState.selectable_agents,
                    _agent_toggle_button,
                ),
                rx.box(flex="1"),
                llm_parameters_accordion(),
                spacing="2",
                align="center",
                flex_wrap="wrap",
                width="100%",
            ),
            spacing="3",
            align="center",
            flex_wrap="wrap",
            width="100%",
        ),

        # Max Debate Rounds (only for auto_consensus/tribunal — compact sub-row)
        rx.cond(
            (AIState.multi_agent_mode == "auto_consensus") | (AIState.multi_agent_mode == "tribunal") | (AIState.multi_agent_mode == "symposion"),
            rx.hstack(
                rx.text(t("max_debate_rounds"), font_size="11px"),
                rx.icon_button(
                    rx.icon("circle-minus", size=16),
                    on_click=AIState.decrease_debate_rounds,
                    size="1",
                    variant="soft",
                    disabled=AIState.max_debate_rounds <= 1,
                    style={"background-color": COLORS["warning_bg"], "color": "#cc8800"},
                ),
                rx.badge(
                    AIState.max_debate_rounds,
                    variant="soft",
                    font_size="11px",
                    font_weight="600",
                    padding_x="8px",
                    padding_y="2px",
                    style={"background-color": "#5d4200", "color": "#cc8800", "min-width": "24px", "text-align": "center"},
                ),
                rx.icon_button(
                    rx.icon("circle-plus", size=16),
                    on_click=AIState.increase_debate_rounds,
                    size="1",
                    variant="soft",
                    disabled=AIState.max_debate_rounds >= 10,
                    style={"background-color": COLORS["warning_bg"], "color": "#cc8800"},
                ),
                spacing="2",
                align="center",
            ),
        ),

        # Processing Progress Banner (above the send button - always visible)
        processing_progress_banner(),

        # Action Buttons
        rx.hstack(
            rx.button(
                rx.icon("send", size=18),
                t("send_text"),
                id="send-button",
                on_click=rx.call_script(
                    "(() => {"
                    " const el = document.getElementById('user-text-input');"
                    " const v = el.value; if (!v.trim()) return '';"
                    " el.value = '';"
                    " return v;"
                    "})()",
                    callback=AIState.send_message,
                ),
                size="2",
                variant="solid",
                loading=AIState.is_generating | AIState.is_compressing | AIState.is_uploading_image,
                disabled=_busy_disabled() | AIState.tts_regenerating,
                flex="2",
                style={
                    "background": "#3d2a00 !important",
                    "color": COLORS["accent_warning"] + " !important",
                    "border": f"1px solid {COLORS['accent_warning']}",
                    "font_weight": "600",
                    "font_size": "14px",
                    "white_space": "nowrap",
                    "&:hover": {
                        "background": "#4d3500 !important",
                        "color": "#ffb84d !important",
                    },
                    "&[disabled]": {"opacity": "0.45"},
                    "&:active": {
                        "background": "#331a00 !important",
                        "color": COLORS["primary_hover"] + " !important",
                        "transform": "scale(0.98)",
                    },
                },
            ),
            # Secondary buttons: icon-only on mobile, icon+text on desktop
            rx.button(
                rx.icon("trash-2", size=16),
                rx.cond(AIState.is_mobile, rx.fragment(), t("clear_chat")),
                on_click=AIState.clear_chat,
                disabled=_busy_disabled(),
                size="2",
                variant="outline",
                color_scheme="orange",
                flex="1",
                style={
                    "background": "rgba(100, 10, 0, 0.4)",
                    "color": "#d98030",
                    "border_color": "#d98030",
                    "&:hover:not([disabled])": {
                        "background": "rgba(150, 15, 0, 0.6) !important",
                        "transform": "scale(1.02)",
                    },
                    "&:active:not([disabled])": {
                        "background": "rgba(80, 5, 0, 0.7) !important",
                        "transform": "scale(0.98)",
                    },
                    "&[disabled]": {"opacity": "0.45"},
                },
            ),
            rx.tooltip(
              rx.button(
                rx.icon("pin", size=16),
                rx.cond(AIState.is_mobile, rx.fragment(), t("save_memory")),
                on_click=AIState.save_session_memory,
                disabled=_busy_disabled(),
                size="2",
                variant="outline",
                color_scheme="green",
                flex="1",
                style={
                    "background": "rgba(0, 80, 30, 0.4)",
                    "color": "#3fb950",
                    "border_color": "#3fb950",
                    "&:hover:not([disabled])": {
                        "background": "rgba(0, 120, 50, 0.6) !important",
                        "transform": "scale(1.02)",
                    },
                    "&:active:not([disabled])": {
                        "background": "rgba(0, 60, 20, 0.7) !important",
                        "transform": "scale(0.98)",
                    },
                    "&[disabled]": {"opacity": "0.45"},
                },
              ),
              content=t("save_memory_hint"),
            ),
            rx.button(
                rx.icon("share-2", size=16),
                rx.cond(AIState.is_mobile, rx.fragment(), t("share_chat")),
                on_click=AIState.share_chat,
                disabled=_busy_disabled(),
                size="2",
                variant="outline",
                color_scheme="blue",
                flex="1",
                style=blue_action_button_style(),
            ),
            rx.button(
                rx.icon("download", size=16),
                rx.cond(AIState.is_mobile, rx.fragment(), t("download_chat")),
                on_click=AIState.download_chat,
                disabled=_busy_disabled(),
                size="2",
                variant="outline",
                color_scheme="blue",
                flex="1",
                style=blue_action_button_style(),
            ),
            spacing="2",
            width="100%",
        ),

        spacing="3",
        width="100%",
        # Kappt den ~2px-Überstand des Hover-scale(1.02) der Buttons —
        # sonst erscheint ein horizontaler Seiten-Scrollbalken.
        #
        # BEWUSST "clip" statt "hidden": Sobald EINE Achse "hidden" ist,
        # macht die CSS-Spezifikation aus der anderen automatisch "auto".
        # Damit wurde dieser Container vertikal scrollbar und der gleiche
        # 2px-Überhang ließ beim Hover einen zweiten, inneren Scrollbalken
        # aufpoppen — der die Zeile verschmälerte, das Layout umbrach und
        # so den Hover beendete: sichtbares Flackern (2026-07-18).
        # "clip" kappt ebenfalls, erzeugt aber KEINEN Scroll-Container,
        # die Y-Achse bleibt "visible".
        overflow_x="clip",
    )


# ============================================================
# DEBUG CONSOLE (Bottom of Page)
# ============================================================


def debug_console() -> rx.Component:
    """Debug console with logs (25 lines like Gradio) - Matrix Terminal Style"""

    # JavaScript-based autoscroll (custom.js) instead of rx.auto_scroll()
    # rx.auto_scroll() breaks during fast State updates (Intent Detection, LLM generation)
    # Using single rx.box ensures scroll position is preserved when toggle is disabled
    debug_content = rx.box(
        rx.foreach(
            AIState.debug_messages,
            lambda msg: rx.text(
                msg,
                font_family="monospace",
                font_size="11px",
                color=rx.cond(
                    msg.contains("\u2717") | msg.contains("\u274c"),  # ✗ or ❌
                    "#ff6b6b",  # Rot für Fehler
                    COLORS["debug_text"],  # Matrix Grün
                ),
                white_space="pre",
            ),
        ),
        id="debug-console-box",
        width="100%",
        overflow_y="auto",
        padding="3",
        background_color=COLORS["debug_bg"],
        border_radius="8px",
        border=f"2px solid {COLORS['debug_border']}",
        style={
            "scroll-behavior": "smooth",
            "max-height": "var(--debug-max-height, 60vh)",
        },
    )

    # Periodic refresh timer for background task state propagation
    # Without this, messages from background tasks (InactivityMonitor) won't appear in UI
    refresh_timer = rx.moment(
        interval=500,  # 500ms — fast enough for Hub/FreeEcho.2 debug messages + ghosting
        on_change=AIState.refresh_debug_console,
        display="none",
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
                        id="autoscroll-switch",
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
                refresh_timer,  # Hidden timer for background task state propagation
                spacing="3",
                width="100%",
            ),
        ),
        collapsible=True,  # WICHTIG: Macht Accordion schließbar!
        default_value="debug",  # Standardmäßig geöffnet
        color_scheme="gray",
        variant="soft",
    )
