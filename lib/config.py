"""
Configuration Module - Central location for all constants and paths

This module contains all global configuration variables used across
the AIfred Intelligence application.
"""

from pathlib import Path

# ============================================================
# PROJECT PATHS
# ============================================================
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
PIPER_MODEL_PATH = PROJECT_ROOT / "piper_models" / "de_DE-thorsten-medium.onnx"
SETTINGS_FILE = PROJECT_ROOT / "assistant_settings.json"
SSL_KEYFILE = PROJECT_ROOT / "ssl" / "privkey.pem"
SSL_CERTFILE = PROJECT_ROOT / "ssl" / "fullchain.pem"

# ============================================================
# DEBUG CONFIGURATION
# ============================================================
DEBUG_ENABLED = True  # Set to False to disable debug output

# ============================================================
# WHISPER MODELS CONFIGURATION
# ============================================================
WHISPER_MODELS = {
    "tiny (39MB, schnell, englisch)": "tiny",
    "base (74MB, schneller, multilingual)": "base",
    "small (466MB, bessere Qualität, multilingual)": "small",
    "medium (1.5GB, hohe Qualität, multilingual)": "medium",
    "large-v3 (2.9GB, beste Qualität, multilingual)": "large-v3"
}

# ============================================================
# DEFAULT SETTINGS
# ============================================================
DEFAULT_SETTINGS = {
    "model": "qwen2.5:14b",
    "automatik_model": "phi3:mini",
    "voice": "Deutsch (Katja)",
    "tts_speed": 1.25,
    "enable_tts": False,
    "tts_engine": "Edge TTS (Cloud, beste Qualität)",
    "whisper_model": "small (466MB, bessere Qualität, multilingual)",
    "research_mode": "🤖 Automatik (variabel, KI entscheidet)",
    "show_transcription": False,
    "enable_gpu": True
    # Temperature wird NICHT gespeichert - immer 0.2 für Web-Recherche (sicher)
    # User kann pro Session im UI ändern, aber es bleibt nicht persistent
}

# ============================================================
# AVAILABLE VOICES
# ============================================================
VOICES = {
    "Deutsch (Katja)": "de-DE-KatjaNeural",
    "Deutsch (Conrad)": "de-DE-ConradNeural",
    "Englisch (Jenny)": "en-US-JennyNeural",
    "Englisch (Guy)": "en-US-GuyNeural",
    "Französisch (Denise)": "fr-FR-DeniseNeural",
    "Spanisch (Elvira)": "es-ES-ElviraNeural"
}

# ============================================================
# RESEARCH MODES
# ============================================================
RESEARCH_MODES = [
    "🤖 Automatik (variabel, KI entscheidet)",
    "❌ Aus (nur eigenes Wissen)",
    "🔍 Web-Suche Schnell (3 Quellen)",
    "📚 Web-Suche Ausführlich (5 Quellen)"
]

# ============================================================
# TTS ENGINES
# ============================================================
TTS_ENGINES = [
    "Edge TTS (Cloud, beste Qualität)",
    "Piper TTS (Lokal, Offline)"
]
