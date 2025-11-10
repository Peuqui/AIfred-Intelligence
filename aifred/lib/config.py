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
# LOGGING CONFIGURATION (Unified System)
# ============================================================
# Console Debug: Messages ins UI Debug-Console senden
CONSOLE_DEBUG_ENABLED = True

# File Debug: Messages ins Log-File schreiben
FILE_DEBUG_ENABLED = True

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
# DEPLOYMENT MODE CONFIGURATION
# ============================================================
# Determines how the AIfred restart button behaves
# True:  Production mode - restarts systemd service (aifred-intelligence.service)
# False: Development mode - soft restart for hot-reload (clears caches/history only)
USE_SYSTEMD_RESTART = True  # Set to False for development with hot-reload

# ============================================================
# LANGUAGE CONFIGURATION (i18n)
# ============================================================
# Language for prompts and UI
# "auto" = Detect from user input
# "de"   = German (Deutsch)
# "en"   = English
DEFAULT_LANGUAGE = "auto"

# ============================================================
# DEFAULT SETTINGS
# ============================================================
DEFAULT_SETTINGS = {
    "model": "qwen3:8b",
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

# ============================================================
# CONTEXT MANAGEMENT
# ============================================================
# Maximale Tokens für RAG-Context (Recherche-Ergebnisse)
# Hinweis: Total Context = RAG_CONTEXT + System-Prompt + History + User-Message
# Bei 40k Model-Limit → empfohlen: 20k RAG Context (50% Reserve)
# Bei größeren Models (z.B. mit Tesla P40) kann dieser Wert erhöht werden
MAX_RAG_CONTEXT_TOKENS = 20000

# Maximale Wörter pro einzelner Quelle (Wikipedia, News-Artikel, etc.)
# Verhindert, dass eine einzelne Quelle den gesamten Context dominiert
MAX_WORDS_PER_SOURCE = 2000

# Token-zu-Zeichen Ratio für Context-Berechnung
# Deutsch/Englisch Mix: ~3 Zeichen pro Token
CHARS_PER_TOKEN = 3

# ============================================================
# HISTORY SUMMARIZATION CONFIGURATION
# ============================================================
# Trigger-Punkt: Bei welchem Prozentsatz des Context-Limits soll komprimiert werden?
HISTORY_COMPRESSION_THRESHOLD = 0.7  # 70% des Context-Limits (Produktiv-Wert)

# Anzahl der Messages die auf einmal komprimiert werden
# (6 Messages = 3 Frage-Antwort-Paare)
HISTORY_MESSAGES_TO_COMPRESS = 6  # 3 Frage-Antwort-Paare

# Maximale Anzahl von Summaries die gespeichert werden
# Bei mehr wird die älteste gelöscht (FIFO)
HISTORY_MAX_SUMMARIES = 10

# Target-Größe für eine Summary in Tokens
HISTORY_SUMMARY_TARGET_TOKENS = 1000

# Target-Größe für eine Summary in Wörtern (für Prompt)
HISTORY_SUMMARY_TARGET_WORDS = 750

# Minimale Anzahl von Messages bevor Compression überhaupt startet
# (verhindert Compression bei kurzen Gesprächen)
# WICHTIG: Muss GRÖSSER sein als HISTORY_MESSAGES_TO_COMPRESS um mindestens 1 Message sichtbar zu lassen!
HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION = 10  # Mindestens 5 Frage-Antwort-Paare vor Kompression

# Temperature für Summary-Generierung (niedriger = faktischer)
HISTORY_SUMMARY_TEMPERATURE = 0.3

# Context-Limit für Summary-LLM (sollte nicht zu groß sein)
HISTORY_SUMMARY_CONTEXT_LIMIT = 4096

# ============================================================
# VECTOR CACHE CONFIGURATION (ChromaDB Similarity Thresholds)
# ============================================================
# Distance-Thresholds für semantische Ähnlichkeit (Cosine Distance)
# 0.0 = identisch, 2.0 = komplett verschieden

# Normale Cache-Abfrage (ohne explizite Keywords wie "recherchiere")
CACHE_DISTANCE_HIGH = 0.5      # < 0.5 = HIGH confidence Cache-Hit
CACHE_DISTANCE_MEDIUM = 0.5   # 0.5-0.85 = MEDIUM confidence Cache-Hit
                               # > 0.85 = CACHE_MISS

# Explizite Recherche-Keywords ("recherchiere", "google", etc.)
# Nur für query_newest() - zeitbasierte Duplikat-Erkennung
CACHE_DISTANCE_DUPLICATE = 0.3  # < 0.3 = Sehr ähnlich (potentielles Duplikat wenn < 5min alt)
                                # Beispiele:
                                # - "recherchiere Python" vs "recherchiere Python Tutorial" = ~0.15
                                # - "recherchiere Wetter Berlin" vs "recherchiere Wetter Hamburg" = ~0.25
                                # - "recherchiere Python" vs "recherchiere Java" = ~0.6

# Zeit-Threshold für Duplikat-Erkennung
CACHE_TIME_THRESHOLD = 300  # 5 Minuten (in Sekunden)

# RAG-Mode Distance Threshold
CACHE_DISTANCE_RAG = 1.2  # < 1.2 = Ähnlich genug für RAG-Kontext (später implementiert)

# Volatile Keywords - Loaded from prompts/cache_volatile_keywords.txt
# Diese Keywords triggern eine LLM-Entscheidung, ob trotzdem gecacht werden soll
def _load_volatile_keywords():
    """
    Load volatile keywords from file (multilingual).
    File contains both German and English keywords.
    """
    keywords = []
    keywords_file = PROJECT_ROOT / "prompts" / "cache_volatile_keywords.txt"

    try:
        with open(keywords_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if line and not line.startswith('#'):
                    keywords.append(line)
    except FileNotFoundError:
        import warnings
        warnings.warn(f"⚠️ Keywords file not found: {keywords_file}, using empty list")

    return keywords

CACHE_EXCLUDE_VOLATILE = _load_volatile_keywords()

# ============================================================
# CONFIG VALIDATION (Safety Checks)
# ============================================================
# Validate History Compression Config zur Laufzeit
if HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION <= HISTORY_MESSAGES_TO_COMPRESS:
    import warnings
    warnings.warn(
        f"⚠️ CONFIG ERROR: HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION ({HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION}) "
        f"must be GREATER than HISTORY_MESSAGES_TO_COMPRESS ({HISTORY_MESSAGES_TO_COMPRESS})! "
        f"Otherwise all messages would be compressed and chat history would become empty. "
        f"Recommended: HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION = HISTORY_MESSAGES_TO_COMPRESS + 1",
        UserWarning
    )
