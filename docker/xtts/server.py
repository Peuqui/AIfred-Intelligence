"""
Coqui XTTS v2 HTTP Server for AIfred.

Provides a simple REST API for text-to-speech generation using XTTS v2.
Supports multilingual code-switching (DE/EN mixed text) automatically.

XTTS v2 is a voice cloning model - it requires a reference audio to clone.
We provide built-in speaker embeddings from the model's speaker library,
plus support for custom voice cloning from WAV files.

**Smart Device Selection:**
Automatically detects available VRAM and decides GPU vs CPU:
- If CUDA available AND >= VRAM_THRESHOLD free: use GPU
- Otherwise: use CPU (slower but doesn't compete with Ollama)

Usage:
    POST /tts
    {
        "text": "Das ist quite interessant",
        "language": "de",
        "speaker": "Claribel Dervla"  // or custom voice name
    }
    Returns: OGG/Opus audio file (48kbps, ~90% smaller than WAV)

    POST /voices/clone (multipart/form-data)
    - audio: WAV file (6-10 seconds of speech, mono, 22-24kHz)
    - name: Voice name to save as
    Returns: {"success": true, "voice": "name"}
"""

import os
import tempfile
import logging
from pathlib import Path
from flask import Flask, request, send_file, jsonify, render_template_string

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================================
# VRAM Configuration
# ============================================================
# Minimum free VRAM required to use GPU (in GB)
# XTTS needs ~1.5-2GB base, cache clear after request should keep it low
# TODO: Test actual VRAM usage with cache clearing enabled
VRAM_THRESHOLD_GB = float(os.environ.get("XTTS_VRAM_THRESHOLD", "3.0"))

# Force CPU mode (override auto-detection)
FORCE_CPU = os.environ.get("XTTS_FORCE_CPU", "").lower() in ("1", "true", "yes")

# Eager loading - load model at startup instead of first request
EAGER_LOAD = os.environ.get("XTTS_EAGER_LOAD", "").lower() in ("1", "true", "yes")

# Lazy loading - model loaded on first request
_model_name = "tts_models/multilingual/multi-dataset/xtts_v2"
_config = None
_synthesizer = None
_speaker_embeddings = None  # Pre-loaded speaker embeddings (built-in)
_speaker_names = None  # Just the names (loaded without model for /voices endpoint)
_custom_voices = {}  # Custom cloned voices
_device = None  # "cuda" or "cpu" - set on model load

# Auto-shutdown configuration (like Ollama's KEEP_ALIVE)
# After KEEP_ALIVE_MINUTES of inactivity, container exits to free ALL VRAM
# (including ~167 MB CUDA context that prevents GPU P8 power state)
# Docker restart policy "unless-stopped" brings it back on next request
# Set to 0 to disable auto-shutdown
KEEP_ALIVE_MINUTES = int(os.environ.get("XTTS_KEEP_ALIVE", "5"))
_last_request_time = None  # Timestamp of last TTS request
_restart_timer = None  # Background timer for auto-shutdown
_active_requests = 0  # Counter for in-flight requests

# Paths
CUSTOM_VOICES_DIR = Path("/app/custom_voices")  # Persistent storage for embeddings
REFERENCE_AUDIO_DIR = Path("/app/voices")  # Reference WAV files (mounted from host)

# Default speaker from XTTS speaker library
DEFAULT_SPEAKER = "Claribel Dervla"

# XTTS has a hard limit of 400 tokens (~250-350 characters depending on language)
# We use a conservative limit to stay safely within the token limit
MAX_CHUNK_CHARS = int(os.environ.get("XTTS_MAX_CHUNK_CHARS", "250"))

# ============================================================
# Inference Parameters (configurable via environment variables)
# ============================================================
# These can be tuned to reduce hallucinations/repetitions
# See: https://github.com/coqui-ai/TTS/discussions/4146
#
# Lower temperature = more stable, less creative
# Higher repetition_penalty = prevents repeating
# Lower top_k/top_p = more deterministic output

XTTS_TEMPERATURE = float(os.environ.get("XTTS_TEMPERATURE", "0.65"))
XTTS_REPETITION_PENALTY = float(os.environ.get("XTTS_REPETITION_PENALTY", "15.0"))
XTTS_LENGTH_PENALTY = float(os.environ.get("XTTS_LENGTH_PENALTY", "1.0"))
XTTS_TOP_K = int(os.environ.get("XTTS_TOP_K", "30"))
XTTS_TOP_P = float(os.environ.get("XTTS_TOP_P", "0.75"))

logger.info(f"XTTS Inference Parameters: temperature={XTTS_TEMPERATURE}, "
            f"repetition_penalty={XTTS_REPETITION_PENALTY}, length_penalty={XTTS_LENGTH_PENALTY}, "
            f"top_k={XTTS_TOP_K}, top_p={XTTS_TOP_P}")


def normalize_text_for_tts(text: str, language: str = "de") -> str:
    """
    Minimal XTTS-specific text normalization (language-aware).

    This function handles ONLY what's necessary for clean XTTS output:
    - Convert laughter emojis to "hahaha" (sounds natural)
    - Remove other emojis (unpronounceable)
    - Remove special characters that cause "quirzel" sounds
    - Ensure proper punctuation for natural pauses (prevents hallucinations)
    - Replace colons with periods (colons cause rushed speech)

    Content filtering (Markdown, code blocks, tables, <think> tags, etc.)
    is the responsibility of the client (e.g., AIfred's clean_text_for_tts).

    Based on community findings:
    - https://github.com/coqui-ai/TTS/discussions/4146
    - https://huggingface.co/coqui/XTTS-v2/discussions/104

    Args:
        text: Text input (should already be content-filtered by client)

    Returns:
        Normalized text suitable for XTTS synthesis
    """
    import re

    if not text or not text.strip():
        return text

    text = text.strip()

    # ============================================================
    # Phase 1: Emoji handling
    # ============================================================

    # Convert laughter emojis to "hahaha" (tested, sounds natural)
    laughter_emojis = ['😂', '🤣', '😆', '😄', '😅', '😁', '🙂', '😊']
    for emoji in laughter_emojis:
        text = text.replace(emoji, ' hahaha ')

    # Remove all other emojis (unpronounceable)
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"  # dingbats
        "\U000024C2-\U0001F251"  # enclosed characters
        "\U0001F900-\U0001F9FF"  # supplemental symbols
        "\U0001FA00-\U0001FA6F"  # chess symbols
        "\U0001FA70-\U0001FAFF"  # symbols extended
        "\U00002600-\U000026FF"  # misc symbols
        "\U00002700-\U000027BF"  # dingbats
        "]+",
        flags=re.UNICODE
    )
    text = emoji_pattern.sub('', text)

    # ============================================================
    # Phase 1.5: Handle special Unicode characters BEFORE whitelist
    # This is "Defense in Depth" - catches anything AIfred might miss
    # ============================================================

    # Box-drawing characters (═ ─ │ etc.) - decorative lines that cause "quirzel"
    # These are often used for ASCII art borders around headings
    # Remove completely - they have no spoken equivalent
    text = re.sub(r'[─━═┄┅┈┉╌╍┼┽┾┿╀╁╂╃╄╅╆╇╈╉╊╋│┃║┆┇┊┋╎╏]+', '', text)

    # En-dash, Em-dash, Figure-dash, Horizontal bar → comma (creates pause)
    # "Text – mehr Text" → "Text, mehr Text" (natural speech pause)
    text = text.replace('–', ',')  # EN DASH (U+2013)
    text = text.replace('—', ',')  # EM DASH (U+2014)
    text = text.replace('‒', ',')  # FIGURE DASH (U+2012)
    text = text.replace('―', ',')  # HORIZONTAL BAR (U+2015)

    # Markdown formatting (in case client didn't filter it)
    text = re.sub(r'\*\*', '', text)  # Bold **text**
    text = re.sub(r'\*', '', text)    # Italic *text*
    text = re.sub(r'`[^`]*`', '', text)  # Inline code `code`
    text = re.sub(r'`', '', text)     # Stray backticks
    text = re.sub(r'#+\s*', '', text)  # Markdown headers ###

    # Zero-width characters (invisible but cause issues)
    text = re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]', '', text)
    text = text.replace('\u00a0', ' ')  # Non-breaking space → normal space

    # ============================================================
    # Phase 1.6: Convert symbols to speakable text (language-aware)
    # ============================================================

    # Temperature units (universal, but spoken differently)
    if language == "de":
        text = re.sub(r'°C\b', ' Grad Celsius', text)
        text = re.sub(r'°F\b', ' Grad Fahrenheit', text)
    else:  # English and others
        text = re.sub(r'°C\b', ' degrees Celsius', text)
        text = re.sub(r'°F\b', ' degrees Fahrenheit', text)

    # Minus before numbers: -5 → "minus 5" (works for DE and EN)
    text = re.sub(r'(?<![a-zA-Z0-9])[-−](\d)', r'minus \1', text)

    # Paragraph symbol (German legal notation)
    if language == "de":
        text = re.sub(r'§\s*(\d)', r'Paragraph \1', text)
        text = text.replace('§', 'Paragraph ')
    else:
        text = re.sub(r'§\s*(\d)', r'section \1', text)
        text = text.replace('§', 'section ')

    # Currencies - MUST come BEFORE decimal separator conversion!
    # Natural speech: "19 dollars 99" not "19.99 dollars"
    if language == "de":
        # Euro: 13,50€ → "13 Euro 50" (with decimal), 13€ → "13 Euro" (without)
        text = re.sub(r'(\d+),(\d+)\s*€', r'\1 Euro \2', text)
        text = re.sub(r'(\d+)\s*€', r'\1 Euro', text)
        text = text.replace('€', ' Euro ')
        # Dollar: $25 or 25$
        text = re.sub(r'\$\s*(\d+),(\d+)', r'\1 Dollar \2', text)
        text = re.sub(r'\$\s*(\d+)', r'\1 Dollar', text)
        text = re.sub(r'(\d+),(\d+)\s*\$', r'\1 Dollar \2', text)
        text = re.sub(r'(\d+)\s*\$', r'\1 Dollar', text)
        text = text.replace('$', ' Dollar ')
        # Pfund
        text = re.sub(r'£\s*(\d+),(\d+)', r'\1 Pfund \2', text)
        text = re.sub(r'£\s*(\d+)', r'\1 Pfund', text)
        text = re.sub(r'(\d+),(\d+)\s*£', r'\1 Pfund \2', text)
        text = re.sub(r'(\d+)\s*£', r'\1 Pfund', text)
        text = text.replace('£', ' Pfund ')
    else:  # English
        # Euro: €19.99 → "19 euros 99"
        text = re.sub(r'€\s*(\d+)\.(\d+)', r'\1 euros \2', text)
        text = re.sub(r'€\s*(\d+)', r'\1 euros', text)
        text = re.sub(r'(\d+)\.(\d+)\s*€', r'\1 euros \2', text)
        text = re.sub(r'(\d+)\s*€', r'\1 euros', text)
        text = text.replace('€', ' euros ')
        # Dollar: $19.99 → "19 dollars 99"
        text = re.sub(r'\$\s*(\d+)\.(\d+)', r'\1 dollars \2', text)
        text = re.sub(r'\$\s*(\d+)', r'\1 dollars', text)
        text = re.sub(r'(\d+)\.(\d+)\s*\$', r'\1 dollars \2', text)
        text = re.sub(r'(\d+)\s*\$', r'\1 dollars', text)
        text = text.replace('$', ' dollars ')
        # Pound: £19.99 → "19 pounds 99"
        text = re.sub(r'£\s*(\d+)\.(\d+)', r'\1 pounds \2', text)
        text = re.sub(r'£\s*(\d+)', r'\1 pounds', text)
        text = re.sub(r'(\d+)\.(\d+)\s*£', r'\1 pounds \2', text)
        text = re.sub(r'(\d+)\s*£', r'\1 pounds', text)
        text = text.replace('£', ' pounds ')

    # Decimal separator - AFTER currencies (so "13,50€" → "13 Euro 50", not "13 Komma 50 Euro")
    if language == "de":
        # German: comma is decimal → 3,14 → "3 Komma 1 4" (digits read separately)
        def split_decimal_de(match):
            whole = match.group(1)
            decimal = ' '.join(match.group(2))  # "14" → "1 4"
            return f"{whole} Komma {decimal}"
        text = re.sub(r'(\d+),(\d+)', split_decimal_de, text)
    # English: period is decimal, XTTS handles "point" natively (one four, not fourteen)

    # SI units with superscripts (language-specific)
    # Use Unicode escapes: ² = \u00b2, ³ = \u00b3, µ = \u00b5
    # Order matters: longer prefixes first (km before m, etc.)
    if language == "de":
        # Area (Quadrat-)
        text = re.sub(r'(\d+)\s*km[\u00b2²]', r'\1 Quadratkilometer', text)
        text = re.sub(r'(\d+)\s*cm[\u00b2²]', r'\1 Quadratzentimeter', text)
        text = re.sub(r'(\d+)\s*mm[\u00b2²]', r'\1 Quadratmillimeter', text)
        text = re.sub(r'(\d+)\s*dm[\u00b2²]', r'\1 Quadratdezimeter', text)
        text = re.sub(r'(\d+)\s*m[\u00b2²]', r'\1 Quadratmeter', text)
        # Volume (Kubik-)
        text = re.sub(r'(\d+)\s*km[\u00b3³]', r'\1 Kubikkilometer', text)
        text = re.sub(r'(\d+)\s*cm[\u00b3³]', r'\1 Kubikzentimeter', text)
        text = re.sub(r'(\d+)\s*mm[\u00b3³]', r'\1 Kubikmillimeter', text)
        text = re.sub(r'(\d+)\s*dm[\u00b3³]', r'\1 Kubikdezimeter', text)
        text = re.sub(r'(\d+)\s*m[\u00b3³]', r'\1 Kubikmeter', text)
        # Micro units (µ = \u00b5 or μ = \u03bc)
        text = re.sub(r'(\d+)\s*[\u00b5\u03bc]m\b', r'\1 Mikrometer', text)
        text = re.sub(r'(\d+)\s*[\u00b5\u03bc]l\b', r'\1 Mikroliter', text)
        text = re.sub(r'(\d+)\s*[\u00b5\u03bc]g\b', r'\1 Mikrogramm', text)
        # Milli/Nano units
        text = re.sub(r'(\d+)\s*nm\b', r'\1 Nanometer', text)
        text = re.sub(r'(\d+)\s*ml\b', r'\1 Milliliter', text)
        text = re.sub(r'(\d+)\s*mg\b', r'\1 Milligramm', text)
        text = re.sub(r'(\d+)\s*dl\b', r'\1 Deziliter', text)
        # Speed units
        text = re.sub(r'(\d+)\s*km/h\b', r'\1 Kilometer pro Stunde', text)
        text = re.sub(r'(\d+)\s*m/s\b', r'\1 Meter pro Sekunde', text)
        text = re.sub(r'(\d+)\s*mph\b', r'\1 Meilen pro Stunde', text)
        text = re.sub(r'(\d+)\s*kn\b', r'\1 Knoten', text)
        text = re.sub(r'(\d+)\s*kts\b', r'\1 Knoten', text)
        # Precipitation (weather)
        text = re.sub(r'(\d+)\s*l/m[\u00b2²]', r'\1 Liter pro Quadratmeter', text)
        text = re.sub(r'(\d+)\s*mm/m[\u00b2²]', r'\1 Millimeter pro Quadratmeter', text)
        # Pressure
        text = re.sub(r'(\d+)\s*hPa\b', r'\1 Hektopascal', text)
        text = re.sub(r'(\d+)\s*mbar\b', r'\1 Millibar', text)
        text = re.sub(r'(\d+)\s*bar\b', r'\1 Bar', text)
        text = re.sub(r'(\d+)\s*psi\b', r'\1 Pfund pro Quadratzoll', text)
        text = re.sub(r'(\d+)\s*kPa\b', r'\1 Kilopascal', text)
        text = re.sub(r'(\d+)\s*MPa\b', r'\1 Megapascal', text)
        # Energy/Power (order: larger prefixes first)
        text = re.sub(r'(\d+)\s*GWh\b', r'\1 Gigawattstunden', text)
        text = re.sub(r'(\d+)\s*MWh\b', r'\1 Megawattstunden', text)
        text = re.sub(r'(\d+)\s*kWh\b', r'\1 Kilowattstunden', text)
        text = re.sub(r'(\d+)\s*Wh\b', r'\1 Wattstunden', text)
        text = re.sub(r'(\d+)\s*GW\b', r'\1 Gigawatt', text)
        text = re.sub(r'(\d+)\s*MW\b', r'\1 Megawatt', text)
        text = re.sub(r'(\d+)\s*kW\b', r'\1 Kilowatt', text)
        text = re.sub(r'(\d+)\s*MJ\b', r'\1 Megajoule', text)
        text = re.sub(r'(\d+)\s*kJ\b', r'\1 Kilojoule', text)
        # Data sizes (order: larger first)
        text = re.sub(r'(\d+)\s*PB\b', r'\1 Petabyte', text)
        text = re.sub(r'(\d+)\s*TB\b', r'\1 Terabyte', text)
        text = re.sub(r'(\d+)\s*GB\b', r'\1 Gigabyte', text)
        text = re.sub(r'(\d+)\s*MB\b', r'\1 Megabyte', text)
        text = re.sub(r'(\d+)\s*KB\b', r'\1 Kilobyte', text)
        # Data rates
        text = re.sub(r'(\d+)\s*Gbit/s\b', r'\1 Gigabit pro Sekunde', text)
        text = re.sub(r'(\d+)\s*Mbit/s\b', r'\1 Megabit pro Sekunde', text)
        text = re.sub(r'(\d+)\s*kbit/s\b', r'\1 Kilobit pro Sekunde', text)
        text = re.sub(r'(\d+)\s*GB/s\b', r'\1 Gigabyte pro Sekunde', text)
        text = re.sub(r'(\d+)\s*MB/s\b', r'\1 Megabyte pro Sekunde', text)
        # Frequency
        text = re.sub(r'(\d+)\s*GHz\b', r'\1 Gigahertz', text)
        text = re.sub(r'(\d+)\s*MHz\b', r'\1 Megahertz', text)
        text = re.sub(r'(\d+)\s*kHz\b', r'\1 Kilohertz', text)
        text = re.sub(r'(\d+)\s*Hz\b', r'\1 Hertz', text)
        # Sound/Other
        text = re.sub(r'(\d+)\s*dB\b', r'\1 Dezibel', text)
        text = re.sub(r'\bCO[\u2082₂]?\b', 'CO2', text)  # Normalize CO₂ to CO2 (XTTS reads it)
        # Generic superscripts (must be last)
        text = re.sub(r'(\d+)\s*[\u00b2²]', r'\1 hoch zwei', text)
        text = re.sub(r'(\d+)\s*[\u00b3³]', r'\1 hoch drei', text)
    else:  # English
        # Area (square)
        text = re.sub(r'(\d+)\s*km[\u00b2²]', r'\1 square kilometers', text)
        text = re.sub(r'(\d+)\s*cm[\u00b2²]', r'\1 square centimeters', text)
        text = re.sub(r'(\d+)\s*mm[\u00b2²]', r'\1 square millimeters', text)
        text = re.sub(r'(\d+)\s*dm[\u00b2²]', r'\1 square decimeters', text)
        text = re.sub(r'(\d+)\s*m[\u00b2²]', r'\1 square meters', text)
        # Volume (cubic)
        text = re.sub(r'(\d+)\s*km[\u00b3³]', r'\1 cubic kilometers', text)
        text = re.sub(r'(\d+)\s*cm[\u00b3³]', r'\1 cubic centimeters', text)
        text = re.sub(r'(\d+)\s*mm[\u00b3³]', r'\1 cubic millimeters', text)
        text = re.sub(r'(\d+)\s*dm[\u00b3³]', r'\1 cubic decimeters', text)
        text = re.sub(r'(\d+)\s*m[\u00b3³]', r'\1 cubic meters', text)
        # Micro units
        text = re.sub(r'(\d+)\s*[\u00b5\u03bc]m\b', r'\1 micrometers', text)
        text = re.sub(r'(\d+)\s*[\u00b5\u03bc]l\b', r'\1 microliters', text)
        text = re.sub(r'(\d+)\s*[\u00b5\u03bc]g\b', r'\1 micrograms', text)
        # Milli/Nano units
        text = re.sub(r'(\d+)\s*nm\b', r'\1 nanometers', text)
        text = re.sub(r'(\d+)\s*ml\b', r'\1 milliliters', text)
        text = re.sub(r'(\d+)\s*mg\b', r'\1 milligrams', text)
        text = re.sub(r'(\d+)\s*dl\b', r'\1 deciliters', text)
        # Speed units
        text = re.sub(r'(\d+)\s*km/h\b', r'\1 kilometers per hour', text)
        text = re.sub(r'(\d+)\s*m/s\b', r'\1 meters per second', text)
        text = re.sub(r'(\d+)\s*mph\b', r'\1 miles per hour', text)
        text = re.sub(r'(\d+)\s*kn\b', r'\1 knots', text)
        text = re.sub(r'(\d+)\s*kts\b', r'\1 knots', text)
        # Precipitation (weather)
        text = re.sub(r'(\d+)\s*l/m[\u00b2²]', r'\1 liters per square meter', text)
        text = re.sub(r'(\d+)\s*mm/m[\u00b2²]', r'\1 millimeters per square meter', text)
        # Pressure
        text = re.sub(r'(\d+)\s*hPa\b', r'\1 hectopascals', text)
        text = re.sub(r'(\d+)\s*mbar\b', r'\1 millibars', text)
        text = re.sub(r'(\d+)\s*bar\b', r'\1 bar', text)
        text = re.sub(r'(\d+)\s*psi\b', r'\1 pounds per square inch', text)
        text = re.sub(r'(\d+)\s*kPa\b', r'\1 kilopascals', text)
        text = re.sub(r'(\d+)\s*MPa\b', r'\1 megapascals', text)
        # Energy/Power (order: larger prefixes first)
        text = re.sub(r'(\d+)\s*GWh\b', r'\1 gigawatt hours', text)
        text = re.sub(r'(\d+)\s*MWh\b', r'\1 megawatt hours', text)
        text = re.sub(r'(\d+)\s*kWh\b', r'\1 kilowatt hours', text)
        text = re.sub(r'(\d+)\s*Wh\b', r'\1 watt hours', text)
        text = re.sub(r'(\d+)\s*GW\b', r'\1 gigawatts', text)
        text = re.sub(r'(\d+)\s*MW\b', r'\1 megawatts', text)
        text = re.sub(r'(\d+)\s*kW\b', r'\1 kilowatts', text)
        text = re.sub(r'(\d+)\s*MJ\b', r'\1 megajoules', text)
        text = re.sub(r'(\d+)\s*kJ\b', r'\1 kilojoules', text)
        # Data sizes (order: larger first)
        text = re.sub(r'(\d+)\s*PB\b', r'\1 petabytes', text)
        text = re.sub(r'(\d+)\s*TB\b', r'\1 terabytes', text)
        text = re.sub(r'(\d+)\s*GB\b', r'\1 gigabytes', text)
        text = re.sub(r'(\d+)\s*MB\b', r'\1 megabytes', text)
        text = re.sub(r'(\d+)\s*KB\b', r'\1 kilobytes', text)
        # Data rates
        text = re.sub(r'(\d+)\s*Gbit/s\b', r'\1 gigabits per second', text)
        text = re.sub(r'(\d+)\s*Mbit/s\b', r'\1 megabits per second', text)
        text = re.sub(r'(\d+)\s*kbit/s\b', r'\1 kilobits per second', text)
        text = re.sub(r'(\d+)\s*GB/s\b', r'\1 gigabytes per second', text)
        text = re.sub(r'(\d+)\s*MB/s\b', r'\1 megabytes per second', text)
        # Frequency
        text = re.sub(r'(\d+)\s*GHz\b', r'\1 gigahertz', text)
        text = re.sub(r'(\d+)\s*MHz\b', r'\1 megahertz', text)
        text = re.sub(r'(\d+)\s*kHz\b', r'\1 kilohertz', text)
        text = re.sub(r'(\d+)\s*Hz\b', r'\1 hertz', text)
        # Sound/Other
        text = re.sub(r'(\d+)\s*dB\b', r'\1 decibels', text)
        text = re.sub(r'\bCO[\u2082₂]?\b', 'CO2', text)  # Normalize CO₂ to CO2 (XTTS reads it)
        # Generic superscripts (must be last)
        text = re.sub(r'(\d+)\s*[\u00b2²]', r'\1 squared', text)
        text = re.sub(r'(\d+)\s*[\u00b3³]', r'\1 cubed', text)

    # ============================================================
    # Phase 2: Remove characters that cause "quirzel" sounds
    # ============================================================

    # Keep basic punctuation and letters (including German/European/Nordic chars)
    # Allow: letters, numbers, basic punctuation, spaces
    # Symbols XTTS handles natively: ° % &
    # All other symbols are converted to text in Phase 1.6
    text = re.sub(r'[^\w\s.,!?;:\-\'\"()\[\]äöüÄÖÜßàáâãèéêëìíîïòóôõùúûýÿñçÀÁÂÃÈÉÊËÌÍÎÏÒÓÔÕÙÚÛÝŸÑÇåæøÅÆØ°−/%&\n]', ' ', text)

    # Clean up multiple spaces
    text = re.sub(r'  +', ' ', text)

    # Fix ordinal number issue: "9." is read as "neunte" (ninth)
    # Add space between digit and period: "9." → "9 ."
    text = re.sub(r'(\d)\.', r'\1 .', text)

    # Final cleanup: remove any multiple spaces that may have accumulated
    text = re.sub(r'  +', ' ', text)

    # ============================================================
    # Phase 3: Ensure proper punctuation for natural pauses
    # ============================================================

    # Replace colons for natural pauses in speech
    # Preserves time formats (19:20) and URLs (https://)
    # Colon at end of line/text → period (clear sentence ending, prevents hallucination)
    # Colon mid-sentence → comma (brief pause)
    text = re.sub(r'(?<!\d):\s*$', '.', text, flags=re.MULTILINE)
    text = re.sub(r'(?<!\d):(?!\d|//)', ',', text)

    # Process lines - add punctuation where missing
    lines = text.split('\n')
    normalized_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            normalized_lines.append('')
            continue

        # Skip lines with only punctuation (no actual words)
        # This catches decorative lines that were filtered to just "." or similar
        if not re.search(r'[a-zA-ZäöüÄÖÜß0-9]', line):
            logger.debug(f"Skipping punctuation-only line: '{line}'")
            continue

        # Add period if line ends without sentence-ending punctuation
        # This prevents XTTS hallucinations
        if not re.search(r'[.!?]["\'\)\]»"]*$', line):
            # If line ends with digit, add space before period to prevent ordinal reading
            # "9." would be read as "neunte" (ninth), "9 ." is read as "neun"
            if line and line[-1].isdigit():
                line = line + ' .'
            else:
                line = line + '.'
            logger.debug(f"Added '.' to line: '{line}'")

        normalized_lines.append(line)

    result = '\n'.join(normalized_lines)

    # Final check: ensure text ends with proper punctuation
    if result and not re.search(r'[.!?]["\'\)\]»"]*$', result):
        result = result + '.'

    return result.strip()


def split_text_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """
    Split text into chunks that fit within XTTS token limit.

    XTTS has a hard limit of 400 tokens per generation.
    We split by sentences first, then by clauses if needed.

    Args:
        text: The text to split
        max_chars: Maximum characters per chunk (default: 250, conservative for token limit)

    Returns:
        List of text chunks
    """
    import re

    # If text is already short enough, return as-is
    if len(text) <= max_chars:
        return [text]

    chunks = []

    # First, split by sentence-ending punctuation
    # Match: . ! ? followed by space or end of string
    # Also handle German quotation marks: »« „"
    raw_sentences = re.split(r'(?<=[.!?])\s+', text)

    # Post-process: Merge ordinal numbers (22. Januar) that were incorrectly split
    sentences = []
    i = 0
    while i < len(raw_sentences):
        sentence = raw_sentences[i]

        # Check if sentence ends with ordinal number (1-3 digits + period)
        # and next sentence starts with uppercase word (month, noun, etc.)
        if i + 1 < len(raw_sentences) and re.search(r'\d{1,3}\.$', sentence):
            next_sentence = raw_sentences[i + 1]
            # Common German month names and continuation patterns
            if re.match(r'^[A-ZÄÖÜ]', next_sentence):
                # Likely an ordinal number continuation - merge them
                sentence = sentence + ' ' + next_sentence
                i += 1  # Skip next sentence since we merged it
                logger.debug(f"Merged ordinal: '{sentence}'")

        sentences.append(sentence)
        i += 1

    current_chunk = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # If this sentence alone is too long, split by comma/semicolon
        if len(sentence) > max_chars:
            # Save current chunk if any
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""

            # Split long sentence by clauses (comma, semicolon, colon, dash)
            clauses = re.split(r'(?<=[,;:\-–—])\s+', sentence)

            for clause in clauses:
                clause = clause.strip()
                if not clause:
                    continue

                # If even a clause is too long, force-split by words
                if len(clause) > max_chars:
                    words = clause.split()
                    temp_chunk = ""
                    for word in words:
                        if len(temp_chunk) + len(word) + 1 <= max_chars:
                            temp_chunk = f"{temp_chunk} {word}".strip()
                        else:
                            if temp_chunk:
                                chunks.append(temp_chunk)
                            temp_chunk = word
                    if temp_chunk:
                        # Don't append yet, might combine with next clause
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = temp_chunk
                else:
                    # Clause fits, try to combine
                    if len(current_chunk) + len(clause) + 1 <= max_chars:
                        current_chunk = f"{current_chunk} {clause}".strip()
                    else:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = clause
        else:
            # Sentence fits within limit
            if len(current_chunk) + len(sentence) + 1 <= max_chars:
                current_chunk = f"{current_chunk} {sentence}".strip()
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence

    # Don't forget the last chunk
    if current_chunk:
        chunks.append(current_chunk.strip())

    # Filter out empty chunks
    chunks = [c for c in chunks if c.strip()]

    logger.info(f"Split text ({len(text)} chars) into {len(chunks)} chunks")
    for i, chunk in enumerate(chunks):
        logger.debug(f"  Chunk {i+1}: {len(chunk)} chars - '{chunk[:50]}...'")

    return chunks


def concatenate_audio_arrays(audio_arrays: list, sample_rate: int = 24000) -> "numpy.ndarray":  # noqa: F821
    """
    Concatenate multiple audio arrays into one.

    Adds a small pause between chunks for natural speech.

    Args:
        audio_arrays: List of numpy arrays containing audio samples
        sample_rate: Sample rate (default: 24000 for XTTS)

    Returns:
        Single numpy array with all audio concatenated
    """
    import numpy as np

    if not audio_arrays:
        return np.array([], dtype=np.float32)

    if len(audio_arrays) == 1:
        return audio_arrays[0]

    # Add a small pause between chunks (100ms silence)
    pause_samples = int(sample_rate * 0.1)  # 100ms
    pause = np.zeros(pause_samples, dtype=np.float32)

    result = []
    for i, audio in enumerate(audio_arrays):
        result.append(audio)
        # Add pause between chunks (but not after the last one)
        if i < len(audio_arrays) - 1:
            result.append(pause)

    return np.concatenate(result)


def get_gpu_memory_info() -> dict:
    """Get GPU memory information using nvidia-smi or torch."""
    import torch

    if not torch.cuda.is_available():
        return {"available": False, "reason": "CUDA not available"}

    try:
        # Get memory info from torch
        device_id = 0
        total = torch.cuda.get_device_properties(device_id).total_memory
        allocated = torch.cuda.memory_allocated(device_id)
        reserved = torch.cuda.memory_reserved(device_id)

        # Free = total - reserved (reserved includes allocated + cached)
        free = total - reserved

        return {
            "available": True,
            "device_name": torch.cuda.get_device_name(device_id),
            "total_gb": round(total / (1024**3), 2),
            "allocated_gb": round(allocated / (1024**3), 2),
            "reserved_gb": round(reserved / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
        }
    except Exception as e:
        return {"available": False, "reason": str(e)}


def check_system_vram() -> dict:
    """
    Check available VRAM across all GPUs using nvidia-smi.
    This gives us the real system-wide free VRAM, not just PyTorch's view.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )

        if result.returncode != 0:
            return {"available": False, "reason": "nvidia-smi failed"}

        gpus = []
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "total_mb": int(parts[2]),
                    "used_mb": int(parts[3]),
                    "free_mb": int(parts[4]),
                    "free_gb": round(int(parts[4]) / 1024, 2),
                })

        return {"available": True, "gpus": gpus}
    except Exception as e:
        return {"available": False, "reason": str(e)}


def select_device() -> str:
    """
    Intelligently select GPU or CPU based on available VRAM.

    Returns "cuda" or "cpu"
    """
    if FORCE_CPU:
        logger.info("🔧 FORCE_CPU enabled - using CPU")
        return "cpu"

    # Check system VRAM via nvidia-smi (more accurate than torch)
    vram_info = check_system_vram()

    if not vram_info.get("available"):
        logger.info(f"🔧 No GPU available ({vram_info.get('reason', 'unknown')}) - using CPU")
        return "cpu"

    # Find GPU with most free VRAM (in case of multi-GPU)
    gpus = vram_info.get("gpus", [])
    if not gpus:
        logger.info("🔧 No GPUs found - using CPU")
        return "cpu"

    # Use GPU 0 (as configured in docker-compose)
    gpu = gpus[0]
    free_gb = gpu["free_gb"]

    logger.info(f"🔍 GPU 0 ({gpu['name']}): {free_gb:.2f} GB free / {gpu['total_mb']/1024:.1f} GB total")

    if free_gb >= VRAM_THRESHOLD_GB:
        logger.info(f"✅ Sufficient VRAM ({free_gb:.2f} GB >= {VRAM_THRESHOLD_GB} GB) - using GPU")
        return "cuda"
    else:
        logger.info(f"⚠️ Insufficient VRAM ({free_gb:.2f} GB < {VRAM_THRESHOLD_GB} GB) - using CPU")
        return "cpu"


def ensure_speaker_names_loaded():
    """Load speaker names without loading the full model (for /voices endpoint)."""
    global _speaker_names
    if _speaker_names is not None:
        return  # Already loaded

    import torch
    from TTS.utils.manage import ModelManager

    logger.info("Loading speaker names (lightweight, no model load)...")

    # Get model path
    manager = ModelManager()
    model_path, _, _ = manager.download_model(_model_name)

    # Load just the speaker names from the embeddings file
    speaker_file = Path(model_path) / "speakers_xtts.pth"
    if speaker_file.exists():
        # Load embeddings temporarily just to get the keys
        embeddings = torch.load(speaker_file, map_location="cpu")
        _speaker_names = sorted(embeddings.keys())
        del embeddings  # Free memory immediately
        logger.info(f"Loaded {len(_speaker_names)} built-in speaker names")
    else:
        _speaker_names = []
        logger.warning("No built-in speaker embeddings found!")


def get_synthesizer():
    """Load XTTS synthesizer on first request (lazy loading)."""
    global _synthesizer, _config, _speaker_embeddings, _device
    if _synthesizer is None:
        logger.info("Loading XTTS v2 model (first request)...")
        import torch
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts
        from TTS.utils.manage import ModelManager

        # Select device based on available VRAM
        _device = select_device()
        logger.info(f"🎯 Selected device: {_device.upper()}")

        # Get model path
        manager = ModelManager()
        model_path, _, _ = manager.download_model(_model_name)

        logger.info(f"Model path: {model_path}")

        # Config is in the model directory
        config_path = Path(model_path) / "config.json"

        # Load config
        _config = XttsConfig()
        _config.load_json(str(config_path))

        # Load model
        _synthesizer = Xtts.init_from_config(_config)

        # Load checkpoint with appropriate device
        if _device == "cuda":
            _synthesizer.load_checkpoint(_config, checkpoint_dir=model_path, eval=True, use_deepspeed=False)
            _synthesizer.cuda()
        else:
            # CPU mode - load without CUDA
            _synthesizer.load_checkpoint(_config, checkpoint_dir=model_path, eval=True, use_deepspeed=False)
            _synthesizer.cpu()

        # Load speaker embeddings from the speaker library
        speaker_file = Path(model_path) / "speakers_xtts.pth"
        if speaker_file.exists():
            _speaker_embeddings = torch.load(speaker_file, map_location=_device)
            logger.info(f"Loaded {len(_speaker_embeddings)} built-in speakers")
        else:
            _speaker_embeddings = {}
            logger.warning("No built-in speaker embeddings found!")

        # Load custom voices from persistent storage
        load_custom_voices()

        # Auto-generate embeddings from reference audio directory
        auto_generate_voice_embeddings()

        logger.info(f"✅ XTTS v2 model loaded successfully on {_device.upper()}")
    return _synthesizer


def load_custom_voices():
    """Load custom voice embeddings from persistent storage."""
    global _custom_voices
    import torch

    CUSTOM_VOICES_DIR.mkdir(parents=True, exist_ok=True)

    for pth_file in CUSTOM_VOICES_DIR.glob("*.pth"):
        voice_name = pth_file.stem
        try:
            # Load to the selected device
            _custom_voices[voice_name] = torch.load(pth_file, map_location=_device)
            logger.info(f"Loaded custom voice: {voice_name}")
        except Exception as e:
            logger.error(f"Failed to load custom voice {voice_name}: {e}")

    logger.info(f"Loaded {len(_custom_voices)} custom voices")


def auto_generate_voice_embeddings():
    """Generate embeddings for WAV files in the reference audio directory."""
    global _custom_voices
    import torch

    if not REFERENCE_AUDIO_DIR.exists():
        logger.info(f"Reference audio directory not found: {REFERENCE_AUDIO_DIR}")
        return

    for wav_file in REFERENCE_AUDIO_DIR.glob("*.wav"):
        voice_name = wav_file.stem
        embedding_file = CUSTOM_VOICES_DIR / f"{voice_name}.pth"

        # Skip if embedding already exists
        if voice_name in _custom_voices:
            continue

        logger.info(f"Generating embedding for: {voice_name}")
        try:
            # Generate embedding from WAV file
            gpt_cond_latent, speaker_embedding = _synthesizer.get_conditioning_latents(
                audio_path=[str(wav_file)]
            )

            # Save embedding
            voice_data = {
                "gpt_cond_latent": gpt_cond_latent,
                "speaker_embedding": speaker_embedding
            }
            torch.save(voice_data, embedding_file)
            _custom_voices[voice_name] = voice_data
            logger.info(f"Generated and saved embedding for: {voice_name}")

        except Exception as e:
            logger.error(f"Failed to generate embedding for {voice_name}: {e}")


def get_speaker_embedding(speaker_name: str):
    """Get speaker embedding by name (built-in or custom)."""
    # Check custom voices first
    if speaker_name in _custom_voices:
        return _custom_voices[speaker_name]

    # Check built-in speakers
    if _speaker_embeddings and speaker_name in _speaker_embeddings:
        return _speaker_embeddings[speaker_name]

    return None


def get_all_speakers() -> list:
    """Get list of all available speakers (built-in + custom)."""
    speakers = []

    # Custom voices first (marked with *)
    speakers.extend([f"* {name}" for name in sorted(_custom_voices.keys())])

    # Built-in speakers
    if _speaker_embeddings:
        speakers.extend(sorted(_speaker_embeddings.keys()))

    return speakers


# ============================================================
# WEB UI
# ============================================================
WEB_UI_HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>XTTS v2 - Voice Cloning TTS</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background: #1a1a2e;
            color: #eee;
        }
        h1 { color: #00d4ff; margin-bottom: 5px; }
        .subtitle { color: #888; margin-bottom: 30px; }
        .section {
            background: #16213e;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .section h2 { color: #00d4ff; margin-top: 0; font-size: 1.2em; }
        label { display: block; margin-bottom: 5px; color: #aaa; }
        textarea, select, input[type="text"] {
            width: 100%;
            padding: 12px;
            border: 1px solid #333;
            border-radius: 5px;
            background: #0f0f23;
            color: #fff;
            font-size: 14px;
            margin-bottom: 15px;
        }
        textarea { min-height: 400px; resize: vertical; }
        select { cursor: pointer; }
        .row { display: flex; gap: 15px; }
        .row > div { flex: 1; }
        button {
            background: linear-gradient(135deg, #00d4ff, #0099cc);
            color: #000;
            border: none;
            padding: 12px 30px;
            border-radius: 5px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            transition: transform 0.1s, box-shadow 0.1s;
        }
        button:hover { transform: translateY(-2px); box-shadow: 0 5px 20px rgba(0,212,255,0.3); }
        button:disabled { background: #444; color: #888; cursor: not-allowed; transform: none; }
        .status {
            margin-top: 15px;
            padding: 10px;
            border-radius: 5px;
            display: none;
        }
        .status.loading { display: block; background: #1e3a5f; color: #00d4ff; }
        .status.success { display: block; background: #1e3f2e; color: #4caf50; }
        .status.error { display: block; background: #3f1e1e; color: #f44336; }
        audio {
            width: 100%;
            margin-top: 15px;
            border-radius: 5px;
        }
        .info {
            background: #0f0f23;
            padding: 15px;
            border-radius: 5px;
            font-size: 13px;
            color: #888;
        }
        .info code { color: #00d4ff; background: #1a1a2e; padding: 2px 6px; border-radius: 3px; }
        .custom-voice { color: #ffd700; }
        .clone-section { border-top: 1px solid #333; padding-top: 20px; margin-top: 20px; }
    </style>
</head>
<body>
    <h1>XTTS v2</h1>
    <p class="subtitle">Voice Cloning Text-to-Speech</p>

    <div class="section">
        <h2>Text-to-Speech</h2>
        <label for="text">Text</label>
        <textarea id="text" placeholder="Guten Tag, ich bin AIfred. How may I assist you today?"></textarea>

        <div class="row">
            <div>
                <label for="voice">Stimme / Voice</label>
                <select id="voice"></select>
            </div>
            <div>
                <label for="language">Sprache / Language</label>
                <select id="language">
                    <option value="de">Deutsch</option>
                    <option value="en">English</option>
                    <option value="fr">Français</option>
                    <option value="es">Español</option>
                    <option value="it">Italiano</option>
                    <option value="pt">Português</option>
                    <option value="pl">Polski</option>
                    <option value="nl">Nederlands</option>
                    <option value="ru">Русский</option>
                    <option value="tr">Türkçe</option>
                    <option value="cs">Čeština</option>
                    <option value="ar">العربية</option>
                    <option value="zh-cn">中文</option>
                    <option value="ja">日本語</option>
                    <option value="ko">한국어</option>
                    <option value="hu">Magyar</option>
                </select>
            </div>
        </div>

        <button onclick="generateTTS()" id="generateBtn">Generate Audio</button>

        <div id="status" class="status"></div>
        <audio id="audioPlayer" controls style="display:none;"></audio>
    </div>

    <div class="section">
        <h2>Voice Cloning</h2>
        <p style="color:#888; margin-bottom:15px;">Upload a WAV file (6-10 seconds of clear speech, mono, 22-24kHz) to clone a voice.</p>

        <div class="row">
            <div>
                <label for="cloneName">Voice Name</label>
                <input type="text" id="cloneName" placeholder="e.g. my_voice">
            </div>
            <div>
                <label for="cloneFile">Audio File (WAV)</label>
                <input type="file" id="cloneFile" accept=".wav,audio/wav" style="padding:8px;">
            </div>
        </div>

        <button onclick="cloneVoice()" id="cloneBtn">Clone Voice</button>
        <div id="cloneStatus" class="status"></div>
    </div>

    <div class="section">
        <h2>Model Management</h2>
        <p style="color:#888; margin-bottom:15px;">Unload the model to free GPU memory (for Ollama, etc.). Model reloads on next TTS request.</p>
        <button onclick="unloadModel()" id="unloadBtn">Unload Model</button>
        <div id="unloadStatus" class="status"></div>
    </div>

    <div class="info">
        <strong>API Endpoints:</strong><br>
        <code>GET /voices</code> - List all voices<br>
        <code>GET /languages</code> - List supported languages<br>
        <code>GET /status</code> - Detailed status with GPU/VRAM info<br>
        <code>POST /tts</code> - Generate speech (JSON: text, speaker, language)<br>
        <code>POST /unload</code> - Unload model to free memory<br>
        <code>POST /voices/clone</code> - Clone voice (multipart: audio, name)<br>
        <code>DELETE /voices/&lt;name&gt;</code> - Delete custom voice
    </div>

    <script>
        // Load voices on page load (with cache-busting and loading indicator)
        async function loadVoices() {
            const select = document.getElementById('voice');

            // Show loading state
            select.innerHTML = '<option disabled>Loading voices...</option>';

            try {
                // Add timestamp to prevent browser caching
                const res = await fetch('/voices?t=' + Date.now());
                const data = await res.json();
                select.innerHTML = '';

                // Custom voices first
                if (data.custom && data.custom.length > 0) {
                    const optgroup = document.createElement('optgroup');
                    optgroup.label = '★ Custom Voices';
                    data.custom.forEach(v => {
                        const opt = document.createElement('option');
                        opt.value = v;
                        opt.textContent = '★ ' + v;
                        opt.className = 'custom-voice';
                        optgroup.appendChild(opt);
                    });
                    select.appendChild(optgroup);
                }

                // Built-in voices
                if (data.builtin && data.builtin.length > 0) {
                    const optgroup = document.createElement('optgroup');
                    optgroup.label = 'Built-in Voices (' + data.builtin.length + ')';
                    data.builtin.forEach(v => {
                        const opt = document.createElement('option');
                        opt.value = v;
                        opt.textContent = v;
                        optgroup.appendChild(opt);
                    });
                    select.appendChild(optgroup);
                }

                // Select default or first custom
                if (data.custom && data.custom.length > 0) {
                    select.value = data.custom[0];
                } else if (data.default) {
                    select.value = data.default;
                }

                // If no voices loaded, show error
                if ((!data.custom || data.custom.length === 0) && (!data.builtin || data.builtin.length === 0)) {
                    select.innerHTML = '<option disabled>No voices available - model may still be loading</option>';
                }
            } catch (e) {
                console.error('Failed to load voices:', e);
                select.innerHTML = '<option disabled>Error loading voices</option>';
            }
        }

        async function generateTTS() {
            const text = document.getElementById('text').value.trim();
            const voice = document.getElementById('voice').value;
            const language = document.getElementById('language').value;
            const status = document.getElementById('status');
            const audio = document.getElementById('audioPlayer');
            const btn = document.getElementById('generateBtn');

            if (!text) {
                status.className = 'status error';
                status.textContent = 'Please enter some text.';
                return;
            }

            btn.disabled = true;
            status.className = 'status loading';
            status.textContent = 'Generating audio...';
            audio.style.display = 'none';

            try {
                const startTime = Date.now();
                const res = await fetch('/tts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text, speaker: voice, language })
                });

                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.error || 'TTS generation failed');
                }

                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const duration = ((Date.now() - startTime) / 1000).toFixed(1);

                audio.src = url;
                audio.style.display = 'block';
                audio.playbackRate = 1.25;  // Default 1.25x speed
                audio.play();

                status.className = 'status success';
                status.textContent = `Generated in ${duration}s`;
            } catch (e) {
                status.className = 'status error';
                status.textContent = 'Error: ' + e.message;
            } finally {
                btn.disabled = false;
            }
        }

        async function cloneVoice() {
            const name = document.getElementById('cloneName').value.trim();
            const fileInput = document.getElementById('cloneFile');
            const status = document.getElementById('cloneStatus');
            const btn = document.getElementById('cloneBtn');

            if (!name) {
                status.className = 'status error';
                status.textContent = 'Please enter a voice name.';
                return;
            }
            if (!fileInput.files.length) {
                status.className = 'status error';
                status.textContent = 'Please select a WAV file.';
                return;
            }

            btn.disabled = true;
            status.className = 'status loading';
            status.textContent = 'Cloning voice...';

            try {
                const formData = new FormData();
                formData.append('name', name);
                formData.append('audio', fileInput.files[0]);

                const res = await fetch('/voices/clone', {
                    method: 'POST',
                    body: formData
                });

                const data = await res.json();
                if (!res.ok) throw new Error(data.error || 'Cloning failed');

                status.className = 'status success';
                status.textContent = `Voice "${name}" cloned successfully!`;

                // Reload voices
                await loadVoices();

                // Clear inputs
                document.getElementById('cloneName').value = '';
                fileInput.value = '';
            } catch (e) {
                status.className = 'status error';
                status.textContent = 'Error: ' + e.message;
            } finally {
                btn.disabled = false;
            }
        }

        // Allow Ctrl+Enter to generate
        document.getElementById('text').addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') generateTTS();
        });

        async function unloadModel() {
            const status = document.getElementById('unloadStatus');
            const btn = document.getElementById('unloadBtn');

            btn.disabled = true;
            status.className = 'status loading';
            status.textContent = 'Unloading model...';

            try {
                const res = await fetch('/unload', { method: 'POST' });
                const data = await res.json();

                if (data.success) {
                    status.className = 'status success';
                    status.textContent = `Model unloaded from ${data.freed_device}. Memory freed.`;
                    // Clear voice dropdown since model is unloaded
                    document.getElementById('voice').innerHTML = '<option disabled>Model unloaded - will reload on next TTS</option>';
                } else {
                    throw new Error(data.error || 'Unload failed');
                }
            } catch (e) {
                status.className = 'status error';
                status.textContent = 'Error: ' + e.message;
            } finally {
                btn.disabled = false;
            }
        }

        // Load voices on start
        loadVoices();
    </script>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    """Web UI for testing XTTS."""
    return render_template_string(WEB_UI_HTML)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "model_loaded": _synthesizer is not None,
        "model": _model_name,
        "device": _device or "not loaded",
        "custom_voices": len(_custom_voices)
    })


@app.route("/keep_alive", methods=["GET", "POST"])
def keep_alive():
    """Reset the auto-shutdown timer without producing audio.

    Used by long-running upstream pipelines (e.g. AIfred FreeEcho.2 Puck
    handler during multi-step web research) to keep the container alive
    while no actual TTS request has happened yet. Each call resets the
    KEEP_ALIVE_MINUTES idle window.
    """
    _reset_restart_timer()
    return jsonify({
        "status": "ok",
        "keep_alive_minutes": KEEP_ALIVE_MINUTES,
    })


@app.route("/status", methods=["GET"])
def status():
    """
    Detailed status endpoint with GPU/VRAM information.

    Returns system info, device selection, and memory stats.
    """
    vram_info = check_system_vram()
    torch_info = get_gpu_memory_info()

    return jsonify({
        "model_loaded": _synthesizer is not None,
        "device": _device or "not loaded yet",
        "force_cpu": FORCE_CPU,
        "vram_threshold_gb": VRAM_THRESHOLD_GB,
        "system_vram": vram_info,
        "torch_memory": torch_info,
        "custom_voices": len(_custom_voices),
        "builtin_speakers": len(_speaker_embeddings) if _speaker_embeddings else 0,
        "inference_params": {
            "temperature": XTTS_TEMPERATURE,
            "repetition_penalty": XTTS_REPETITION_PENALTY,
            "length_penalty": XTTS_LENGTH_PENALTY,
            "top_k": XTTS_TOP_K,
            "top_p": XTTS_TOP_P,
            "max_chunk_chars": MAX_CHUNK_CHARS,
        },
    })


@app.route("/unload", methods=["POST"])
def unload_model():
    """
    Unload the XTTS model from memory.

    This frees GPU VRAM (or RAM if on CPU) by:
    - Setting all model references to None
    - Clearing CUDA cache
    - Running Python garbage collection

    The model will be lazy-loaded again on the next /tts request.

    Returns:
        {"success": true, "freed_device": "cuda|cpu|not_loaded"}
    """
    global _synthesizer, _config, _speaker_embeddings, _custom_voices, _device

    if _synthesizer is None:
        return jsonify({
            "success": True,
            "freed_device": "not_loaded",
            "message": "Model was not loaded"
        })

    freed_device = _device or "unknown"
    logger.info(f"🗑️ Unloading XTTS model from {freed_device}...")

    # Cancel any pending auto-restart timer
    global _restart_timer
    if _restart_timer is not None:
        _restart_timer.cancel()
        _restart_timer = None

    # Clear model references
    _synthesizer = None
    _config = None
    _speaker_embeddings = None
    _custom_voices = {}
    _device = None

    # Deep CUDA cleanup
    _deep_cuda_cleanup()

    logger.info(f"✅ XTTS model unloaded from {freed_device}")

    return jsonify({
        "success": True,
        "freed_device": freed_device,
        "message": f"Model unloaded from {freed_device}, memory freed"
    })


def _deep_cuda_cleanup():
    """Aggressive CUDA memory cleanup."""
    import gc

    # Multiple GC passes
    for _ in range(3):
        gc.collect()

    try:
        import torch
        if torch.cuda.is_available():
            # Clear all cached memory
            torch.cuda.empty_cache()
            torch.cuda.synchronize()

            # Reset memory stats
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.reset_accumulated_memory_stats()

            # IPC cleanup (shared memory)
            torch.cuda.ipc_collect()

            logger.info("✅ Deep CUDA cleanup completed")
    except Exception as e:
        logger.warning(f"CUDA cleanup error: {e}")


def _auto_restart_server():
    """Background task: Shutdown container after KEEP_ALIVE_MINUTES of inactivity.

    Kills the Gunicorn master to fully exit the container, releasing all VRAM
    including the ~167 MB CUDA context overhead (allows GPU P8 power state).
    Docker restart policy ("unless-stopped") brings the container back up
    with EAGER_LOAD=1, so the model lazy-loads on the next /tts request.
    """
    global _active_requests

    if _synthesizer is None:
        logger.debug("Auto-shutdown: Model not loaded, skipping")
        return

    # Don't shutdown if requests are in-flight
    if _active_requests > 0:
        logger.info(f"⏰ Auto-shutdown delayed: {_active_requests} request(s) in progress")
        import threading
        retry_timer = threading.Timer(30, _auto_restart_server)
        retry_timer.daemon = True
        retry_timer.start()
        return

    logger.info(f"⏰ Auto-shutdown after {KEEP_ALIVE_MINUTES} min inactivity - freeing all VRAM...")

    # Give logs time to flush
    import time
    time.sleep(0.5)

    # Signal Gunicorn master (parent process) to terminate
    # Docker "unless-stopped" will restart the container automatically
    import os
    import signal
    os.kill(os.getppid(), signal.SIGTERM)


def _reset_restart_timer():
    """Reset the auto-restart timer after a TTS request."""
    global _restart_timer, _last_request_time
    import time
    import threading

    if KEEP_ALIVE_MINUTES <= 0:
        return  # Auto-restart disabled

    _last_request_time = time.time()

    # Cancel existing timer
    if _restart_timer is not None:
        _restart_timer.cancel()

    # Start new timer
    _restart_timer = threading.Timer(KEEP_ALIVE_MINUTES * 60, _auto_restart_server)
    _restart_timer.daemon = True  # Don't block shutdown
    _restart_timer.start()
    logger.debug(f"Auto-restart timer reset: {KEEP_ALIVE_MINUTES} min")


@app.route("/tts", methods=["POST"])
def tts():
    """
    Generate TTS audio from text.

    Automatically handles long texts by splitting into chunks and concatenating.
    XTTS has a 400 token limit (~250 chars), so texts are split at sentence
    boundaries when needed.

    Output is OGG/Opus encoded for smaller file size (~90% smaller than WAV).
    Falls back to WAV if ffmpeg conversion fails.

    Request JSON:
        text (str): Text to synthesize (any length)
        language (str, optional): Language code (default: de)
        speaker (str, optional): Speaker name (default: Claribel Dervla)
            - Use "* name" for custom voices or just "name"

    Returns:
        OGG/Opus audio file (48kbps) or WAV as fallback
    """
    import torch
    import torchaudio

    # Parse request
    data = request.get_json()
    if not data or "text" not in data:
        return jsonify({"error": "Missing 'text' field"}), 400

    text = data.get("text", "")
    language = data.get("language", "de")
    speaker = data.get("speaker", DEFAULT_SPEAKER)

    # Remove "* " prefix if present (UI marker for custom voices)
    if speaker.startswith("* "):
        speaker = speaker[2:]

    if not text.strip():
        return jsonify({"error": "Empty text"}), 400

    # Validate language
    supported_languages = ["de", "en", "es", "fr", "it", "pt", "pl", "tr", "ru", "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko"]
    if language not in supported_languages:
        return jsonify({"error": f"Unsupported language: {language}. Supported: {supported_languages}"}), 400

    # Normalize text to prevent hallucinations (ensure proper punctuation)
    original_text = text
    text = normalize_text_for_tts(text, language)
    if text != original_text:
        logger.info("Text normalized for TTS (added punctuation)")

    logger.info(f"Generating TTS: '{text[:50]}...' ({len(text)} chars) with language {language}, speaker {speaker}")

    # Track active requests for safe auto-restart
    global _active_requests
    _active_requests += 1

    try:
        model = get_synthesizer()

        # Get speaker embedding
        speaker_data = get_speaker_embedding(speaker)
        if speaker_data is None:
            _active_requests -= 1
            return jsonify({
                "error": f"Unknown speaker: {speaker}",
                "available_speakers": get_all_speakers()[:30]
            }), 400

        # Move embeddings to the selected device
        if _device == "cuda":
            gpt_cond_latent = speaker_data["gpt_cond_latent"].cuda()
            speaker_embedding = speaker_data["speaker_embedding"].cuda()
        else:
            gpt_cond_latent = speaker_data["gpt_cond_latent"].cpu()
            speaker_embedding = speaker_data["speaker_embedding"].cpu()

        # Split text into chunks to avoid 400 token limit
        chunks = split_text_into_chunks(text)

        # Generate audio for each chunk
        audio_arrays = []
        for i, chunk in enumerate(chunks):
            logger.info(f"  Generating chunk {i+1}/{len(chunks)}: '{chunk[:40]}...' ({len(chunk)} chars)")

            # XTTS inference with configurable parameters (via environment variables)
            # See: https://github.com/coqui-ai/TTS/discussions/4146
            outputs = model.inference(
                text=chunk,
                language=language,
                gpt_cond_latent=gpt_cond_latent,
                speaker_embedding=speaker_embedding,
                temperature=XTTS_TEMPERATURE,
                repetition_penalty=XTTS_REPETITION_PENALTY,
                length_penalty=XTTS_LENGTH_PENALTY,
                top_k=XTTS_TOP_K,
                top_p=XTTS_TOP_P,
            )

            audio_arrays.append(outputs["wav"])

        # Concatenate all audio chunks
        if len(audio_arrays) > 1:
            logger.info(f"Concatenating {len(audio_arrays)} audio chunks")
            final_audio = concatenate_audio_arrays(audio_arrays)
        else:
            final_audio = audio_arrays[0]

        # Create temp file for output
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name

        # Save to WAV first (sample rate is 24kHz for XTTS)
        torchaudio.save(wav_path, torch.tensor(final_audio).unsqueeze(0), 24000)

        # Convert WAV to OGG/Opus for smaller file size (~90% reduction)
        ogg_path = wav_path.replace(".wav", ".ogg")
        import subprocess
        ffmpeg_result = subprocess.run([
            "ffmpeg", "-y", "-i", wav_path,
            "-c:a", "libopus", "-b:a", "48k",  # 48kbps Opus - excellent for speech
            ogg_path
        ], capture_output=True, text=True)

        # Clean up WAV immediately
        try:
            os.unlink(wav_path)
        except Exception:
            pass

        if ffmpeg_result.returncode != 0:
            logger.error(f"ffmpeg conversion failed: {ffmpeg_result.stderr}")
            # Fallback: recreate WAV and send that
            torchaudio.save(wav_path, torch.tensor(final_audio).unsqueeze(0), 24000)
            temp_path = wav_path
            mimetype = "audio/wav"
            download_name = "xtts_tts.wav"
        else:
            temp_path = ogg_path
            mimetype = "audio/ogg"
            download_name = "xtts_tts.ogg"

        logger.info(f"Generated audio: {temp_path} ({len(chunks)} chunks)")

        # Clear CUDA cache to free VRAM for other services (e.g., Ollama)
        if _device == "cuda":
            try:
                torch.cuda.empty_cache()
                logger.debug("CUDA cache cleared after TTS request")
            except Exception:
                pass

        # Request done - decrement counter and reset timer
        _active_requests -= 1
        _reset_restart_timer()

        # Send file and schedule cleanup
        response = send_file(
            temp_path,
            mimetype=mimetype,
            as_attachment=True,
            download_name=download_name
        )

        # Cleanup after response
        @response.call_on_close
        def cleanup():
            try:
                os.unlink(temp_path)
            except Exception:
                pass

        return response

    except Exception as e:
        # Request done (with error) - decrement counter
        _active_requests -= 1
        logger.error(f"TTS generation failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/voices/clone", methods=["POST"])
def clone_voice():
    """
    Clone a voice from a WAV file.

    Form data:
        audio: WAV file (6-10 seconds of clear speech, mono, 22-24kHz)
        name: Name for the cloned voice

    Returns:
        {"success": true, "voice": "name"}
    """
    import torch

    if "audio" not in request.files:
        return jsonify({"error": "Missing 'audio' file"}), 400

    if "name" not in request.form:
        return jsonify({"error": "Missing 'name' field"}), 400

    audio_file = request.files["audio"]
    voice_name = request.form["name"].strip()

    if not voice_name:
        return jsonify({"error": "Empty voice name"}), 400

    # Sanitize voice name
    voice_name = "".join(c for c in voice_name if c.isalnum() or c in "_ -").strip()
    if not voice_name:
        return jsonify({"error": "Invalid voice name"}), 400

    logger.info(f"Cloning voice: {voice_name}")

    try:
        # Ensure model is loaded
        model = get_synthesizer()

        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_file.save(f.name)
            temp_audio_path = f.name

        # Generate embedding
        gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
            audio_path=[temp_audio_path]
        )

        # Clean up temp file
        os.unlink(temp_audio_path)

        # Save embedding
        CUSTOM_VOICES_DIR.mkdir(parents=True, exist_ok=True)
        embedding_file = CUSTOM_VOICES_DIR / f"{voice_name}.pth"

        voice_data = {
            "gpt_cond_latent": gpt_cond_latent,
            "speaker_embedding": speaker_embedding
        }
        torch.save(voice_data, embedding_file)

        # Add to in-memory cache
        _custom_voices[voice_name] = voice_data

        logger.info(f"Successfully cloned voice: {voice_name}")

        return jsonify({
            "success": True,
            "voice": voice_name,
            "message": f"Voice '{voice_name}' cloned successfully"
        })

    except Exception as e:
        logger.error(f"Voice cloning failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/voices", methods=["GET"])
def list_voices():
    """List all available voices (built-in + custom). Does NOT load the model."""
    # Load only speaker names (lightweight, no model load)
    ensure_speaker_names_loaded()

    # Get custom voice names from filesystem (no model needed)
    custom = sorted([f.stem for f in CUSTOM_VOICES_DIR.glob("*.pth")])
    builtin = _speaker_names if _speaker_names else []

    # Build combined list with custom voices marked
    all_speakers = [f"* {name}" for name in custom] + builtin

    return jsonify({
        "custom": custom,
        "builtin": builtin,
        "all": all_speakers,
        "default": DEFAULT_SPEAKER
    })


@app.route("/voices/<name>", methods=["DELETE"])
def delete_voice(name: str):
    """Delete a custom voice."""
    if name not in _custom_voices:
        return jsonify({"error": f"Custom voice not found: {name}"}), 404

    try:
        # Remove from disk
        embedding_file = CUSTOM_VOICES_DIR / f"{name}.pth"
        if embedding_file.exists():
            embedding_file.unlink()

        # Remove from memory
        del _custom_voices[name]

        logger.info(f"Deleted custom voice: {name}")

        return jsonify({
            "success": True,
            "message": f"Voice '{name}' deleted"
        })

    except Exception as e:
        logger.error(f"Failed to delete voice: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/speakers", methods=["GET"])
def list_speakers():
    """List available speakers (legacy endpoint, use /voices instead). Does NOT load the model."""
    # Load only speaker names (lightweight, no model load)
    ensure_speaker_names_loaded()

    # Get custom voice names from filesystem
    custom = sorted([f.stem for f in CUSTOM_VOICES_DIR.glob("*.pth")])
    builtin = _speaker_names if _speaker_names else []

    # Build combined list
    all_speakers = [f"* {name}" for name in custom] + builtin

    return jsonify({
        "speakers": all_speakers,
        "default": DEFAULT_SPEAKER
    })


@app.route("/languages", methods=["GET"])
def list_languages():
    """List available languages."""
    languages = {
        "de": "German",
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "it": "Italian",
        "pt": "Portuguese",
        "pl": "Polish",
        "tr": "Turkish",
        "ru": "Russian",
        "nl": "Dutch",
        "cs": "Czech",
        "ar": "Arabic",
        "zh-cn": "Chinese",
        "ja": "Japanese",
        "hu": "Hungarian",
        "ko": "Korean",
    }
    return jsonify(languages)


# Eager load model at startup (if enabled)
if EAGER_LOAD:
    logger.info("🚀 EAGER_LOAD enabled - loading model at startup...")
    get_synthesizer()
    logger.info("✅ Model loaded and ready")

# Start auto-shutdown timer at container startup
# Container exits after inactivity, Docker restarts it (lazy-load on next request)
if KEEP_ALIVE_MINUTES > 0:
    logger.info(f"⏰ Auto-shutdown timer started: {KEEP_ALIVE_MINUTES} min")
    _reset_restart_timer()
else:
    logger.info("⏰ Auto-shutdown disabled (XTTS_KEEP_ALIVE=0)")


if __name__ == "__main__":
    # Development server
    app.run(host="0.0.0.0", port=5051, debug=False)
