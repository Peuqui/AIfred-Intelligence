import gradio as gr
from faster_whisper import WhisperModel
import ollama
import edge_tts
import asyncio
import os
import subprocess
import json
from pathlib import Path
import logging
import time

# Agent Tools Import
from agent_tools import search_web, scrape_webpage, build_context

# ============================================================
# DEBUG KONFIGURATION - Hier ein-/ausschalten
# ============================================================
# Setze auf False, um alle Debug-Ausgaben auszuschalten
# Setze auf True, um detaillierte Logs zu sehen (Model, TTS, etc.)
DEBUG_ENABLED = True  # True = Debug an, False = aus

# Logging Setup
logging.basicConfig(
    level=logging.DEBUG if DEBUG_ENABLED else logging.INFO,
    format='%(message)s',
    force=True
)
logger = logging.getLogger(__name__)

def debug_print(message, **kwargs):
    """Debug-Ausgabe nur wenn DEBUG_ENABLED = True (geht ins systemd journal via stdout)"""
    if DEBUG_ENABLED:
        # Nur print nutzen - systemd loggt stdout automatisch ins journal
        # logger.info() würde zu doppelten Messages führen!
        print(message, flush=True, **kwargs)

def debug_log(message):
    """Logging-basierte Debug-Ausgabe"""
    if DEBUG_ENABLED:
        logger.debug(message)

# ============================================================
# WHISPER MODEL KONFIGURATION
# ============================================================
# Verfügbare Whisper Modelle mit Beschreibungen
WHISPER_MODELS = {
    "base (142MB, schnell, multilingual)": "Systran/faster-whisper-base",
    "small (466MB, bessere Qualität, multilingual)": "Systran/faster-whisper-small",
    "turbo-multilingual (1.6GB, beste Qualität, 100 Sprachen)": "deepdml/faster-whisper-large-v3-turbo-ct2",
    "turbo-german (1.6GB, Deutsch-Spezialist)": "aseifert/faster-whisper-large-v3-turbo-german"
}

# Cache für geladene Whisper Modelle (Lazy Loading)
whisper_model_cache = {}
current_whisper_model_name = None

def get_whisper_model(model_display_name):
    """
    Lädt Whisper-Modell bei Bedarf (Lazy Loading).
    Cached bereits geladene Modelle im RAM für schnellen Zugriff.
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

# Initial: base Modell vorladen
whisper_model_cache["Systran/faster-whisper-base"] = WhisperModel("base", device="cpu", compute_type="int8")
current_whisper_model_name = "base (142MB, schnell, multilingual)"

# Piper TTS Config
PIPER_MODEL_PATH = "/home/mp/Projekte/voice-assistant/piper_models/de_DE-thorsten-medium.onnx"
PIPER_BIN = "/home/mp/Projekte/voice-assistant/venv/bin/piper"

# Settings Datei
SETTINGS_FILE = Path("/home/mp/Projekte/voice-assistant/assistant_settings.json")

# Verfügbare Ollama models - dynamisch laden
def get_ollama_models():
    """Lädt alle installierten Ollama-Modelle dynamisch"""
    try:
        result = subprocess.run(['ollama', 'list'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')[1:]  # Skip header
            models = []
            for line in lines:
                if line.strip():
                    # Parse: "NAME    ID    SIZE    MODIFIED"
                    model_name = line.split()[0]  # Erster Spalte = Name
                    models.append(model_name)
            debug_print(f"📋 {len(models)} Ollama-Modelle gefunden: {', '.join(models)}")
            return models if models else ["llama3.2:3b"]  # Fallback
    except Exception as e:
        debug_print(f"⚠️ Fehler beim Laden der Ollama-Modelle: {e}")

    # Fallback: Hardcoded Liste
    return ["llama3.2:3b", "mistral", "llama2:13b", "mixtral:8x7b-instruct-v0.1-q4_0"]

models = get_ollama_models()

# Sprachen für TTS
voices = {
    "Deutsch (Katja)": "de-DE-KatjaNeural",
    "Deutsch (Conrad)": "de-DE-ConradNeural",
    "English (Jenny)": "en-US-JennyNeural",
    "English (Guy)": "en-US-GuyNeural"
}

# Default Settings
DEFAULT_SETTINGS = {
    "model": "llama3.2:3b",
    "voice": "Deutsch (Katja)",
    "tts_speed": 1.25,
    "enable_tts": True,
    "tts_engine": "Edge TTS (Cloud, beste Qualität)",
    "whisper_model": "base (142MB, schnell, multilingual)",
    "research_mode": "⚡ Web-Suche Schnell (KI-analysiert, 3 beste)",  # Agent-Modus
    "show_transcription": False  # Neu: Transcription vor Senden zeigen
}

def load_settings():
    """Lädt Einstellungen aus JSON-Datei mit Migration für alte Werte"""
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

def save_settings(model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription):
    """Speichert Einstellungen in JSON-Datei"""
    try:
        settings = {
            "model": model,
            "voice": voice,
            "tts_speed": tts_speed,
            "enable_tts": enable_tts,
            "tts_engine": tts_engine,
            "whisper_model": whisper_model,
            "research_mode": research_mode,
            "show_transcription": show_transcription
        }
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        debug_print(f"💾 Settings gespeichert nach {SETTINGS_FILE}:")
        debug_print(f"   AI Model: {model}")
        debug_print(f"   Whisper Model: {whisper_model}")
        debug_print(f"   TTS Engine: {tts_engine}")
        debug_print(f"   Voice: {voice}")
        debug_print(f"   Speed: {tts_speed}")
        debug_print(f"   TTS Enabled: {enable_tts}")
        debug_print(f"   Research Mode: {research_mode}")
        debug_print(f"   Show Transcription: {show_transcription}")
    except Exception as e:
        debug_print(f"❌ Fehler beim Speichern der Settings: {e}")
        import traceback
        traceback.print_exc()

async def generate_speech_edge(text, voice, rate="+0%"):
    """Edge TTS - Cloud-based"""
    import time

    # Edge TTS rate Format: +X% oder -X% (z.B. "+25%" für 25% schneller)
    debug_print(f"Edge TTS DEBUG: voice={voice}, rate={rate}, text_length={len(text)}")
    tts = edge_tts.Communicate(text, voice, rate=rate)
    output_file = f"/tmp/audio_{int(time.time())}.mp3"

    # Speichern mit detailliertem Debug
    await tts.save(output_file)

    debug_print(f"Edge TTS: Audio saved to: {output_file}, size: {os.path.getsize(output_file)} bytes")

    return output_file

def generate_speech_piper(text, speed=1.0):
    """Piper TTS - Local, fast"""
    import time

    output_file = f"/tmp/audio_{int(time.time())}.wav"

    try:
        # Piper via subprocess aufrufen
        # length_scale: höher = langsamer (1.0 = normal, 0.8 = 1.25x schneller, 0.5 = 2x schneller)
        length_scale = 1.0 / speed
        debug_print(f"Piper TTS: speed={speed}, length_scale={length_scale}")

        result = subprocess.run(
            [PIPER_BIN, "--model", PIPER_MODEL_PATH, "--output_file", output_file, "--length_scale", str(length_scale)],
            input=text.encode('utf-8'),
            capture_output=True,
            timeout=30
        )

        if result.returncode == 0 and os.path.exists(output_file):
            debug_print(f"Piper TTS: Audio saved to: {output_file}, size: {os.path.getsize(output_file)} bytes")
            return output_file
        else:
            debug_print(f"Piper TTS Error: {result.stderr.decode()}")
            return None

    except Exception as e:
        debug_print(f"Piper TTS Exception: {e}")
        return None

# Stufe 1: Transkribieren
def format_thinking_process(ai_response):
    """
    Formatiert <think> Tags als Collapsible Accordion für den Chat.

    Input: "Some text <think>thinking process</think> More text"
    Output: Formatierter Text mit Collapsible für Denkprozess
    """
    import re

    # Suche nach <think>...</think> Tags
    think_pattern = r'<think>(.*?)</think>'
    match = re.search(think_pattern, ai_response, re.DOTALL)

    if match:
        thinking = match.group(1).strip()
        # Entferne ALLE Leerzeilen komplett (kompakte Darstellung)
        thinking = re.sub(r'\n\n+', '\n', thinking)
        # Entferne <think> Tags aus der Antwort
        clean_response = re.sub(think_pattern, '', ai_response, flags=re.DOTALL).strip()

        # Formatiere mit HTML Details/Summary (Gradio unterstützt HTML in Markdown)
        formatted = f"""<details style="font-size: 0.85em; color: #888; margin-bottom: 1em; margin-top: 0.2em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa;">💭 Denkprozess anzeigen</summary>
<div style="margin: 0; padding: 0.3em 0.8em; background: #3a3a3a; border-left: 3px solid #666; font-size: 0.9em; color: #e8e8e8; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%; overflow-x: hidden;">{thinking}</div>
</details>

{clean_response}"""

        return formatted
    else:
        # Keine <think> Tags gefunden, gebe Original zurück
        return ai_response

def build_debug_accordion(query_reasoning, rated_urls, ai_text, query_time=None, rating_time=None, final_time=None):
    """
    Baut Debug-Accordion für Agent-Recherche mit allen KI-Denkprozessen.

    Args:
        query_reasoning: <think> Content from Query Optimization (qwen3:8b)
        rated_urls: Liste von {'url', 'score', 'reasoning'} von URL-Rating (qwen2.5:14b)
        ai_text: Final AI response with optional <think> tags (user's model)
        query_time: Inferenz-Zeit für Query Optimization (optional)
        rating_time: Inferenz-Zeit für URL Rating (optional)
        final_time: Inferenz-Zeit für finale Antwort (optional)

    Returns:
        Formatted AI response with debug accordion prepended
    """
    import re

    debug_sections = []

    # 1. Query Optimization Reasoning (falls vorhanden)
    if query_reasoning:
        query_think = re.sub(r'\n\n+', '\n', query_reasoning)  # Kompakt
        time_suffix = f" • {query_time:.1f}s" if query_time else ""
        debug_sections.append(f"""<details style="font-size: 0.85em; color: #888; margin-bottom: 0.5em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa;">🔍 Query-Optimierung (qwen3:8b){time_suffix}</summary>
<div style="margin: 0; padding: 0.3em 0.8em; background: #3a3a3a; border-left: 3px solid #666; font-size: 0.9em; color: #e8e8e8; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%; overflow-x: hidden;">{query_think}</div>
</details>""")

    # 2. URL Rating Results (Top 5)
    if rated_urls:
        rating_text = ""
        for idx, item in enumerate(rated_urls[:5], 1):
            emoji = "✅" if item['score'] >= 7 else "⚠️" if item['score'] >= 5 else "❌"
            url_short = item['url'][:60] + '...' if len(item['url']) > 60 else item['url']
            rating_text += f"{idx}. {emoji} Score {item['score']}/10: {url_short}\n   Grund: {item['reasoning']}\n"

        rating_text = rating_text.strip()
        time_suffix = f" • {rating_time:.1f}s" if rating_time else ""
        debug_sections.append(f"""<details style="font-size: 0.85em; color: #888; margin-bottom: 0.5em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa;">📊 URL-Bewertung Top 5 (qwen2.5:14b){time_suffix}</summary>
<div style="margin: 0; padding: 0.3em 0.8em; background: #3a3a3a; border-left: 3px solid #666; font-size: 0.9em; color: #e8e8e8; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%; overflow-x: hidden;">{rating_text}</div>
</details>""")

    # 3. Final Answer <think> process (extract but don't remove yet)
    think_match = re.search(r'<think>(.*?)</think>', ai_text, re.DOTALL)
    if think_match:
        final_think = think_match.group(1).strip()
        final_think = re.sub(r'\n\n+', '\n', final_think)  # Kompakt
        time_suffix = f" • {final_time:.1f}s" if final_time else ""
        debug_sections.append(f"""<details style="font-size: 0.85em; color: #888; margin-bottom: 0.5em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa;">💭 Finale Antwort Denkprozess{time_suffix}</summary>
<div style="margin: 0; padding: 0.3em 0.8em; background: #3a3a3a; border-left: 3px solid #666; font-size: 0.9em; color: #e8e8e8; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%; overflow-x: hidden;">{final_think}</div>
</details>""")

    # Kombiniere alle Debug-Sections
    debug_accordion = "\n".join(debug_sections)

    # Entferne <think> Tags aus ai_text (clean response)
    clean_response = re.sub(r'<think>.*?</think>', '', ai_text, flags=re.DOTALL).strip()

    # Return: Debug Accordion + Clean Response
    if debug_accordion:
        return f"{debug_accordion}\n\n{clean_response}"
    else:
        return clean_response

def chat_audio_step1_transcribe(audio, whisper_model_choice):
    """Schritt 1: Audio zu Text transkribieren mit Zeitmessung"""
    if audio is None or audio == "":
        return "", 0.0

    # Hole das gewählte Whisper-Modell
    whisper = get_whisper_model(whisper_model_choice)

    debug_print(f"🎙️ Whisper Modell: {whisper_model_choice}")

    # Zeit messen
    start_time = time.time()
    segments, _ = whisper.transcribe(audio)
    stt_time = time.time() - start_time

    user_text = " ".join([s.text for s in segments])
    debug_print(f"✅ Transkription: {user_text[:100]}{'...' if len(user_text) > 100 else ''} (STT: {stt_time:.1f}s)")
    return user_text, stt_time

# Stufe 2: AI-Antwort generieren
def chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """Schritt 2: AI-Antwort generieren mit Zeitmessung"""
    if not user_text:
        return "", history, 0.0

    # Debug-Ausgabe
    debug_print("=" * 60)
    debug_print(f"🤖 AI Model: {model_choice}")
    debug_print(f"🎙️ TTS Engine: {tts_engine}")
    if "Edge" in tts_engine:
        debug_print(f"🎤 Voice: {voice_choice}")
    debug_print(f"⚡ TTS Speed: {speed_choice}x")
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

    # Zeit messen
    start_time = time.time()
    response = ollama.chat(model=model_choice, messages=messages)
    inference_time = time.time() - start_time

    ai_text = response['message']['content']

    # User-Text mit STT-Zeit anhängen (falls vorhanden)
    user_with_time = f"{user_text} (STT: {stt_time:.1f}s)" if stt_time > 0 else user_text

    # Formatiere <think> Tags als Collapsible (falls vorhanden)
    ai_text_formatted = format_thinking_process(ai_text)

    # AI-Text wird später in step3 mit TTS-Zeit ergänzt
    history.append([user_with_time, ai_text_formatted])
    debug_print(f"✅ AI-Antwort generiert ({len(ai_text)} Zeichen, Inferenz: {inference_time:.1f}s)")
    return ai_text, history, inference_time

# Stufe 3: TTS generieren
def chat_audio_step3_tts(ai_text, inference_time, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """Schritt 3: TTS Audio generieren mit Zeitmessung"""
    tts_time = 0.0

    if ai_text and enable_tts:
        # Entferne <think> Tags und Emojis aus Text für TTS (nur den reinen Text vorlesen)
        import re
        clean_text = re.sub(r'<think>.*?</think>', '', ai_text, flags=re.DOTALL).strip()
        # Entferne ALLE Emojis (umfassende Unicode-Bereiche)
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # Emoticons
            "\U0001F300-\U0001F5FF"  # Symbole & Piktogramme (inkl. Uhrzeiten 🕐-🕧)
            "\U0001F680-\U0001F6FF"  # Transport & Karten
            "\U0001F700-\U0001F77F"  # Alchemie Symbole
            "\U0001F780-\U0001F7FF"  # Geometrische Formen Extended
            "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
            "\U0001F900-\U0001F9FF"  # Supplemental Symbols & Pictographs
            "\U0001FA00-\U0001FA6F"  # Chess Symbols
            "\U0001FA70-\U0001FAFF"  # Symbols & Pictographs Extended-A
            "\U0001F1E0-\U0001F1FF"  # Flaggen
            "\U00002600-\U000027BF"  # Misc Symbole (☀️⭐)
            "\U0000FE00-\U0000FE0F"  # Variation Selectors
            "\U0001F018-\U0001F270"  # Weitere Symbole
            "\U0000238C-\U00002454"  # Misc Technical
            "\u200d"                  # Zero Width Joiner
            "\ufe0f"                  # Variation Selector
            "\u3030"                  # Wavy Dash
            "]+",
            flags=re.UNICODE
        )
        clean_text = emoji_pattern.sub(r'', clean_text).strip()

        # Entferne Markdown-Formatierung und Sonderzeichen
        clean_text = re.sub(r'\*\*', '', clean_text)  # Bold **text**
        clean_text = re.sub(r'\*', '', clean_text)    # Italic *text* oder Bullet-Points
        clean_text = re.sub(r'`', '', clean_text)     # Code `text`
        clean_text = re.sub(r'#+\s', '', clean_text)  # Markdown Headers ### Text

        # Entferne URLs (http://, https://, www.)
        clean_text = re.sub(r'https?://\S+', '', clean_text)  # http:// und https://
        clean_text = re.sub(r'www\.\S+', '', clean_text)      # www.beispiel.de

        # Zeit messen
        start_time = time.time()

        audio_file = None
        if "Piper" in tts_engine:
            # Piper TTS (lokal)
            audio_file = generate_speech_piper(clean_text, speed_choice)
        else:
            # Edge TTS (Cloud)
            rate = f"+{int((speed_choice - 1.0) * 100)}%"
            audio_file = asyncio.run(generate_speech_edge(clean_text, voices[voice_choice], rate))

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

# Text-Chat: Stufe 1 - AI-Antwort generieren
def chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """Text-Chat: AI-Antwort generieren mit Zeitmessung"""
    if not text_input:
        return "", history, 0.0

    # Debug-Ausgabe
    debug_print("=" * 60)
    debug_print(f"🤖 AI Model: {model_choice}")
    debug_print(f"🎙️ TTS Engine: {tts_engine}")
    if "Edge" in tts_engine:
        debug_print(f"🎤 Voice: {voice_choice}")
    debug_print(f"⚡ TTS Speed: {speed_choice}x")
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

    # Formatiere <think> Tags als Collapsible (falls vorhanden)
    ai_text_formatted = format_thinking_process(ai_text)

    # Text-Input hat keine STT-Zeit
    history.append([text_input, ai_text_formatted])
    debug_print(f"✅ AI-Antwort generiert ({len(ai_text)} Zeichen, Inferenz: {inference_time:.1f}s)")
    return ai_text, history, inference_time

# Text-Chat: Stufe 2 - TTS generieren (gleiche wie Audio-Chat)
# Verwendet chat_audio_step3_tts

def regenerate_tts(ai_text, voice_choice, speed_choice, enable_tts, tts_engine):
    """Generiert TTS neu für bereits vorhandenen AI-Text"""
    if not ai_text or not enable_tts:
        return None, gr.update(interactive=False)

    # Entferne <think> Tags und Emojis aus Text für TTS (nur den reinen Text vorlesen)
    import re
    clean_text = re.sub(r'<think>.*?</think>', '', ai_text, flags=re.DOTALL).strip()
    # Entferne ALLE Emojis (umfassende Unicode-Bereiche)
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # Emoticons
        "\U0001F300-\U0001F5FF"  # Symbole & Piktogramme (inkl. Uhrzeiten 🕐-🕧)
        "\U0001F680-\U0001F6FF"  # Transport & Karten
        "\U0001F700-\U0001F77F"  # Alchemie Symbole
        "\U0001F780-\U0001F7FF"  # Geometrische Formen Extended
        "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols & Pictographs
        "\U0001FA00-\U0001FA6F"  # Chess Symbols
        "\U0001FA70-\U0001FAFF"  # Symbols & Pictographs Extended-A
        "\U0001F1E0-\U0001F1FF"  # Flaggen
        "\U00002600-\U000027BF"  # Misc Symbole (☀️⭐)
        "\U0000FE00-\U0000FE0F"  # Variation Selectors
        "\U0001F018-\U0001F270"  # Weitere Symbole
        "\U0000238C-\U00002454"  # Misc Technical
        "\u200d"                  # Zero Width Joiner
        "\ufe0f"                  # Variation Selector
        "\u3030"                  # Wavy Dash
        "]+",
        flags=re.UNICODE
    )
    clean_text = emoji_pattern.sub(r'', clean_text).strip()

    # Entferne Markdown-Formatierung und Sonderzeichen
    clean_text = re.sub(r'\*\*', '', clean_text)  # Bold **text**
    clean_text = re.sub(r'\*', '', clean_text)    # Italic *text* oder Bullet-Points
    clean_text = re.sub(r'`', '', clean_text)     # Code `text`
    clean_text = re.sub(r'#+\s', '', clean_text)  # Markdown Headers ### Text

    # Entferne URLs (http://, https://, www.)
    clean_text = re.sub(r'https?://\S+', '', clean_text)  # http:// und https://
    clean_text = re.sub(r'www\.\S+', '', clean_text)      # www.beispiel.de

    # Debug-Ausgabe
    debug_print("=" * 60)
    debug_print("🔄 TTS NEU GENERIEREN")
    debug_print(f"🎙️ TTS Engine: {tts_engine}")
    if "Edge" in tts_engine:
        debug_print(f"🎤 Voice: {voice_choice}")
    debug_print(f"⚡ TTS Speed: {speed_choice}x")
    debug_print(f"📝 Text length: {len(clean_text)} characters")
    debug_print("=" * 60)

    audio_file = None
    if "Piper" in tts_engine:
        # Piper TTS (lokal)
        audio_file = generate_speech_piper(clean_text, speed_choice)
    else:
        # Edge TTS (Cloud)
        rate = f"+{int((speed_choice - 1.0) * 100)}%"
        audio_file = asyncio.run(generate_speech_edge(clean_text, voices[voice_choice], rate))

    return audio_file, gr.update(interactive=True if ai_text else False)

# ============================================================
# AGENT FUNKTIONEN
# ============================================================

def ai_rate_urls(urls, query, model_choice):
    """
    KI bewertet alle URLs auf einmal (effizient!)

    Args:
        urls: Liste von URLs
        query: Suchanfrage
        model_choice: Ollama Model

    Returns:
        Liste von {'url', 'score', 'reasoning'}, sortiert nach Score
    """
    if not urls:
        return []

    # Erstelle nummerierte Liste für KI
    url_list = "\n".join([f"{i+1}. {url}" for i, url in enumerate(urls)])

    prompt = f"""Du bist ein Recherche-Experte. Bewerte diese URLs für die Suchanfrage.

**Suchanfrage:** "{query}"

**URLs:**
{url_list}

**Aufgabe:**
Bewerte jede URL auf einer Skala von 0-10:
- 10 = Perfekt (Hauptquelle, sehr relevant, vertrauenswürdig)
- 5-7 = Gut (relevante Quelle, verwendbar)
- 0-4 = Schlecht (Spam, irrelevant, unzuverlässig)

**Kriterien:**
- Ist die Domain vertrauenswürdig?
  - SEHR GUT (9-10): spiegel.de, tagesschau.de, zdf.de, sueddeutsche.de, faz.net, zeit.de, wikipedia.org, .gov, .edu
  - GUT (7-8): bekannte Nachrichtenseiten, Fachmedien, offizielle Organisationen
  - MITTEL (5-6): Blogs von Experten, Fachforen, regionale Medien
  - SCHLECHT (0-4): unbekannte Blogs, Spam-Seiten, unzuverlässige Quellen
- Passt die URL zur Frage? (Titel/Pfad relevant?)
- Für NEWS/POLITIK: Bevorzuge etablierte deutsche Nachrichtenmedien!
- Für AKTUELLES: Bevorzuge aktuelle Quellen (2024+)

**WICHTIG:** Bewerte URLs großzügig! Lieber Score 6-7 geben als 4-5!

**FORMAT (EXAKT EINHALTEN!):**
Antworte NUR mit einer nummerierten Liste in EXAKT diesem Format:
1. Score: 9 - Reasoning: Spiegel.de, relevanter Artikel zu Trump
2. Score: 7 - Reasoning: ZDF, aktuelle Berichterstattung
3. Score: 3 - Reasoning: Forum, keine Primärquelle

**KRITISCH:**
- JEDE Zeile MUSS mit "Score: [ZAHL] - Reasoning: [TEXT]" beginnen!
- KEINE zusätzlichen Erklärungen oder Kommentare!
- KEINE Abweichungen vom Format!
- Sortiere NICHT, gib sie in der gleichen Reihenfolge zurück!

**BEISPIEL KORREKT:**
1. Score: 9 - Reasoning: Tagesschau, vertrauenswürdig
2. Score: 8 - Reasoning: FAZ, gute Nachrichtenquelle
3. Score: 4 - Reasoning: unbekannter Blog

**BEISPIEL FALSCH (NICHT MACHEN!):**
1. Diese URL ist gut (Score 9)
2. Ich denke Score: 8 weil...
3. Relevanz: hoch, Score = 7"""

    try:
        response = ollama.chat(
            model=model_choice,
            messages=[{'role': 'user', 'content': prompt}]
        )

        answer = response['message']['content']

        # Parse Antwort
        rated_urls = []
        lines = answer.strip().split('\n')

        for i, line in enumerate(lines):
            if not line.strip() or i >= len(urls):
                continue

            try:
                # Parse: "1. Score: 9 - Reasoning: ..."
                score_part = line.split('Score:')[1].split('-')[0].strip()
                score = int(score_part)

                reasoning_part = line.split('Reasoning:')[1].strip() if 'Reasoning:' in line else "N/A"

                rated_urls.append({
                    'url': urls[i],
                    'score': score,
                    'reasoning': reasoning_part
                })
            except Exception as e:
                debug_print(f"⚠️ Parse-Fehler für URL {i+1}: {e}")
                # Fallback
                rated_urls.append({
                    'url': urls[i],
                    'score': 5,
                    'reasoning': "Parse-Fehler"
                })

        # Sortiere nach Score (beste zuerst)
        rated_urls.sort(key=lambda x: x['score'], reverse=True)

        debug_print(f"✅ {len(rated_urls)} URLs bewertet")

        return rated_urls

    except Exception as e:
        debug_print(f"❌ Fehler bei URL-Rating: {e}")
        # Fallback: Gib URLs ohne Rating zurück
        return [{'url': url, 'score': 5, 'reasoning': 'Rating fehlgeschlagen'} for url in urls]


def chat_interactive_mode(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """
    Automatik-Modus: KI entscheidet selbst, ob Web-Recherche nötig ist

    Args:
        user_text: User-Frage
        stt_time: STT-Zeit (0.0 bei Text-Eingabe)
        model_choice: Ollama Model
        voice_choice, speed_choice, enable_tts, tts_engine: Für Fallback zu Eigenes Wissen
        history: Chat History

    Returns:
        (ai_text, history, inference_time)
    """

    debug_print("🤖 Automatik-Modus: KI prüft, ob Recherche nötig...")

    # Schritt 1: KI fragen, ob Recherche nötig ist
    decision_prompt = f"""Du bist ein intelligenter Assistant. Analysiere diese Frage und entscheide: Brauchst du Web-Recherche?

**Frage:** "{user_text}"

**WICHTIG: Du hast KEINEN Echtzeit-Zugang! Deine Trainingsdaten sind veraltet (bis Jan 2025)!**

**Analyse-Kriterien:**
- ✅ **WEB-RECHERCHE UNBEDINGT NÖTIG** wenn:
  - **WETTER** (heute, morgen, aktuell, Vorhersage) → IMMER Web-Suche!
  - **AKTUELLE NEWS** (Was passiert gerade? Wer gewann? Neueste ...)
  - **LIVE-DATEN** (Aktienkurse, Bitcoin, Sport-Ergebnisse, Wahlen)
  - **ZEITABHÄNGIG** (heute, jetzt, gestern, diese Woche, aktuell)
  - **FAKTEN NACH JAN 2025** (alles nach deinem Wissenstand)
  - **SPEZIFISCHE EVENTS** (Konzerte, Veranstaltungen, aktuelle Produkte)

- ❌ **EIGENES WISSEN REICHT** wenn:
  - **ALLGEMEINWISSEN** (Was ist Photosynthese? Erkläre Quantenphysik)
  - **DEFINITIONEN** (Was bedeutet X? Wie heißt Y?)
  - **THEORIE & KONZEPTE** (Wie funktioniert Z? Was ist der Unterschied zwischen A und B?)
  - **HISTORISCHE FAKTEN** (vor 2025: Wer war Einstein? Wann war 2. Weltkrieg?)
  - **MATHEMATIK & LOGIK** (Berechne, erkläre, löse)

**BEISPIELE:**
- "Wetter in Berlin" → `<search>yes</search>` (Wetter = IMMER Web-Suche!)
- "Aktueller Bitcoin-Kurs" → `<search>yes</search>` (Live-Daten)
- "Was ist Photosynthese?" → `<search>no</search>` (Allgemeinwissen)
- "Neueste Trump News" → `<search>yes</search>` (Aktuelle News)
- "Wie funktioniert ein Verbrennungsmotor?" → `<search>no</search>` (Theorie)

**Antworte NUR mit einem dieser Tags:**
- `<search>yes</search>` - Wenn Web-Recherche nötig
- `<search>no</search>` - Wenn eigenes Wissen ausreicht

**Keine weiteren Erklärungen!** Nur das Tag!"""

    try:
        response = ollama.chat(
            model=model_choice,
            messages=[{'role': 'user', 'content': decision_prompt}]
        )

        decision = response['message']['content'].strip().lower()

        debug_print(f"🤖 KI-Entscheidung: {decision}")

        # Parse Entscheidung
        if '<search>yes</search>' in decision or 'yes' in decision:
            debug_print("✅ KI entscheidet: Web-Recherche nötig → Web-Suche Ausführlich (3 Quellen)")
            return perform_agent_research(user_text, stt_time, "deep", model_choice, history)
        else:
            debug_print("❌ KI entscheidet: Eigenes Wissen ausreichend → Kein Agent")
            return chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)

    except Exception as e:
        debug_print(f"⚠️ Fehler bei Automatik-Modus Entscheidung: {e}")
        debug_print("   Fallback zu Eigenes Wissen")
        return chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)


def optimize_search_query(user_text, model_choice):
    """
    Extrahiert optimierte Suchbegriffe aus User-Frage

    Args:
        user_text: Volle User-Frage (kann lang sein)
        model_choice: Ollama Model

    Returns:
        Optimierte Search Query (3-8 Keywords)
    """
    prompt = f"""Du bist ein Suchmaschinen-Experte. Extrahiere die wichtigsten Suchbegriffe aus dieser Frage.

**Frage:** "{user_text}"

**Aufgabe:**
Erstelle eine optimierte Suchmaschinen-Query mit 3-8 Keywords.

**Regeln:**
- Nur wichtige Begriffe (Namen, Orte, Konzepte, Aktionen)
- Entferne Füllwörter (der, die, das, bitte, ist, hat, etc.)
- Entferne Höflichkeitsfloskeln (bitte, danke, könntest du, etc.)
- Bei Fragen zu aktuellen Events: Füge Jahr "2025" hinzu
- Bei Wetter-Fragen: Füge "Wetter" + Ort + Zeitpunkt hinzu
- Sortiere: Wichtigste Begriffe zuerst
- **KRITISCH: Nutze die GLEICHE SPRACHE wie die Frage! Deutsch → deutsche Keywords, Englisch → englische Keywords**

**Beispiele:**
- "Präsident Trump hat mit Hamas ein Friedensabkommen geschlossen, das Biden vorbereitet hat. Recherchiere die Dokumente."
  → "Trump Hamas Netanyahu Biden Friedensabkommen Dokumente 2025"

- "Wie ist das Wetter morgen in Berlin?"
  → "Wetter Berlin morgen"

- "Was sind die neuesten Entwicklungen im KI-Bereich?"
  → "KI Entwicklungen neueste 2025"

- "Hat die Bundesregierung neue Klimaschutzgesetze beschlossen?"
  → "Bundesregierung Klimaschutzgesetze neu 2025"

- "What is the weather forecast for London tomorrow?"
  → "weather London tomorrow forecast"

- "Latest news about Trump and Netanyahu?"
  → "Trump Netanyahu latest news 2025"

**WICHTIG:**
- Antworte NUR mit den Keywords (keine Erklärung!)
- Nutze Leerzeichen zwischen Keywords
- Keine Sonderzeichen, keine Anführungszeichen
- Maximal 8 Keywords
- **SPRACHE BEIBEHALTEN: Deutsch in → Deutsch raus, Englisch in → Englisch raus**

**Deine optimierte Query:**"""

    try:
        response = ollama.chat(
            model=model_choice,
            messages=[{'role': 'user', 'content': prompt}]
        )

        raw_response = response['message']['content'].strip()

        # Extrahiere <think> Inhalt BEVOR wir ihn entfernen (für Debug-Output)
        import re
        think_match = re.search(r'<think>(.*?)</think>', raw_response, re.DOTALL)
        think_content = think_match.group(1).strip() if think_match else None

        # Säubern: Entferne <think> Tags und deren Inhalt
        optimized_query = re.sub(r'<think>.*?</think>', '', raw_response, flags=re.DOTALL)

        # Entferne Anführungszeichen und Sonderzeichen
        optimized_query = re.sub(r'["\'\n\r]', '', optimized_query)
        optimized_query = ' '.join(optimized_query.split())  # Normalize whitespace

        debug_print(f"🔍 Query-Optimierung:")
        debug_print(f"   Original: {user_text[:80]}{'...' if len(user_text) > 80 else ''}")
        debug_print(f"   Optimiert: {optimized_query}")

        # Return: Tuple (optimized_query, reasoning)
        return (optimized_query, think_content)

    except Exception as e:
        debug_print(f"⚠️ Fehler bei Query-Optimierung: {e}")
        debug_print(f"   Fallback zu Original-Query")
        return (user_text, None)


def perform_agent_research(user_text, stt_time, mode, model_choice, history):
    """
    Agent-Recherche mit AI-basierter URL-Bewertung

    Args:
        user_text: User-Frage
        stt_time: STT-Zeit
        mode: "quick" oder "deep"
        model_choice: Ollama Model
        history: Chat History

    Returns:
        (ai_text, history, inference_time, agent_time)
    """

    agent_start = time.time()
    tool_results = []

    # 1. Query Optimization: KI extrahiert Keywords (mit Zeitmessung)
    query_opt_start = time.time()
    optimized_query, query_reasoning = optimize_search_query(user_text, model_choice)
    query_opt_time = time.time() - query_opt_start

    # 2. Web-Suche (Brave → Tavily → SearXNG Fallback) mit optimierter Query
    debug_print("=" * 60)
    debug_print(f"🔍 Web-Suche mit optimierter Query")
    debug_print("=" * 60)

    search_result = search_web(optimized_query)
    tool_results.append(search_result)

    # 2. URLs extrahieren (bis zu 10)
    related_urls = search_result.get('related_urls', [])[:10]

    # Initialisiere Variablen für Fälle ohne URLs
    rated_urls = []
    rating_time = None

    if not related_urls:
        debug_print("⚠️ Keine URLs gefunden, nur Abstract")
    else:
        debug_print(f"📋 {len(related_urls)} URLs gefunden")

        # 3. AI bewertet alle URLs (1 Call!) - Verwende qwen2.5:14b für beste RAG-Performance (mit Zeitmessung)
        debug_print("🤖 KI bewertet URLs (mit qwen2.5:14b für bessere Genauigkeit)...")
        rating_start = time.time()
        rated_urls = ai_rate_urls(related_urls, user_text, "qwen2.5:14b")
        rating_time = time.time() - rating_start

        # Debug: Zeige ALLE Bewertungen (nicht nur Top 5)
        debug_print("=" * 60)
        debug_print("📊 URL-BEWERTUNGEN (alle):")
        debug_print("=" * 60)
        for idx, item in enumerate(rated_urls, 1):
            url_short = item['url'][:70] + '...' if len(item['url']) > 70 else item['url']
            reasoning_short = item['reasoning'][:80] + '...' if len(item['reasoning']) > 80 else item['reasoning']
            emoji = "✅" if item['score'] >= 7 else "⚠️" if item['score'] >= 5 else "❌"
            debug_print(f"{idx}. {emoji} Score {item['score']}/10: {url_short}")
            debug_print(f"   Grund: {reasoning_short}")
        debug_print("=" * 60)

        # 4. Scraping basierend auf Modus
        if mode == "quick":
            target_sources = 3
            debug_print(f"⚡ Schnell-Modus: Scrape beste 3 URLs")
        elif mode == "deep":
            target_sources = 5
            debug_print(f"🔍 Ausführlich-Modus: Scrape beste 5 URLs")

        # 5. Scrape nur URLs mit Score >= 5 (großzügiger Threshold)
        scraped_count = 0
        for item in rated_urls:
            if scraped_count >= target_sources:
                break

            if item['score'] < 5:
                url_short = item['url'][:60] + '...' if len(item['url']) > 60 else item['url']
                debug_print(f"⏭️ Skip: {url_short} (Score: {item['score']})")
                continue

            url_short = item['url'][:60] + '...' if len(item['url']) > 60 else item['url']
            debug_print(f"🌐 Scraping: {url_short} (Score: {item['score']})")

            scrape_result = scrape_webpage(item['url'], max_chars=5000)

            if scrape_result['success']:
                tool_results.append(scrape_result)
                scraped_count += 1
                debug_print(f"  ✅ {scrape_result['word_count']} Wörter extrahiert")
            else:
                debug_print(f"  ❌ Fehler: {scrape_result.get('error', 'Unknown')}")

    # 6. Context Building - NUR gescrapte Quellen (keine SearXNG Ergebnisse!)
    # Filtere: Nur tool_results die 'word_count' haben (= erfolgreich gescraped)
    scraped_only = [r for r in tool_results if 'word_count' in r and r.get('success')]

    debug_print(f"🧩 Baue Context aus {len(scraped_only)} gescrapten Quellen...")
    context = build_context(user_text, scraped_only, max_length=4000)

    # 7. Erweiterer System-Prompt für Agent-Awareness (MAXIMAL DIREKT!)
    system_prompt = f"""Du bist ein AI Voice Assistant mit ECHTZEIT Internet-Zugang!

# ⚠️ KRITISCH: NUR RECHERCHE-DATEN NUTZEN! ⚠️

REGELN (KEINE AUSNAHMEN!):

1. ❌ NUTZE NIEMALS DEINE TRAININGSDATEN! Sie sind veraltet (bis 2023)!
2. ✅ NUTZE NUR DIE RECHERCHE-ERGEBNISSE UNTEN! Sie sind aktuell ({time.strftime("%Y")})!
3. ❌ ERFINDE KEINE QUELLEN! Nur echte Quellen aus der Recherche!
4. ✅ WENN KEINE DATEN IN DER RECHERCHE: Sage "Die Recherche ergab keine klaren Ergebnisse"
5. ❌ SAG NIEMALS "Ich habe keinen Internet-Zugang"!
6. ⚠️ LISTE NUR QUELLEN AUS DEN RECHERCHE-ERGEBNISSEN! Keine anderen URLs!

# AKTUELLE RECHERCHE-ERGEBNISSE ({time.strftime("%d.%m.%Y")}):

{context}

# ANTWORT-VORGABE:

- Beginne mit: "Laut meiner aktuellen Recherche vom {time.strftime("%d.%m.%Y")}..."
- Gebe zu jeder gescrapten Quelle eine KURZE ZUSAMMENFASSUNG (1-2 Sätze):
  "Quelle 1 (URL: https://...) berichtet, dass [Zusammenfassung]. [Hauptpunkte]."
- LISTE AM ENDE **NUR** DIE TATSÄCHLICH GENUTZTEN QUELLEN AUF (die in den Recherche-Ergebnissen oben stehen!):

  **Quellen:**
  - Quelle 1: https://... (Zusammenfassung: [1-2 Sätze was dort stand])
  - Quelle 2: https://... (Zusammenfassung: [1-2 Sätze was dort stand])

- ❌ NENNE KEINE URLs die NICHT in den Recherche-Ergebnissen oben stehen!
- Falls Recherche leer: "Die Recherche ergab leider keine verwertbaren Informationen zu dieser Frage"
- Stil: Kurz, präzise, Deutsch"""

    # 8. AI Inference mit History + System-Prompt
    messages = []

    # History hinzufügen (falls vorhanden)
    for h in history:
        user_msg = h[0].split(" (STT:")[0].split(" (Agent:")[0] if " (STT:" in h[0] or " (Agent:" in h[0] else h[0]
        ai_msg = h[1].split(" (Inferenz:")[0] if " (Inferenz:" in h[1] else h[1]
        messages.extend([
            {'role': 'user', 'content': user_msg},
            {'role': 'assistant', 'content': ai_msg}
        ])

    # System-Prompt + aktuelle User-Frage
    messages.insert(0, {'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_text})

    inference_start = time.time()
    response = ollama.chat(model=model_choice, messages=messages)
    inference_time = time.time() - inference_start

    agent_time = time.time() - agent_start

    ai_text = response['message']['content']

    # 9. History mit Agent-Timing + Debug Accordion
    mode_label = "Schnell" if mode == "quick" else "Ausführlich"
    user_with_time = f"{user_text} (STT: {stt_time:.1f}s, Agent: {agent_time:.1f}s, {mode_label}, {len(scraped_only)} Quellen)"

    # Formatiere mit Debug Accordion (Query Reasoning + URL Rating + Final Answer <think>) inkl. Inferenz-Zeiten
    ai_text_formatted = build_debug_accordion(query_reasoning, rated_urls, ai_text, query_opt_time, rating_time, inference_time)

    history.append([user_with_time, ai_text_formatted])

    debug_print(f"✅ Agent fertig: {agent_time:.1f}s gesamt, {len(ai_text)} Zeichen")
    debug_print("=" * 60)

    return ai_text, history, inference_time


def chat_audio_step2_with_mode(user_text, stt_time, research_mode, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """
    Routing-Funktion: Entscheidet basierend auf research_mode

    Returns:
        (ai_text, history, inference_time)
    """

    if not user_text:
        return "", history, 0.0

    # Parse research_mode und route entsprechend
    if "Eigenes Wissen" in research_mode:
        # Standard-Pipeline ohne Agent
        debug_print(f"🧠 Modus: Eigenes Wissen (kein Agent)")
        return chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)

    elif "Schnell" in research_mode:
        # Web-Suche Schnell: Multi-API (Brave → Tavily → SearXNG) + beste 1 URL
        debug_print(f"⚡ Modus: Web-Suche Schnell (Agent)")
        return perform_agent_research(user_text, stt_time, "quick", model_choice, history)

    elif "Ausführlich" in research_mode:
        # Web-Suche Ausführlich: Multi-API (Brave → Tavily → SearXNG) + beste 3 URLs
        debug_print(f"🔍 Modus: Web-Suche Ausführlich (Agent)")
        return perform_agent_research(user_text, stt_time, "deep", model_choice, history)

    elif "Automatik" in research_mode:
        # Automatik-Modus: KI entscheidet selbst, ob Recherche nötig
        debug_print(f"🤖 Modus: Automatik (KI entscheidet)")
        return chat_interactive_mode(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)

    else:
        # Fallback: Eigenes Wissen
        debug_print(f"⚠️ Unbekannter Modus: {research_mode}, fallback zu Eigenes Wissen")
        return chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)


def chat_text_step1_with_mode(text_input, research_mode, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history):
    """
    Text-Chat mit Modus-Routing (ohne STT-Zeit)

    Returns:
        (ai_text, history, inference_time)
    """

    if not text_input:
        return "", history, 0.0

    # Parse research_mode und route entsprechend
    if "Eigenes Wissen" in research_mode:
        # Standard-Pipeline ohne Agent
        debug_print(f"🧠 Modus: Eigenes Wissen (kein Agent)")
        return chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)

    elif "Schnell" in research_mode:
        # Web-Suche Schnell: Multi-API (Brave → Tavily → SearXNG) + beste 1 URL
        debug_print(f"⚡ Modus: Web-Suche Schnell (Agent)")
        return perform_agent_research(text_input, 0.0, "quick", model_choice, history)

    elif "Ausführlich" in research_mode:
        # Web-Suche Ausführlich: Multi-API (Brave → Tavily → SearXNG) + beste 3 URLs
        debug_print(f"🔍 Modus: Web-Suche Ausführlich (Agent)")
        return perform_agent_research(text_input, 0.0, "deep", model_choice, history)

    elif "Automatik" in research_mode:
        # Automatik-Modus: KI entscheidet selbst, ob Recherche nötig
        debug_print(f"🤖 Modus: Automatik (KI entscheidet)")
        return chat_interactive_mode(text_input, 0.0, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)

    else:
        # Fallback: Eigenes Wissen
        debug_print(f"⚠️ Unbekannter Modus: {research_mode}, fallback zu Eigenes Wissen")
        return chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, history)


# Settings beim Start laden
debug_print("=" * 60)
debug_print("🚀 AI Voice Assistant startet...")
debug_print("=" * 60)
saved_settings = load_settings()
debug_print(f"📋 Geladene Settings:")
debug_print(f"   AI Model: {saved_settings['model']}")
debug_print(f"   Whisper Model: {saved_settings.get('whisper_model', 'base (142MB, schnell, multilingual)')}")
debug_print(f"   TTS Engine: {saved_settings['tts_engine']}")
debug_print(f"   Voice: {saved_settings['voice']}")
debug_print(f"   Speed: {saved_settings['tts_speed']}")
debug_print(f"   TTS Enabled: {saved_settings['enable_tts']}")
debug_print("=" * 60)

# Gradio Interface - Default Theme (automatisches Dark Mode je nach System)
with gr.Blocks(title="Alfred Intelligence") as app:
    gr.Markdown("# 🎩 Alfred Intelligence")
    gr.Markdown("*AI at your service* • Benannt nach Alfred (Großvater) und Wolfgang Alfred (Vater)")
    gr.Markdown("""
    **Tipp:** Nach dem Stoppen der Aufnahme läuft automatisch die Transkription. Du kannst die Aufnahme vorher anhören (mit Playback-Speed-Kontrolle im Browser-Player).
    """)
    
    with gr.Row():
        with gr.Column():
            # Audio Input mit Waveform
            audio_input = gr.Audio(
                sources=["microphone"],
                type="filepath",
                label="🎙️ Spracheingabe (nach Aufnahme automatisch bereit)",
                waveform_options={"show_recording_waveform": True}
            )

            # Info Text
            gr.Markdown("💡 **Tipp:** Nach dem Stoppen läuft automatisch die Transkription")

            # Transcription Checkbox (direkt unter Audio)
            show_transcription = gr.Checkbox(
                value=saved_settings.get("show_transcription", False),
                label="✏️ Text nach Transkription zeigen (ermöglicht Korrektur vor dem Senden)",
                info="An: Audio → Text im Textfeld → Bearbeiten → 'Text senden' | Aus: Audio → Automatisch zur AI"
            )

            gr.Markdown("---")

            # Text Input
            text_input = gr.Textbox(
                label="⌨️ Texteingabe (Alternative)",
                lines=3,
                interactive=True,
                placeholder="Oder schreibe hier deine Frage..."
            )

            # Research-Modus Radio-Buttons (direkt bei Texteingabe für schnellen Zugriff)
            research_mode = gr.Radio(
                choices=[
                    "🧠 Eigenes Wissen (schnell)",
                    "⚡ Web-Suche Schnell (KI-analysiert, 3 beste)",
                    "🔍 Web-Suche Ausführlich (KI-analysiert, 5 beste)",
                    "🤖 Automatik (variabel, KI entscheidet)"
                ],
                value=saved_settings.get("research_mode", "⚡ Web-Suche Schnell (KI-analysiert, 3 beste)"),
                label="🎯 Recherche-Modus",
                info="Wähle, wie der Assistant Fragen beantwortet"
            )

            # Accordion mit Erklärungen (kompakt)
            with gr.Accordion("ℹ️ Was bedeuten die Modi?", open=False):
                gr.Markdown("""
                **🧠 Eigenes Wissen** - Schnell, offline, nur AI-Training (bis Jan 2025)

                **⚡ Web-Suche Schnell** - 3 beste Quellen (Brave → Tavily → SearXNG)

                **🔍 Web-Suche Ausführlich** - 5 beste Quellen (Brave → Tavily → SearXNG)

                **🤖 Automatik** - KI entscheidet intelligent, ob Web-Recherche nötig ist (nutzt 3 Quellen bei Recherche)

                ---

                **3-Stufen Fallback:**
                1. Brave Search (2.000/Monat) - Primary
                2. Tavily AI (1.000/Monat) - Fallback
                3. SearXNG (Unlimited) - Last Resort

                *Aktuell aktiv: SearXNG (setup Brave/Tavily in .env)*
                """)

            text_submit = gr.Button("Text senden", variant="primary")

        with gr.Column():
            user_text = gr.Textbox(label="Eingabe:", lines=3, interactive=False)
            ai_text = gr.Textbox(label="AI Antwort:", lines=5, interactive=False)

            # Sprachausgabe - Audio Widget mit integrierter Checkbox
            with gr.Group():
                gr.Markdown("### 🔊 Sprachausgabe (AI-Antwort)")

                # TTS Toggle direkt im Audio-Bereich
                enable_tts = gr.Checkbox(
                    value=saved_settings["enable_tts"],
                    label="Sprachausgabe aktiviert"
                )

                audio_output = gr.Audio(
                    label="",  # Kein Label, da schon in Group-Header
                    autoplay=True,
                    type="filepath",
                    show_download_button=True
                )

    # Chat Verlauf direkt unter den Eingabefeldern
    chatbot = gr.Chatbot(label="💬 Chat Verlauf", height=1200)
    history = gr.State([])
    recording_state = gr.State("idle")  # idle, recording, stopped

    # Einstellungen ganz unten
    with gr.Row():
        with gr.Column():
            gr.Markdown("### ⚙️ AI Einstellungen")
            model = gr.Dropdown(choices=models, value=saved_settings["model"], label="🤖 AI Model (Ollama)")

            # Collapsible Hilfe für LLM-Auswahl
            with gr.Accordion("ℹ️ Welches Model soll ich wählen?", open=False):
                gr.Markdown("""
                | Model | Größe | RAG | Speed | Bester Einsatz |
                |-------|-------|-----|-------|----------------|
                | **qwen2.5:14b** | 9 GB | ✅✅✅ | Mittel | **Web-Recherche, aktuelle News** |
                | **qwen3:8b** | 5.2 GB | ✅✅ | Schnell | Balance: Schnell + RAG-fähig |
                | **command-r** | 18 GB | ✅✅✅ | Langsam | Enterprise RAG, lange Dokumente |
                | **mixtral:8x7b** | 26 GB | ✅✅ | Mittel | Komplexe Tasks, Multi-Domain (MoE!) |
                | **llama3.1:8b** | 4.9 GB | ✅ | Schnell | Allgemein, zuverlässig |
                | **mistral** | 4.4 GB | ✅ | Schnell | Code, Instruktionen, effizient |
                | **llama2:13b** | 7.4 GB | ⚠️ | Mittel | Wissen (mischt 78% RAG + 22% Training Data) |
                | **llama3.2:3b** | 2 GB | ❌ | Sehr schnell | Einfache Fragen (ignoriert RAG oft!) |

                ---

                **RAG-Legende:**
                - ✅✅✅ = **Perfekt** (100% Research, 0% Training Data)
                - ✅✅ = **Gut** (90%+ Research, minimal Training Data)
                - ✅ = **Möglich** (nutzt Research, aber Mix mit Training Data)
                - ⚠️ = **Unzuverlässig** (~78% Research, ~22% Training Data)
                - ❌ = **Kein RAG** (ignoriert Research, nur Training Data)

                ---

                **🏆 Top-Empfehlung für Web-Recherche (Agent-Modi):**
                → **`qwen2.5:14b`** (RAG Score: 1.0 = perfekt!)
                - Ignoriert Training Data **komplett**
                - Nutzt NUR aktuelle Web-Ergebnisse
                - Zitiert Quellen korrekt mit URLs
                - **Perfekt für:** "Trump News", "aktuelle Ereignisse", "Was passiert heute?"

                **⚡ Für schnelle Antworten (ohne Agent):**
                → **`qwen3:8b`** oder **`llama3.1:8b`**
                - Gute Balance zwischen Speed & Qualität
                - Allgemeine Konversation, Erklärungen
                - **Perfekt für:** "Was ist Quantenphysik?", "Erkläre Python"

                **📚 Für lange Dokumente (mit Agent ausführlich):**
                → **`command-r`** (18 GB, braucht 32 GB RAM!)
                - Speziell für RAG & Enterprise gebaut
                - Kann sehr lange Contexts verarbeiten
                - **Perfekt für:** PDFs analysieren, komplexe Research

                **🧩 Für komplexe Multi-Domain Tasks:**
                → **`mixtral:8x7b`** (26 GB, Mixture-of-Experts!)
                - 8 Expert-Modelle à 7B Parameter (insgesamt 47B!)
                - Aktiviert je nach Task nur relevante Experten (effizient!)
                - Gut für Code, Mathe, Reasoning, Sprachen gleichzeitig
                - **Perfekt für:** Komplexe Projekte, Code-Review + Doku + Tests
                - **Achtung:** 26 GB! Läuft mit 32 GB RAM, aber langsam

                **💻 Für Code & Instruktionen:**
                → **`mistral`** (4.4 GB, kompakt & effizient!)
                - Kleiner Bruder von Mixtral, aber single-model
                - Sehr gutes Instruction-Following
                - Gut für Code-Generierung, Scripting
                - **Perfekt für:** Python-Code, Bash-Scripts, strukturierte Tasks
                - **Schneller als:** llama3.1:8b bei Code-Tasks

                **⚠️ Bedingt für Web-Recherche:**
                → **`llama2:13b`**
                - Nutzt Web-Research, aber mischt 22% Training Data rein
                - Kann aktuelle Infos mit alten Daten vermischen
                - **OK für:** Allgemeine Fragen, wenn Ungenauigkeit OK ist

                **❌ NICHT für Web-Recherche:**
                → **`llama3.2:3b`**
                - Ignoriert RAG komplett (70% Training Data)
                - Erfindet oft Quellen oder nutzt alte Daten
                - **Nur für:** Tests, einfache Fragen ohne Agent-Modus

                ---

                **🎓 Was ist "Mixture-of-Experts" (MoE)?**

                Mixtral nutzt **8 spezialisierte Experten-Modelle** (je 7B Parameter):
                - Expert 1: Code & Programmierung
                - Expert 2: Mathematik & Logik
                - Expert 3: Sprachen & Übersetzung
                - Expert 4: Kreatives Schreiben
                - Expert 5-8: Weitere Spezialisierungen

                **Wie funktioniert's:**
                - Bei Code-Frage: Aktiviert Expert 1 (Code) + Expert 2 (Logik)
                - Bei Übersetzung: Aktiviert Expert 3 (Sprachen)
                - **Vorteil:** Nutzt nur 12-14B aktiv (nicht alle 47B!)
                - **Resultat:** Qualität von 47B Model, Speed von 14B Model

                **Wann nutzen:**
                - ✅ Komplexe Projekte (Code + Doku + Tests gleichzeitig)
                - ✅ Multi-Language (Deutsch + Englisch + Code gemischt)
                - ✅ Reasoning-Heavy Tasks (Mathe, Logik, Planung)
                - ❌ Einfache Fragen (Overkill, nutze mistral oder qwen3:8b)
                - ⚠️ Langsam wegen 26 GB Größe!

                ---

                **💡 Tipp - Welches Model wann:**
                - **Web-Recherche (Agent):** qwen2.5:14b oder command-r
                - **Allgemein (ohne Agent):** qwen3:8b oder llama3.1:8b
                - **Code schreiben:** mistral (schnell!) oder mixtral (komplex)
                - **Komplexe Projekte:** mixtral:8x7b (MoE-Power!)
                - **Hardware:** Dein System (32 GB RAM) kann ALLE Models! 🚀
                """)

            # Whisper Model Auswahl
            whisper_model = gr.Dropdown(
                choices=list(WHISPER_MODELS.keys()),
                value=saved_settings.get("whisper_model", "base (142MB, schnell, multilingual)"),
                label="🎙️ Whisper Spracherkennung Model",
                info="base/small = schnell | turbo = beste Qualität (lädt beim 1. Mal)"
            )

        with gr.Column():
            gr.Markdown("### ⚡ TTS Einstellungen")

            # TTS Engine Auswahl
            tts_engine = gr.Radio(
                choices=[
                    "Edge TTS (Cloud, beste Qualität)",
                    "Piper TTS (Lokal, sehr schnell)"
                ],
                value=saved_settings["tts_engine"],
                label="🎙️ TTS Engine",
                info="Edge = Microsoft Cloud | Piper = Thorsten Stimme (lokal)"
            )

            # Stimmenauswahl (nur für Edge TTS sichtbar)
            voice = gr.Dropdown(
                choices=list(voices.keys()),
                value=saved_settings["voice"],
                label="🎤 Stimme (nur Edge TTS)",
                visible=True
            )

            tts_speed = gr.Slider(
                minimum=1.0,
                maximum=2.0,
                value=saved_settings["tts_speed"],
                step=0.25,
                label="🔊 TTS Generierungs-Geschwindigkeit",
                info="Geschwindigkeit beim Erstellen der Sprachausgabe (1.25 = empfohlen für Edge TTS)"
            )

            # Button zum Neu-Generieren der Sprachausgabe
            regenerate_audio = gr.Button(
                "🔄 Sprachausgabe neu generieren",
                variant="secondary",
                size="sm",
                interactive=False
            )

            gr.Markdown("""
            **💡 Tipp für Aufnahme-Wiedergabe:**
            Die Geschwindigkeit deiner Aufnahme kannst du direkt im Audio-Player mit dem **1x Button** ändern.
            Klicke mehrmals darauf um zwischen 1x → 1.25x → 1.5x → 1.75x → 2x zu wechseln.
            """)

    clear = gr.Button("🗑️ Chat komplett löschen", variant="secondary", size="sm")

    # State für vorheriges Model (um Separator zu zeigen)
    previous_model = gr.State(saved_settings["model"])

    # Settings Speichern bei Änderungen
    def update_settings(model_val, voice_val, speed_val, tts_val, engine_val, whisper_val, research_val, show_trans_val):
        save_settings(model_val, voice_val, speed_val, tts_val, engine_val, whisper_val, research_val, show_trans_val)

    # Model Change Handler - fügt Separator hinzu
    def model_changed(new_model, prev_model, hist, voice_val, speed_val, tts_val, engine_val, whisper_val, research_val, show_trans_val):
        """Wenn Model wechselt, füge Separator im Chat ein"""
        save_settings(new_model, voice_val, speed_val, tts_val, engine_val, whisper_val, research_val, show_trans_val)

        # Nur Separator hinzufügen wenn es History gibt UND Model wirklich geändert wurde
        if hist and prev_model and new_model != prev_model:
            separator_msg = f"─────── 🔄 KI-Wechsel auf {new_model} ───────"
            hist.append([separator_msg, ""])  # Leere AI-Antwort für saubere Darstellung
            debug_print(f"🔄 Model gewechselt: {prev_model} → {new_model}")
            return new_model, hist  # Aktualisiere previous_model state & history
        else:
            return new_model, hist  # Nur State update, keine History-Änderung

    # TTS Engine Toggle - Zeigt/versteckt Stimmenauswahl UND speichert Settings
    def tts_engine_changed(engine_val, model_val, voice_val, speed_val, tts_val, whisper_val, research_val, show_trans_val):
        save_settings(model_val, voice_val, speed_val, tts_val, engine_val, whisper_val, research_val, show_trans_val)
        return gr.update(visible="Edge" in engine_val)

    tts_engine.change(
        tts_engine_changed,
        inputs=[tts_engine, model, voice, tts_speed, enable_tts, whisper_model, research_mode, show_transcription],
        outputs=[voice]
    )

    # Model-Änderung speziell behandeln (mit Separator)
    model.change(
        model_changed,
        inputs=[model, previous_model, history, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription],
        outputs=[previous_model, history]
    ).then(
        lambda h: h,  # Update chatbot UI
        inputs=[history],
        outputs=[chatbot]
    )

    # Andere Settings-Änderungen
    whisper_model.change(update_settings, inputs=[model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription])
    voice.change(update_settings, inputs=[model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription])
    tts_speed.change(update_settings, inputs=[model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription])
    enable_tts.change(update_settings, inputs=[model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription])
    research_mode.change(update_settings, inputs=[model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription])
    show_transcription.change(update_settings, inputs=[model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription])

    # On Load Event - Lädt Settings und initialisiert UI
    def on_page_load():
        """Wird bei jedem Page-Load aufgerufen - lädt aktuelle Settings"""
        current_settings = load_settings()
        debug_print(f"🔄 Page Load - Settings neu geladen:")
        debug_print(f"   Model: {current_settings['model']}")
        debug_print(f"   TTS Engine: {current_settings['tts_engine']}")
        debug_print(f"   Whisper: {current_settings.get('whisper_model', 'base')}")
        debug_print(f"   Research Mode: {current_settings.get('research_mode', '⚡ Web-Suche Schnell (mittel)')}")
        debug_print(f"   Show Transcription: {current_settings.get('show_transcription', False)}")

        return (
            None,  # audio_input
            "idle",  # recording_state
            gr.update(value=current_settings["model"]),  # model dropdown
            gr.update(value=current_settings["voice"]),  # voice dropdown
            gr.update(value=current_settings["tts_speed"]),  # tts_speed slider
            gr.update(value=current_settings["enable_tts"]),  # enable_tts checkbox
            gr.update(value=current_settings["tts_engine"]),  # tts_engine radio
            gr.update(value=current_settings.get("whisper_model", "base (142MB, schnell, multilingual)")),  # whisper_model
            gr.update(value=current_settings.get("research_mode", "⚡ Web-Suche Schnell (mittel)")),  # research_mode
            gr.update(value=current_settings.get("show_transcription", False)),  # show_transcription
            current_settings["model"]  # previous_model state
        )

    app.load(
        on_page_load,
        outputs=[audio_input, recording_state, model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, previous_model]
    )

    # Audio State Tracking
    audio_input.start_recording(
        lambda: "recording",
        outputs=[recording_state]
    )

    # States für STT und AI Inference Timing
    stt_time_state = gr.State(0.0)
    inference_time_state = gr.State(0.0)

    # Funktion die entscheidet: Text ins Textfeld ODER direkt zur AI
    def audio_auto_process(audio, whisper_choice, show_trans):
        """
        Auto-Trigger nach Stop-Recording:
        - Wenn show_transcription AN: Nur STT, Text ins text_input
        - Wenn show_transcription AUS: STT (Rest passiert in .then() chains)

        Nur user_text wird hier aktualisiert um Fortschrittsbalken nur dort zu zeigen
        """
        if audio is None:
            return ("", 0.0)

        # Immer zuerst transkribieren
        user_text, stt_time = chat_audio_step1_transcribe(audio, whisper_choice)

        if show_trans:
            # Checkbox AN: Text ins Textfeld, kein AI-Call
            debug_print(f"✏️ Transcription-Modus: Text wird ins Textfeld geschrieben")
        else:
            # Checkbox AUS: Normaler Flow, return nur user_text und stt_time
            # Der Rest passiert in .then() calls
            debug_print(f"🚀 Direkt-Modus: Audio wird direkt zur AI geschickt")

        return (user_text, stt_time)

    # WICHTIG: Auto-Trigger nach Stop-Recording!
    # Wenn Aufnahme stoppt → Automatisch STT → (conditional) → Textfeld ODER AI
    audio_input.stop_recording(
        # Schritt 0: Inputs deaktivieren während Verarbeitung (inkl. Audio-Aufnahme)
        lambda: ("stopped", gr.update(interactive=False), gr.update(interactive=False), gr.update(interactive=False)),
        outputs=[recording_state, audio_input, text_input, text_submit]
    ).then(
        # Schritt 1: STT - Nur user_text zeigt Fortschrittsbalken
        audio_auto_process,
        inputs=[audio_input, whisper_model, show_transcription],
        outputs=[user_text, stt_time_state]
    ).then(
        # Schritt 1.5: Text ins Textfeld kopieren (nur wenn show_transcription AN)
        lambda show_trans, usr_txt: usr_txt if show_trans else "",
        inputs=[show_transcription, user_text],
        outputs=[text_input]
    ).then(
        # Schritt 2: AI Inference - Nur ai_text zeigt Fortschrittsbalken
        lambda show_trans, user_txt, stt_t, res_mode, mdl, voi, spd, tts_en, tts_eng, hist: \
            chat_audio_step2_with_mode(user_txt, stt_t, res_mode, mdl, voi, spd, tts_en, tts_eng, hist) if not show_trans else ("", hist, 0.0),
        inputs=[show_transcription, user_text, stt_time_state, research_mode, model, voice, tts_speed, enable_tts, tts_engine, history],
        outputs=[ai_text, history, inference_time_state]
    ).then(
        # Schritt 3: TTS - Nur audio_output zeigt Fortschrittsbalken
        lambda show_trans, ai_txt, inf_t, voi, spd, tts_en, tts_eng, hist: \
            chat_audio_step3_tts(ai_txt, inf_t, voi, spd, tts_en, tts_eng, hist) if not show_trans else (None, hist),
        inputs=[show_transcription, ai_text, inference_time_state, voice, tts_speed, enable_tts, tts_engine, history],
        outputs=[audio_output, history]
    ).then(
        # Cleanup: Audio löschen, Chatbot updaten, Buttons/Inputs wieder aktivieren
        lambda show_trans, h: \
            (None, h, "idle", gr.update(interactive=True), gr.update(interactive=True), gr.update(interactive=True)) if not show_trans else (None, h, "idle", gr.update(interactive=False), gr.update(interactive=True), gr.update(interactive=True)),
        inputs=[show_transcription, history],
        outputs=[audio_input, chatbot, recording_state, regenerate_audio, text_input, text_submit]
    )

    # Text Submit - 3-stufiger Prozess mit Zeitmessung (ohne STT)
    text_submit.click(
        # Schritt 0: Alle Inputs deaktivieren während Verarbeitung
        lambda t: (t, gr.update(interactive=False), gr.update(interactive=False), gr.update(interactive=False)),
        inputs=[text_input],
        outputs=[user_text, audio_input, text_input, text_submit]
    ).then(
        # Stufe 1: AI-Antwort generieren mit Modus-Routing (Agent oder Standard)
        chat_text_step1_with_mode,
        inputs=[text_input, research_mode, model, voice, tts_speed, enable_tts, tts_engine, history],
        outputs=[ai_text, history, inference_time_state]
    ).then(
        # Stufe 2: TTS generieren + History mit Timing aktualisieren
        chat_audio_step3_tts,
        inputs=[ai_text, inference_time_state, voice, tts_speed, enable_tts, tts_engine, history],
        outputs=[audio_output, history]
    ).then(
        # Cleanup: Textfeld leeren + aktivieren, History updaten, alle Inputs wieder aktivieren
        lambda h: (gr.update(value="", interactive=True), h, gr.update(interactive=True), gr.update(interactive=True), gr.update(interactive=True)),
        inputs=[history],
        outputs=[text_input, chatbot, audio_input, regenerate_audio, text_submit]
    )

    # Regenerate Audio Button
    regenerate_audio.click(
        regenerate_tts,
        inputs=[ai_text, voice, tts_speed, enable_tts, tts_engine],
        outputs=[audio_output, regenerate_audio]
    )

    # Clear Button - kompletter Chat
    clear.click(
        lambda: (None, "", "", "", None, [], "idle", gr.update(interactive=False)),
        outputs=[audio_input, text_input, user_text, ai_text, audio_output, chatbot, recording_state, regenerate_audio]
    ).then(
        lambda: [],
        outputs=[history]
    )

import ssl
import os

# SSL-Verifikation komplett deaktivieren
os.environ['PYTHONHTTPSVERIFY'] = '0'
ssl._create_default_https_context = ssl._create_unverified_context

app.queue()
app.launch(
    server_name="0.0.0.0",
    server_port=8443,
    ssl_keyfile="/home/mp/Projekte/voice-assistant/ssl/privkey.pem",
    ssl_certfile="/home/mp/Projekte/voice-assistant/ssl/fullchain.pem",
    ssl_verify=False,
    share=False,
    state_session_capacity=10,
    max_threads=10
)
