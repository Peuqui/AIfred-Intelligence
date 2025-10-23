"""
Prompt Loader Module
====================

Lädt Prompts aus externen Dateien im /prompts/ Verzeichnis.

Vorteile:
- Prompts getrennt vom Code
- Einfachere Wartung und Anpassung
- Versionskontrolle für Prompts
- Kein Code-Reload nötig bei Prompt-Änderungen

Verwendung:
    from lib.prompt_loader import load_prompt

    prompt = load_prompt('url_rating', query="Wetter Berlin", url_list="...")
"""

import os
from pathlib import Path

# Basis-Verzeichnis für Prompts (relativ zur Projektroot)
PROMPTS_DIR = Path(__file__).parent.parent / 'prompts'


def load_prompt(prompt_name, **kwargs):
    """
    Lädt einen Prompt aus einer Datei und ersetzt Platzhalter

    Args:
        prompt_name: Name der Prompt-Datei (ohne .txt Extension)
        **kwargs: Keyword-Arguments für String-Formatierung (z.B. query="...", url_list="...")

    Returns:
        str: Formatierter Prompt-String

    Raises:
        FileNotFoundError: Wenn Prompt-Datei nicht existiert
        KeyError: Wenn erforderliche Platzhalter fehlen

    Beispiele:
        # URL-Rating Prompt
        prompt = load_prompt('url_rating', query="Wetter Berlin", url_list="1. ...")

        # Query-Optimization Prompt
        prompt = load_prompt('query_optimization', user_text="Wie wird das Wetter?")

        # System RAG Prompt
        prompt = load_prompt('system_rag',
                           current_year="2025",
                           current_date="24.10.2025",
                           context="...")
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
                f"Erforderliche Platzhalter: {get_placeholders(prompt_template)}\n"
                f"Übergebene kwargs: {list(kwargs.keys())}"
            )

    return prompt_template


def list_available_prompts():
    """
    Listet alle verfügbaren Prompts auf

    Returns:
        list: Liste aller verfügbaren Prompt-Namen (ohne .txt)
    """
    if not PROMPTS_DIR.exists():
        return []

    return [
        p.stem for p in PROMPTS_DIR.glob('*.txt')
    ]


def get_placeholders(prompt_template):
    """
    Extrahiert alle Platzhalter aus einem Prompt-Template

    Args:
        prompt_template: Prompt-String mit {placeholders}

    Returns:
        set: Set aller gefundenen Platzhalter
    """
    import re
    # Finde alle {placeholder} Muster
    placeholders = re.findall(r'\{(\w+)\}', prompt_template)
    return set(placeholders)


def reload_prompt(prompt_name):
    """
    Lädt einen Prompt neu (nützlich während Entwicklung/Testing)

    Args:
        prompt_name: Name der Prompt-Datei

    Returns:
        str: Neu geladener Prompt (ohne Formatierung)
    """
    return load_prompt(prompt_name)


# Convenience-Funktionen für häufig genutzte Prompts
def get_url_rating_prompt(query, url_list):
    """URL-Rating Prompt laden"""
    return load_prompt('url_rating', query=query, url_list=url_list)


def get_query_optimization_prompt(user_text):
    """Query-Optimization Prompt laden"""
    return load_prompt('query_optimization', user_text=user_text)


def get_decision_making_prompt(user_text, cache_metadata=""):
    """Decision-Making Prompt laden"""
    return load_prompt('decision_making', user_text=user_text, cache_metadata=cache_metadata)


def get_intent_detection_prompt(user_query):
    """Intent-Detection Prompt laden"""
    return load_prompt('intent_detection', user_query=user_query)


def get_followup_intent_prompt(original_query, followup_query):
    """Followup-Intent-Detection Prompt laden"""
    return load_prompt('followup_intent_detection',
                      original_query=original_query,
                      followup_query=followup_query)


def get_system_rag_prompt(current_year, current_date, context):
    """System RAG Prompt laden"""
    return load_prompt('system_rag',
                      current_year=current_year,
                      current_date=current_date,
                      context=context)


# Debug-Funktion
if __name__ == "__main__":
    print("Verfügbare Prompts:")
    for prompt in list_available_prompts():
        print(f"  - {prompt}")

    print("\nTest: URL-Rating Prompt laden:")
    try:
        prompt = get_url_rating_prompt(
            query="Wetter Berlin",
            url_list="1. https://wetter.com/berlin\n   Titel: Wetter Berlin"
        )
        print(f"✅ Erfolgreich geladen ({len(prompt)} Zeichen)")
    except Exception as e:
        print(f"❌ Fehler: {e}")
