"""
Intent Detector - Query Intent Classification

Classifies user queries for adaptive temperature selection:
- FAKTISCH: Factual queries (low temperature)
- KREATIV: Creative queries (high temperature)
- GEMISCHT: Mixed queries (medium temperature)
"""

from .logging_utils import log_message
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
        log_message(f"⚠️ {prefix} unbekannt: '{intent_raw}' → Default: FAKTISCH")
        return "FAKTISCH"


async def detect_query_intent(
    user_query: str,
    automatik_model: str,
    llm_client
) -> str:
    """
    Erkennt die Intent einer User-Anfrage für adaptive Temperature-Wahl

    Args:
        user_query: User-Frage
        automatik_model: LLM für Intent-Detection
        llm_client: LLMClient instance

    Returns:
        str: "FAKTISCH", "KREATIV" oder "GEMISCHT"
    """
    # Spracherkennung für Nutzereingabe
    from .prompt_loader import detect_language
    detected_user_language = detect_language(user_query)
    log_message(f"🌐 Spracherkennung: Nutzereingabe ist wahrscheinlich '{detected_user_language.upper()}' (für Prompt-Auswahl)")

    prompt = get_intent_detection_prompt(user_query=user_query, lang=detected_user_language)

    try:
        log_message(f"🎯 Intent-Detection für Query: {user_query[:60]}...")

        response = await llm_client.chat(
            model=automatik_model,
            messages=[{'role': 'user', 'content': prompt}],
            options={
                'temperature': 0.2,  # Niedrig für konsistente Intent-Detection
                'num_ctx': 4096,  # Standard Context für Intent-Detection
                'enable_thinking': False  # Fast decisions, no reasoning needed
            }
        )
        intent_raw = response.text

        intent = parse_intent_from_response(intent_raw, context="general")
        log_message(f"✅ Intent erkannt: {intent}")
        return intent

    except Exception as e:
        log_message(f"❌ Intent-Detection Fehler: {e} → Fallback: FAKTISCH")
        return "FAKTISCH"  # Safe Fallback


async def detect_cache_followup_intent(
    original_query: str,
    followup_query: str,
    automatik_model: str,
    llm_client
) -> str:
    """
    Erkennt die Intent einer Nachfrage zu einer gecachten Recherche

    Args:
        original_query: Ursprüngliche Recherche-Frage
        followup_query: Nachfrage des Users
        automatik_model: LLM für Intent-Detection
        llm_client: LLMClient instance

    Returns:
        str: "FAKTISCH", "KREATIV" oder "GEMISCHT"
    """
    # Spracherkennung für Nachfrage
    from .prompt_loader import detect_language
    detected_user_language = detect_language(followup_query)
    log_message(f"🌐 Spracherkennung: Nachfrage ist wahrscheinlich '{detected_user_language.upper()}' (für Prompt-Auswahl)")

    prompt = get_followup_intent_prompt(
        original_query=original_query,
        followup_query=followup_query,
        lang=detected_user_language
    )

    try:
        log_message(f"🎯 Cache-Followup Intent-Detection mit {automatik_model}: {followup_query[:60]}...")

        response = await llm_client.chat(
            model=automatik_model,
            messages=[{'role': 'user', 'content': prompt}],
            options={
                'temperature': 0.2,
                'num_ctx': 4096,
                'enable_thinking': False  # Fast decisions, no reasoning needed
            }
        )
        intent_raw = response.text

        intent = parse_intent_from_response(intent_raw, context="cache_followup")
        log_message(f"✅ Cache-Followup Intent ({automatik_model}): {intent}")
        return intent

    except Exception as e:
        log_message(f"❌ Cache-Followup Intent-Detection Fehler: {e} → Fallback: FAKTISCH")
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


def get_temperature_label(intent: str) -> str:
    """
    Gibt das deutsche Label für einen Intent zurück (für UI-Anzeige)

    Args:
        intent: "FAKTISCH", "KREATIV" oder "GEMISCHT"

    Returns:
        str: "faktisch", "kreativ" oder "gemischt"
    """
    label_map = {
        "FAKTISCH": "faktisch",
        "KREATIV": "kreativ",
        "GEMISCHT": "gemischt"
    }
    return label_map.get(intent, "faktisch")  # Fallback: faktisch
