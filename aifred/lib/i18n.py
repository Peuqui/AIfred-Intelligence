"""
Internationalization (i18n) Module for AIfred Intelligence

Provides UI string translation functionality
"""

from typing import Dict, Optional
from .prompt_loader import get_language


class TranslationManager:
    """Manages UI translations"""
    
    # Translation dictionary
    _translations: Dict[str, Dict[str, str]] = {
        "de": {
            # UI Labels
            "voice_input": "🎙️ Spracheingabe",
            "audio_input_placeholder": "Audio-Eingabe (Microphone Recording)",
            "stt_not_ported": "⚠️ STT/TTS noch nicht portiert - Coming Soon!",
            "tip_automatic_transcription": "💡 Tipp: Nach dem Stoppen läuft automatisch die Transkription",
            "text_input": "⌨️ Texteingabe",
            "enter_your_question": "Oder schreibe hier deine Frage...",
            "research_mode": "🎯 Recherche-Modus",
            "research_mode_auto": "🤖 Automatik (KI entscheidet)",
            "research_mode_none": "🧠 Eigenes Wissen (schnell)",
            "research_mode_quick": "⚡ Web-Suche Schnell (3 beste)",
            "research_mode_deep": "🔍 Web-Suche Ausführlich (7 beste)",
            "choose_research_mode": "Wähle, wie der Assistant Fragen beantwortet",
            "send_text": "💬 Text senden",
            "clear_chat": "🗑️ Chat löschen",
            "llm_parameters": "⚙️ LLM-Parameter (Erweitert)",
            "temperature": "🌡️ Temperature",
            "current": "Aktuell:",
            "temperature_info": "0.0 = deterministisch, 0.2 = fakten, 0.8 = ausgewogen, 1.5+ = kreativ",
            "context_window": "📦 Context Window (num_ctx)",
            "context_window_info": "Auto-berechnet basierend auf Message-Größe",
            
            # Progress Banner
            "automatic_decision": "Automatik-Entscheidung ...",
            "web_scraping": "Web-Scraping",
            "compressing_context": "Komprimiere Kontext ...",
            "generating_answer": "Generiere Antwort ...",
            "websites_unreachable": "Website nicht erreichbar",
            "websites_unreachable_plural": "Websites nicht erreichbar",
            
            # Chat Display
            "input": "Eingabe:",
            "ai_response": "AI Antwort:",
            
            # TTS Section
            "tts_output": "🔊 Sprachausgabe (AI-Antwort)",
            "tts_enabled": "Sprachausgabe aktiviert",
            "tts_regenerate": "🔄 Neu generieren",
            "tts_not_ported": "⚠️ TTS noch nicht portiert - Coming Soon!",
            
            # Chat History
            "chat_history": "💬 Chat Verlauf",
            "messages_count": "messages",
            
            # Debug Console
            "debug_console": "🐛 Debug Console",
            "live_debug_output": "Live Debug-Output: LLM-Starts, Entscheidungen, Statistiken",
            "auto_scroll": "Auto-Scroll",
            
            # Settings
            "settings": "⚙️ Einstellungen",
            "backend": "Backend:",
            "main_llm": "Haupt-LLM:",
            "automatic_llm": "Automatik-LLM:",
            "system_control": "🔄 System-Steuerung",
            "restart_ollama": "🔄 Ollama neu starten",
            "restart_aifred": "🔄 AIfred neu starten",
            "ollama_restart_info": "ℹ️ Ollama-Neustart: Stoppt laufende Generierungen, lädt Models neu",
            "chat_preserved": "(Chats bleiben erhalten)",
            "aifred_restart_warning": "⚠️ AIfred-Neustart: Löscht ALLE Chats, Caches und Debug-Logs komplett!",
            
            # Main Page
            "aifred_intelligence": "🎩 AIfred Intelligence",
            "subtitle": "AI at your service • Benannt nach Alfred (Großvater) und Wolfgang Alfred (Vater)",
            
            # Research mode mapping (for internal use)
            "research_mode_map": {
                "🧠 Eigenes Wissen (schnell)": "none",
                "⚡ Web-Suche Schnell (3 beste)": "quick",
                "🔍 Web-Suche Ausführlich (7 beste)": "deep",
                "🤖 Automatik (KI entscheidet)": "automatik"
            },
            "reverse_research_mode_map": {
                "none": "🧠 Eigenes Wissen (schnell)",
                "quick": "⚡ Web-Suche Schnell (3 beste)",
                "deep": "🔍 Web-Suche Ausführlich (7 beste)",
                "automatik": "🤖 Automatik (KI entscheidet)"
            }
        },
        "en": {
            # UI Labels
            "voice_input": "🎙️ Voice Input",
            "audio_input_placeholder": "Audio Input (Microphone Recording)",
            "stt_not_ported": "⚠️ STT/TTS not yet ported - Coming Soon!",
            "tip_automatic_transcription": "💡 Tip: Automatic transcription runs after stopping",
            "text_input": "⌨️ Text Input",
            "enter_your_question": "Or write your question here...",
            "research_mode": "🎯 Research Mode",
            "research_mode_auto": "🤖 Automatic (AI decides)",
            "research_mode_none": "🧠 Own Knowledge (fast)",
            "research_mode_quick": "⚡ Web Search Quick (3 best)",
            "research_mode_deep": "🔍 Web Search Detailed (7 best)",
            "choose_research_mode": "Choose how the assistant answers questions",
            "send_text": "💬 Send Text",
            "clear_chat": "🗑️ Clear Chat",
            "llm_parameters": "⚙️ LLM Parameters (Advanced)",
            "temperature": "🌡️ Temperature",
            "current": "Current:",
            "temperature_info": "0.0 = deterministic, 0.2 = factual, 0.8 = balanced, 1.5+ = creative",
            "context_window": "📦 Context Window (num_ctx)",
            "context_window_info": "Auto-calculated based on message size",
            
            # Progress Banner
            "automatic_decision": "Automatic decision ...",
            "web_scraping": "Web Scraping",
            "compressing_context": "Compressing Context ...",
            "generating_answer": "Generating Answer ...",
            "websites_unreachable": "Website unreachable",
            "websites_unreachable_plural": "Websites unreachable",
            
            # Chat Display
            "input": "Input:",
            "ai_response": "AI Response:",
            
            # TTS Section
            "tts_output": "🔊 Text-to-Speech (AI Answer)",
            "tts_enabled": "Text-to-Speech enabled",
            "tts_regenerate": "🔄 Regenerate",
            "tts_not_ported": "⚠️ TTS not yet ported - Coming Soon!",
            
            # Chat History
            "chat_history": "💬 Chat History",
            "messages_count": "messages",
            
            # Debug Console
            "debug_console": "🐛 Debug Console",
            "live_debug_output": "Live Debug Output: LLM starts, decisions, statistics",
            "auto_scroll": "Auto-Scroll",
            
            # Settings
            "settings": "⚙️ Settings",
            "backend": "Backend:",
            "main_llm": "Main LLM:",
            "automatic_llm": "Automatic LLM:",
            "system_control": "🔄 System Control",
            "restart_ollama": "🔄 Restart Ollama",
            "restart_aifred": "🔄 Restart AIfred",
            "ollama_restart_info": "ℹ️ Ollama restart: Stops ongoing generations, reloads models",
            "chat_preserved": "(Chats are preserved)",
            "aifred_restart_warning": "⚠️ AIfred restart: Deletes ALL chats, caches and debug logs completely!",
            
            # Main Page
            "aifred_intelligence": "🎩 AIfred Intelligence",
            "subtitle": "AI at your service • Named after Alfred (grandfather) and Wolfgang Alfred (father)",
            
            # Research mode mapping (for internal use)
            "research_mode_map": {
                "🧠 Own Knowledge (fast)": "none",
                "⚡ Web Search Quick (3 best)": "quick",
                "🔍 Web Search Detailed (7 best)": "deep",
                "🤖 Automatic (AI decides)": "automatik"
            },
            "reverse_research_mode_map": {
                "none": "🧠 Own Knowledge (fast)",
                "quick": "⚡ Web Search Quick (3 best)",
                "deep": "🔍 Web Search Detailed (7 best)",
                "automatik": "🤖 Automatic (AI decides)"
            }
        }
    }

    @staticmethod
    def get_text(key: str, lang: Optional[str] = None) -> str:
        """
        Get translated text for a given key
        
        Args:
            key: Translation key
            lang: Language code (de, en) or None for current language
            
        Returns:
            Translated string
        """
        if lang is None:
            lang = get_language()
            # If language is auto, default to German for now
            if lang == "auto":
                lang = "de"
        
        # Fallback to German if language not found
        if lang not in TranslationManager._translations:
            lang = "de"
        
        # Get translation for the key
        translation = TranslationManager._translations[lang]
        
        if key in translation:
            return translation[key]
        
        # Fallback to English if key not found in current language
        if lang != "en" and key in TranslationManager._translations["en"]:
            return TranslationManager._translations["en"][key]
        
        # Final fallback: return the key itself
        return key

    @staticmethod
    def get_research_mode_value(display_text: str, lang: Optional[str] = None) -> str:
        """
        Get internal research mode value for display text
        
        Args:
            display_text: Display text of research mode
            lang: Language code or None for current language
            
        Returns:
            Internal mode value (none, quick, deep, automatik)
        """
        if lang is None:
            lang = get_language()
            if lang == "auto":
                lang = "de"
        
        if lang not in TranslationManager._translations:
            lang = "de"
        
        # Get the mapping for the current language
        mode_map = TranslationManager._translations[lang].get("research_mode_map", {})
        
        # Return the internal value or default to automatik
        return mode_map.get(display_text, "automatik")

    @staticmethod
    def get_research_mode_display(mode_value: str, lang: Optional[str] = None) -> str:
        """
        Get display text for research mode value
        
        Args:
            mode_value: Internal mode value (none, quick, deep, automatik)
            lang: Language code or None for current language
            
        Returns:
            Display text for the mode
        """
        if lang is None:
            lang = get_language()
            if lang == "auto":
                lang = "de"
        
        if lang not in TranslationManager._translations:
            lang = "de"
        
        # Get the reverse mapping for the current language
        reverse_mode_map = TranslationManager._translations[lang].get("reverse_research_mode_map", {})
        
        # Return the display text or default to automatik display
        return reverse_mode_map.get(mode_value, "🤖 Automatic (AI decides)")


# Convenience function
def t(key: str, lang: Optional[str] = None) -> str:
    """
    Convenience function to get translated text
    
    Args:
        key: Translation key
        lang: Language code (de, en) or None for current language
        
    Returns:
        Translated string
    """
    return TranslationManager.get_text(key, lang)