"""
Configuration Module - Central location for all constants and paths

This module contains all global configuration variables used across
the AIfred Intelligence application.
"""

import os
import platform
from pathlib import Path

# ============================================================
# PROJECT PATHS
# ============================================================
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()  # Go up to repo root

# Centralized data directory (all persistent user data)
# Structure:
#   data/
#   ├── sessions/           # Chat session files (.json)
#   ├── images/             # Uploaded/cropped images
#   ├── tts_audio/          # Generated TTS audio files
#   ├── html_preview/       # Exported HTML chat previews
#   ├── logs/               # Debug log files
#   ├── settings.json       # User settings
#   ├── accounts.json       # User accounts (username → password hash)
#   ├── allowed_users.json  # Whitelist of allowed usernames
#   └── model_vram_cache.json  # VRAM calibration cache
#
# Benefits:
# - All data in one place (easy backup, portable)
# - Excluded from Reflex hot reload (REFLEX_HOT_RELOAD_EXCLUDE_PATHS=data)
# - Excluded from git (.gitignore)
DATA_DIR = PROJECT_ROOT / "data"

# ============================================================
# BACKEND URL FOR STATIC FILES (HTML Preview, Images)
# ============================================================
# With NGINX proxy: Leave empty ("") - NGINX routes /_upload/ to backend
# Without NGINX (dev): Set to backend URL, e.g. "http://localhost:8002"
# Example for WSL dev: BACKEND_URL=http://172.30.8.72:8002
BACKEND_URL = os.environ.get("BACKEND_URL", "")

# ============================================================
# DEBUG CONFIGURATION
# ============================================================
DEBUG_MESSAGES_MAX = 500  # Maximum number of debug messages to keep in UI console

# ============================================================
# LOGGING CONFIGURATION (Unified System)
# ============================================================
# Console Debug: Send messages to UI debug console
CONSOLE_DEBUG_ENABLED = True

# File Debug: Write messages to log file
FILE_DEBUG_ENABLED = True

# Whisper STT: runs as Docker container (docker/whisper/)
# Config constants in docker-compose.yml, not here.
# See WHISPER_SERVICE_URL and WHISPER_DOCKER_COMPOSE_PATH below.

# ============================================================
# LANGUAGE CONFIGURATION (i18n)
# ============================================================
# Language for prompts and UI (synced with ui_language in state.py)
# Language detection is done via LLM-based Intent Detection
# "de" = German (Deutsch)
# "en" = English
DEFAULT_LANGUAGE = "de"

# ============================================================
# DEFAULT SETTINGS
# ============================================================
DEFAULT_SETTINGS = {
    # NOTE: Model names are defined in BACKEND_DEFAULT_MODELS below (backend-specific)
    # They will be merged in settings.py get_default_settings()
    "user_name": "",  # User's name (leave empty - set via UI, saved in settings.json)
    "backend_type": "ollama",  # Default backend: "ollama", "vllm", "llamacpp"
    # Calibration mode: "legacy" (deterministic algorithm) or "ai-<model>"
    # (LLM-driven via DashScope/Qwen). UI auto-selects "legacy" when no
    # DashScope API key is configured.
    "calibration_mode": "legacy",
    # Allow hybrid mode (CPU-offload of layers when the model doesn't fit
    # on GPUs alone). Default off — hybrid is slow at inference and the
    # calibration itself takes much longer. Enable explicitly when you
    # need to run a model that exceeds total GPU VRAM.
    "calibration_allow_hybrid": False,
    "voice": "Deutsch (Katja)",
    "tts_playback_rate": "1.25x",  # Browser playback speed (1.25 = default, speed via Agent Settings)
    "enable_tts": False,
    "tts_engine": "edge",
    "whisper_model": "medium (1.5GB, high quality, multilingual)",
    "research_mode": "automatik",  # Internal value: "automatik", "quick", "deep", "none"
    "show_transcription": False,
    "enable_gpu": True,
    "temperature": 0.7,
    "temperature_mode": "auto",  # "auto" (Intent-Detection) or "manual" (user slider)
    "enable_thinking": False  # Qwen3 Thinking Mode (Chain-of-Thought Reasoning)
}

# ============================================================
# OLLAMA SYSTEMD CONFIGURATION
# ============================================================
# Ollama runs as a systemd service and reads environment variables from:
# /etc/systemd/system/ollama.service.d/override.conf
#
# Current configuration (2x Tesla P40, 48GB total VRAM):
#   CUDA_VISIBLE_DEVICES=0,1          # Both GPUs visible
#   OLLAMA_MAX_LOADED_MODELS=2        # Max 2 models in VRAM (Automatik + Main)
#   OLLAMA_NUM_PARALLEL=2             # Parallel inference on both GPUs
#   OLLAMA_GPU_OVERHEAD=536870912     # 512 MB GPU overhead (default ~1GB)
#
# Note: For Dual-LLM Debate System (future feature), OLLAMA_MAX_LOADED_MODELS=2
# is perfect since we only need 2 models debating each other (no Automatik needed).
#
# To modify: Edit override.conf and reload systemd
#   sudo systemctl daemon-reload
#   sudo systemctl restart ollama
# ============================================================

# ============================================================
# BACKEND-SPECIFIC DEFAULT MODELS
# ============================================================
# For performance comparisons: All backends use the same model sizes
# - Main LLM: Qwen3-30B-A3B-Instruct-2507 (~18GB, MoE with 3B active)
# - Automatik: Qwen3-4B-Instruct-2507 (~2.6GB)
# - Multi-Agent (Sokrates, Salomo, AIfred): Qwen3-4B-Instruct-2507 (~2.6GB)
BACKEND_DEFAULT_MODELS = {
    "ollama": {
        "aifred": "qwen3:4b-instruct-2507-q4_K_M",                  # AIfred Main-LLM: GGUF Q8_0, ~32GB
        "automatik": "qwen3:4b-instruct-2507-q4_K_M",               # Automatik: GGUF Q4_K_M, ~2.6GB
        "sokrates": "qwen3:4b-instruct-2507-q4_K_M",                # Sokrates: GGUF Q4_K_M, ~2.6GB
        "salomo": "qwen3:4b-instruct-2507-q4_K_M",                  # Salomo: GGUF Q4_K_M, ~2.6GB
        "vision": "qwen3-vl:8b",                                    # Vision: Qwen3-VL 8B
    },
    "vllm": {
        "aifred": "cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit",   # AIfred Main-LLM: AWQ 4-bit, ~18GB (CONFIRMED)
        "automatik": "cpatonn/Qwen3-4B-Instruct-2507-AWQ-4bit",     # Automatik: AWQ 4-bit, ~2.8GB (CONFIRMED)
        "sokrates": "cpatonn/Qwen3-4B-Instruct-2507-AWQ-4bit",      # Sokrates: AWQ 4-bit, ~2.8GB
        "salomo": "cpatonn/Qwen3-4B-Instruct-2507-AWQ-4bit",        # Salomo: AWQ 4-bit, ~2.8GB
        "vision": "",                                                # Vision: Auto-detect
    },
    "llamacpp": {
        "aifred": "qwen3-30b-a3b-instruct-2507-q8_0",               # AIfred Main-LLM: Q8_0, ~32GB (2x P40)
        "automatik": "qwen3-4b-instruct-2507-q4_k_m",               # Automatik: Q4_K_M, ~2.6GB
        "sokrates": "qwen3-8b-q4_k_m",                              # Sokrates: Q4_K_M, ~4.7GB
        "salomo": "qwen3-8b-q4_k_m",                                # Salomo: Q4_K_M, ~4.7GB
        "vision": "",                                                # Vision: Auto-detect
    },
    "cloud_api": {
        "aifred": "qwen-plus",                                          # Default: Qwen Plus (free tier)
        "automatik": "qwen-turbo",                                      # Automatik: Qwen Turbo (faster, free)
        "sokrates": "qwen-turbo",                                       # Sokrates: Qwen Turbo
        "salomo": "qwen-turbo",                                         # Salomo: Qwen Turbo
        "vision": "",                                                   # Vision: Not yet supported
    },
}

# ============================================================
# CLOUD API PROVIDERS
# ============================================================
# Configuration for cloud-based LLM APIs (OpenAI-compatible)
# API keys are read from environment variables (not stored in settings!)
CLOUD_API_PROVIDERS = {
    "claude": {
        "name": "Claude (Anthropic)",
        "base_url": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY",
        # Models are fetched dynamically from API - no hardcoded list!
    },
    "qwen": {
        "name": "Qwen (DashScope)",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "env_key": "DASHSCOPE_API_KEY",
        # Models are fetched dynamically from API - no hardcoded list!
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
        # Models are fetched dynamically from API - no hardcoded list!
    },
    "kimi": {
        "name": "Kimi (Moonshot)",
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        # Models are fetched dynamically from API - no hardcoded list!
    },
}

# Cloud API: No context calculation needed
# Cloud providers manage context themselves - we don't need to track limits
# History compression uses LOCAL models only (where we know actual limits)

# ============================================================
# BACKEND URLs
# ============================================================
# Default URLs for each backend type (localhost development)
# Use these constants instead of hardcoding URLs!
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_VLLM_URL = "http://localhost:8001/v1"
DEFAULT_LLAMACPP_URL = os.environ.get("LLAMACPP_URL", "http://localhost:11435/v1")

# llama-swap / llama-server calibration
LLAMASWAP_CONFIG_PATH = Path(os.environ.get(
    "LLAMASWAP_CONFIG", str(Path.home() / ".config" / "llama-swap" / "config.yaml")
))
# Health timeout: upper bound for llama-server to become ready after spawn.
# Polling-based — small models get ready in seconds and don't burn the budget.
# 360 s (6 min) fits a Q8 80B+ model loading from NVMe across multiple GPUs
# over PCIe/OCuLink/USB4 (measured: 86.7 GB takes ~3 min).
LLAMACPP_HEALTH_TIMEOUT = 360          # Seconds (6 minutes) — floor for small models
LLAMACPP_HYBRID_HEALTH_TIMEOUT = 900   # Hybrid mode: CPU offload + mlock — extra slack
# Health-Timeout skaliert mit der Modell-Dateigröße: der 360-s-Floor reichte
# beim 122B (125 GB) nur hauchknapp (Basis-Load 317 s), Varianten mit mehr
# Layern auf der langsamen P40 rissen ihn → falscher "server not ready"-Timeout,
# der als Fit-Fehler fehlgedeutet wurde (2026-07-05). 125 GB von der USB-NVMe
# (~750 MB/s) sind allein ~170 s reines Lesen + mlock-Double-Touch + Layer-Init.
# 6 s/GB = ~2.4× der gemessenen Basis-Ladezeit, komfortabler Puffer.
LLAMACPP_HEALTH_TIMEOUT_PER_GB = 6     # Seconds per GB model size (added to floor logic)
LLAMACPP_CALIBRATION_PORT = int(os.environ.get("LLAMACPP_CALIBRATION_PORT", "9999"))

BACKEND_URLS = {
    "ollama": DEFAULT_OLLAMA_URL,
    "vllm": DEFAULT_VLLM_URL,      # Port 8001 for dev (8000 on production MiniPC)
    "llamacpp": DEFAULT_LLAMACPP_URL,  # llama-swap proxy (see docs/en/guides/llamacpp-setup.md)
    "cloud_api": "",  # Dynamic - set based on provider selection
}

# Backend display labels (for UI dropdowns)
BACKEND_LABELS = {
    "ollama": "Ollama",
    "llamacpp": "llama.cpp",
    "vllm": "vLLM",
    "cloud_api": "Cloud APIs",
}

# Engine-specific voice catalogues now live in aifred/lib/tts_engines/<engine>.py.
# config.py stays engine-agnostic — adding a new engine is a one-file drop.

# FreeEcho.2 TTS fallback voice (last resort if no agent/AIfred voice configured)
PUCK_TTS_FALLBACK_VOICE = "Deutsch (Karlsson)"

# ============================================================
# TTS ENGINES
# ============================================================
# Engine list is derived from the TTSEngine plugin registry — the
# single source of truth for "which TTS engines exist" lives in
# ``aifred/lib/tts_engines/`` (one file per engine, auto-discovered).
# To add a new engine, drop a file there; no edit needed here.
def _build_tts_engine_keys() -> list[str]:
    from .tts_engines import TTS_ENGINES
    return ["off", *TTS_ENGINES.keys()]

TTS_ENGINE_KEYS = _build_tts_engine_keys()

# Default TTS engine — preselected in fresh state and in the agent
# editor's backend dropdown until the user picks one. Single source of
# truth: the state mixins read this instead of each hardcoding a key.
TTS_DEFAULT_ENGINE = "qwen3local"

# narrate_file (narrator plugin): characters per TTS synthesis call.
# Qwen3-TTS is LLM-based — page-length inputs risk omissions, prosody
# drift and KV growth toward the VRAM reserve, so text is chunked at
# paragraph boundaries and the audio is ffmpeg-concatenated afterwards.
# Raise cautiously after empirical testing with long inputs.
NARRATE_CHUNK_LIMIT_CHARS = 800

# narrate_file: fallback voice when the caller does not pass one.
# Must match a reference voice shipped in docker/tts/voices/.
NARRATE_DEFAULT_VOICE = "AIfred"

# Channel-plugin TTS-engine dropdown options — derived from the
# TTSEngine registry (aifred.lib.tts_engines). Each engine declares
# ``suitable_for_channels`` itself, so adding a new engine to the
# FreeEcho-style dropdowns is a one-line change in its TTSEngine class.
# The previous TTS_ENGINE_SHORT_LABELS and TTS_ENGINE_KEYS_FOR_CHANNELS
# constants are gone — they were duplicates of metadata that now lives
# on the engine classes directly.

def get_tts_engine_channel_options() -> list[tuple[str, str]]:
    """SSOT for the channel-plugin TTS-engine dropdown options.

    Returns a list of (key, short_label) pairs in registry order,
    filtered to engines that ``suitable_for_channels``.
    """
    from .tts_engines import channel_engine_options
    return channel_engine_options()

# ============================================================
# TTS engine specifics live in aifred/lib/tts_engines/<engine>.py.
# Each engine class owns its service URL, voice fallback list, compose
# directory, VRAM reserve, and Docker image name. config.py stays
# engine-agnostic on purpose — adding a new engine is a one-file drop.
# ============================================================

# ============================================================
# TTS Container Keep-Alive (heartbeat ping interval)
# ============================================================
# While a long-running pipeline (FreeEcho.2 inference, browser web research)
# holds a GPU TTS engine, AIfred pings ``/keep_alive`` on the container
# every TTS_KEEPALIVE_INTERVAL_SECONDS to reset its idle timer.
# Container-side timeout is set via XTTS_KEEP_ALIVE / MOSS_KEEP_ALIVE
# env vars in the docker-compose files (default: 30 minutes).
# Choose this interval comfortably below the container timeout so a
# single missed ping (network hiccup) is harmless. Default 5 min.
TTS_KEEPALIVE_INTERVAL_SECONDS = 300

# Per-request HTTP timeout when pinging /keep_alive — should be tiny,
# the endpoint just resets a timer and returns immediately.
TTS_KEEPALIVE_HTTP_TIMEOUT = 5

# Long text used for the calibration-time test inference that drives the
# Qwen3-TTS KV-cache up to its real-world high-water mark. About ~800
# chars — same body the container's removed warmup pass used to use.
# Per-agent TTS language override — the user can pin an agent to a
# specific synthesis language (so e.g. "Sokrates always speaks English
# with a German accent" works as a stylistic choice). "auto" preserves
# the previous behaviour: detected LLM language → UI language fallback.
# The 10 listed languages are exactly the ones Qwen3-TTS supports;
# XTTS / MOSS / Edge accept their own language tags and ignore unknown
# values, so the option set is safe to expose for every engine.
TTS_LANGUAGE_OPTIONS: list[tuple[str, str]] = [
    ("auto", "Auto"),
    # Europäische Sprachen zuerst (Auswahl-Komfort)
    ("de", "Deutsch"),
    ("en", "English"),
    ("fr", "Français"),
    ("it", "Italiano"),
    ("es", "Español"),
    ("pt", "Português"),
    ("ru", "Русский"),
    # Asiatische Sprachen am Schluss
    ("zh", "Chinese"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
]
TTS_LANGUAGE_LABELS = [label for _, label in TTS_LANGUAGE_OPTIONS]
TTS_LANGUAGE_LABEL_TO_CODE = {label: code for code, label in TTS_LANGUAGE_OPTIONS}
TTS_LANGUAGE_CODE_TO_LABEL = {code: label for code, label in TTS_LANGUAGE_OPTIONS}


def sort_voices_custom_first(voices: list[str]) -> list[str]:
    """Sort voices: ★ custom voices first, then built-in alphabetically."""
    custom = sorted(v for v in voices if v.startswith("★"))
    builtin = sorted(v for v in voices if not v.startswith("★"))
    return custom + builtin


# ============================================================
# DEFAULT TTS VOICES PER LANGUAGE
# ============================================================
# When UI language changes, these voices are selected as defaults.
# User can override in Settings → saved per language in assistant_settings.json
TTS_DEFAULT_VOICES = {
    "edge": {
        "de": "Deutsch (Katja)",
        "en": "Englisch (Jenny)",
    },
    "piper": {
        "de": "Deutsch (Thorsten)",
        "en": "Deutsch (Thorsten)",  # No English Piper model yet
    },
    "espeak": {
        "de": "Deutsch Standard",
        "en": "Englisch mbrola UK (M)",  # User preference: en1
    },
    "xtts": {
        "de": "★ AIfred",  # Custom voice
        "en": "★ AIfred",  # Custom voice (multilingual)
    },
    "moss": {
        "de": "AIfred",  # Custom voice
        "en": "AIfred",  # Custom voice (multilingual)
    },
    "fishspeech": {
        "de": "AIfred",  # Custom cloned voice
        "en": "AIfred",  # Custom cloned voice (multilingual)
    },
    "dashscope": {
        "de": "★ AIfred",
        "en": "★ AIfred",
    },
}

# Per-agent TTS voice defaults moved to data/agents.json under each
# agent's ``tts_voices`` block. Access them via
# ``aifred.lib.agent_config.get_tts_voice_default(agent_id, engine)`` or
# ``get_tts_voice_defaults_for_engine(engine)``.

# Per-engine TTS toggle defaults (autoplay, streaming)
# MOSS-TTS: streaming=False because ~20s per sentence (not suitable for real-time)
# XTTS/Edge: streaming=True (fast enough for sentence-by-sentence)
# Piper/eSpeak: streaming=False (local, instant, full response preferred)
TTS_TOGGLE_DEFAULTS: dict[str, dict[str, bool]] = {
    "xtts": {"autoplay": True, "streaming": True},
    "moss": {"autoplay": True, "streaming": False},
    "fishspeech": {"autoplay": True, "streaming": True},
    "edge": {"autoplay": True, "streaming": True},
    "piper": {"autoplay": True, "streaming": False},
    "espeak": {"autoplay": True, "streaming": False},
    "dashscope": {"autoplay": True, "streaming": True},
}

# ============================================================
# CONTEXT MANAGEMENT
# ============================================================
# Maximum tokens for RAG context (research results)
# Note: Total Context = RAG_CONTEXT + System-Prompt + History + User-Message
# For 40k model limit → recommended: 20k RAG context (50% reserve)
# For larger models (e.g., with Tesla P40) this value can be increased
MAX_RAG_CONTEXT_TOKENS = 20000

# Maximum words per single source (Wikipedia, news articles, etc.)
# Prevents a single source from dominating the entire context
MAX_WORDS_PER_SOURCE = 2000

# Maximum words for single-source research (Direct URL)
# For only 1 source (e.g., PDF analysis, scientific paper) we need the full document
# Typical scientific paper: 4000-8000 words
# Longer reviews/guidelines: up to 15000 words
MAX_WORDS_SINGLE_SOURCE = 12000

# Token-to-character ratio for context calculation
# German/English mix: ~3 characters per token
CHARS_PER_TOKEN = 3

# Token-to-character ratio for history/prompt token estimation.
# Deliberately less conservative than CHARS_PER_TOKEN: RAG budgeting
# over-estimates tokens for safety, history compression triggers are
# calibrated against 3.5 chars/token for German text.
HISTORY_CHARS_PER_TOKEN = 3.5

# ============================================================
# CONTEXT ESTIMATION CONSTANTS
# ============================================================
# Token estimates for system prompt, history and user input
# Used for VRAM-based context calculation

# System prompt token estimate (RAG mode)
SYSTEM_PROMPT_ESTIMATE_RAG = 2000  # RAG system prompt is ~2K tokens

# System prompt token estimate (Cache-Hit mode - slightly larger)
SYSTEM_PROMPT_ESTIMATE_CACHE = 2500  # Cache-Hit prompt with extra context

# Token estimate per history turn (question + answer)
TOKENS_PER_HISTORY_TURN = 500  # Rough estimate: 500 tok/turn

# ============================================================
# AUTOMATIK-LLM CONTEXT CONSTANTS
# ============================================================
# Context window for Automatik-LLM tasks (Decision, Query-Opt, Intent, RAG-Check, URL-Ranking)
# CRITICAL: Models like Qwen3:4B have 262K default context!
# Without explicit num_ctx, Ollama allocates HUGE KV-Cache across all GPUs.
# 12K is sufficient for all Automatik tasks including URL ranking with 30+ URLs.
# Note: num_ctx only affects max context size, NOT processing speed!
AUTOMATIK_LLM_NUM_CTX = 12288  # 12K context for all Automatik tasks

# Maximum tokens for the session title generation call. Hoch genug, damit auch
# Thinking-Modelle (z.B. Step-3.5-Flash) genug Budget haben um nach langem
# Reasoning den eigentlichen Titel auszugeben. Bei Instruct-Modellen schadet
# das Limit nicht — der EOS-Token greift bei der erwarteten 5-10-Wort-Antwort
# laengst vor diesem Cap.
SESSION_TITLE_NUM_PREDICT = 2000

# Hard timeout fuer den Session-Title-Call. Muss zur SESSION_TITLE_NUM_PREDICT
# passen: bei 30 tok/s und 2000 Tokens braucht das ~70 s, bei 100 tok/s nur 20 s.
# Obergrenze fuer die Titel-Generierung. Sehr grosse MoE-Modelle (z.B. das
# 397B mit ~127 s TTFT allein) brauchen deutlich laenger, bis der Titel-Call
# durchlaeuft — 120 s reichten da nicht. 300 s gibt auch den langsamsten
# Modellen Raum. Nur eine Notbremse: ist der Titel frueher fertig, schliesst
# der "processing"-Toast frueher.
SESSION_TITLE_TIMEOUT_SECONDS = 300.0

# Heartbeat-Intervall (s) fuer lang laufende Tool-Calls: solange ein Tool
# awaited wird (z. B. VLM-Analyse > 60 s), sendet die Pipeline alle N s einen
# tool_progress-Tick Richtung Browser. Ohne den Tick ist die Antwort-
# Verbindung byte-still und Proxies mit Read-Timeout (nginx-Default 60 s)
# kappen die Leitung — der ganze Turn stirbt dann per Task-Cancel.
TOOL_HEARTBEAT_INTERVAL_SEC = 20.0

# ============================================================
# LOOKUP-CACHE GARBAGE COLLECTION
# ============================================================
# llama.cpp's Speculative-Decoding-Lookup-Cache (--lookup-cache-dynamic) waechst
# monoton mit jedem indexierten n-gram. Ohne Eviction-Logik wuerde die Datei
# pro Modell nach Wochen-Monaten in den Multi-GB-Bereich kommen. Da aktuelle
# Patterns wertvoller sind als historische und der Cache nach Loeschung in
# 1-2 Tagen Normal-Use wieder voll aufgebaut ist, ist Loeschen bei Schwellwert-
# Ueberschreitung pragmatischer als Truncation des Binaerformats.
LOOKUP_CACHE_MAX_BYTES = 300 * 1024 * 1024  # 300 MB pro Lookup-Cache-Datei
LOOKUP_CACHE_GLOB = "/home/mp/.cache/llama_lookup_*.bin"

# Gemeinsamer Wartungsslot fuer alle Background-GC-Tasks (Lookup-Cache,
# Vector-Cache, Audio-State). 03:00 lokale Zeit — vorhersagbar, ausserhalb
# der typischen Arbeitszeit, kein Cleanup waehrend der Nutzung.
GARBAGE_COLLECTION_HOUR = 3

# Fallback context for Main LLM (AIfred, Sokrates, Salomo) when not VRAM-calibrated
# Used when a model has no calibration data in the VRAM cache.
# 32K is a safe default that works on most GPUs without triggering CPU offload.
# For optimal performance, models should be calibrated via the Model Manager.
MAIN_LLM_FALLBACK_CONTEXT = 32768  # 32K context for uncalibrated local models
CLOUD_API_FALLBACK_CONTEXT = 131072  # 128K context for cloud API models (most support 128k+)

# Maximum manual num_ctx value (for UI input validation)
# 2M tokens should cover even the largest context windows (Gemini 2M, future models)
NUM_CTX_MANUAL_MAX = 2097152  # 2M tokens

# Minimum context for Ollama calibration binary search
# This is the lower bound - models with context < this are unusable for conversation
# 8K ensures models can handle multi-turn conversations and summaries
# If GPU-only calibration yields < 8K, Hybrid calibration is triggered
CALIBRATION_MIN_CONTEXT = 8192  # 8K minimum for usable context

# ============================================================
# HYBRID MODE THRESHOLD CONFIGURATION
# ============================================================
# When VRAM-only calibration yields less than this, switch to Hybrid mode.
# Also used as minimum context target for the Speed variant in dual calibration.
# 32K is sufficient for multi-turn chat with RAG, system prompts, and reasoning.
MIN_USEFUL_CONTEXT_TOKENS = 32768  # 32K - below this, VRAM-only is not useful

# Minimum free RAM to maintain during Hybrid mode calibration.
# This is a FIXED reserve (not dynamic) to ensure system stability.
# 3 GB leaves enough headroom for OS, browser, and other processes.
MIN_FREE_RAM_MB = 3072  # 3 GB fixed RAM reserve for Hybrid mode

# Maximum allowed swap increase during a single calibration test.
# If swap increases by more than this during model load, the context is too large.
# This prevents the "infinite swap" problem where Linux keeps swapping to make
# RAM "available", hiding the fact that the system is overloaded.
# 512 MB allows for minor swap activity but catches excessive swapping.
MAX_SWAP_INCREASE_MB = 512  # Max swap increase per test iteration

# ============================================================
# VISION/OCR CONTEXT CONSTANTS
# ============================================================
# NOTE: Vision models now use the CALIBRATED num_ctx from model_vram_cache.json
# This is more accurate than any hardcoded calculation because:
# - The calibration is done on THIS hardware with THIS model
# - Thinking models need full context for <think> blocks (can be 40K+ tokens)
# - No more guessing with arbitrary "response reserves"
#
# The old constants (VISION_MINIMUM_CONTEXT, VISION_RESPONSE_RESERVE) were removed
# because they led to incorrect 15K context limits for models that support 160K+.

# ============================================================
# WEB SCRAPING CONSTANTS
# ============================================================
# Playwright fallback threshold for web scraping
# When trafilatura extracts fewer words than this,
# Playwright (headless browser) is tried as fallback
PLAYWRIGHT_FALLBACK_THRESHOLD = 800  # words - below this value Playwright is tried

# Non-scrapable domains are loaded from data/non_scrapable_domains.txt
# (Single Source of Truth - one domain per line, easy to maintain)

# ============================================================
# HISTORY SUMMARIZATION CONFIGURATION
# ============================================================
# Trigger: At what percentage of context limit should compression occur?
HISTORY_COMPRESSION_TRIGGER = 0.7  # 70% - when to compress

# Target: Compress down to this percentage (aggressive, leaves room for ~2 roundtrips)
HISTORY_COMPRESSION_TARGET = 0.3  # 30% - where to compress to

# Summary size: Percentage of content being compressed (4:1 compression ratio)
HISTORY_SUMMARY_RATIO = 0.25  # 25% of compressed content = 4:1 ratio

# Minimum summary size in tokens (for very small compressions)
HISTORY_SUMMARY_MIN_TOKENS = 500

# Tolerance: How much larger than target is acceptable before truncation
HISTORY_SUMMARY_TOLERANCE = 0.5  # 50% over target allowed, above that: truncate

# Maximum number of summaries stored (FIFO when exceeded)
HISTORY_MAX_SUMMARIES = 10

# Maximum percentage of context that can be used by summaries
# Used for dynamic max_summaries calculation based on context size
HISTORY_SUMMARY_MAX_RATIO = 0.2  # 20% of context for summaries

# Temperature for summary generation (lower = more factual)
HISTORY_SUMMARY_TEMPERATURE = 0.3

# ============================================================
# INTENT-BASED TEMPERATURE (Auto-Temperature Mode)
# ============================================================
# Temperature values for automatic intent-based temperature selection.
# Used when temperature_mode="auto" in settings.

# Factual queries: precise, deterministic answers (research, facts, code)
INTENT_TEMPERATURE_FAKTISCH = 0.2

# Mixed queries: general conversation, explanations
INTENT_TEMPERATURE_GEMISCHT = 0.5

# Creative queries: stories, poems, brainstorming (higher = more creative)
INTENT_TEMPERATURE_KREATIV = 1.0

# Temperature offsets for multi-agent mode (auto temperature)
# Sokrates and Salomo get slightly higher temperatures for more varied responses
SOKRATES_TEMPERATURE_OFFSET = 0.2  # Sokrates = AIfred + 0.2
SALOMO_TEMPERATURE_OFFSET = 0.3   # Salomo = AIfred + 0.3 (wisest, most creative)

# ============================================================
# DEFAULT SAMPLING PARAMETERS (Per-Agent Configurable)
# ============================================================
DEFAULT_TOP_K = 40          # Top-K sampling (0 = disabled)
DEFAULT_TOP_P = 0.9         # Top-P (nucleus) sampling
DEFAULT_MIN_P = 0.05        # Min-P sampling (0 = disabled)
DEFAULT_REPEAT_PENALTY = 1.1  # Repetition penalty (1.0 = disabled)

# llama-server built-in defaults (used for reset when no YAML overrides exist)
LLAMASERVER_DEFAULT_TEMPERATURE = 0.8
LLAMASERVER_DEFAULT_TOP_K = 40
LLAMASERVER_DEFAULT_TOP_P = 0.95
LLAMASERVER_DEFAULT_MIN_P = 0.1
LLAMASERVER_DEFAULT_REPEAT_PENALTY = 1.0

# Thinking-mode detection probe temperature (used in calibration/testing)
THINKING_PROBE_TEMPERATURE = 0.6
# Vision model temperature (low for factual/deterministic output)
VISION_MODEL_TEMPERATURE = 0.1

# ============================================================
# VISION SAMPLING DEFAULTS (Qwen3-VL recommended values)
# ============================================================
VISION_DEFAULT_TEMPERATURE = 0.7
VISION_DEFAULT_TOP_K = 20
VISION_DEFAULT_TOP_P = 0.8
VISION_DEFAULT_MIN_P = 0.0
VISION_DEFAULT_REPEAT_PENALTY = 1.0

# ============================================================
# FACE-DETECTION PROVIDER (InsightFace / onnxruntime)
# ============================================================
# InsightFace buffalo_l läuft bei 640×480 auf CPU schnell genug
# (~30–80 ms je Detection) und belegt keine GPU dauerhaft. Erst bei
# höherer Auflösung oder vielen parallelen Cams lohnt sich CUDA.
#
# - False (Default): nur CPUExecutionProvider — GPU bleibt frei für
#   andere Modelle (VLM, TTS, LLMs)
# - True: bevorzugt CUDAExecutionProvider, fällt nur auf CPU zurück
#   wenn cuDNN/CUDA nicht vorgeladen werden konnten
FACE_DETECT_USE_GPU = False
# Wenn ``FACE_DETECT_USE_GPU=True``: GPU-ID für die onnxruntime-
# Session. CUDA_DEVICE_ORDER=FAST_FIRST sortiert nach Performance —
# auf der MiniPC-Anlage:
#   0,2 = Quadro RTX 8000 (48 GB, frei für LLMs)
#   1,4 = Tesla P40 (24 GB, „Resterampe" im greedy cascade)
#   3   = Tesla V100 (32 GB, TTS + Ollama-VLM festgetackert)
# Default 4 = zweite P40: niedrigste Last, 300 MB InsightFace stören
# da niemanden, RTX 8000 + V100 bleiben für die großen Modelle frei.
FACE_DETECT_GPU_ID = 4
# Qualitäts-Filter gegen Phantom-Gesichter: InsightFace' interner
# RetinaFace-Cutoff liegt bei 0.5 — knapp darüber (0.5–0.6) halluziniert
# er Gesichter in Texturen (Blattwerk, Astgabeln). Echte Gesichter
# scoren 0.8–0.95. Detections unter MIN_SCORE werden verworfen.
FACE_DETECT_MIN_SCORE = 0.65
# Mindest-Kantenlänge der Gesichts-Box in Pixeln. Unterhalb ~40 px kann
# ArcFace kein verlässliches Embedding extrahieren — das Match wäre
# Rauschen, der Alert wertlos.
FACE_DETECT_MIN_SIZE_PX = 40

# ============================================================
# PERSON DETECTION (YOLO body detection)
# ============================================================
# Vision-Modell-Gewichte (YOLO etc.) liegen im Vigilantia-Datenbaum,
# Seite an Seite mit Motion-Frames und der Vision-DB. data/* ist
# gitignored — binäre Gewichte gehören nicht in die Versionierung.
VISION_MODELS_DIR = DATA_DIR / "vigilantia" / "models"
# YOLO-Person-Detektor: erkennt GANZE Personen (Körper), ergänzend zur
# InsightFace-Gesichtserkennung. Läuft motion-gated über onnxruntime.
PERSON_DETECT_MODEL = "yolo11n.onnx"
# COCO-Klassen-Index für "person". Standard-COCO: 0.
PERSON_DETECT_CLASS_ID = 0
# Eingangsgröße (quadratisch, letterbox). 320 = schnell (~10-25 ms CPU,
# reicht für "Person ja/nein"), 640 = genauer bei kleinen/fernen Objekten.
# 640 (statt früher 480): der Detektor bestätigt jetzt auch das Edge-AI-Gate
# (Person/Fahrzeug/Tier), da zählt Trefferquote bei kleinen/fernen Objekten.
# Läuft weiter auf CPU, nur motion-/trigger-gated → der Mehraufwand
# (~480²→640² ≈ 1,8×, grob 20-45 ms statt 10-25 ms) fällt nicht ins Gewicht.
PERSON_DETECT_INPUT_SIZE = 640
# Mindest-Konfidenz, damit eine Box als Person zählt.
PERSON_DETECT_CONFIDENCE = 0.35
# IoU-Schwelle für Non-Maximum-Suppression überlappender Boxen.
PERSON_DETECT_NMS_IOU = 0.45
# GPU analog zu FACE_DETECT — Default CPU, GPU bleibt frei für LLM/VLM/TTS.
PERSON_DETECT_USE_GPU = False

# ── Edge-AI-Confirmation-Policy ───────────────────────────────────────
# Pro Edge-AI-Klasse: muss UNSER YOLO den Kamera-Trigger bestätigen,
# bevor Event + Alert entstehen? Die On-Device-KI der Reolink feuert
# besonders im IR-Nachtbild massenhaft falsch-positive Personen.
#   True  = bestätigen — YOLO muss die Klasse selbst sehen, sonst verworfen.
#   False = vertrauen   — Kamera-Behauptung gilt direkt (kein eigener Check).
# animal=False, weil das Nano-Modell kleine/ferne Tiere oft übersieht — ein
# hartes Veto würde echte Tier-Events schlucken; lieber der Kamera glauben
# (ein verpasstes Tier wiegt schwerer als ein seltener Tier-Fehlalarm).
# Umschaltbar: bei größerem YOLO-Modell kann animal auf True gesetzt werden.
EDGE_AI_CONFIRM = {
    "person": True,
    "vehicle": True,
    "animal": False,
}
# Welche COCO-Klassen-Indizes zählen als unsere Kategorie (volles COCO-80
# Modell liefert alle aus derselben Inferenz). vehicle: car/motorcycle/bus/
# truck; animal: bird/cat/dog/horse/sheep/cow/elephant/bear/zebra/giraffe.
EDGE_AI_COCO_MAP = {
    "person": [0],
    "vehicle": [2, 3, 5, 7],
    "animal": [14, 15, 16, 17, 18, 19, 20, 21, 22, 23],
}

# ============================================================
# VLM VRAM-BUDGET (gemessene Werte pro Modell)
# ============================================================
# Tatsächliche VRAM-Belegung in MiB pro Ollama-VLM-Modell. Wird vom
# Bulk-Worker (Story 4) genutzt, um vor dem Start zu prüfen ob das
# Modell auf die Ziel-GPU passt. Wenn nicht: Liste verdrängbarer
# Modelle anzeigen.
#
# Werte direkt am laufenden System gemessen via ``nvidia-smi`` nach
# einem ``prewarm_vlm()``-Call (Ollama-Overhead schon eingerechnet,
# inklusive Vision-Encoder + KV-Cache bei num_ctx=4096).
# VLM choices offered in the calibration picker. Each entry creates a
# ``<base>-vlm-<key>`` YAML variant (and a ``<base>-tts-<engine>-vlm-<key>``
# combo for every selected TTS engine). 30B is intentionally excluded —
# its 30.8 GB footprint leaves nothing for the LLM next to it on the V100;
# users who really want it still get it via ``vision_mode != off`` +
# ``vlm.model`` in plugin settings (Phase-1 fallback path).
VLM_CALIBRATION_CHOICES: list[dict[str, str]] = [
    {
        "key": "qwen3vl4b",
        "model_id": "qwen3-vl:4b-instruct-q8_0",
        "label": "Vigilantia 4B",
    },
    {
        "key": "qwen3vl8b",
        "model_id": "qwen3-vl:8b-instruct-q8_0",
        "label": "Vigilantia 8B",
    },
]


# Obergrenze für die Pixelzahl, mit der ein Frame ans VLM geht. Frames
# werden vor dem VLM-Call auf höchstens so viele Gesamtpixel herunter-
# skaliert (Seitenverhältnis erhalten); kleinere Bilder bleiben unberührt.
#
# WARUM: Das VLM beschreibt nur die Szene grob ("Person an der Tür, Garten")
# — dafür reicht ~1 MP. Volle Sensor-Auflösung kostet bei dynamischen VLMs
# (Qwen-VL skaliert ~linear mit den Pixeln) nur Vision-Tokens/VRAM/Latenz,
# ohne die Beschreibung zu verbessern. Die Gesichtserkennung läuft komplett
# SEPARAT auf dem Vollbild (eigener Pfad im Watcher, eigener Crop-Store) und
# ist von diesem Downscale NICHT betroffen — Detailerkennung bleibt voll.
#
# Gemessen qwen3-vl-4b/Ollama: ~985 Vision-Tokens je MP (linear). 0,8 MP
# (1193×671) ≈ 786 tk/Frame; bei VISION_DESCRIBE_MAX_FRAMES=10 also ~7.860
# Token für Bilder + ~120 Prompt = ~7.983 (gemessen) → passt in 9216 ctx.
# 0,8 statt 1,0 MP ist für die Szenenbeschreibung nicht unterscheidbar,
# spart aber gerade so viel ctx, dass der KV-Cache-Zuwachs minimal bleibt.
VISION_VLM_MAX_PIXELS = 800_000

# Fixer ``num_ctx`` für ALLE VLM-Anfragen (Chat-Pfad + Vigilantia-
# Pfad). Keine Calibration, kein Manual-Override — einfach ein
# vernünftiger Wert, der für die typischen Vision-Use-Cases reicht.
#
# Token-Bedarf je Bild — gemessen für qwen3-vl-4b über Ollama, ~985 tk/MP
# (dynamische Auflösung, ~linear mit den Pixeln; modell-/serving-spezifisch,
# bei anderem VLM neu messen):
#   0,6 MP   ~ 585 Tokens
#   0,8 MP   ~ 786 Tokens  (VLM-Downscale-Ziel, siehe VISION_VLM_MAX_PIXELS)
#   1,0 MP   ~ 975 Tokens
#   1080p (2,07 MP, UN-skaliert)  ~ 2.045 Tokens
#
# Worst Case = VISION_DESCRIBE_MAX_FRAMES Keyframes (NICHT die Cluster-Größe;
# der Cluster-Pfad sampelt immer max. so viele Keyframes, egal ob 8 oder 51
# Frames im Cluster). 10 Frames @ 0,8 MP = ~7.983 Token Prompt (gemessen) +
# Antwort → passt in 9216 mit Puffer. Das sind nur +1024 über dem alten 8192,
# also minimaler KV-Cache-Zuwachs (~0,15 GB). Wer mehr Frames oder höheres
# max_pixels fährt, schraubt hier hoch (SSOT). Bei Änderung VLM neu kalibrieren.
VLM_NUM_CTX = 9216

# Hard wall-clock ceiling for a single Ollama VLM call (seconds). A VLM
# request that lands on a GPU finishes in seconds; one that gets evicted
# to CPU offload (no free VRAM next to a large resident LLM) runs for many
# minutes with no upper bound — the ollama AsyncClient has no default
# timeout, so the call would hang forever and the bulk worker with it.
# This bound turns that hang into a clean error the caller can handle.
VLM_CALL_TIMEOUT_S = 300

# ============================================================
# DEBUG LOG PERSISTENCE
# ============================================================
# Maximum number of debug log entries to persist in session
# Allows debug log to survive browser refresh during long inferences
DEBUG_LOG_MAX_ENTRIES = 250


# Log RAW messages sent to LLMs (debug.log only)
# Useful for debugging prompt injection issues
# Shows full message list with role and content preview for each LLM call
DEBUG_LOG_RAW_MESSAGES = False

# Log the raw VLM response text to aifred_debug.log on every vision_analyze
# call. Metriken (TTFT, tok/s, inference) werden IMMER geloggt — diese
# Konstante steuert nur ob der vollständige beschreibende VLM-Text auch
# nochmal komplett ins Log geschrieben wird. Nützlich um genau zu sehen
# ob eine falsche Bildbeschreibung schon vom VLM kommt oder erst von
# AIfreds nachfolgender Verarbeitung.
DEBUG_LOG_VLM_RAW = False

# Continuous-watch VLM history depth — how many of the last
# descriptions get prepended to the prompt as "previous observations"
# so the VLM can write a delta ("unverändert" / "die Person hat sich
# umgedreht") instead of a full fresh report every tick.
# 0 = no history (fully stateless), 10 ≈ 50 s of watch coverage at the
# default 5 s cooldown (~500 tokens of context, fits comfortably in
# the 4K window). Higher = longer memory but risk of the VLM echoing
# its own history instead of describing the image.
VISION_VLM_CONTINUOUS_HISTORY = 10

# ============================================================
# VIGILANTIA BULK-DESCRIBE CLUSTERING
# ============================================================
# Clustering gruppiert Motion-/Face-/Person-Events zu Vorkommnissen —
# ein Vorkommnis = ein Cluster = ein VLM-Call + ein Alert. LÜCKENBASIERT:
# solange Bewegung mit höchstens GAP_SECONDS Abstand weiterläuft, bleibt
# es dasselbe Vorkommnis; eine größere Lücke (Szene leer) öffnet ein neues.
#
# * GAP_SECONDS — Maximale Bewegungs-Lücke innerhalb EINES Vorkommnisses.
#   Eine Person an der Türkamera steht mal kurz still — unter dieser Lücke
#   gilt das noch als derselbe Besuch. Geht jemand weg und kommt wieder
#   (Lücke größer), ist es ein neues Vorkommnis = neuer Alert. 10 s trennt
#   getrennte Besucher zuverlässig, ohne einen Besuch zu zerstückeln.
# * MAX_SECONDS — Harte Obergrenze für die Cluster-Dauer als Sicherheitsnetz
#   gegen Dauerbewegung (wehender Baum, belebte Straße): auch bei
#   lückenloser Bewegung wird spätestens danach ein neuer Cluster
#   aufgemacht, sonst entstünde ein ewiger Cluster. 300 = 5 Min.
VISION_CLUSTER_GAP_SECONDS = 10
VISION_CLUSTER_MAX_SECONDS = 300

# Beschreibung (VLM) pro Cluster: maximale Anzahl Keyframes, die als
# zeitliche Bildfolge ans VLM gehen. Statt gleichmäßigem Sampling wird die
# Cluster-Zeitspanne in so viele Zeit-Fächer geteilt und je Fach das Frame
# mit der größten pHash-Differenz zum zuletzt gewählten genommen — regelmäßig
# über die Zeit verteilt UND an den Änderungspunkten (Tür auf, Person tritt
# ein). So sieht das VLM den Ablauf statt eines statischen Einzelbilds.
#
# 10 ist der Sweet Spot für das 4B-VLM: genug zeitliche Auflösung (bei einem
# durchgehenden ~1-min-Cluster ein Bild alle ~6 s), ohne dass die Qualität
# über zu viele fast gleiche Bilder kippt (das kleine Modell verliert dann
# den Faden / halluziniert Bewegung). 10 @ 0,8 MP passt in VLM_NUM_CTX=9216;
# höher gehen heißt num_ctx mitziehen (siehe dort) und ggf. 8B-VLM.
VISION_DESCRIBE_MAX_FRAMES = 10

# ============================================================
# PROAKTIVE ALERTS
# ============================================================
# Wie der Alert-Text erzeugt wird, wenn eine Regel nichts anderes vorgibt:
#   "template" — fester Formatstring, deterministisch, kein LLM (Default)
#   "llm"      — AIfred formuliert via process_inbound (ein LLM-Call pro
#                Alert; sinnvoll z.B. für gesprochene Puck-Ausgabe), sieht
#                aber NUR Titel+Body, nicht das Bild
#   "vlm"      — das aktive VLM beschreibt den Alert-Frame (ein VLM-Call,
#                Einzelbild über VISION_VLM_MAX_PIXELS), die Beschreibung
#                geht in den Alert-Body. So weiß die Telegram-Meldung was
#                tatsächlich auf dem Foto ist.
#   "vlm+llm"  — VLM beschreibt das Bild UND AIfred formuliert daraus den
#                finalen Text (ein VLM- + ein LLM-Call). Bildverständnis +
#                natürliche Sprache. Beim VLM-Call wird das Kamera-Briefing
#                (prompt_context) als Kontext vorangestellt.
# Pro Regel über das Feld "compose" in alert_rules.json überschreibbar.
ALERT_COMPOSE_DEFAULT = "template"

# Wie lange ein bereits ausgelöster dedup_key (Vision: cluster_id) in
# Erinnerung bleibt, sodass Wiederholungen DESSELBEN Vorkommnisses
# unterdrückt werden ("ein Alert pro Cluster"). Cluster-IDs sind
# deterministisch + zeit-gebucketed, wiederholen sich also nicht — der
# Wert begrenzt nur den Speicher. Großzügig über die Cluster-Lebensdauer.
ALERT_DEDUP_RETENTION_SEC = 1800.0

# ============================================================
# VLM Ollama hosts (orchestrated by AIfred per call)
# ============================================================
# We run two Ollama daemons:
#  * default  :11434 — visible to all GPUs, used when chat backend = ollama
#                       (so a large chat model isn't restricted to the V100)
#  * vlm-pin  :11436 — pinned to the V100 via CUDA_VISIBLE_DEVICES in its
#                       systemd unit, used when chat backend != ollama
#                       (keeps the VLM out of the llama-swap GPU pool)
#
# resolve_vlm_host() picks the right endpoint based on the live
# backend_type. If the pinned daemon isn't installed yet, callers
# fall back to the default — degrades gracefully.
VISION_VLM_HOST_DEFAULT = "http://localhost:11434"
VISION_VLM_HOST_PINNED = "http://localhost:11436"


def resolve_vlm_host(chat_backend_type: str | None = None) -> str:
    """Pick the Ollama host for VLM calls based on the active chat backend.

    * ``chat_backend_type == "ollama"`` → default daemon on :11434 (the
      chat model already lives here, no point spinning up a second one).
    * Anything else (``llamacpp``, ``vllm``, ``cloud_api``,
      or ``None``) → pinned daemon on :11436, which is restricted to
      the V100 by its systemd unit.

    If ``chat_backend_type`` is None we read it from the persisted user
    settings — useful for boot-time callers (prewarm) that don't have
    a state reference handy.
    """
    if chat_backend_type is None:
        try:
            from .settings import load_settings
            s = load_settings() or {}
            chat_backend_type = str(s.get("backend_type", "ollama"))
        except Exception:  # noqa: BLE001
            chat_backend_type = "ollama"
    return (
        VISION_VLM_HOST_DEFAULT
        if chat_backend_type == "ollama"
        else VISION_VLM_HOST_PINNED
    )

# ============================================================
# LOUDNESS NORMALIZATION (Music-Wiedergabe via FreeEcho.2)
# ============================================================
# Pro File einmal gemessen + in data/loudness.sqlite gecacht. Bei
# Wiedergabe wird (Target − gemessener LUFS) als volume-dB-Filter an
# mpv uebergeben, plus Fade-In/Out. Aenderungen hier wirken beim
# naechsten Play — kein Re-Scan noetig.
#
# Target-Lautheit fuer normalisierte Music. Streaming-Konvention:
# Spotify/YouTube ~-14 LUFS, Apple Music -16 LUFS. Wohnzimmer-tauglich
# meist -16 bis -18. Negativ = leiser.
LOUDNESS_TARGET_LUFS = -16.0

# True-Peak-Ceiling (Brick-Wall) — der Gain wird so geclamped, dass
# der True-Peak nach Anwendung nicht ueber diesem Wert liegt.
# -1 dBFS = Streaming-Standard (Headroom fuer DA-Conversion).
LOUDNESS_CEILING_DBFS = -1.0

# Fade-In am Track-Start (Sekunden). Verhindert harten Anschlag —
# 300-500 ms ist unauffaellig, darueber wirkt es zoegerlich.
LOUDNESS_FADE_IN_SEC = 1.0

# Fade-Out am natuerlichen Track-Ende (Sekunden). Bei 0 deaktiviert
# (Track endet abrupt — fuer manche Mastering-Endings korrekt). Wird
# bei sehr kurzen Tracks (< 2x Fade-Out-Laenge) automatisch
# uebersprungen, damit Fade-In und Fade-Out nicht ueberlappen.
LOUDNESS_FADE_OUT_SEC = 1.0

# ============================================================
# VRAM MANAGEMENT (Dynamic Context Calculation)
# ============================================================
# Enable VRAM-based context calculation to prevent CPU offloading
# When disabled, uses model's architectural limit only
ENABLE_VRAM_CONTEXT_CALCULATION = True

# Safety margin reserved for OS and other GPU processes (MB)
# General VRAM safety margin (vLLM, gpu_utils)
VRAM_SAFETY_MARGIN = 512  # MB

# llama.cpp VRAM safety margin — platform-dependent.
# On WSL2/Windows: WDDM silently swaps VRAM to system RAM instead of OOM → 7x slowdown.
# On native Linux: cudaMalloc returns OOM, no silent swapping — small margin sufficient.
# 64 MB covers runtime allocations (scratch buffers, cuBLAS workspace) that fit-params
# and server startup don't account for.
# Measured on WSL2: 512 → 70 tok/s (VMM), 1024 → marginal, 1536 → 137 tok/s (full speed)
_is_wddm = "microsoft" in platform.release().lower() or os.name == "nt"
LLAMACPP_VRAM_SAFETY_MARGIN = 1536 if _is_wddm else 192  # MB

# Extra margin for draft-sidecar profiles (--model-draft, e.g. DSpark):
# production OOM'd 2026-08-03 ~15 min into real chat on a profile that had
# passed verification. Root cause was the too-soft verify probe (fixed:
# probe now fills ≥2 microbatches and cycles draft/verify buffers), so the
# extra margin only covers long-run fragmentation on top. Kept small on
# purpose: on MLA models (cheap KV) every 100 MB of margin costs a six-
# figure token count of calibrated context — the opposite trade-off of
# dense-KV models. Only --model-draft profiles pay this; plain/MTP
# profiles keep the base margin.
LLAMACPP_DRAFT_SAFETY_MARGIN_EXTRA_MB = 64
LLAMACPP_CALIBRATION_PRECISION = 256  # Token step size for context binary search

# MAX_TOOL_ROUNDS ist als SSOT vom Call-Budget abgeleitet und lebt
# direkt bei seiner Quelle SECURITY_MAX_TOOL_CHAIN_DEPTH im
# SECURITY-CONFIGURATION-Block weiter unten.

# Forced-Research pipeline URL counts (quick vs deep).
# Es wird einmal gescraped, alles was klappt wird genommen — keine Re-Try-Logik.
RESEARCH_QUICK_URLS = 3
RESEARCH_DEEP_URLS = 7

# Referenz-Auflösung für die Vision-Probe der Kalibrierung (Breite, Höhe).
# Vision-Modelle (--mmproj) allozieren ihren CLIP-Compute-Buffer erst bei
# der ersten Bildanalyse, skalierend mit der Bild-Token-Anzahl. Statt
# eines pauschalen VRAM-Zuschlags (früher LLAMACPP_VISION_VRAM_RESERVE)
# schickt die Verify-Probe ein synthetisches Testbild in dieser Auflösung
# — der Vision-Bedarf ist damit real gemessen (Probe-first, 2026-07-07).
# 4K = Worst Case der Kamera-Frames (Vigilantia/Reolink).
LLAMACPP_VISION_PROBE_RESOLUTION = (3840, 2160)

# Extra headroom added on top of the stress-prewarm-measured VLM peak
# before subtracting from the LLM's VRAM budget. Covers Ollama
# compute-graph reallocation spikes and small drift between
# calibration-time measurement and production-time peak.
# Bewusst knapp (2026-06-12 geprüft): Der größte Varianz-Treiber —
# abweichendes num_ctx des auslösenden Calls — ist seit dem Clamp in
# analyze_sequence eliminiert; die gemessene Rest-Streuung der Ollama-
# Allokation liegt bei ~100 MB (4B: 6,8 vs. 6,9 GB). Ein zu knapper
# Puffer ist außerdem detektierbar statt fatal (VLM lädt degradiert →
# langsam, sichtbar via /api/ps und calibration.log) — jedes MB mehr
# wäre dauerhaft verlorenes Kontextfenster. Nach einem Ollama-Upgrade
# den Stress-Prewarm neu messen lassen (vlm_vram_cache.json löschen),
# die Peaks können sich versionsbedingt verschieben.
LLAMACPP_VLM_HEADROOM_MB = 500

# Extra headroom added on top of the stress-burn-in-measured TTS peak
# before subtracting from the LLM's VRAM budget on the TTS GPU. 512 MB
# covers minor run-to-run drift between the burn-in and production
# inference, plus container restart overhead.
LLAMACPP_TTS_BURNIN_HEADROOM_MB = 512

# Number of stress syntheses per TTS-engine burn-in. Real-world data
# (Fish-Speech, Qwen3-TTS, XTTS) shows the VRAM peak is reached by
# synthesis 1 with at most +10 MiB drift on synthesis 2 — fully covered
# by the headroom above. Default 2 = DE + EN; one warm-up, one
# steady-state. Raise this if a new engine shows late-arriving peaks.
LLAMACPP_TTS_BURNIN_ITERATIONS = 2

# Pre-calibration GPU-cleanliness guard. After Step 0 (stop TTS/VLM
# containers, unload Ollama models, stop llama-swap) the calibration must
# run with NO compute process left on the GPUs — every probe measures real
# free VRAM, so a leftover model makes the planner subtract phantom MB and
# discard valid configs (397B: a residual model triggered 64 min of OOM
# oscillation). Readiness is checked process-based (nvidia-smi
# compute-apps), NOT via memory.used — the driver keeps reserved
# page-table memory allocated after a model unloads with no process
# holding it, so a used==0 check would never pass.
#
# How long to keep polling for the GPUs to drain after cleanup before
# giving up and warning (seconds). The container/service stops return
# before the CUDA context teardown finishes, which lags a few seconds.
LLAMACPP_CALIBRATION_DRAIN_TIMEOUT_S = 30.0

# TTS VRAM reserve for tensor-split calculation (MB).
# TTS models can spike during inference (e.g. MOSS-TTS: ~350 MB peak above idle).
# Subtracted from the TTS GPU's free VRAM before computing tensor-split ratios.
LLAMACPP_TTS_VRAM_RESERVE = 512  # MB (peak spike + safety buffer)

# XTTS VRAM reservation (MB)
# Idle: ~2073 MiB, Peak during inference: ~2837 MiB (RTX 8000)
# Use peak + buffer so LLM context doesn't compete with TTS during generation.
XTTS_VRAM_MB = 2900  # MB (measured peak 2837 + 63 buffer)

# MOSS-TTS VRAM reservation (MB)
# Idle: ~13.299 MiB, Peak during inference: ~13.609 MiB (RTX 8000, 1.7B model)
# Use peak + buffer so LLM context doesn't compete with TTS during generation.
MOSS_TTS_VRAM_MB = 13700  # MB (measured peak 13609 + 91 buffer)

def get_effective_model_from_settings(agent: str = "aifred") -> str:
    """Resolve effective model ID from settings.json (no Reflex state needed).

    Headless counterpart to State._effective_model_id. Delegates suffix
    resolution to ``resolve_variant_suffix`` — the SSOT shared with the
    state-based resolver, so both pick the same variant under the same
    toggles (including the Speed+TTS fallback to TTS-base when no
    combined variant exists).
    """
    from .settings import load_settings
    settings = load_settings() or {}
    backend_type = settings.get("backend_type", "llamacpp")

    # Base model. ``speed_agent`` tracks whose Speed toggle applies:
    # an agent that shares AIfred's LLM must also share AIfred's Speed
    # toggle, otherwise Hub and browser resolve different variants and
    # llama-swap double-loads (base ↔ -speed) on every channel request.
    saved = settings.get("backend_models", {}).get(backend_type, {})
    base_id = saved.get(agent, "")
    speed_agent = agent
    if not base_id:
        # Fall back to AIfred's model (other agents share the same LLM)
        base_id = saved.get("aifred", "")
        speed_agent = "aifred"
    if not base_id:
        return str(base_id)
    # The Automatik has no independent Speed toggle — it mirrors AIfred's
    # (same rule as State._effective_automatik_id in the browser path).
    if agent == "automatik":
        speed_agent = "aifred"

    if backend_type != "llamacpp":
        # Other backends don't have llama-swap variants
        return str(base_id)

    from .agent_settings import get_persisted_tuning
    from .calibration import parse_llamaswap_config, resolve_effective_suffix

    swap_cfg = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
    has_speed_variant = f"{base_id}-speed" in swap_cfg

    suffix = resolve_effective_suffix(
        Path(LLAMASWAP_CONFIG_PATH),
        base_id,
        speed_on=get_persisted_tuning(settings, speed_agent, "speed_mode", False),
        has_speed_variant=has_speed_variant,
        tts_active=settings.get("enable_tts", False),
        tts_engine=settings.get("tts_engine", ""),
    )
    return str(base_id + suffix)


# Docker-Compose paths for non-TTS services. TTS engines derive their
# compose paths inside their respective TTSEngine class (convention:
# ``docker/tts/<compose_subdir or key>/docker-compose.yml``).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WHISPER_DOCKER_COMPOSE_PATH = os.path.join(_PROJECT_ROOT, "docker", "whisper", "docker-compose.yml")

# Whisper STT Docker Service (faster-whisper, dual-device: CPU permanent + GPU with TTL)
WHISPER_SERVICE_URL = "http://localhost:5080"

# Audio upload for STT transcription: generous sanity cap only — local Whisper
# has no API limit (the old 25 MB was OpenAI's cloud upload cap). A 2 h meeting
# as uncompressed WAV is ~1.3 GB. NOTE: uploads through the nginx reverse proxy
# are additionally capped by its client_max_body_size.
AUDIO_UPLOAD_MAX_MB = 2048

# Whisper transcribes each file in a single request (no chunking), so long
# recordings need a matching HTTP timeout. GPU large-v3 runs at roughly
# 10-20x realtime → 2 h audio ≈ 6-12 min.
WHISPER_TRANSCRIBE_TIMEOUT_S = 1800

# Device routing for audio uploads: files above this size go to the GPU
# engine (whisper-stt starts a per-request GPU worker with TTL, VRAM is
# fully released afterwards). Small mic dictations stay on the permanent
# CPU model — fast enough there, and no VRAM churn. A 2-minute voice memo
# is ~1-2 MB; meeting recordings are tens of MB.
WHISPER_GPU_MIN_FILE_MB = 10

# Duration estimation: processing time ≈ audio duration × realtime factor.
# Engine-specific, deliberately rough (content/VAD-dependent, accurate to
# ~1.5-2x) — good enough for proceed/abort decisions, not for progress bars.
WHISPER_RTF_GPU = 0.07   # medium float16 ≈ 15-20x realtime
WHISPER_RTF_CPU = 0.6    # medium int8 ≈ 1.5-2x realtime

# Uploads whose estimated transcription time exceeds this ask the user for
# confirmation before starting (estimates beyond the transcribe timeout are
# rejected outright — they cannot succeed).
WHISPER_CONFIRM_THRESHOLD_S = 180

# Before an LLM cold start, a RUNNING GPU transcription is granted this much
# time to finish (polling); afterwards the worker is force-killed — the
# interactive chat must not stall behind a stuck transcription forever.
WHISPER_RELEASE_WAIT_MAX_S = 600

# Transcripts longer than this are written to the workspace (data/documents/)
# as a text file instead of flooding the input field — the agent can then
# process them with its file tools (translate_file, read_file, …).
TRANSCRIPT_TO_WORKSPACE_THRESHOLD_CHARS = 4000

# Draft-sidecar headroom for calibration projection (--model-draft profiles,
# e.g. DSpark). The sidecar weights are exact (file size); this covers the
# draft's own KV cache (draft heads have 1-2 layers → small) plus its
# compute buffers. fit-params cannot load draft-head GGUFs ("failed to
# create llama_context"), so a parametric projection is not possible —
# the real verify probes measure the true footprint and the adaptive
# bias corrects any residual error.
LLAMACPP_DRAFT_SIDECAR_HEADROOM_MB = 2048

# Empirical ratio: MB of VRAM per context token
# Based on KV cache measurements and research:
# - LLaMA-2 7B: ~0.5 MB/token (research baseline)
# - Qwen3-4B Q4_K_M: ~0.15 MB/token (empirically tested, 120k tokens @ 21GB VRAM)
# - Qwen3-30B-A3B MoE Q4_K_M: ~0.10 MB/token (empirically tested, 26.2k tokens @ 22GB VRAM)
# Different ratios for Dense vs MoE models:
# - Dense models: Use all parameters → higher KV cache overhead → 0.15 MB/token
# - MoE models: Only activate subset of experts → lower KV cache overhead → 0.10 MB/token
VRAM_CONTEXT_RATIO_DENSE = 0.15  # ~150KB per token (Dense models)
VRAM_CONTEXT_RATIO_MOE = 0.10    # ~100KB per token (MoE models, 48% more context!)

# vLLM Context Calibration Safety Buffer (Tokens)
# Fixed token buffer applied when parsing vLLM error messages
# Compensates for constant VRAM overhead (~100 tokens) between startup attempts:
# - CUDA context switches (~50MB)
# - GPU memory fragmentation (~30MB)
# - PyTorch cache residue (~20MB)
# - vLLM's VRAM estimates have ~2-3% variance between startup attempts
# Using percentage-based buffer to scale with context size (2% of vLLM's reported max)
VLLM_CONTEXT_SAFETY_PERCENT = 0.02  # 2% safety buffer (iteratively applied to each vLLM-reported max)

# vLLM Idle Shutdown (TTL)
# Auto-stop vLLM server after this many seconds of inactivity to free VRAM.
# Server restarts automatically on next chat message (lazy-start).
VLLM_IDLE_TTL_SECONDS = 900  # 15 min (matches llama-swap default)

# ============================================================
# OLLAMA HYBRID MODE (CPU OFFLOAD) CONFIGURATION
# ============================================================
# When a model is larger than available VRAM, Ollama automatically offloads
# some layers to CPU/RAM. This "hybrid mode" requires careful RAM management
# to avoid swapping.

# ============================================================
# VECTOR CACHE CONFIGURATION (ChromaDB Similarity Thresholds)
# ============================================================
# Distance thresholds for semantic similarity (Cosine Distance)
# 0.0 = identical, 2.0 = completely different

# Normal cache query (without explicit keywords like "research")
CACHE_DISTANCE_HIGH = 0.5      # < 0.5 = HIGH confidence Cache-Hit (direct answer)

# Per-volatility cache-hit threshold for the research-tools "Phase 0"
# duplicate check: how similar must the new query be to a cached entry to
# reuse the cached answer instead of running a fresh web search?
# Stable knowledge tolerates wider matches; time-sensitive topics need to
# stay tight to avoid serving outdated facts under a slightly different
# wording. NOCACHE entries are never stored, so no threshold is needed.
CACHE_DISTANCE_PER_VOLATILITY = {
    'PERMANENT': 0.20,   # Goethe, photosynthesis — knowledge is stable
    'MONTHLY':   0.15,
    'WEEKLY':    0.10,
    'DAILY':     0.05,   # Politics, news — keep tight to avoid stale facts
}
# Fallback threshold when a cache entry has no volatility tag (legacy data)
CACHE_DISTANCE_DEFAULT = 0.05

# ============================================================
# TTL-BASED CACHE SYSTEM (Volatility Levels)
# ============================================================
# Time-To-Live values for different volatility categories
# Main LLM determines volatility via <volatility> tag in response
TTL_HOURS = {
    'NOCACHE': 0,       # NEVER cache (weather, live scores, stock prices)
    'DAILY': 24,        # News, current events, "latest developments"
    'WEEKLY': 168,      # Political updates (7 days)
    'MONTHLY': 720,     # Semi-current topics (30 days)
    'PERMANENT': None   # Timeless facts, no expiry
}

# Cache cleanup configuration
# Hinweis: Cleanup-Slot ist jetzt zentral via GARBAGE_COLLECTION_HOUR (s.o.).
CACHE_STARTUP_CLEANUP = True        # Delete expired entries on server startup

# Audio state cleanup configuration
AUDIO_STATE_CLEANUP_AGE_DAYS = 7        # Completed entries older than this are removed

# Debug logging — full toolkit JSON schemas (off by default; 70+ tools = log spam)
DEBUG_LOG_TOOLKIT_DEFINITIONS = False

# Audio index (SQLite/FTS5) — periodic mtime-based incremental sync
AUDIO_INDEX_SYNC_INTERVAL_HOURS = 24

# Media root — sandbox for the file-picker.
# Audio sources live as folders or symlinks under MEDIA_AUDIO_DIR; future
# video sources will live under MEDIA_VIDEO_DIR. Anything outside this
# tree is unreachable from the picker UI, no path-traversal possible.
MEDIA_DIR = DATA_DIR / "media"
MEDIA_AUDIO_DIR = MEDIA_DIR / "audio"
MEDIA_VIDEO_DIR = MEDIA_DIR / "video"
MEDIA_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

# Explicit research keywords ("research", "google", etc.)
# Semantic duplicate detection (time-independent)
CACHE_DISTANCE_DUPLICATE = 0.3  # < 0.3 = Very similar (semantic duplicate, always merged)
                                # Examples:
                                # - "research Python" vs "research Python Tutorial" = ~0.15
                                # - "research weather Berlin" vs "research weather Hamburg" = ~0.25
                                # - "research Python" vs "research Java" = ~0.6

# ============================================================
# AGENT MEMORY CONFIGURATION
# ============================================================
AGENT_MEMORY_COLLECTION_MAX = 1000   # Max entries per agent collection
AGENT_MEMORY_DISTANCE_THRESHOLD = 1.0  # Max distance for relevant memories (nomic-embed scale)
AGENT_MEMORY_RESULTS = 5             # Semantic search results
AGENT_MEMORY_RECENT_COUNT = 10       # Always load N most recent memories

# ============================================================
# SANDBOX (CODE EXECUTION) CONFIGURATION
# ============================================================
SANDBOX_TIMEOUT_SECONDS = 30         # Max execution time per run
SANDBOX_MAX_RAM_MB = 2048            # RLIMIT_AS for subprocess (numpy/pandas need ~1GB)
SANDBOX_MAX_OUTPUT_BYTES = 1_000_000 # Truncate stdout/stderr beyond this
SANDBOX_MAX_FILE_SIZE_MB = 512       # RLIMIT_FSIZE: single-file write cap inside the
                                     # sandbox — stops code filling the host disk/tmpfs.
SANDBOX_MAX_PROCESSES = 64           # RLIMIT_NPROC: cap child processes so a fork bomb
                                     # can't multiply past the per-process RAM limit.
SANDBOX_WORK_DIR = "/tmp/aifred_sandbox"

# Browser-Render-Tool (render_html): Playwright drives the SYSTEM Chrome
# (channel launch, no bundled browser) to render sandbox HTML, perform
# interactions (click/fill/drag) and capture console messages + screenshots
# so agents can verify their HTML/JS output actually works.
BROWSER_RENDER_CHANNEL = "chrome"            # Playwright browser channel (system Chrome)
BROWSER_RENDER_TIMEOUT_SECONDS = 60          # hard cap for the whole render session
BROWSER_RENDER_WINDOW_SIZE = "1280,800"      # viewport for screenshots
BROWSER_RENDER_DEFAULT_WAIT_MS = 2000        # settle time after load (real time —
                                             # animations run live before the shot)
BROWSER_RENDER_ACTION_TIMEOUT_MS = 5000      # per-action timeout (missing selector
                                             # fails fast instead of hanging)

# ============================================================
# EMAIL CONFIGURATION
# ============================================================
# Credentials managed by credential_broker.py (not as global variables!)
# Non-secret tuning values live plugin-local in
# aifred/plugins/channels/email_channel/config.py (each plugin owns its
# own configuration) — EMAIL_MAX_FETCH / EMAIL_MAX_BODY_CHARS /
# EMAIL_MAX_PROCESS_ATTEMPTS moved there 2026-07-06.

# ============================================================
# DISCORD CONFIGURATION
# ============================================================
# Credentials managed by credential_broker.py (not as global variables!)

# ============================================================
# MESSAGE HUB CONFIGURATION
# ============================================================
MESSAGE_HUB_OWNER = os.environ.get("MESSAGE_HUB_OWNER", "mp")  # Sessions created by hub belong to this user
EMAIL_MONITOR_AUTO_REPLY = os.environ.get("EMAIL_MONITOR_AUTO_REPLY", "false").lower() == "true"

# ============================================================
# AUTH CONFIGURATION
# ============================================================
# Server-side lifetime of the signed login cookie. The expiry timestamp is
# PART of the HMAC signature (lib/auth) — after expiry the cookie is invalid
# on the server, no matter what the browser has stored. The browser-side
# max-age (browser_storage) uses the same value so both layers agree.
AUTH_COOKIE_MAX_AGE_DAYS = int(os.environ.get("AUTH_COOKIE_MAX_AGE_DAYS", "30"))

# ============================================================
# DOCUMENT UPLOAD & RAG CONFIGURATION
# ============================================================
DOCUMENTS_DIR = DATA_DIR / "documents"
DOCUMENT_CHUNK_SIZE = 800           # Tokens per chunk. With bge-m3 (8192 token
                                     # context) this leaves ample headroom even
                                     # for token-dense German+Hebrew content.
                                     # Larger chunks = richer semantic embeddings.
DOCUMENT_CHUNK_OVERLAP = 80         # Overlap between chunks (tokens, ~10% of chunk)
DOCUMENT_EMBED_BATCH_SIZE = 64      # Chunks pro Embed-API-Call beim Indexieren.
                                     # Wenn die ganze Datei (z.B. 2686 Bibel-Chunks)
                                     # in einem einzigen Ollama-Call landet, dauert
                                     # der >90 s und granian killt den Reflex-Worker
                                     # (kein WS-Heartbeat in der Zeit). Mit 64er-Batches
                                     # dauert jeder Call ~3-4 s und der asyncio-Loop
                                     # bleibt responsiv. 64 ist ein Kompromiss
                                     # zwischen API-Overhead (kleinere Batches → mehr
                                     # Calls) und Worker-Responsiveness.
DOCUMENT_MAX_FILE_SIZE_MB = 0       # 0 = no limit
WORKSPACE_READ_MAX_BYTES = 25 * 1024 * 1024  # read_file tool: reject files larger
                                     # than this (the whole file is loaded into RAM;
                                     # a huge file would blow the worker's memory).
                                     # The model should page/line-range large files.
# ChromaDB vector store endpoint (workspace ChromaDB tools + vector_cache default).
CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))
# Web scraper: hard cap on a single fetched response body. Without it a
# malicious/compromised URL could stream gigabytes within the timeout window
# and OOM the worker (the body is buffered fully for trafilatura/PyMuPDF).
SCRAPER_MAX_RESPONSE_BYTES = 25 * 1024 * 1024
EMBEDDING_USE_GPU = True           # True = GPU (~900MB VRAM, ~10× faster — needed for large docs), False = CPU
DOCUMENT_SEARCH_MAX_RESULTS = 100   # Hard cap for the search_documents tool's n_results parameter
DOCUMENT_SEARCH_NEIGHBOR_WINDOW = 1 # ±N neighbor chunks returned per hit. Compensates for
                                     # mid-sentence chunk cuts: a hit at chunk K also returns
                                     # K-1 and K+1 so the model sees the full sentence/paragraph
                                     # context, not just a fragment. 0 = off, 1 = standard, 2 = wide.
# Relevance gating for the search_documents tool. The aifred_documents
# collection uses ChromaDB's default L2 distance. Measured on the indexed
# Bible corpus (bge-m3 embeddings): a verbatim-quote query bottoms out at
# ~0.67, a topical query sits at ~0.77-1.07, clearly off-topic text lands
# at ~1.34+. There is no sharp relevance cliff — these thresholds only
# separate on-topic from off-topic and gate the pagination hint; they do
# NOT pinpoint an exact passage.
DOCUMENT_SEARCH_DISTANCE_MAX = 1.20    # Hits beyond this are dropped as off-topic.
DOCUMENT_SEARCH_DISTANCE_STRONG = 0.85 # Below = "high" relevance. The next-page hint
                                        # is only emitted while a page is still mostly
                                        # high-relevance hits.
DOCUMENT_COLLECTION = "aifred_documents"  # ChromaDB collection name

# Tool-output budget — caps how many tokens a single tool result may occupy
# in the LLM conversation. A tool result that exceeds the budget is
# truncated (JSON-aware where possible) so the model still has room for
# its own answer. Computed dynamically from the active model's context
# window, not hard-coded — small models stay safe, large models stay free.
TOOL_OUTPUT_TOTAL_INPUT_RATIO = 0.75   # max share of context for combined input
                                        # (system + history + memory + tool result)
                                        # the remaining 25% is reserved for the answer
TOOL_OUTPUT_MIN_TOKENS = 2000          # floor — even on tight contexts the tool
                                        # may still emit at least this much, otherwise
                                        # results would be useless
DOCUMENT_ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}

# ============================================================
# EPIM DATABASE CONFIGURATION
# ============================================================
_epim_db_str = os.environ.get("EPIM_DB_PATH", "")
EPIM_ENABLED = bool(_epim_db_str)
EPIM_DB_PATH = Path(_epim_db_str) if _epim_db_str else Path()
EPIM_FB_LIB = PROJECT_ROOT / "lib" / "firebird25" / "libfbembed.so"
EPIM_FB_DIR = PROJECT_ROOT / "lib" / "firebird25"

# ============================================================
# SECURITY CONFIGURATION
# ============================================================
SECURITY_AUDIT_DB = DATA_DIR / "security" / "audit.db"
SECURITY_AUDIT_RETENTION_DAYS = 7       # tool_audit-Zeilen aelter als N Tage taeglich pruegen (sonst append-only unbegrenzt)
# Max tool calls per single LLM request (counter lives on the per-request
# ToolKit — every new user request starts at 0 again). Havarie-Deckel, kein
# Arbeits-Budget: ehrliche Selbsttest-Zyklen (Codine: schreiben → rendern →
# Screenshot → nachbessern) erreichten die alten 10 in Minuten und wurden
# beim finalen Verifikations-Render geblockt (2026-08-04). Echte Schleifen
# fängt SECURITY_MAX_IDENTICAL_TOOL_CALLS; dieser Deckel stoppt nur noch
# degenerierte Varianten-Ketten kleiner Modelle. 0 = deaktiviert.
SECURITY_MAX_TOOL_CHAIN_DEPTH = 50

# Maximum tool call rounds per LLM response — der harte Not-Aus der
# Loop-SCHLEIFE (nach Limit: Finalantwort ohne Tools erzwungen), während
# SECURITY_MAX_TOOL_CHAIN_DEPTH einzelne CALLS budgetiert (Modell bekommt
# Fehler-Results und kann reagieren). Beide teilen denselben Geist, daher
# SSOT: Rounds wird vom Call-Budget ABGELEITET statt separat gepflegt —
# zwei getrennte Zehner erzeugten 2026-08-04 den Bug, dass das Runden-
# Finale tools-lose Aufrufe erzwang, in denen DeepSeek seine Calls als
# rohen DSML-Text in die Bubble schrieb. Invariante Rounds >= Calls ist
# damit strukturell gesichert (eine Runde kann mehrere Calls enthalten,
# d.h. das Call-Budget erschöpft nie NACH den Runden). Fallback 100:
# Call-Budget 0 = "deaktiviert" darf den Not-Aus nicht mitdeaktivieren.
MAX_TOOL_ROUNDS = SECURITY_MAX_TOOL_CHAIN_DEPTH or 100
SECURITY_MAX_IDENTICAL_TOOL_CALLS = 2   # Same tool+args this many times → next identical call is refused (loop breaker; 0 = off)
SECURITY_RATE_LIMIT_WINDOW_SEC = 60     # Rate limit window in seconds
SECURITY_RATE_LIMITS: dict[str, int] = {
    "browser": 0,       # 0 = unlimited
    "email": 5,         # Max 5 tool calls per minute
    "discord": 10,
    "telegram": 10,
    "cron": 20,
    "webhook": 3,
}

# ============================================================
# HTML PREVIEW CONFIGURATION
# ============================================================
HTML_PREVIEW_MAX_FILES = 200         # LRU cache limit for data/html_preview/
SANDBOX_OUTPUT_DIR = DATA_DIR / "sandbox_output"
# Max size of a file a channel *_send tool may attach. Guards against a
# huge sandbox/upload file stalling the send or hitting provider limits
# (Telegram bot API: 50 MB, most SMTP: ~25 MB). env-overridable.
OUTBOUND_ATTACHMENT_MAX_BYTES = int(os.environ.get("OUTBOUND_ATTACHMENT_MAX_BYTES", str(20 * 1024 * 1024)))

# ============================================================
# XML TAG FORMATTING CONFIGURATION
# ============================================================
# Collapsible formatting for XML tags in AI responses
# Config dictionary defines icon, label and CSS class per tag
# ALL XML tags are recognized - this list is only for nice icons!
# Unknown tags automatically get "📄 Tagname" as fallback
def get_xml_tag_config(lang: str = "de") -> dict:
    """
    Get XML tag config with i18n labels.

    Args:
        lang: Language code ("de" or "en")

    Returns:
        Config dict with icon, label and CSS class per tag
    """
    # Import here to avoid circular imports
    from .i18n import t

    return {
        "think": {"icon": "💭", "label": t("collapsible_thinking", lang=lang), "class": "thinking-compact"},
        "analysis": {"icon": "🧠", "label": t("collapsible_thinking", lang=lang), "class": "thinking-compact"},  # GPT-OSS Harmony
        "data": {"icon": "📊", "label": t("collapsible_data", lang=lang), "class": "thinking-compact"},
        "python": {"icon": "🐍", "label": t("collapsible_python", lang=lang), "class": "thinking-compact"},
        "code": {"icon": "💻", "label": t("collapsible_code", lang=lang), "class": "thinking-compact"},
        "sql": {"icon": "🗃️", "label": t("collapsible_sql", lang=lang), "class": "thinking-compact"},
        "json": {"icon": "📋", "label": t("collapsible_json", lang=lang), "class": "thinking-compact"},
        "vlm_output": {"icon": "👁️", "label": t("collapsible_vlm", lang=lang), "class": "thinking-compact"},
        "image_descriptions": {"icon": "📷", "label": t("collapsible_image_descriptions", lang=lang), "class": "thinking-compact"},
    }



# ============================================================
# VISION/OCR CONFIGURATION
# ============================================================
# Maximum image dimension (longest edge) for Vision-LLM processing
# Images larger than this will be resized (preserving aspect ratio)
# Trade-offs:
# - 2048px: Fast inference (8-15s), low VRAM (~512MB), good for most documents
# - 3072px: Medium inference (15-25s), medium VRAM (~1-1.5GB), high detail
# - 4096px: Slow inference (25-40s), high VRAM (~2-3GB), excellent detail
VISION_MAX_IMAGE_DIMENSION = 3840  # 4K UHD - beste OCR-Qualität bei akzeptabler Inferenzzeit

# REMOVED: VISION_CONTEXT_LIMIT (v2.5.3)
# Vision context is now dynamically calculated using the same VRAM-based logic
# as the Main-LLM (via calculate_vram_based_context()). The model's intrinsic
# context limit serves as the upper bound instead of a hardcoded value.
# This allows Vision-LLMs with larger context (e.g., 131K for gemma3) to use
# more context when VRAM allows it.

# ============================================================
# UI LAYOUT CONSTANTS (Single Source of Truth for CSS)
# ============================================================
# These constants are injected as CSS custom properties (:root variables)
# and used in both Python (Reflex components) and CSS (media queries)

# Chat History Box
UI_CHAT_HISTORY_MAX_HEIGHT_DESKTOP = "70vh"    # Desktop: 70% of viewport height (dynamic scrolling)
UI_CHAT_HISTORY_MAX_HEIGHT_MOBILE = "60vh"     # Mobile: 60% viewport, leaves 40% "grip space"

# Sandbox: collapsible + iframe height
UI_SANDBOX_MAX_HEIGHT = "60vh"       # details[data-sandbox] max-height
SANDBOX_IFRAME_HEIGHT = "57vh"       # iframe height (container minus ~summary header)

# Thinking Process Collapsible (<details> tag)
UI_THINKING_MAX_HEIGHT_DESKTOP = "450px"       # Desktop: ~15-20 lines of text
UI_THINKING_MAX_HEIGHT_MOBILE = "40vh"         # Mobile: 40% viewport height

# Debug Console
UI_DEBUG_CONSOLE_MAX_HEIGHT = "60vh"           # 60% of viewport height (dynamic scrolling)

# Media Query Breakpoint
UI_MOBILE_BREAKPOINT = "768px"                 # Mobile: <= 768px, Desktop: > 768px

# ============================================================
# CONFIG VALIDATION (Safety Checks)
# ============================================================
# No validation needed - token-based compression handles all edge cases
