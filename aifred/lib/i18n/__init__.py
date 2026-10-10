"""
Internationalization (i18n) Module for AIfred Intelligence

Provides UI string translation functionality. The actual translation
strings live in per-language JSON files next to this module (de.json,
en.json, ...) — one file per language, filename = language code.
"""

import json
from pathlib import Path
from typing import Dict, Optional

from ..prompt_loader import get_language


def _load_translations() -> Dict[str, Dict[str, str]]:
    """Load every <lang>.json in this package directory."""
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path(__file__).parent.glob("*.json"))
    }


class TranslationManager:
    """Manages UI translations"""

    # Translation dictionary, loaded from the per-language JSON files
    _translations: Dict[str, Dict[str, str]] = _load_translations()

    @staticmethod
    def _resolve_lang(lang: Optional[str]) -> str:
        """Normalize a language argument to a key of ``_translations``."""
        if lang is None:
            lang = get_language()
            # If language is auto, default to German for now
            if lang == "auto":
                lang = "de"
        if lang not in TranslationManager._translations:
            lang = "de"
        return lang

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
        lang = TranslationManager._resolve_lang(lang)
        translation = TranslationManager._translations[lang]

        if key in translation:
            return translation[key]

        # Fallback to English if key not found in current language
        if lang != "en" and key in TranslationManager._translations["en"]:
            return TranslationManager._translations["en"][key]

        # Final fallback: return the key itself
        return key


# Convenience function
def t(key: str, lang: Optional[str] = None, count: Optional[int] = None, **kwargs) -> str:
    """
    Convenience function to get translated text with optional formatting and pluralization.

    Args:
        key: Translation key (base key, without _plural suffix)
        lang: Language code (de, en) or None for current language
        count: If provided, auto-selects singular/plural key and adds {count} to format args
        **kwargs: Additional format arguments for placeholders like {name}, etc.

    Returns:
        Translated string (formatted if count or kwargs provided)

    Examples:
        t("greeting")  # Simple lookup
        t("sources_unavailable", count=3)  # Auto-pluralization: uses key + "_plural"
        t("sources_unavailable", count=1)  # Singular: uses key as-is
        t("welcome_user", lang="de", name="Max")  # With language + formatting
    """
    # Handle pluralization: count=1 → singular key, count>1 → key_plural
    if count is not None:
        actual_key = key if count == 1 else f"{key}_plural"
        kwargs["count"] = count
    else:
        actual_key = key

    template = TranslationManager.get_text(actual_key, lang)
    if kwargs:
        return template.format(**kwargs)
    return template


def tts_label_to_key(label: str) -> str:
    """Map a translated TTS engine label back to its internal key.

    Searches all languages since get_language() is unreliable in handler context.
    """
    from ..config import TTS_ENGINE_KEYS
    for lang_translations in TranslationManager._translations.values():
        for key in TTS_ENGINE_KEYS:
            if lang_translations.get(f"tts_engine_{key}") == label:
                return key
    return label


def tts_key_to_label(key: str, lang: Optional[str] = None) -> str:
    """Map an internal TTS engine key to its translated display label.

    Used by the engine dropdowns (escalation list, agent editor, narrator).
    """
    return t(f"tts_engine_{key}", lang=lang)
