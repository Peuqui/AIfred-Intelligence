"""
Prompt Loader Module with i18n Support

Loads prompts from language-specific directories (de/ or en/).
Supports automatic language detection.
No fallbacks - prompts must exist in both languages.
"""

from pathlib import Path
from typing import Optional
import re

# Base directory for prompts (relative to project root)
PROMPTS_DIR = Path(__file__).parent.parent.parent / 'prompts'

# Global language setting (can be overridden)
_current_language = "auto"  # "auto", "de", "en"


def detect_language(text: str) -> str:
    """
    Simple language detection based on keywords

    Args:
        text: Text to analyze

    Returns:
        "en" or "de"
    """
    # Common English indicators
    english_patterns = [
        r'\b(the|is|are|what|where|when|how|why|can|could|would|should)\b',
        r'\b(weather|today|tomorrow|please|thanks|hello)\b',
        r'\b(I|you|he|she|it|we|they|my|your)\b'
    ]

    # Common German indicators
    german_patterns = [
        r'\b(der|die|das|ein|eine|ist|sind|was|wo|wann|wie|warum)\b',
        r'\b(wetter|heute|morgen|bitte|danke|hallo)\b',
        r'\b(ich|du|er|sie|es|wir|ihr|mein|dein)\b',
        r'\b(können|könntest|würde|sollte)\b'
    ]

    text_lower = text.lower()

    # Count matches
    en_score = sum(1 for pattern in english_patterns if re.search(pattern, text_lower, re.IGNORECASE))
    de_score = sum(1 for pattern in german_patterns if re.search(pattern, text_lower, re.IGNORECASE))

    # Default to German if unclear (since AIfred is primarily German)
    return "en" if en_score > de_score else "de"


def set_language(lang: str):
    """
    Set the global language for prompts

    Args:
        lang: "auto", "de", or "en"
    """
    global _current_language
    if lang in ["auto", "de", "en"]:
        _current_language = lang
    else:
        raise ValueError(f"Unsupported language: {lang}. Use 'auto', 'de', or 'en'")


def get_language() -> str:
    """Get the current language setting"""
    return _current_language


def load_prompt(prompt_name: str, lang: Optional[str] = None, user_text: str = None, **kwargs) -> str:
    """
    Load a prompt from a file with language support

    Args:
        prompt_name: Name of the prompt file (without .txt extension)
        lang: Language override ("de", "en", or None for current setting)
        user_text: User text for auto-detection (if lang="auto")
        **kwargs: Keyword arguments for string formatting

    Returns:
        Formatted prompt string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
        KeyError: If required placeholders are missing
    """
    # Determine language
    if lang is None:
        lang = _current_language

    if lang == "auto":
        # Need user_text for auto-detection
        if user_text:
            lang = detect_language(user_text)
        elif 'user_text' in kwargs:
            lang = detect_language(kwargs['user_text'])
        else:
            # Default to German if no text to analyze
            lang = "de"

    # Load from language-specific directory only (no fallback)
    prompt_file = PROMPTS_DIR / lang / f"{prompt_name}.txt"

    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {prompt_file}\n"
            f"Expected language: {lang}\n"
            f"Available prompts: {list_available_prompts()}"
        )

    # Load prompt file
    with open(prompt_file, 'r', encoding='utf-8') as f:
        prompt_template = f.read()

    # Format with kwargs if provided
    if kwargs or user_text:
        try:
            # Merge user_text into kwargs if not already there
            if user_text and 'user_text' not in kwargs:
                kwargs['user_text'] = user_text
            return prompt_template.format(**kwargs)
        except KeyError as e:
            raise KeyError(
                f"Missing placeholder in prompt '{prompt_name}': {e}\n"
                f"Provided kwargs: {list(kwargs.keys())}"
            )

    return prompt_template


def list_available_prompts() -> list:
    """
    List all available prompts across all languages

    Returns:
        List of all available prompt names (without .txt)
    """
    if not PROMPTS_DIR.exists():
        return []

    prompts = set()

    # Check language directories only (no root directory)
    for lang_dir in ['de', 'en']:
        lang_path = PROMPTS_DIR / lang_dir
        if lang_path.exists():
            prompts.update(p.stem for p in lang_path.glob('*.txt'))

    return sorted(list(prompts))


# ============================================================
# Convenience functions for frequently used prompts
# ============================================================

def get_query_optimization_prompt(user_text: str, lang: Optional[str] = None) -> str:
    """Load query optimization prompt with current date context"""
    from datetime import datetime
    current_date = datetime.now().strftime("%d.%m.%Y")
    current_year = datetime.now().strftime("%Y")
    return load_prompt('query_optimization', lang=lang, user_text=user_text,
                      current_date=current_date, current_year=current_year)


def get_decision_making_prompt(user_text: str, lang: Optional[str] = None) -> str:
    """Load decision-making prompt with current date context"""
    from datetime import datetime
    current_date = datetime.now().strftime("%d.%m.%Y")  # Format: 12.11.2025
    return load_prompt('decision_making', lang=lang, user_text=user_text, current_date=current_date)


# Cache decision addon removed - will be replaced with Vector DB semantic search


def get_intent_detection_prompt(user_query: str, lang: Optional[str] = None) -> str:
    """Load intent detection prompt"""
    return load_prompt('intent_detection', lang=lang, user_query=user_query)


def get_followup_intent_prompt(original_query: str, followup_query: str, lang: Optional[str] = None) -> str:
    """Load followup intent detection prompt"""
    return load_prompt(
        'followup_intent_detection',
        lang=lang,
        original_query=original_query,
        followup_query=followup_query
    )


def get_system_rag_prompt(current_year: str, current_date: str, context: str,
                          user_text: str = "", lang: Optional[str] = None) -> str:
    """Load system RAG prompt"""
    return load_prompt(
        'system_rag',
        lang=lang,
        user_text=user_text,
        current_year=current_year,
        current_date=current_date,
        context=context
    )


# Cache metadata prompt removed - will be replaced with Vector DB embeddings