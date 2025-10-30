"""
Dark Theme Configuration for AIfred Intelligence (Gradio-Style)
"""

# Hybrid Dark Theme - GitHub Professional + Matrix Debug Console
COLORS = {
    # === BACKGROUNDS (3-Stufen Hierarchie) ===
    "page_bg": "#0d1117",        # GitHub Dark (dunkelster)
    "card_bg": "#161b22",        # GitHub Cards (mittel)
    "input_bg": "#21262d",       # GitHub Inputs (hellster)
    "readonly_bg": "#161b22",    # Wie Cards

    # === TEXT (Hoher Kontrast) ===
    "text_primary": "#e6edf3",   # GitHub Text (fast weiß)
    "text_secondary": "#7d8590",  # GitHub Gray (Labels)
    "text_muted": "#484f58",     # GitHub Muted (Hilfstext)

    # === ACCENT COLORS ===
    "primary": "#e67700",        # Abgeschwächtes Orange (professioneller)
    "primary_hover": "#ff9500",  # Helleres Orange beim Hover
    "primary_active": "#cc6a00",  # Dunkler beim Klick
    "primary_bg": "rgba(230, 119, 0, 0.15)",  # Semi-transparent Orange Background (15% Opacity)
    "accent_blue": "#58a6ff",    # GitHub Blau (für Links)
    "accent_success": "#3fb950", # GitHub Grün (Erfolg)
    "accent_warning": "#d29922", # GitHub Gelb (Warnung)
    "danger": "#f85149",         # GitHub Rot (Fehler/Löschen)

    # === CHAT BUBBLES (Subtile Unterscheidung) ===
    "user_msg": "#21262d",       # Dunkles Blaugrau - User
    "user_text": "#d1d5db",      # Softeres Grau (nicht grell)
    "ai_msg": "#161b22",         # Noch dunkler - AI
    "ai_text": "#e6edf3",        # Leicht gedimmt

    # === BORDERS (GitHub Style) ===
    "border": "#30363d",         # GitHub Border (subtil aber sichtbar)
    "border_light": "#484f58",   # Etwas heller

    # === DEBUG CONSOLE (Matrix/Hacker Terminal) ===
    "debug_bg": "#0d1117",       # Sehr dunkel (fast schwarz)
    "debug_text": "#00ff41",     # Matrix Neon-Grün
    "debug_border": "#1a4d1a",   # Dunkles Grün (subtil)
    "debug_accent": "#00aa00",   # Mittel-Grün (für Header)

    # === WARNING/INFO ===
    "warning_bg": "#2d1f00",     # Noch dunkleres Braun (dunkler als Text senden Button)
    "warning_text": "#d29922",   # GitHub Gelb
}

# Custom CSS for Reflex components
CUSTOM_CSS = """
/* Global dark theme */
body {
    background-color: #0b0f19;
    color: #e2e8f0;
}

/* Override Reflex default card backgrounds */
.rx-Box {
    background-color: transparent;
}

/* Debug console monospace */
.debug-console {
    font-family: 'Courier New', Consolas, monospace !important;
    font-size: 11px !important;
    line-height: 1.4 !important;
    background-color: #1a1a1a !important;
    color: #00ff00 !important;
}

/* Switch Styling - Orange Warning Colors */
.rt-SwitchRoot[data-state="checked"] {
    background-color: #2d1f00 !important;  /* Dunkles Orange Hintergrund */
}

.rt-SwitchRoot[data-state="unchecked"] {
    background-color: #30363d !important;  /* Grau wenn aus */
}

.rt-SwitchThumb {
    background-color: #d29922 !important;  /* Helles Orange für Kreis */
}

/* Scrollbar styling for dark theme */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: #1a202c;
}

::-webkit-scrollbar-thumb {
    background: #4a5568;
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: #718096;
}
"""
