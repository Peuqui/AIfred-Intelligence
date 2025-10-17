#!/usr/bin/env python3
"""
Refactoring Script - Konvertiert aifred_intelligence.py zu neuem Modul-System

Entfernt alle Funktionen die jetzt in lib/ Modulen sind und fügt neue Imports hinzu.
"""

import re

# Lese Original-Datei
with open('aifred_intelligence.py', 'r', encoding='utf-8') as f:
    content = f.read()

# ============================================================
# NEUE IMPORTS (Ersetze alte imports durch neue)
# ============================================================

new_imports = '''import gradio as gr
import ollama
import asyncio
import os
import time
from pathlib import Path

# Lib Modules
from lib.config import (
    WHISPER_MODELS, DEFAULT_SETTINGS, VOICES, RESEARCH_MODES, TTS_ENGINES,
    SETTINGS_FILE, SSL_KEYFILE, SSL_CERTFILE
)
from lib.logging_utils import debug_print
from lib.formatting import format_thinking_process
from lib.settings_manager import load_settings, save_settings
from lib.memory_manager import smart_model_load, register_signal_handlers
from lib.ollama_interface import get_ollama_models, get_whisper_model, initialize_whisper_base
from lib.audio_processing import (
    clean_text_for_tts, transcribe_audio, generate_tts
)
from lib.agent_core import perform_agent_research, chat_interactive_mode

# Agent Tools (already external)
from agent_tools import search_web, scrape_webpage, build_context
'''

# ============================================================
# FINDE FUNKTIONEN DIE WIR ENTFERNEN MÜSSEN
# ============================================================

# Funktionen die in lib/ verschoben wurden
functions_to_remove = [
    'get_model_size',
    'get_available_memory',
    'get_loaded_models_size',
    'unload_all_models',
    'cleanup_on_exit',
    'register_signal_handlers',
    'smart_model_load',
    'get_whisper_model',
    'get_ollama_models',
    'generate_speech_edge',
    'generate_speech_piper',
    'format_thinking_process',
    'build_debug_accordion',
    'load_settings',
    'save_settings',
    'optimize_search_query',
    'ai_rate_urls',
    'perform_agent_research',
    'chat_interactive_mode'
]

# Finde Anfang der Imports bis zum DEBUG-Kommentar
import_section_end = content.find('# ============================================================\n# DEBUG KONFIGURATION')

if import_section_end == -1:
    print("❌ Could not find DEBUG section!")
    exit(1)

# Ersetze Import-Sektion
before_imports = ""  # Nichts davor
after_debug = content[import_section_end:]

# Entferne alle Funktionen die verschoben wurden
# Strategie: Finde jede Funktion und lösche bis zur nächsten Funktion oder zum Gradio-Interface

# Finde "# Settings beim Start laden" - alles davor sind Funktionsdefinitionen
settings_start = content.find('# Settings beim Start laden')
if settings_start == -1:
    print("❌ Could not find Settings section!")
    exit(1)

# Alles nach "# Settings" behalten (Settings loading + Gradio Interface)
gradio_section = content[settings_start:]

# Erstelle NEUE Funktionen die wir brauchen (Wrapper für Gradio)

wrapper_functions = '''
# ============================================================
# WRAPPER FUNCTIONS für Gradio Interface
# ============================================================

def chat_audio_step1_transcribe(audio, whisper_model_choice):
    """Schritt 1: Audio zu Text transkribieren mit Zeitmessung"""
    if audio is None or audio == "":
        return "", 0.0

    # Hole das gewählte Whisper-Modell
    whisper = get_whisper_model(whisper_model_choice)

    debug_print(f"🎙️ Whisper Modell: {whisper_model_choice}")

    # Transkription durchführen
    user_text, stt_time = transcribe_audio(audio, whisper)
    return user_text, stt_time


def chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """Schritt 2: AI-Antwort generieren mit Zeitmessung (ohne Agent)"""
    if not user_text:
        return "", history, 0.0

    # Debug-Ausgabe
    debug_print("=" * 60)
    debug_print(f"🤖 AI Model: {model_choice}")
    debug_print(f"💬 User: {user_text[:100]}{'...' if len(user_text) > 100 else ''}")
    debug_print("=" * 60)

    messages = []
    for h in history:
        # Extrahiere nur Text ohne Timing-Info für Ollama
        user_msg = h[0].split(" (STT:")[0] if " (STT:" in h[0] else h[0]
        ai_msg = h[1].split(" (Inferenz:")[0] if " (Inferenz:" in h[1] else h[1]
        messages.extend([
            {'role': 'user', 'content': user_msg},
            {'role': 'assistant', 'content': ai_msg}
        ])
    messages.append({'role': 'user', 'content': user_text})

    # Smart Model Loading: Entlade kleine Modelle wenn großes Modell kommt
    smart_model_load(model_choice)

    # Zeit messen
    start_time = time.time()
    response = ollama.chat(model=model_choice, messages=messages)
    inference_time = time.time() - start_time

    ai_text = response['message']['content']

    # User-Text mit STT-Zeit anhängen (falls vorhanden)
    user_with_time = f"{user_text} (STT: {stt_time:.1f}s)" if stt_time > 0 else user_text

    # Formatiere <think> Tags als Collapsible (falls vorhanden) mit Modell-Name und Inferenz-Zeit
    ai_text_formatted = format_thinking_process(ai_text, model_name=model_choice, inference_time=inference_time)

    # AI-Text wird später in step3 mit TTS-Zeit ergänzt
    history.append([user_with_time, ai_text_formatted])
    debug_print(f"✅ AI-Antwort generiert ({len(ai_text)} Zeichen, Inferenz: {inference_time:.1f}s)")
    debug_print("═" * 80)  # Separator nach jeder Anfrage
    return ai_text, history, inference_time


def chat_audio_step3_tts(ai_text, inference_time, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """Schritt 3: TTS Audio generieren mit Zeitmessung"""
    tts_time = 0.0

    if ai_text and enable_tts:
        # Bereinige Text für TTS
        clean_text = clean_text_for_tts(ai_text)

        # Zeit messen
        start_time = time.time()

        audio_file = generate_tts(clean_text, voice_choice, speed_choice, tts_engine)

        tts_time = time.time() - start_time
        debug_print(f"✅ TTS generiert (TTS: {tts_time:.1f}s)")
    else:
        audio_file = None

    # History aktualisieren: Letzte AI-Antwort mit Timing ergänzen
    if history:
        last_user, last_ai = history[-1]
        ai_with_time = f"{last_ai} (Inferenz: {inference_time:.1f}s, TTS: {tts_time:.1f}s)" if enable_tts else f"{last_ai} (Inferenz: {inference_time:.1f}s)"
        history[-1] = [last_user, ai_with_time]

    return audio_file, history


def chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """Text-Chat: AI-Antwort generieren mit Zeitmessung (ohne Agent)"""
    if not text_input:
        return "", history, 0.0

    # Debug-Ausgabe
    debug_print("=" * 60)
    debug_print(f"🤖 AI Model: {model_choice}")
    debug_print(f"💬 User: {text_input[:100]}{'...' if len(text_input) > 100 else ''}")
    debug_print("=" * 60)

    messages = []
    for h in history:
        # Extrahiere nur Text ohne Timing-Info für Ollama
        user_msg = h[0].split(" (STT:")[0] if " (STT:" in h[0] else h[0]
        ai_msg = h[1].split(" (Inferenz:")[0] if " (Inferenz:" in h[1] else h[1]
        messages.extend([
            {'role': 'user', 'content': user_msg},
            {'role': 'assistant', 'content': ai_msg}
        ])
    messages.append({'role': 'user', 'content': text_input})

    # Zeit messen
    start_time = time.time()
    response = ollama.chat(model=model_choice, messages=messages)
    inference_time = time.time() - start_time

    ai_text = response['message']['content']

    # Formatiere <think> Tags als Collapsible (falls vorhanden) mit Modell-Name und Inferenz-Zeit
    ai_text_formatted = format_thinking_process(ai_text, model_name=model_choice, inference_time=inference_time)

    # Text-Input hat keine STT-Zeit
    history.append([text_input, ai_text_formatted])
    debug_print(f"✅ AI-Antwort generiert ({len(ai_text)} Zeichen, Inferenz: {inference_time:.1f}s)")
    debug_print("═" * 80)  # Separator nach jeder Anfrage
    return ai_text, history, inference_time


def regenerate_tts(ai_text, voice_choice, speed_choice, enable_tts, tts_engine):
    """Generiert TTS neu für bereits vorhandenen AI-Text"""
    if not ai_text or not enable_tts:
        import gradio as gr
        return None, gr.update(interactive=False)

    # Bereinige Text für TTS
    clean_text = clean_text_for_tts(ai_text)

    # TTS generieren
    audio_file = generate_tts(clean_text, voice_choice, speed_choice, tts_engine)

    debug_print(f"🔄 TTS regeneriert")

    import gradio as gr
    return audio_file, gr.update(interactive=True)


def chat_audio_step2_with_mode(user_text, stt_time, research_mode, model_choice, automatik_model, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """
    Routing-Funktion: Entscheidet basierend auf research_mode

    Returns:
        (ai_text, history, inference_time)
    """

    if not user_text:
        return "", history, 0.0

    # Parse research_mode und route entsprechend
    if "Aus" in research_mode:
        # Standard-Pipeline ohne Agent
        debug_print(f"🧠 Modus: Eigenes Wissen (kein Agent)")
        return chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)

    elif "Schnell" in research_mode:
        # Web-Suche Schnell: Multi-API (Brave → Tavily → SearXNG) + beste 3 URLs
        debug_print(f"⚡ Modus: Web-Suche Schnell (Agent)")
        return perform_agent_research(user_text, stt_time, "quick", model_choice, automatik_model, history)

    elif "Ausführlich" in research_mode:
        # Web-Suche Ausführlich: Multi-API (Brave → Tavily → SearXNG) + beste 5 URLs
        debug_print(f"🔍 Modus: Web-Suche Ausführlich (Agent)")
        return perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history)

    elif "Automatik" in research_mode:
        # Automatik-Modus: KI entscheidet selbst, ob Recherche nötig
        debug_print(f"🤖 Modus: Automatik (KI entscheidet)")
        try:
            return chat_interactive_mode(user_text, stt_time, model_choice, automatik_model, voice_choice, speed_choice, enable_tts, tts_engine, history)
        except:
            # Fallback wenn Fehler
            debug_print("⚠️ Fallback zu Eigenes Wissen")
            return chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)

    else:
        # Fallback: Eigenes Wissen
        debug_print(f"⚠️ Unbekannter Modus: {research_mode}, fallback zu Eigenes Wissen")
        return chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)


def chat_text_step1_with_mode(text_input, research_mode, model_choice, automatik_model, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """
    Text-Chat mit Modus-Routing (ohne STT-Zeit)

    Returns:
        (ai_text, history, inference_time)
    """

    if not text_input:
        return "", history, 0.0

    # Parse research_mode und route entsprechend
    if "Aus" in research_mode:
        # Standard-Pipeline ohne Agent
        debug_print(f"🧠 Modus: Eigenes Wissen (kein Agent)")
        return chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)

    elif "Schnell" in research_mode:
        # Web-Suche Schnell: Multi-API (Brave → Tavily → SearXNG) + beste 3 URLs
        debug_print(f"⚡ Modus: Web-Suche Schnell (Agent)")
        return perform_agent_research(text_input, 0.0, "quick", model_choice, automatik_model, history)

    elif "Ausführlich" in research_mode:
        # Web-Suche Ausführlich: Multi-API (Brave → Tavily → SearXNG) + beste 5 URLs
        debug_print(f"🔍 Modus: Web-Suche Ausführlich (Agent)")
        return perform_agent_research(text_input, 0.0, "deep", model_choice, automatik_model, history)

    elif "Automatik" in research_mode:
        # Automatik-Modus: KI entscheidet selbst, ob Recherche nötig
        debug_print(f"🤖 Modus: Automatik (KI entscheidet)")
        try:
            return chat_interactive_mode(text_input, 0.0, model_choice, automatik_model, voice_choice, speed_choice, enable_tts, tts_engine, history)
        except:
            # Fallback wenn Fehler
            debug_print("⚠️ Fallback zu Eigenes Wissen")
            return chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)

    else:
        # Fallback: Eigenes Wissen
        debug_print(f"⚠️ Unbekannter Modus: {research_mode}, fallback zu Eigenes Wissen")
        return chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)


# ============================================================
# STARTUP
# ============================================================

# Register Signal Handlers für sauberen Shutdown
register_signal_handlers()

# Initialize Whisper Base Model
initialize_whisper_base()

# Load available Ollama models
models = get_ollama_models()

'''

# ============================================================
# BAUE NEUE DATEI
# ============================================================

new_content = new_imports + "\n\n" + wrapper_functions + "\n" + gradio_section

# Schreibe neue Datei
with open('aifred_intelligence_refactored.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("✅ Refactoring complete!")
print(f"   Original: 2019 lines")
print(f"   Refactored: {len(new_content.splitlines())} lines")
print("   Output: aifred_intelligence_refactored.py")
