"""
Prompt Loader Module - Reflex Edition

Lädt Prompts aus externen Dateien im /prompts/ Verzeichnis.

Portiert von Gradio-Legacy
"""

from pathlib import Path

# Basis-Verzeichnis für Prompts (relativ zur Projektroot)
# aifred/lib/prompt_loader.py → ../../prompts/
PROMPTS_DIR = Path(__file__).parent.parent.parent / 'prompts'


def load_prompt(prompt_name: str, **kwargs) -> str:
    """
    Lädt einen Prompt aus einer Datei und ersetzt Platzhalter

    Args:
        prompt_name: Name der Prompt-Datei (ohne .txt Extension)
        **kwargs: Keyword-Arguments für String-Formatierung

    Returns:
        Formatierter Prompt-String

    Raises:
        FileNotFoundError: Wenn Prompt-Datei nicht existiert
        KeyError: Wenn erforderliche Platzhalter fehlen
    """
    prompt_file = PROMPTS_DIR / f"{prompt_name}.txt"

    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Prompt-Datei nicht gefunden: {prompt_file}\n"
            f"Verfügbare Prompts: {list_available_prompts()}"
        )

    # Lade Prompt-Datei
    with open(prompt_file, 'r', encoding='utf-8') as f:
        prompt_template = f.read()

    # Formatiere mit kwargs (falls vorhanden)
    if kwargs:
        try:
            return prompt_template.format(**kwargs)
        except KeyError as e:
            raise KeyError(
                f"Fehlender Platzhalter in Prompt '{prompt_name}': {e}\n"
                f"Übergebene kwargs: {list(kwargs.keys())}"
            )

    return prompt_template


def list_available_prompts() -> list:
    """
    Listet alle verfügbaren Prompts auf

    Returns:
        Liste aller verfügbaren Prompt-Namen (ohne .txt)
    """
    if not PROMPTS_DIR.exists():
        return []

    return [p.stem for p in PROMPTS_DIR.glob('*.txt')]


# ============================================================
# Convenience-Funktionen für häufig genutzte Prompts
# ============================================================

def get_query_optimization_prompt(user_text: str) -> str:
    """Query-Optimization Prompt laden"""
    return load_prompt('query_optimization', user_text=user_text)


def get_decision_making_prompt(user_text: str, cache_metadata: str = "") -> str:
    """Decision-Making Prompt laden"""
    return load_prompt('decision_making', user_text=user_text, cache_metadata=cache_metadata)


def get_intent_detection_prompt(user_query: str) -> str:
    """Intent-Detection Prompt laden"""
    return load_prompt('intent_detection', user_query=user_query)


def get_followup_intent_prompt(original_query: str, followup_query: str) -> str:
    """Followup-Intent-Detection Prompt laden"""
    return load_prompt(
        'followup_intent_detection',
        original_query=original_query,
        followup_query=followup_query
    )


def get_system_rag_prompt(current_year: str, current_date: str, context: str) -> str:
    """System RAG Prompt laden"""
    return load_prompt(
        'system_rag',
        current_year=current_year,
        current_date=current_date,
        context=context
    )


def get_cache_metadata_prompt(sources_preview: str) -> str:
    """Cache-Metadata Prompt laden"""
    return load_prompt('cache_metadata', sources_preview=sources_preview)
