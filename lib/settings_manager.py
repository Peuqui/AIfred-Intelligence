"""
Settings Manager - Handle user settings persistence

This module manages loading and saving user settings to a JSON file,
including migration of old setting values to new formats.
"""

import json
from .config import SETTINGS_FILE, DEFAULT_SETTINGS
from .logging_utils import debug_print


def load_settings():
    """
    Lädt Einstellungen aus JSON-Datei mit Migration für alte Werte

    Returns:
        dict: User settings (or default settings if file doesn't exist)
    """
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)

                # Migration: Alte research_mode Werte auf neue umbenennen
                mode_migrations = {
                    "🤝 Interaktiv (variabel)": "🤖 Automatik (variabel, KI entscheidet)",
                    "🤖 Automatik (variabel)": "🤖 Automatik (variabel, KI entscheidet)",
                    "⚡ Web-Suche Schnell (mittel)": "⚡ Web-Suche Schnell (KI-analysiert, 3 beste)",
                    "🔍 Web-Suche Ausführlich (langsam)": "🔍 Web-Suche Ausführlich (KI-analysiert, 5 beste)"
                }

                old_mode = settings.get("research_mode")
                if old_mode in mode_migrations:
                    new_mode = mode_migrations[old_mode]
                    settings["research_mode"] = new_mode
                    debug_print(f"🔄 Migration: '{old_mode}' → '{new_mode}'")
                    # Settings sofort zurückspeichern
                    with open(SETTINGS_FILE, 'w', encoding='utf-8') as fw:
                        json.dump(settings, fw, indent=2, ensure_ascii=False)

                debug_print(f"✅ Settings geladen: {settings}")
                return settings
    except Exception as e:
        debug_print(f"⚠️ Fehler beim Laden der Settings: {e}")

    debug_print(f"📝 Verwende Default-Settings")
    return DEFAULT_SETTINGS.copy()


def save_settings(model, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu):
    """
    Speichert Einstellungen in JSON-Datei

    Args:
        model: Haupt-LLM Modell
        automatik_model: Automatik-LLM für Agent-Recherche
        voice: TTS Voice
        tts_speed: TTS Speed multiplier
        enable_tts: TTS aktiviert (bool)
        tts_engine: TTS Engine (Edge oder Piper)
        whisper_model: Whisper STT Model
        research_mode: Research Mode (Automatik/Aus/Schnell/Ausführlich)
        show_transcription: Transkription im Chat anzeigen (bool)
        enable_gpu: GPU-Beschleunigung aktiviert (bool)
    """
    try:
        settings = {
            "model": model,
            "automatik_model": automatik_model,
            "voice": voice,
            "tts_speed": tts_speed,
            "enable_tts": enable_tts,
            "tts_engine": tts_engine,
            "whisper_model": whisper_model,
            "research_mode": research_mode,
            "show_transcription": show_transcription,
            "enable_gpu": enable_gpu
        }
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        debug_print(f"💾 Settings gespeichert nach {SETTINGS_FILE}:")
        debug_print(f"   Haupt-LLM: {model}")
        debug_print(f"   Automatik-LLM: {automatik_model}")
        debug_print(f"   Whisper Model: {whisper_model}")
        debug_print(f"   TTS Engine: {tts_engine}")
        debug_print(f"   Voice: {voice}")
        debug_print(f"   Speed: {tts_speed}")
        debug_print(f"   TTS Enabled: {enable_tts}")
        debug_print(f"   Research Mode: {research_mode}")
        debug_print(f"   Show Transcription: {show_transcription}")
        debug_print(f"   GPU Enabled: {enable_gpu}")
    except Exception as e:
        debug_print(f"❌ Fehler beim Speichern der Settings: {e}")
        import traceback
        traceback.print_exc()
