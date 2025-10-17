import gradio as gr
import ollama
import asyncio
import os
import time
from pathlib import Path

# Lib Modules
from lib.config import (
    WHISPER_MODELS, DEFAULT_SETTINGS, VOICES, RESEARCH_MODES, TTS_ENGINES,
    SETTINGS_FILE, SSL_KEYFILE, SSL_CERTFILE, PROJECT_ROOT
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
from lib.ollama_wrapper import set_gpu_mode, clear_gpu_mode

# Agent Tools (already external)
from agent_tools import search_web, scrape_webpage, build_context



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


def chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, enable_gpu, llm_options, history):
    """Schritt 2: AI-Antwort generieren mit Zeitmessung (ohne Agent)"""
    if not user_text:
        return "", history, 0.0

    # Debug-Ausgabe
    debug_print("=" * 60)
    debug_print(f"🤖 AI Model: {model_choice}")
    debug_print(f"💬 User (KOMPLETT): {user_text}")
    debug_print(f"🎮 GPU: {'Aktiviert' if enable_gpu else 'Deaktiviert (CPU only)'}")
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

    # GPU-Modus und LLM-Parameter setzen (gilt für ALLE ollama.chat() Calls in diesem Request)
    set_gpu_mode(enable_gpu, llm_options)

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


def chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, enable_gpu, llm_options, history):
    """Text-Chat: AI-Antwort generieren mit Zeitmessung (ohne Agent)"""
    if not text_input:
        return "", history, 0.0

    # Debug-Ausgabe
    debug_print("=" * 60)
    debug_print(f"🤖 AI Model: {model_choice}")
    debug_print(f"💬 User (KOMPLETT): {text_input}")
    debug_print(f"🎮 GPU: {'Aktiviert' if enable_gpu else 'Deaktiviert (CPU only)'}")
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

    # Smart Model Loading vor Ollama-Call
    smart_model_load(model_choice)

    # GPU-Modus und LLM-Parameter setzen (gilt für ALLE ollama.chat() Calls in diesem Request)
    set_gpu_mode(enable_gpu, llm_options)

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


def chat_audio_step2_with_mode(user_text, stt_time, research_mode, model_choice, automatik_model, voice_choice, speed_choice, enable_tts, tts_engine, enable_gpu, llm_options, history):
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
        return chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, enable_gpu, llm_options, history)

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
            return chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, enable_gpu, llm_options, history)

    else:
        # Fallback: Eigenes Wissen
        debug_print(f"⚠️ Unbekannter Modus: {research_mode}, fallback zu Eigenes Wissen")
        return chat_audio_step2_ai(user_text, stt_time, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, enable_gpu, llm_options, history)


def chat_text_step1_with_mode(text_input, research_mode, model_choice, automatik_model, voice_choice, speed_choice, enable_tts, tts_engine, enable_gpu, llm_options, history):
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
        return chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, enable_gpu, llm_options, history)

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
            return chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, enable_gpu, llm_options, history)

    else:
        # Fallback: Eigenes Wissen
        debug_print(f"⚠️ Unbekannter Modus: {research_mode}, fallback zu Eigenes Wissen")
        return chat_text_step1_ai(text_input, model_choice, voice_choice, speed_choice, enable_tts, tts_engine, enable_gpu, llm_options, history)


# ============================================================
# STARTUP
# ============================================================

# Register Signal Handlers für sauberen Shutdown
register_signal_handlers()

# Initialize Whisper Base Model
initialize_whisper_base()

# Load available Ollama models
models = get_ollama_models()


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
with gr.Blocks(title="AIfred Intelligence") as app:
    gr.Markdown("# 🎩 AIfred Intelligence")
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

            # GPU Toggle Checkbox
            enable_gpu = gr.Checkbox(
                value=saved_settings.get("enable_gpu", True),
                label="🎮 GPU-Beschleunigung aktivieren (AMD 780M)",
                info="An: GPU (15.3 GiB VRAM) | Aus: CPU only (langsamer, spart Strom)"
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

            # LLM-Parameter Accordion (zwischen Research-Modus und Text-Button)
            with gr.Accordion("⚙️ LLM-Parameter (Erweitert)", open=False):
                gr.Markdown("**Steuere die Antwort-Generierung mit Sampling-Parametern**")

                with gr.Row():
                    llm_temperature = gr.Slider(
                        minimum=0.0,
                        maximum=2.0,
                        value=0.8,
                        step=0.1,
                        label="🌡️ Temperature",
                        info="Kreativität: 0.0 = deterministisch, 0.8 = ausgewogen, 1.5+ = sehr kreativ"
                    )
                    llm_num_predict = gr.Number(
                        value=-1,
                        label="📏 Max Tokens",
                        info="-1 = unbegrenzt, 100-500 = kurz, 1000+ = lang",
                        precision=0
                    )

                with gr.Row():
                    llm_repeat_penalty = gr.Slider(
                        minimum=1.0,
                        maximum=2.0,
                        value=1.1,
                        step=0.05,
                        label="🔁 Repeat Penalty",
                        info="Wiederholungs-Vermeidung: 1.0 = aus, 1.1 = leicht, 1.5+ = stark"
                    )
                    llm_seed = gr.Number(
                        value=-1,
                        label="🎲 Seed",
                        info="-1 = zufällig, fester Wert = reproduzierbar",
                        precision=0
                    )

                # Nested Accordion für fortgeschrittene Parameter
                with gr.Accordion("🔧 Fortgeschrittene Parameter", open=False):
                    gr.Markdown("**Sampling-Strategien für Feintuning**")

                    with gr.Row():
                        llm_top_p = gr.Slider(
                            minimum=0.0,
                            maximum=1.0,
                            value=0.9,
                            step=0.05,
                            label="🎯 Top P (Nucleus Sampling)",
                            info="0.9 = Standard, 0.5 = fokussiert, 0.95+ = diverse"
                        )
                        llm_top_k = gr.Slider(
                            minimum=1,
                            maximum=100,
                            value=40,
                            step=1,
                            label="🔝 Top K",
                            info="Anzahl Kandidaten: 40 = Standard, 10 = fokussiert, 80+ = diverse"
                        )

                    gr.Markdown("""
**Tipps:**
- **Fakten/Code:** temp=0.3, top_p=0.5 (präzise)
- **Chat:** temp=0.8, top_p=0.9 (ausgewogen)
- **Kreativ:** temp=1.2, top_p=0.95 (vielfältig)
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
            model = gr.Dropdown(choices=models, value=saved_settings["model"], label="🤖 Haupt-LLM (Ollama) - Finale Antwort")

            # Zweites Dropdown für Automatik-Modell
            automatik_model = gr.Dropdown(
                choices=models,
                value=saved_settings.get("automatik_model", "qwen3:1.7b"),
                label="⚡ Automatik-LLM (Ollama) - Entscheidungen & Recherche",
                info="Für: Automatik-Entscheidung, Query-Optimierung, URL-Bewertung"
            )

            # Collapsible: Was macht das Automatik-Modell?
            with gr.Accordion("ℹ️ Was macht das Automatik-Modell?", open=False):
                gr.Markdown("""
Das Automatik-Modell wird für **3 schnelle AI-Entscheidungen** verwendet:

**1. 🤔 Automatik-Entscheidung** (~5-30s)
→ Brauche ich Web-Recherche für diese Frage?

**2. 🔍 Query-Optimierung** (~5-15s)
→ Welche Keywords soll ich suchen?

**3. 📊 URL-Bewertung** (~105s für 15 URLs)
→ Welche URLs sind relevant? (Score 1-10)

---

**⭐ Empfehlung: qwen3:1.7b** (schnell & zuverlässig)
- 7s pro URL
- Content-basierte Bewertung
- Alle Tests bestanden

Nach dieser Vorauswahl generiert dein **Haupt-LLM** die finale Antwort.
""")

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
                choices=list(VOICES.keys()),
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
    def update_settings(model_val, automatik_val, voice_val, speed_val, tts_val, engine_val, whisper_val, research_val, show_trans_val, gpu_val):
        save_settings(model_val, automatik_val, voice_val, speed_val, tts_val, engine_val, whisper_val, research_val, show_trans_val, gpu_val)

    # Model Change Handler - fügt Separator hinzu
    def model_changed(new_model, prev_model, hist, automatik_val, voice_val, speed_val, tts_val, engine_val, whisper_val, research_val, show_trans_val, gpu_val):
        """Wenn Model wechselt, füge Separator im Chat ein"""
        save_settings(new_model, automatik_val, voice_val, speed_val, tts_val, engine_val, whisper_val, research_val, show_trans_val, gpu_val)

        # Nur Separator hinzufügen wenn es History gibt UND Model wirklich geändert wurde
        if hist and prev_model and new_model != prev_model:
            separator_msg = f"─────── 🔄 KI-Wechsel auf {new_model} ───────"
            hist.append([separator_msg, ""])  # Leere AI-Antwort für saubere Darstellung
            debug_print(f"🔄 Model gewechselt: {prev_model} → {new_model}")
            return new_model, hist  # Aktualisiere previous_model state & history
        else:
            return new_model, hist  # Nur State update, keine History-Änderung

    # TTS Engine Toggle - Zeigt/versteckt Stimmenauswahl UND speichert Settings
    def tts_engine_changed(engine_val, model_val, automatik_val, voice_val, speed_val, tts_val, whisper_val, research_val, show_trans_val, gpu_val):
        save_settings(model_val, automatik_val, voice_val, speed_val, tts_val, engine_val, whisper_val, research_val, show_trans_val, gpu_val)
        return gr.update(visible="Edge" in engine_val)

    tts_engine.change(
        tts_engine_changed,
        inputs=[tts_engine, model, automatik_model, voice, tts_speed, enable_tts, whisper_model, research_mode, show_transcription, enable_gpu],
        outputs=[voice]
    )

    # Model-Änderung speziell behandeln (mit Separator)
    model.change(
        model_changed,
        inputs=[model, previous_model, history, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu],
        outputs=[previous_model, history]
    ).then(
        lambda h: h,  # Update chatbot UI
        inputs=[history],
        outputs=[chatbot]
    )

    # Andere Settings-Änderungen
    automatik_model.change(update_settings, inputs=[model, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu])
    whisper_model.change(update_settings, inputs=[model, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu])
    voice.change(update_settings, inputs=[model, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu])
    tts_speed.change(update_settings, inputs=[model, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu])
    enable_tts.change(update_settings, inputs=[model, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu])
    research_mode.change(update_settings, inputs=[model, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu])
    show_transcription.change(update_settings, inputs=[model, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu])
    enable_gpu.change(update_settings, inputs=[model, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu])

    # On Load Event - Lädt Settings und initialisiert UI
    def on_page_load():
        """Wird bei jedem Page-Load aufgerufen - lädt aktuelle Settings"""
        current_settings = load_settings()
        debug_print(f"🔄 Page Load - Settings neu geladen:")
        debug_print(f"   Haupt-LLM: {current_settings['model']}")
        debug_print(f"   Automatik-LLM: {current_settings.get('automatik_model', 'qwen3:1.7b')}")
        debug_print(f"   TTS Engine: {current_settings['tts_engine']}")
        debug_print(f"   Whisper: {current_settings.get('whisper_model', 'base')}")
        debug_print(f"   Research Mode: {current_settings.get('research_mode', '⚡ Web-Suche Schnell (mittel)')}")
        debug_print(f"   Show Transcription: {current_settings.get('show_transcription', False)}")
        debug_print(f"   GPU Enabled: {current_settings.get('enable_gpu', True)}")

        return (
            None,  # audio_input
            "idle",  # recording_state
            gr.update(value=current_settings["model"]),  # model dropdown
            gr.update(value=current_settings.get("automatik_model", "qwen3:1.7b")),  # automatik_model dropdown
            gr.update(value=current_settings["voice"]),  # voice dropdown
            gr.update(value=current_settings["tts_speed"]),  # tts_speed slider
            gr.update(value=current_settings["enable_tts"]),  # enable_tts checkbox
            gr.update(value=current_settings["tts_engine"]),  # tts_engine radio
            gr.update(value=current_settings.get("whisper_model", "base (142MB, schnell, multilingual)")),  # whisper_model
            gr.update(value=current_settings.get("research_mode", "⚡ Web-Suche Schnell (mittel)")),  # research_mode
            gr.update(value=current_settings.get("show_transcription", False)),  # show_transcription
            gr.update(value=current_settings.get("enable_gpu", True)),  # enable_gpu
            current_settings["model"]  # previous_model state
        )

    app.load(
        on_page_load,
        outputs=[audio_input, recording_state, model, automatik_model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode, show_transcription, enable_gpu, previous_model]
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
        lambda show_trans, user_txt, stt_t, res_mode, mdl, auto_mdl, voi, spd, tts_en, tts_eng, gpu_en, temp, num_pred, rep_pen, sd, tp_p, tp_k, hist: \
            chat_audio_step2_with_mode(user_txt, stt_t, res_mode, mdl, auto_mdl, voi, spd, tts_en, tts_eng, gpu_en, {"temperature": temp, "num_predict": int(num_pred) if num_pred != -1 else None, "repeat_penalty": rep_pen, "seed": int(sd) if sd != -1 else None, "top_p": tp_p, "top_k": int(tp_k)}, hist) if not show_trans else ("", hist, 0.0),
        inputs=[show_transcription, user_text, stt_time_state, research_mode, model, automatik_model, voice, tts_speed, enable_tts, tts_engine, enable_gpu, llm_temperature, llm_num_predict, llm_repeat_penalty, llm_seed, llm_top_p, llm_top_k, history],
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
        lambda txt, res_mode, mdl, auto_mdl, voi, spd, tts_en, tts_eng, gpu_en, temp, num_pred, rep_pen, sd, tp_p, tp_k, hist: \
            chat_text_step1_with_mode(txt, res_mode, mdl, auto_mdl, voi, spd, tts_en, tts_eng, gpu_en, {"temperature": temp, "num_predict": int(num_pred) if num_pred != -1 else None, "repeat_penalty": rep_pen, "seed": int(sd) if sd != -1 else None, "top_p": tp_p, "top_k": int(tp_k)}, hist),
        inputs=[text_input, research_mode, model, automatik_model, voice, tts_speed, enable_tts, tts_engine, enable_gpu, llm_temperature, llm_num_predict, llm_repeat_penalty, llm_seed, llm_top_p, llm_top_k, history],
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

# ============================================================
# SSL KONFIGURATION (Portable)
# ============================================================
# SSL-Zertifikate (relativ zu PROJECT_ROOT, optional)
SSL_KEYFILE = PROJECT_ROOT / "ssl" / "privkey.pem"
SSL_CERTFILE = PROJECT_ROOT / "ssl" / "fullchain.pem"

# Prüfe ob SSL-Zertifikate vorhanden sind
ssl_available = SSL_KEYFILE.exists() and SSL_CERTFILE.exists()

if ssl_available:
    debug_print(f"✅ SSL-Zertifikate gefunden: {SSL_KEYFILE}, {SSL_CERTFILE}")
    debug_print(f"🔒 Server läuft mit HTTPS auf Port 8443")
else:
    debug_print(f"⚠️ SSL-Zertifikate nicht gefunden (optional)")
    debug_print(f"   Erwartete Pfade:")
    debug_print(f"   - {SSL_KEYFILE}")
    debug_print(f"   - {SSL_CERTFILE}")
    debug_print(f"🌐 Server läuft ohne HTTPS auf Port 8443")

app.queue()
app.launch(
    server_name="0.0.0.0",
    server_port=8443,
    ssl_keyfile=str(SSL_KEYFILE) if ssl_available else None,
    ssl_certfile=str(SSL_CERTFILE) if ssl_available else None,
    ssl_verify=False,
    share=False,
    state_session_capacity=10,
    max_threads=10
)
