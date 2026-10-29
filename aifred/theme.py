"""
Dark Theme Configuration for AIfred Intelligence (Gradio-Style)

COLORS ist die einzige Quelle aller Farben. Python liest sie als ``COLORS["name"]``,
CSS/JS/HTML als ``var(--aifred-name)`` (``css_variables()`` erzeugt die Variablen).
Eine neue Farbe gehört hierher, nie als Hex-Wert in den Code.
"""

# Hybrid Dark Theme - GitHub Professional + Matrix Debug Console
# Delegation to a sub-agent: the task goes out to the right, the report comes
# back to the left. Drawn in COLORS["primary"] wherever it appears (bubble
# transcript title, debug console), so core renders it without knowing the plugin.
DELEGATION_ICON = "⇄"

COLORS = {
    # === FLÄCHEN (3 Stufen: Seite < Karte/Panel/Popup < Eingabe/erhöht) ===
    "page_bg": "#0d1117",        # Seite, Debug-Konsole (darkest)
    "card_bg": "#161b22",        # Karten, Chat-Bubble der KI, Popups, eingelassene Flächen
    "input_bg": "#21262d",       # Eingaben, User-Bubble, erhöhte Flächen (Zeilen, Pillen)
    "media_bg": "#000000",       # Hinter Bildern, Videos und Frames

    # === RAHMEN ===
    "border": "#30363d",         # Normaler Rahmen (subtle but visible)
    "border_light": "#484f58",   # Hellerer Rahmen, Popup-Rahmen

    # === TEXT (4 Stufen, alle ≥ 4,1:1 auf input_bg) ===
    "text_primary": "#e6edf3",   # Haupttext (almost white), Tipp-Text
    "text_soft": "#cccccc",      # Fließtext in Listen und Editoren
    "text_secondary": "#aaaaaa",  # Beschriftungen, Hinweise (7:1 auf card_bg)
    "text_muted": "#7d8590",     # Gedämpft: inaktiv, Nebensächliches (4,6:1 auf card_bg)
    "text_on_accent": "#ffffff",  # Text auf farbigen Flächen (Buttons, Badges)

    # === AKZENTE ===
    "primary": "#e67700",        # AIfred-Orange (Marke, Schalter, Hervorhebung)
    "primary_hover": "#ff9500",  # Brighter orange on hover
    "primary_active": "#cc6a00",  # Darker on click
    "primary_bg": "rgba(230, 119, 0, 0.15)",  # Semi-transparent orange background (15% opacity)
    "accent_blue": "#58a6ff",    # Links, Info
    "accent_cyan": "#56d4dd",    # Sekundärer Info-Akzent (Quellen, Audio)
    "accent_purple": "#a371f7",  # Sonderfälle (Vision/Tools)
    "accent_success": "#3fb950", # Erfolg, aktiv
    "accent_warning": "#d29922", # Warnung, gedämpftes Gold
    "accent_gold": "#FFD700",    # Glühbirnen und Überschriften der Hilfe-Texte
    "accent_amber": "#FFA500",   # Speed-/Aktiv-Markierung
    "danger": "#f85149",         # Fehler, Löschen
    "user_name": "#c06050",      # Name des Users im Chat (gedämpftes Rostrot, bewusst nicht danger)

    # === WARNUNGS-FLÄCHEN (braun, 3 Stufen: Grund, Schalter, Hover) ===
    "warning_bg": "#2d1f00",
    "warning_bg_mid": "#3d2a00",
    "warning_bg_hover": "#4d3500",

    # === DEBUG CONSOLE (Matrix/Hacker Terminal) ===
    "debug_text": "#00ff41",     # Matrix neon green
    "debug_border": "#1a4d1a",   # Dark green (subtle)
    "debug_accent": "#00aa00",   # Medium green (for header)
}


def css_variables() -> str:
    """``:root``-Block mit einer Variable je COLORS-Eintrag (``--aifred-text-primary``, …).

    custom.css, JS und HTML-Seiten lesen die Farben ausschließlich über diese Variablen;
    geändert wird nur COLORS.
    """
    lines = (f"    --aifred-{name.replace('_', '-')}: {value};" for name, value in COLORS.items())
    return ":root {\n" + "\n".join(lines) + "\n}"
