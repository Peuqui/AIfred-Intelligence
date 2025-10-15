"""
Audio Processing Module - TTS and STT functionality

This module handles Text-to-Speech (Edge TTS, Piper TTS) and Speech-to-Text
(Whisper) operations, including text cleanup for TTS.
"""

import os
import re
import time
import subprocess
import asyncio
import edge_tts
from .config import VOICES, PIPER_MODEL_PATH, PROJECT_ROOT
from .logging_utils import debug_print


# Determine platform-specific Piper binary path
if os.name == 'nt':  # Windows
    PIPER_BIN = PROJECT_ROOT / "venv" / "Scripts" / "piper.exe"
else:  # Linux/Mac
    PIPER_BIN = PROJECT_ROOT / "venv" / "bin" / "piper"


async def generate_speech_edge(text, voice, rate="+0%"):
    """
    Edge TTS - Cloud-based Text-to-Speech

    Args:
        text: Text to synthesize
        voice: Voice name (e.g. "de-DE-KatjaNeural")
        rate: Speed adjustment (e.g. "+25%" for 25% faster)

    Returns:
        str: Path to generated MP3 file
    """
    # Edge TTS rate Format: +X% oder -X% (z.B. "+25%" für 25% schneller)
    debug_print(f"Edge TTS DEBUG: voice={voice}, rate={rate}, text_length={len(text)}")
    tts = edge_tts.Communicate(text, voice, rate=rate)
    output_file = f"/tmp/audio_{int(time.time())}.mp3"

    # Speichern mit detailliertem Debug
    await tts.save(output_file)

    debug_print(f"Edge TTS: Audio saved to: {output_file}, size: {os.path.getsize(output_file)} bytes")

    return output_file


def generate_speech_piper(text, speed=1.0):
    """
    Piper TTS - Local, fast Text-to-Speech

    Args:
        text: Text to synthesize
        speed: Speed multiplier (1.0 = normal, 1.25 = 25% faster)

    Returns:
        str: Path to generated WAV file, or None on error
    """
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


def clean_text_for_tts(text):
    """
    Bereitet Text für TTS-Ausgabe vor: Entfernt <think> Tags, Emojis,
    Markdown, URLs und andere störende Elemente.

    Args:
        text: Roh-Text von AI-Antwort

    Returns:
        str: Bereinigter Text für TTS
    """
    # Entferne <think> Tags und Inhalt
    clean_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

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

    return clean_text


def transcribe_audio(audio_path, whisper_model):
    """
    Transkribiert Audio zu Text mit Whisper.

    Args:
        audio_path: Pfad zur Audio-Datei
        whisper_model: Geladenes WhisperModel Objekt

    Returns:
        tuple: (transkribierter_text, zeit_in_sekunden)
    """
    if audio_path is None or audio_path == "":
        return "", 0.0

    start_time = time.time()
    segments, _ = whisper_model.transcribe(audio_path)
    stt_time = time.time() - start_time

    user_text = " ".join([s.text for s in segments])
    debug_print(f"✅ Transkription: {user_text[:100]}{'...' if len(user_text) > 100 else ''} (STT: {stt_time:.1f}s)")

    return user_text, stt_time


def generate_tts(text, voice_choice, speed_choice, tts_engine):
    """
    Generiert TTS-Audio aus Text (Edge oder Piper).

    Args:
        text: Text für TTS (bereits bereinigt)
        voice_choice: Voice display name (z.B. "Deutsch (Katja)")
        speed_choice: Speed multiplier (z.B. 1.25)
        tts_engine: Engine name (z.B. "Edge TTS (Cloud, beste Qualität)")

    Returns:
        str: Pfad zur generierten Audio-Datei, oder None
    """
    try:
        if "Piper" in tts_engine:
            # Piper TTS (lokal)
            return generate_speech_piper(text, speed_choice)
        else:
            # Edge TTS (Cloud)
            rate = f"+{int((speed_choice - 1.0) * 100)}%"
            voice_id = VOICES.get(voice_choice, "de-DE-KatjaNeural")
            return asyncio.run(generate_speech_edge(text, voice_id, rate))
    except Exception as e:
        debug_print(f"❌ TTS Fehler: {e}")
        return None
