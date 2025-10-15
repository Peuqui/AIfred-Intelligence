"""
Ollama Interface Module - Ollama & Whisper Model Management

This module handles interaction with Ollama models and Whisper model loading,
including model discovery, sorting, and caching.
"""

import subprocess
import re
from faster_whisper import WhisperModel
from .config import WHISPER_MODELS
from .logging_utils import debug_print


# Global cache for loaded Whisper models (Lazy Loading)
whisper_model_cache = {}
current_whisper_model_name = None


def get_ollama_models():
    """
    Lädt alle installierten Ollama-Modelle dynamisch und sortiert sie intelligent:
    1. Nach Modell-Familie gruppiert (qwen3, qwen2.5, llama, etc.)
    2. Innerhalb der Familie nach Größe sortiert (kleinste zuerst)

    Returns:
        list: Liste sortierter Modell-Namen
    """
    try:
        result = subprocess.run(
            ['ollama', 'list'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')[1:]  # Skip header
            models = []

            for line in lines:
                if line.strip():
                    # Parse: "NAME    ID    SIZE    MODIFIED"
                    model_name = line.split()[0]  # Erste Spalte = Name
                    models.append(model_name)

            if not models:
                return ["llama3.2:3b"]  # Fallback

            # Sortier-Logik: Nach Familie + Größe
            def sort_key(model_name):
                """
                Sortier-Schlüssel: (Familie, Größe_numerisch)
                Beispiel: qwen3:8b → ("qwen3", 8.0)
                          qwen2.5:32b → ("qwen2.5", 32.0)
                          command-r → ("command-r", 0)
                """
                # Familie extrahieren (alles vor dem ersten ":")
                if ':' in model_name:
                    family, size_part = model_name.split(':', 1)
                else:
                    family = model_name
                    size_part = ""

                # Größe extrahieren (z.B. "8b", "32b", "1.7b")
                size_match = re.search(r'(\d+\.?\d*)b', size_part.lower())
                if size_match:
                    size_value = float(size_match.group(1))
                else:
                    size_value = 0  # Modelle ohne Größenangabe ganz vorne

                return (family, size_value)

            # Sortieren
            models.sort(key=sort_key)

            debug_print(f"📋 {len(models)} Ollama-Modelle gefunden (sortiert): {', '.join(models)}")
            return models

    except Exception as e:
        debug_print(f"⚠️ Fehler beim Laden der Ollama-Modelle: {e}")

    # Fallback: Hardcoded Liste
    return ["llama3.2:3b", "mistral", "llama2:13b", "mixtral:8x7b-instruct-v0.1-q4_0"]


def get_whisper_model(model_display_name):
    """
    Lädt Whisper-Modell bei Bedarf (Lazy Loading).
    Cached bereits geladene Modelle im RAM für schnellen Zugriff.

    Args:
        model_display_name: Display-Name aus WHISPER_MODELS Dict
                           (z.B. "small (466MB, bessere Qualität, multilingual)")

    Returns:
        WhisperModel: Geladenes Whisper-Modell
    """
    global current_whisper_model_name

    model_id = WHISPER_MODELS.get(model_display_name, "Systran/faster-whisper-base")

    # Prüfe ob Modell bereits im Cache
    if model_id in whisper_model_cache:
        debug_print(f"🔄 Whisper Modell aus Cache: {model_display_name}")
        current_whisper_model_name = model_display_name
        return whisper_model_cache[model_id]

    # Modell laden
    debug_print(f"⏬ Lade Whisper Modell: {model_display_name} ({model_id})")
    debug_print(f"   Dies kann beim ersten Mal einige Minuten dauern...")

    try:
        model = WhisperModel(model_id, device="cpu", compute_type="int8")
        whisper_model_cache[model_id] = model
        current_whisper_model_name = model_display_name
        debug_print(f"✅ Whisper Modell geladen: {model_display_name}")
        return model
    except Exception as e:
        debug_print(f"❌ Fehler beim Laden von {model_display_name}: {e}")
        debug_print(f"   Fallback zu base Modell")

        # Fallback zu base
        if "Systran/faster-whisper-base" not in whisper_model_cache:
            model = WhisperModel("base", device="cpu", compute_type="int8")
            whisper_model_cache["Systran/faster-whisper-base"] = model

        return whisper_model_cache["Systran/faster-whisper-base"]


def initialize_whisper_base():
    """
    Lädt base Whisper Modell beim Programm-Start vor.

    Muss beim Startup einmal aufgerufen werden.
    """
    global current_whisper_model_name

    whisper_model_cache["Systran/faster-whisper-base"] = WhisperModel(
        "base",
        device="cpu",
        compute_type="int8"
    )
    current_whisper_model_name = "base (142MB, schnell, multilingual)"
    debug_print("✅ Whisper base model pre-loaded")
