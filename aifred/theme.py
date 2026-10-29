"""
Dark Theme Configuration for AIfred Intelligence (Gradio-Style)
"""

# Hybrid Dark Theme - GitHub Professional + Matrix Debug Console
# Delegation to a sub-agent: the task goes out to the right, the report comes
# back to the left. Drawn in COLORS["primary"] wherever it appears (bubble
# transcript title, debug console), so core renders it without knowing the plugin.
DELEGATION_ICON = "⇄"

COLORS = {
    # === BACKGROUNDS (3-level hierarchy) ===
    "page_bg": "#0d1117",        # GitHub Dark (darkest)
    "card_bg": "#161b22",        # GitHub Cards (medium)
    "input_bg": "#21262d",       # GitHub Inputs (lightest)
    "readonly_bg": "#161b22",    # Same as cards

    # Zusätzliche Flächen der Editoren/Modals (dunkler bzw. heller als card_bg)
    "panel_dark": "#1a1a1a",     # Eingelassene Fläche (Listen, Vorschau, Code)
    "panel_raised": "#2a2a2a",   # Erhöhte Fläche (Zeilen, Pillen)

    # === TEXT (4 Stufen, alle ≥ 4,1:1 auf input_bg) ===
    "text_primary": "#e6edf3",   # GitHub Text (almost white)
    "text_soft": "#cccccc",      # Fließtext in Listen und Editoren
    "text_secondary": "#aaaaaa",  # Beschriftungen, Hinweise (7:1 auf card_bg)
    "text_muted": "#7d8590",     # Gedämpft: inaktiv, Nebensächliches (4,6:1 auf card_bg)

    # === ACCENT COLORS ===
    "primary": "#e67700",        # Muted orange (more professional)
    "primary_hover": "#ff9500",  # Brighter orange on hover
    "primary_active": "#cc6a00",  # Darker on click
    "primary_bg": "rgba(230, 119, 0, 0.15)",  # Semi-transparent orange background (15% opacity)
    "accent_blue": "#58a6ff",    # GitHub Blue (for links)
    "accent_success": "#3fb950", # GitHub Green (success)
    "accent_warning": "#d29922", # GitHub Yellow (warning)
    "accent_gold": "#FFD700",    # Glühbirnen und Überschriften der Hilfe-Texte
    "accent_amber": "#FFA500",   # Speed-/Aktiv-Markierung
    "danger": "#f85149",         # GitHub Red (error/delete)

    # === CHAT BUBBLES (subtle distinction) ===
    "user_msg": "#21262d",       # Dark blue-gray - User
    "user_text": "#d1d5db",      # Softer gray (not harsh)
    "ai_msg": "#161b22",         # Even darker - AI
    "ai_text": "#e6edf3",        # Slightly dimmed

    # === BORDERS (GitHub Style) ===
    "border": "#30363d",         # GitHub Border (subtle but visible)
    "border_light": "#484f58",   # Slightly lighter

    # === DEBUG CONSOLE (Matrix/Hacker Terminal) ===
    "debug_bg": "#0d1117",       # Very dark (almost black)
    "debug_text": "#00ff41",     # Matrix neon green
    "debug_border": "#1a4d1a",   # Dark green (subtle)
    "debug_accent": "#00aa00",   # Medium green (for header)

    # === HILFE-TIPPS (Glühbirne: Hover-Tooltip und Klick-Popup; Icon = accent_gold, Beschriftung = text_secondary) ===
    "tip_text": "#e6edf3",       # Tipp-Text (14:1 auf tip_bg)
    "tip_bg": "#1c2128",         # Hintergrund von Tooltip und Popup
    "tip_border": "#484f58",     # Rahmen von Tooltip und Popup

    # === WARNING/INFO ===
    "warning_bg": "#2d1f00",     # Even darker brown (darker than send text button)
    "warning_text": "#d29922",   # GitHub Yellow
}


def css_variables() -> str:
    """``:root``-Block mit einer Variable je COLORS-Eintrag (``--aifred-text-primary``, …).

    custom.css liest die Farben ausschließlich über diese Variablen; geändert wird nur COLORS.
    """
    lines = (f"    --aifred-{name.replace('_', '-')}: {value};" for name, value in COLORS.items())
    return ":root {\n" + "\n".join(lines) + "\n}"
