"""
Intent Detector - Query Intent Classification

Classifies user queries for adaptive temperature selection:
- FAKTISCH: Factual queries (low temperature)
- KREATIV: Creative queries (high temperature)
- GEMISCHT: Mixed queries (medium temperature)
"""

from typing import Optional
from .logging_utils import debug_print
from .prompt_loader import get_intent_detection_prompt, get_followup_intent_prompt


def parse_intent_from_response(intent_raw: str, context: str = "general") -> str:
    """
    Extrahiert Intent aus LLM-Antwort (auch wenn LLM mehr Text schreibt)

    Args:
        intent_raw: Rohe LLM-Antwort
        context: Kontext für Logging ("general" oder "cache_followup")

    Returns:
        str: "FAKTISCH", "KREATIV" oder "GEMISCHT"
    """
    intent_upper = intent_raw.strip().upper()

    # Extrahiere Intent (Priorisierung)
    if "FAKTISCH" in intent_upper:
        return "FAKTISCH"
    elif "KREATIV" in intent_upper:
        return "KREATIV"
    elif "GEMISCHT" in intent_upper:
        return "GEMISCHT"
    else:
        # Fallback
        prefix = "Cache-Intent" if context == "cache_followup" else "Intent"
        debug_print(f"⚠️ {prefix} unbekannt: '{intent_raw}' → Default: FAKTISCH")
        return "FAKTISCH"


def detect_query_intent(
    user_query: str,
    automatik_model: str = "qwen3:1.7b",
    llm_client = None
) -> str:
    """
    Erkennt die Intent einer User-Anfrage für adaptive Temperature-Wahl

    Args:
        user_query: User-Frage
        automatik_model: LLM für Intent-Detection (default: qwen3:1.7b)
        llm_client: LLMClient instance (if None, uses ollama directly - legacy)

    Returns:
        str: "FAKTISCH", "KREATIV" oder "GEMISCHT"
    """
    prompt = get_intent_detection_prompt(user_query=user_query)

    try:
        debug_print(f"🎯 Intent-Detection für Query: {user_query[:60]}...")

        if llm_client:
            # New unified client
            response = llm_client.chat_sync(
                model=automatik_model,
                messages=[{'role': 'user', 'content': prompt}],
                options={
                    'temperature': 0.2,  # Niedrig für konsistente Intent-Detection
                    'num_ctx': 4096  # Standard Context für Intent-Detection
                }
            )
            intent_raw = response.text
        else:
            # Legacy fallback (will be removed after async conversion)
            import ollama
            response = ollama.chat(
                model=automatik_model,
                messages=[{'role': 'user', 'content': prompt}],
                options={
                    'temperature': 0.2,
                    'num_ctx': 4096
                }
            )
            intent_raw = response['message']['content']

        intent = parse_intent_from_response(intent_raw, context="general")
        debug_print(f"✅ Intent erkannt: {intent}")
        return intent

    except Exception as e:
        debug_print(f"❌ Intent-Detection Fehler: {e} → Fallback: FAKTISCH")
        return "FAKTISCH"  # Safe Fallback


def detect_cache_followup_intent(
    original_query: str,
    followup_query: str,
    automatik_model: str = "qwen3:1.7b",
    llm_client = None
) -> str:
    """
    Erkennt die Intent einer Nachfrage zu einer gecachten Recherche

    Args:
        original_query: Ursprüngliche Recherche-Frage
        followup_query: Nachfrage des Users
        automatik_model: LLM für Intent-Detection
        llm_client: LLMClient instance (if None, uses ollama directly - legacy)

    Returns:
        str: "FAKTISCH", "KREATIV" oder "GEMISCHT"
    """
    prompt = get_followup_intent_prompt(
        original_query=original_query,
        followup_query=followup_query
    )

    try:
        debug_print(f"🎯 Cache-Followup Intent-Detection mit {automatik_model}: {followup_query[:60]}...")

        if llm_client:
            # New unified client
            response = llm_client.chat_sync(
                model=automatik_model,
                messages=[{'role': 'user', 'content': prompt}],
                options={
                    'temperature': 0.2,
                    'num_ctx': 4096
                }
            )
            intent_raw = response.text
        else:
            # Legacy fallback (will be removed after async conversion)
            import ollama
            response = ollama.chat(
                model=automatik_model,
                messages=[{'role': 'user', 'content': prompt}],
                options={
                    'temperature': 0.2,
                    'num_ctx': 4096
                }
            )
            intent_raw = response['message']['content']

        intent = parse_intent_from_response(intent_raw, context="cache_followup")
        debug_print(f"✅ Cache-Followup Intent ({automatik_model}): {intent}")
        return intent

    except Exception as e:
        debug_print(f"❌ Cache-Followup Intent-Detection Fehler: {e} → Fallback: FAKTISCH")
        return "FAKTISCH"


def get_temperature_for_intent(intent: str) -> float:
    """
    Gibt die passende Temperature für einen Intent zurück

    Args:
        intent: "FAKTISCH", "KREATIV" oder "GEMISCHT"

    Returns:
        float: Temperature (0.2, 0.5 oder 0.8)
    """
    temp_map = {
        "FAKTISCH": 0.2,
        "KREATIV": 0.8,
        "GEMISCHT": 0.5
    }
    return temp_map.get(intent, 0.2)  # Fallback: 0.2
