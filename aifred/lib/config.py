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

PIPER_MODEL_PATH = PROJECT_ROOT / "piper_models" / "de_DE-thorsten-medium.onnx"

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
    "backend_type": "ollama",  # Default backend: "ollama", "vllm", "tabbyapi"
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
        "aifred_model": "qwen3:4b-instruct-2507-q4_K_M",                  # AIfred Main-LLM: GGUF Q8_0, ~32GB
        "automatik_model": "qwen3:4b-instruct-2507-q4_K_M",               # Automatik: GGUF Q4_K_M, ~2.6GB
        "sokrates_model": "qwen3:4b-instruct-2507-q4_K_M",                # Sokrates: GGUF Q4_K_M, ~2.6GB
        "salomo_model": "qwen3:4b-instruct-2507-q4_K_M",                  # Salomo: GGUF Q4_K_M, ~2.6GB
        "vision_model": "qwen3-vl:8b",                                    # Vision: Qwen3-VL 8B
    },
    "vllm": {
        "aifred_model": "cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit",   # AIfred Main-LLM: AWQ 4-bit, ~18GB (CONFIRMED)
        "automatik_model": "cpatonn/Qwen3-4B-Instruct-2507-AWQ-4bit",     # Automatik: AWQ 4-bit, ~2.8GB (CONFIRMED)
        "sokrates_model": "cpatonn/Qwen3-4B-Instruct-2507-AWQ-4bit",      # Sokrates: AWQ 4-bit, ~2.8GB
        "salomo_model": "cpatonn/Qwen3-4B-Instruct-2507-AWQ-4bit",        # Salomo: AWQ 4-bit, ~2.8GB
        "vision_model": "",                                                # Vision: Auto-detect
    },
    "tabbyapi": {
        "aifred_model": "turboderp/Qwen3-30B-A3B-exl3",                   # AIfred Main-LLM: EXL3, ~18GB (CONFIRMED)
        "automatik_model": "ArtusDev/Qwen_Qwen3-4B-Instruct-2507-EXL3",   # Automatik: EXL3, ~2.8GB (CONFIRMED)
        "sokrates_model": "ArtusDev/Qwen_Qwen3-4B-Instruct-2507-EXL3",    # Sokrates: EXL3, ~2.8GB
        "salomo_model": "ArtusDev/Qwen_Qwen3-4B-Instruct-2507-EXL3",      # Salomo: EXL3, ~2.8GB
        "vision_model": "",                                                # Vision: Auto-detect
    },
    "llamacpp": {
        "aifred_model": "qwen3-30b-a3b-instruct-2507-q8_0",               # AIfred Main-LLM: Q8_0, ~32GB (2x P40)
        "automatik_model": "qwen3-4b-instruct-2507-q4_k_m",               # Automatik: Q4_K_M, ~2.6GB
        "sokrates_model": "qwen3-8b-q4_k_m",                              # Sokrates: Q4_K_M, ~4.7GB
        "salomo_model": "qwen3-8b-q4_k_m",                                # Salomo: Q4_K_M, ~4.7GB
        "vision_model": "",                                                # Vision: Auto-detect
    },
    "cloud_api": {
        "aifred_model": "qwen-plus",                                          # Default: Qwen Plus (free tier)
        "automatik_model": "qwen-turbo",                                      # Automatik: Qwen Turbo (faster, free)
        "sokrates_model": "qwen-turbo",                                       # Sokrates: Qwen Turbo
        "salomo_model": "qwen-turbo",                                         # Salomo: Qwen Turbo
        "vision_model": "",                                                   # Vision: Not yet supported
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
DEFAULT_TABBYAPI_URL = "http://localhost:5000/v1"
DEFAULT_LLAMACPP_URL = os.environ.get("LLAMACPP_URL", "http://localhost:11435/v1")

# llama-swap / llama-server calibration
LLAMASWAP_CONFIG_PATH = Path(os.environ.get(
    "LLAMASWAP_CONFIG", str(Path.home() / ".config" / "llama-swap" / "config.yaml")
))
LLAMACPP_HEALTH_TIMEOUT = 120     # Seconds until llama-server ready (large models need 60-90s)
LLAMACPP_CALIBRATION_PORT = int(os.environ.get("LLAMACPP_CALIBRATION_PORT", "9999"))

BACKEND_URLS = {
    "ollama": DEFAULT_OLLAMA_URL,
    "vllm": DEFAULT_VLLM_URL,      # Port 8001 for dev (8000 on production MiniPC)
    "tabbyapi": DEFAULT_TABBYAPI_URL,
    "llamacpp": DEFAULT_LLAMACPP_URL,  # llama-swap proxy (see docs/en/guides/llamacpp-setup.md)
    "cloud_api": "",  # Dynamic - set based on provider selection
}

# Backend display labels (for UI dropdowns)
BACKEND_LABELS = {
    "ollama": "Ollama",
    "llamacpp": "llama.cpp",
    "tabbyapi": "TabbyAPI",
    "vllm": "vLLM",
    "cloud_api": "Cloud APIs",
}

# ============================================================
# AVAILABLE VOICES (Engine-specific)
# ============================================================
# Edge TTS Voices (Cloud - Microsoft Neural Voices)
EDGE_TTS_VOICES = {
    # Deutschland (de-DE)
    "Deutsch (Katja)": "de-DE-KatjaNeural",
    "Deutsch (Amala)": "de-DE-AmalaNeural",
    "Deutsch (Seraphina)": "de-DE-SeraphinaMultilingualNeural",
    "Deutsch (Conrad)": "de-DE-ConradNeural",
    "Deutsch (Killian)": "de-DE-KillianNeural",
    "Deutsch (Florian)": "de-DE-FlorianMultilingualNeural",
    # Österreich (de-AT)
    "Österreich (Ingrid)": "de-AT-IngridNeural",
    "Österreich (Jonas)": "de-AT-JonasNeural",
    # Schweiz (de-CH)
    "Schweiz (Leni)": "de-CH-LeniNeural",
    "Schweiz (Jan)": "de-CH-JanNeural",
    # Englisch
    "Englisch (Jenny)": "en-US-JennyNeural",
    "Englisch (Guy)": "en-US-GuyNeural",
    # Weitere Sprachen
    "Französisch (Denise)": "fr-FR-DeniseNeural",
    "Spanisch (Elvira)": "es-ES-ElviraNeural",
}

# Piper TTS Voices (Local - ONNX models)
# Format: Display Name -> (model_filename, language_code)
# Models stored in ~/.local/share/piper/
PIPER_VOICES = {
    # Deutsch - Männliche Stimmen
    "Deutsch (Thorsten)": ("de_DE-thorsten-high.onnx", "de"),
    "Deutsch (Karlsson)": ("de_DE-karlsson-low.onnx", "de"),
    # Deutsch - Weibliche Stimmen
    "Deutsch (Ramona)": ("de_DE-ramona-low.onnx", "de"),
    "Deutsch (Kerstin)": ("de_DE-kerstin-low.onnx", "de"),
    "Deutsch (Eva K)": ("de_DE-eva_k-x_low.onnx", "de"),
    "Deutsch (MLS)": ("de_DE-mls-medium.onnx", "de"),  # Multi-speaker
}

# Puck TTS fallback voice (last resort if no agent/AIfred voice configured)
PUCK_TTS_FALLBACK_VOICE = "Deutsch (Karlsson)"

# ============================================================
# TTS ENGINES
# ============================================================
TTS_ENGINE_KEYS = [
    "off",
    "xtts",
    "moss",
    "dashscope",
    "piper",
    "espeak",
    "edge",
]

# ============================================================
# XTTS v2 CONFIGURATION (Docker Service)
# ============================================================
# XTTS v2 runs as a Docker service for voice cloning and multilingual TTS
# Start with: cd docker/xtts && docker-compose up -d
XTTS_SERVICE_URL = "http://localhost:5051"

# ============================================================
# MOSS-TTS CONFIGURATION (Docker Service)
# ============================================================
# MOSS-TTS Local Transformer (1.7B) - zero-shot voice cloning, 20 languages
# Start with: cd docker/moss-tts && docker-compose up -d
MOSS_TTS_SERVICE_URL = "http://localhost:5055"

# ============================================================
# XTTS voices are loaded dynamically from the service
# Custom voices are auto-generated from WAV files in docker/xtts/voices/
# Built-in voices (58 speakers) are always available
# Use get_xtts_voices() to fetch the current list from the service

def get_xtts_voices() -> dict:
    """
    Fetch available XTTS voices from the Docker service.

    Returns:
        dict: Voice name -> voice ID mapping
              Custom voices are prefixed with "★ " in the display name
              Returns empty dict if service is unavailable
    """
    import requests

    try:
        response = requests.get(f"{XTTS_SERVICE_URL}/voices", timeout=5)
        if response.status_code == 200:
            data = response.json()
            voices = {}
            # Custom voices first (marked with ★)
            for name in data.get("custom", []):
                voices[f"★ {name}"] = name
            # Built-in voices
            for name in data.get("builtin", []):
                voices[name] = name
            return voices
    except (requests.RequestException, ValueError) as e:
        print(f"⚠️ Failed to fetch XTTS voices: {e}")
    return {}

def get_moss_voices() -> dict:
    """
    Fetch available MOSS-TTS voices from the Docker service.

    Returns:
        dict: Voice name -> voice ID mapping
              Returns empty dict if service is unavailable
    """
    import requests

    try:
        response = requests.get(f"{MOSS_TTS_SERVICE_URL}/voices", timeout=5)
        if response.status_code == 200:
            data = response.json()
            voices = {}
            for name in data.get("voices", []):
                voices[name] = name
            return voices
    except (requests.RequestException, ValueError) as e:
        print(f"⚠️ Failed to fetch MOSS-TTS voices: {e}")
    return {}

MOSS_TTS_VOICES_FALLBACK = {
    "AIfred": "AIfred",
    "Salomo": "Salomo",
    "Sokrates": "Sokrates",
}

# ============================================================
# DASHSCOPE QWEN3-TTS CONFIGURATION (Cloud API)
# ============================================================
# Cloud-based TTS via DashScope (Alibaba Cloud) - 0 GPU VRAM, 40+ voices
# Requires DASHSCOPE_API_KEY environment variable
DASHSCOPE_TTS_MODEL = "qwen3-tts-flash"
DASHSCOPE_TTS_VC_MODEL = "qwen3-tts-vc-2026-01-22"  # Voice cloning model (batch, must match enrollment target_model)
DASHSCOPE_TTS_VC_REALTIME_MODEL = "qwen3-tts-vc-realtime-2026-01-15"  # Voice cloning model (WebSocket realtime)
DASHSCOPE_TTS_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"
DASHSCOPE_WS_URL = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
DASHSCOPE_TTS_GAIN = 3.0  # Volume boost for DashScope TTS (1.0 = unchanged, 2.0 = double, etc.)

# Language mapping: ISO code -> DashScope language_type
DASHSCOPE_LANGUAGE_MAP: dict[str, str] = {
    "de": "German",
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
}

# Available DashScope voices (batch mode - sentence-based TTS / Re-Synth)
# Custom cloned voices (★ prefix) use VC model, built-in voices use flash model
DASHSCOPE_VOICES: dict[str, str] = {
    # Custom cloned voices (enrolled via DashScope Voice Enrollment API)
    "★ AIfred": "qwen-tts-vc-aifred-voice-20260215200351981-1e03",
    "★ Sokrates": "qwen-tts-vc-sokrates-voice-20260215200356508-96af",
    "★ Salomo": "qwen-tts-vc-salomo-voice-20260215200400827-48f6",
    # Built-in voices (multilingual, all support German)
    "Cherry": "Cherry",
    "Serena": "Serena",
    "Ethan": "Ethan",
    "Chelsie": "Chelsie",
    "Momo": "Momo",
    "Vivian": "Vivian",
    "Moon": "Moon",
    "Maia": "Maia",
    "Kai": "Kai",
    "Bella": "Bella",
    "Jennifer": "Jennifer",
    "Ryan": "Ryan",
    "Aiden": "Aiden",
    "Mia": "Mia",
    "Vincent": "Vincent",
    "Neil": "Neil",
    "Elias": "Elias",
    "Arthur": "Arthur",
    "Stella": "Stella",
    "Emilien": "Emilien",
    "Andre": "Andre",
    "Lenn": "Lenn",
}

# Realtime WebSocket voice IDs (for streaming during LLM generation)
# Cloned voices need separate enrollment for the realtime model
# Built-in voices use same name as batch model
DASHSCOPE_VOICES_REALTIME: dict[str, str] = {
    # Custom cloned voices (enrolled for realtime model)
    "★ AIfred": "qwen-tts-vc-aifred_rt-voice-20260215200414292-7bcd",
    "★ Sokrates": "qwen-tts-vc-sokrates_rt-voice-20260215200418894-da62",
    "★ Salomo": "qwen-tts-vc-salomo_rt-voice-20260215200423193-f528",
    # Built-in voices use same ID for realtime
    "Cherry": "Cherry",
    "Serena": "Serena",
    "Ethan": "Ethan",
    "Chelsie": "Chelsie",
    "Momo": "Momo",
    "Vivian": "Vivian",
    "Moon": "Moon",
    "Maia": "Maia",
    "Kai": "Kai",
    "Bella": "Bella",
    "Jennifer": "Jennifer",
    "Ryan": "Ryan",
    "Aiden": "Aiden",
    "Mia": "Mia",
    "Vincent": "Vincent",
    "Neil": "Neil",
    "Elias": "Elias",
    "Arthur": "Arthur",
    "Stella": "Stella",
    "Emilien": "Emilien",
    "Andre": "Andre",
    "Lenn": "Lenn",
}

def sort_voices_custom_first(voices: list[str]) -> list[str]:
    """Sort voices: ★ custom voices first, then built-in alphabetically."""
    custom = sorted(v for v in voices if v.startswith("★"))
    builtin = sorted(v for v in voices if not v.startswith("★"))
    return custom + builtin


# Fallback voices when service is unavailable (for UI initialization)
# Custom cloned voices first (★ prefix), then built-in voices
XTTS_VOICES_FALLBACK = {
    "★ AIfred": "AIfred",
    "★ Salomo": "Salomo",
    "★ Sokrates": "Sokrates",
    "Claribel Dervla": "Claribel Dervla",
    "Daisy Studious": "Daisy Studious",
    "Gracie Wise": "Gracie Wise",
    "Tammie Ema": "Tammie Ema",
    "Alison Dietlinde": "Alison Dietlinde",
}

# eSpeak Voices (Local - system package)
# Install: sudo apt install espeak-ng (or espeak)
# Format: "Display Name": ("voice_id", "language_code")
# Voice variants: +m1/+m2 = male, +f1/+f2 = female
# mbrola voices: mb/mb-deX (more natural, requires mbrola package)

# All known eSpeak voices (will be filtered by get_available_espeak_voices())
_ESPEAK_VOICES_ALL = {
    # Deutsch - Standard eSpeak (roboterhaft, always available)
    "Deutsch Standard": ("de", "de"),
    "Deutsch Männlich 1": ("de+m1", "de"),
    "Deutsch Männlich 2": ("de+m2", "de"),
    "Deutsch Weiblich 1": ("de+f1", "de"),
    "Deutsch Weiblich 2": ("de+f2", "de"),
    # Deutsch - mbrola Stimmen (natürlicher, requires mbrola + mbrola-deX packages)
    "Deutsch mbrola-2 (M)": ("mb/mb-de2", "de"),
    "Deutsch mbrola-3 (F)": ("mb/mb-de3", "de"),
    "Deutsch mbrola-4 (M)": ("mb/mb-de4", "de"),
    "Deutsch mbrola-5 (F)": ("mb/mb-de5", "de"),
    "Deutsch mbrola-6 (M)": ("mb/mb-de6", "de"),
    "Deutsch mbrola-7 (F)": ("mb/mb-de7", "de"),
    # Englisch - Standard eSpeak (always available)
    "Englisch Standard": ("en", "en"),
    "Englisch US": ("en-us", "en"),
    "Englisch UK": ("en-gb", "en"),
    # Englisch - mbrola Stimmen (requires mbrola + mbrola-en1/us1-3 packages)
    "Englisch mbrola UK (M)": ("mb/mb-en1", "en"),
    "Englisch mbrola US-1 (F)": ("mb/mb-us1", "en"),
    "Englisch mbrola US-2 (M)": ("mb/mb-us2", "en"),
    "Englisch mbrola US-3 (M)": ("mb/mb-us3", "en"),
}

def get_available_espeak_voices() -> dict:
    """
    Detect available eSpeak voices at runtime.

    Standard eSpeak voices (de, en, etc.) are always available.
    mbrola voices require: sudo apt install mbrola mbrola-deX mbrola-en1 etc.

    Returns:
        dict: Filtered ESPEAK_VOICES with only available voices
    """
    import subprocess

    available = {}

    # Get list of available mbrola voices from espeak-ng (or espeak fallback)
    mbrola_available = set()
    try:
        # Try espeak-ng first (modern), fallback to espeak (legacy)
        espeak_cmd = "espeak-ng"
        try:
            subprocess.run([espeak_cmd, "--version"], capture_output=True, timeout=2)
        except (FileNotFoundError, OSError):
            espeak_cmd = "espeak"

        result = subprocess.run(
            [espeak_cmd, "--voices=mb"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n')[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 5:
                    # File column contains voice ID like "mb/mb-de2"
                    voice_file = parts[4] if len(parts) > 4 else ""
                    if voice_file.startswith("mb/"):
                        mbrola_available.add(voice_file)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # espeak not installed or timeout

    # Filter voices: include standard voices, only include available mbrola voices
    for name, (voice_id, lang) in _ESPEAK_VOICES_ALL.items():
        if voice_id.startswith("mb/"):
            # mbrola voice - check if available
            if voice_id in mbrola_available:
                available[name] = (voice_id, lang)
        else:
            # Standard eSpeak voice - always available
            available[name] = (voice_id, lang)

    return available

# Initialize ESPEAK_VOICES with available voices (cached at module load)
ESPEAK_VOICES = get_available_espeak_voices()

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
    "dashscope": {
        "de": "★ AIfred",
        "en": "★ AIfred",
    },
}

# ============================================================
# DEFAULT AGENT VOICES PER ENGINE
# ============================================================
# When switching TTS engines, these are the default per-agent voice settings.
# User preferences are saved per engine in assistant_settings.json.
TTS_AGENT_VOICE_DEFAULTS = {
    "xtts": {
        "aifred": {"voice": "★ AIfred", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "sokrates": {"voice": "★ Sokrates", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "salomo": {"voice": "Baldur Sanjin", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "hal": {"voice": "★ HAL9000", "speed": "1.0x", "pitch": "1.0", "enabled": True},
    },
    "moss": {
        "aifred": {"voice": "AIfred", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "sokrates": {"voice": "Sokrates", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "salomo": {"voice": "Salomo", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "hal": {"voice": "HAL9000", "speed": "1.0x", "pitch": "1.0", "enabled": True},
    },
    "piper": {
        "aifred": {"voice": "Deutsch (Karlsson)", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "sokrates": {"voice": "Deutsch (Karlsson)", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "salomo": {"voice": "Deutsch (MLS)", "speed": "1.25x", "pitch": "1.0", "enabled": True},
    },
    "espeak": {
        "aifred": {"voice": "Deutsch Standard", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "sokrates": {"voice": "Deutsch Standard", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "salomo": {"voice": "Deutsch Standard", "speed": "1.25x", "pitch": "1.0", "enabled": True},
    },
    "edge": {
        "aifred": {"voice": "Deutsch (Katja)", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "sokrates": {"voice": "Deutsch (Conrad)", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "salomo": {"voice": "Deutsch (Florian)", "speed": "1.25x", "pitch": "1.0", "enabled": True},
    },
    "dashscope": {
        "aifred": {"voice": "★ AIfred", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "sokrates": {"voice": "★ Sokrates", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "salomo": {"voice": "★ Salomo", "speed": "1.25x", "pitch": "1.0", "enabled": True},
        "hal": {"voice": "★ HAL9000", "speed": "1.0x", "pitch": "1.0", "enabled": True},
    },
}

# Per-engine TTS toggle defaults (autoplay, streaming)
# MOSS-TTS: streaming=False because ~20s per sentence (not suitable for real-time)
# XTTS/Edge: streaming=True (fast enough for sentence-by-sentence)
# Piper/eSpeak: streaming=False (local, instant, full response preferred)
TTS_TOGGLE_DEFAULTS: dict[str, dict[str, bool]] = {
    "xtts": {"autoplay": True, "streaming": True},
    "moss": {"autoplay": True, "streaming": False},
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

# If f16 KV-cache reaches this context or more, prefer f16 over quantized KV.
# f16 is faster (no dequantization) and higher quality. Beyond this threshold,
# more context has diminishing returns (model attention degrades, compression kicks in).
F16_KV_PREFER_THRESHOLD = 262144  # 256K - f16 preferred if it reaches this

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
# DEBUG LOG PERSISTENCE
# ============================================================
# Maximum number of debug log entries to persist in session
# Allows debug log to survive browser refresh during long inferences
DEBUG_LOG_MAX_ENTRIES = 250

# Log RAW messages sent to LLMs (debug.log only)
# Useful for debugging prompt injection issues
# Shows full message list with role and content preview for each LLM call
DEBUG_LOG_RAW_MESSAGES = True

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
LLAMACPP_CALIBRATION_PRECISION = 256  # Token step size for context binary search

# Maximum tool call rounds per LLM response (safety net against infinite loops)
# After this limit, a final response without tools is forced.
MAX_TOOL_ROUNDS = 10

# Extra VRAM reserve for vision-language models (MB)
# VL models (--mmproj) need a CLIP compute buffer that scales with image token count.
# Measured: Qwen3-VL with 4096 max image tokens needs ~682 MiB compute buffer on the
# GPU holding the CLIP model. Without this reserve, large camera photos (3000+ tokens)
# cause cudaMalloc OOM → GGML_ASSERT crash during ggml_gallocr reallocation.
LLAMACPP_VISION_VRAM_RESERVE = 768  # MB (~682 measured + margin)

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

    Same logic as State._effective_model_id but reads from settings file.
    Use this in API/Message Hub/async contexts without State access.

    Priority: speed variant > TTS variant > base model
    """
    from .settings import load_settings
    settings = load_settings() or {}
    backend_type = settings.get("backend_type", "llamacpp")

    # Base model
    saved = settings.get("backend_models", {}).get(backend_type, {})
    model_key = f"{agent}_model" if agent != "aifred" else "aifred_model"
    base_id = saved.get(model_key, "")
    if not base_id:
        # Fall back to AIfred's model (other agents share the same LLM)
        base_id = saved.get("aifred_model", "")
    if not base_id:
        return str(base_id)

    # Speed mode
    speed_on = settings.get(f"{agent}_speed_mode", False)
    if speed_on and backend_type == "llamacpp":
        speed_id = f"{base_id}-speed"
        from .llamacpp_calibration import parse_llamaswap_config
        swap_cfg = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        if speed_id in swap_cfg:
            return speed_id

    # TTS variant — check if a GPU TTS container is actually running
    # This works for both browser (enable_tts toggle) and Puck (TTS always on).
    # Instead of relying on settings.json flags, check the real container state.
    if backend_type == "llamacpp":
        from .tts_engine_manager import _detect_running_tts_engine
        tts_engine = _detect_running_tts_engine()
        if tts_engine:
            tts_id = f"{base_id}-tts-{tts_engine}"
            from .llamacpp_calibration import parse_llamaswap_config
            swap_cfg = parse_llamaswap_config(Path(LLAMASWAP_CONFIG_PATH))
            if tts_id in swap_cfg:
                return tts_id

    return str(base_id)


# Docker-Compose paths (for container start/stop)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
XTTS_DOCKER_COMPOSE_PATH = os.path.join(_PROJECT_ROOT, "docker", "xtts", "docker-compose.yml")
MOSS_TTS_DOCKER_COMPOSE_PATH = os.path.join(_PROJECT_ROOT, "docker", "moss-tts", "docker-compose.yml")
WHISPER_DOCKER_COMPOSE_PATH = os.path.join(_PROJECT_ROOT, "docker", "whisper", "docker-compose.yml")

# Whisper STT Docker Service (faster-whisper, dual-device: CPU permanent + GPU with TTL)
WHISPER_SERVICE_URL = "http://localhost:5052"

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
CACHE_CLEANUP_INTERVAL_HOURS = 12  # Background task runs every 12 hours
CACHE_STARTUP_CLEANUP = True        # Delete expired entries on server startup

# Explicit research keywords ("research", "google", etc.)
# Semantic duplicate detection (time-independent)
CACHE_DISTANCE_DUPLICATE = 0.3  # < 0.3 = Very similar (semantic duplicate, always merged)
                                # Examples:
                                # - "research Python" vs "research Python Tutorial" = ~0.15
                                # - "research weather Berlin" vs "research weather Hamburg" = ~0.25
                                # - "research Python" vs "research Java" = ~0.6

# RAG-Mode Distance Threshold
CACHE_DISTANCE_RAG = 1.2  # < 1.2 = Similar enough for RAG context (implemented later)

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
SANDBOX_WORK_DIR = "/tmp/aifred_sandbox"
SANDBOX_ALLOWED_IMPORTS: list[str] = [
    # stdlib
    "math", "statistics", "datetime", "json", "csv", "re",
    "collections", "itertools", "functools", "operator",
    "fractions", "decimal", "random", "string", "textwrap",
    "pprint", "io", "os", "sys", "pathlib", "hashlib", "base64",
    # data science
    "numpy", "pandas", "matplotlib", "matplotlib.pyplot",
    "scipy", "sklearn", "seaborn", "plotly",
]

# ============================================================
# EMAIL CONFIGURATION
# ============================================================
# Credentials managed by credential_broker.py (not as global variables!)
# Non-secret config values that don't need the broker:
EMAIL_MAX_FETCH = 20                 # Max emails per inbox check
EMAIL_MAX_BODY_CHARS = 10_000        # Truncate email body for LLM context

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
# DOCUMENT UPLOAD & RAG CONFIGURATION
# ============================================================
DOCUMENTS_DIR = DATA_DIR / "documents"
DOCUMENT_CHUNK_SIZE = 500           # Tokens per chunk (~375 words)
DOCUMENT_CHUNK_OVERLAP = 50         # Overlap between chunks (tokens)
DOCUMENT_MAX_FILE_SIZE_MB = 0       # 0 = no limit
EMBEDDING_USE_GPU = False           # True = GPU (~900MB VRAM, fast), False = CPU (no VRAM conflict with large models)
DOCUMENT_RAG_MAX_CHUNKS = 100       # Max chunks auto-injected as RAG context per query
DOCUMENT_COLLECTION = "aifred_documents"  # ChromaDB collection name
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
SECURITY_MAX_TOOL_CHAIN_DEPTH = 10      # Max tool calls per single LLM request
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
SANDBOX_OUTPUT_MAX_FILES = 200       # LRU cache limit for data/sandbox_output/

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
