"""
Intent Detector - Query Intent Classification

Classifies user queries for adaptive temperature selection:
- FAKTISCH: Factual queries (low temperature)
- KREATIV: Creative queries (high temperature)
- GEMISCHT: Mixed queries (medium temperature)

Also detects dialog addressing (who is being spoken to):
- aifred: User is directly addressing AIfred
- sokrates: User is directly addressing Sokrates
- salomo: User is directly addressing Salomo
- None: No specific addressee
"""

from typing import Optional, Dict, Tuple, Any, List
from .logging_utils import log_message
from .prompt_loader import get_intent_detection_prompt, get_followup_intent_prompt
from .context_manager import strip_thinking_blocks


# Valid values for mode-switch field validation (defensive parsing)
_VALID_RESEARCH_MODES = {"none", "quick", "deep", "automatik"}
_VALID_MULTI_AGENT_MODES = {
    "standard", "sokrates", "tribunal", "symposion",
    "critical_review", "auto_consensus",
}


def _parse_mode_switch(mode_field: str) -> Dict[str, Any]:
    """
    Parse the MODE_SWITCH field of the intent detection output.

    Format: comma-separated key=value pairs, e.g. "multi=tribunal,research=deep"
    or "agent=sokrates,multi=standard".

    Defensive parsing: unknown keys/values are ignored, never raises.

    Args:
        mode_field: Raw mode switch string from LLM

    Returns:
        Dict with validated config updates (keys: research_mode,
        multi_agent_mode, active_agent). Empty dict if nothing valid.
    """
    updates: Dict[str, Any] = {}
    if not mode_field or not mode_field.strip():
        return updates

    for pair in mode_field.split(","):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        key = key.strip().lower()
        value = value.strip().lower()
        if not key or not value:
            continue

        if key == "research":
            if value in _VALID_RESEARCH_MODES:
                updates["research_mode"] = value
        elif key == "multi":
            if value in _VALID_MULTI_AGENT_MODES:
                updates["multi_agent_mode"] = value
        elif key == "agent":
            # Check against known agents (built-in + custom)
            from .agent_config import load_agents_raw
            agents = load_agents_raw()
            if value in agents:
                updates["active_agent"] = value
    return updates


def format_mode_switch_summary(updates: Dict[str, Any], lang: str = "de") -> str:
    """Format a human-readable summary of a mode switch for confirmation messages."""
    if not updates:
        return ""
    parts: List[str] = []
    if lang == "de":
        research_labels = {
            "none": "eigenes Wissen",
            "quick": "schnelle Recherche",
            "deep": "Tiefrecherche",
            "automatik": "Automatik",
        }
        multi_labels = {
            "standard": "Standard",
            "sokrates": "Sokrates",
            "tribunal": "Tribunal",
            "symposion": "Symposion",
            "critical_review": "Kritische Prüfung",
            "auto_consensus": "Auto-Konsens",
        }
    else:
        research_labels = {
            "none": "own knowledge",
            "quick": "quick research",
            "deep": "deep research",
            "automatik": "automatic",
        }
        multi_labels = {
            "standard": "standard",
            "sokrates": "Sokrates",
            "tribunal": "Tribunal",
            "symposion": "Symposion",
            "critical_review": "critical review",
            "auto_consensus": "auto consensus",
        }
    if "research_mode" in updates:
        parts.append(f"Research: {research_labels.get(updates['research_mode'], updates['research_mode'])}")
    if "multi_agent_mode" in updates:
        parts.append(f"Mode: {multi_labels.get(updates['multi_agent_mode'], updates['multi_agent_mode'])}")
    if "active_agent" in updates:
        parts.append(f"Agent: {updates['active_agent'].capitalize()}")
    return " · ".join(parts)


def format_intent_result(intent: str, addressee: Optional[str], language: str) -> str:
    """Format intent detection result as debug string (single source of truth).

    Used by browser (add_debug), message_processor (debug), and log output.
    Resolves agent ID to display name (e.g. "pater" → "Pater Tuck").
    """
    if addressee:
        from .agent_config import get_agent_config
        cfg = get_agent_config(addressee)
        addr_display = cfg.display_name if cfg else addressee.capitalize()
    else:
        addr_display = "–"
    return f"Intent: {intent}, Addressee: {addr_display}, Lang: {language.upper()}"


def parse_intent_addressee_language(
    response_raw: str,
    context: str = "general"
) -> Tuple[str, Optional[str], str, Dict[str, Any], str]:
    """
    Extract intent, addressee, language, mode-switch, and remaining query from LLM response.

    Expected format: "INTENT|ADDRESSEE|LANGUAGE|MODE_SWITCH|REMAINING"
    Examples:
        "FACTUAL||DE||"                         → no mode switch, no remainder
        "FACTUAL||EN|multi=tribunal|"           → tribunal switch, pure command
        "FACTUAL||DE|research=deep|zu Thema X"  → deep research + question about X
        "MIXED|sokrates|DE||"                   → direct addressing Sokrates

    Legacy 3-field format is also supported for backward compatibility with
    cache_followup and other callers (MODE_SWITCH + REMAINING default to empty).

    Args:
        response_raw: Raw LLM response
        context: Context for logging ("general" or "cache_followup")

    Returns:
        Tuple[str, Optional[str], str, Dict[str, Any], str]:
            (intent, addressee, language, mode_switch_updates, remaining_query)
    """
    raw = response_raw.strip()

    # Parse pipe-separated format
    parts = raw.split("|") if "|" in raw else [raw]

    intent_part = parts[0].strip().upper() if len(parts) > 0 else ""
    addressee_part = parts[1].strip().lower() if len(parts) > 1 else ""

    # Handle language field - can be at parts[2] OR parts[3] if LLM adds "(empty)"
    # Expected: "FACTUAL||DE" → parts[2]="DE"
    # But LLM may return: "FACTUAL||(empty)|DE" → parts[2]="(empty)", parts[3]="DE"
    language_part = ""
    language_index = 2
    if len(parts) > 3 and parts[2].strip().lower() in ("(empty)", "empty", "none", ""):
        # LLM inserted "(empty)" as 3rd field — language shifts by one
        language_part = parts[3].strip().upper() if len(parts) > 3 else ""
        language_index = 3
    elif len(parts) > 2:
        language_part = parts[2].strip().upper()
        language_index = 2

    # MODE_SWITCH field is at position language_index + 1
    mode_switch_part = ""
    if len(parts) > language_index + 1:
        mode_switch_part = parts[language_index + 1].strip()

    # REMAINING is everything after position language_index + 2 (join back)
    # in case the remaining query contains pipes
    remaining_query = ""
    if len(parts) > language_index + 2:
        remaining_query = "|".join(parts[language_index + 2:]).strip()

    # Parse intent (with English/German support)
    if "FAKTISCH" in intent_part or "FACTUAL" in intent_part:
        intent = "FAKTISCH"
    elif "KREATIV" in intent_part or "CREATIVE" in intent_part:
        intent = "KREATIV"
    elif "GEMISCHT" in intent_part or "MIXED" in intent_part:
        intent = "GEMISCHT"
    else:
        prefix = "Cache-Intent" if context == "cache_followup" else "Intent"
        log_message(f"⚠️ {prefix} unknown: '{response_raw}' → Default: FAKTISCH")
        intent = "FAKTISCH"

    # Parse addressee
    addressee: Optional[str] = None
    if addressee_part:
        # Normalize variations for default agents
        if addressee_part in ("aifred", "alfred", "eifred", "ai fred"):
            addressee = "aifred"
        elif addressee_part in ("sokrates", "socrates"):
            addressee = "sokrates"
        elif addressee_part in ("salomo", "solomon"):
            addressee = "salomo"
        else:
            # Check custom agents by ID and display_name
            from .agent_config import load_agents_raw
            agents = load_agents_raw()
            for aid, adata in agents.items():
                if addressee_part == aid or addressee_part == adata.get("display_name", "").lower():
                    addressee = aid
                    break

    # Parse language (fallback to UI language if LLM didn't specify)
    if language_part in ("DE", "DEUTSCH", "GERMAN"):
        language = "de"
    elif language_part in ("EN", "ENGLISH"):
        language = "en"
    else:
        # No language detected or unknown → Fallback to UI language
        from .prompt_loader import get_language
        language = get_language()

    # Parse mode switch field (defensive — unknown values are ignored)
    mode_switch_updates = _parse_mode_switch(mode_switch_part)

    # Strip trailing/leading placeholders from remaining query
    if remaining_query.lower() in ("(empty)", "empty", "none"):
        remaining_query = ""

    return (intent, addressee, language, mode_switch_updates, remaining_query)


# Keep old function for backwards compatibility (used by cache_followup)
def parse_intent_from_response(intent_raw: str, context: str = "general") -> str:
    """
    Extract intent from LLM response (legacy wrapper).

    Args:
        intent_raw: Raw LLM response
        context: Context for logging ("general" or "cache_followup")

    Returns:
        str: "FAKTISCH", "KREATIV" or "GEMISCHT"
    """
    intent, _, _, _, _ = parse_intent_addressee_language(intent_raw, context)
    return intent


async def detect_query_intent_and_addressee(
    user_query: str,
    automatik_model: str,
    llm_client,
    llm_options: Optional[Dict] = None,
    automatik_num_ctx: Optional[int] = None
) -> Tuple[str, Optional[str], str, Dict[str, Any], str, str]:
    """
    Detect intent, addressee, language, mode-switch, and remaining query.

    Combines all five detection tasks into a single LLM call:
    - intent (for temperature selection)
    - addressee (for dialog routing)
    - language (for prompt selection)
    - mode-switch (voice/text-based config change requests)
    - remaining query (for Option C: mode-switch + question in one message)

    Args:
        user_query: User question
        automatik_model: LLM for intent detection
        llm_client: LLMClient instance
        llm_options: Optional Dict with enable_thinking toggle
        automatik_num_ctx: Context size for Automatik call.
            None = don't set (model keeps current context, avoids reload).
            int = explicit value (e.g. AUTOMATIK_LLM_NUM_CTX for different models).

    Returns:
        Tuple[str, Optional[str], str, Dict[str, Any], str, str]:
            (intent, addressee, detected_language, mode_switch_updates,
             remaining_query, raw_response)

            - intent: "FAKTISCH", "KREATIV" or "GEMISCHT"
            - addressee: "aifred", "sokrates", "salomo" or None
            - detected_language: "de" or "en" (LLM-detected from user query)
            - mode_switch_updates: dict with config changes ({} if none)
            - remaining_query: user's question after stripping mode command
            - raw_response: Raw LLM output for debugging
    """
    # Use English prompt for intent detection (universal, handles all languages)
    prompt = get_intent_detection_prompt(user_query=user_query, lang="en")

    log_message(f"🎯 Intent+Addressee+Language detection for query: {user_query[:60]}...")

    intent_options: Dict = {
        'temperature': 0.2,  # Low for consistent detection
        'enable_thinking': False  # Fast detection without reasoning
    }
    if automatik_num_ctx is not None:
        intent_options['num_ctx'] = automatik_num_ctx

    log_message("🧠 Intent enable_thinking: False (Automatik-Task)")

    response = await llm_client.chat(
        model=automatik_model,
        messages=[{'role': 'user', 'content': prompt}],
        options=intent_options
    )
    response_raw = response.text
    # Strip thinking blocks — models like GPT-OSS always reason regardless of enable_thinking
    response_clean = strip_thinking_blocks(response_raw).strip()

    intent, addressee, detected_language, mode_switch, remaining = parse_intent_addressee_language(
        response_clean, context="general"
    )
    log_message(
        f"✅ {format_intent_result(intent, addressee, detected_language)}, "
        f"ModeSwitch: {mode_switch or '-'}, Remaining: {remaining[:40] or '-'}, "
        f"Raw: '{response_clean}'"
    )
    return (intent, addressee, detected_language, mode_switch, remaining, response_raw)


async def detect_cache_followup_intent(
    original_query: str,
    followup_query: str,
    automatik_model: str,
    llm_client,
    llm_options: Optional[Dict] = None,
    automatik_num_ctx: Optional[int] = None,
    detected_language: str = "de"
) -> str:
    """
    Detect intent of follow-up question to cached research

    Args:
        original_query: Original research question
        followup_query: User's follow-up question
        automatik_model: LLM for intent detection
        llm_client: LLMClient instance
        llm_options: Optional Dict with enable_thinking toggle
        detected_language: Language from Intent Detection ("de" or "en")

    Returns:
        str: "FAKTISCH", "KREATIV" or "GEMISCHT"
    """
    # Use detected_language from Intent Detection (passed from caller)
    detected_user_language = detected_language
    log_message(f"🌐 Cache Followup using language: {detected_user_language.upper()}")

    prompt = get_followup_intent_prompt(
        original_query=original_query,
        followup_query=followup_query,
        lang=detected_user_language
    )

    log_message(f"🎯 Cache-Followup Intent-Detection mit {automatik_model}: {followup_query[:60]}...")

    # Build options
    followup_intent_options: Dict = {
        'temperature': 0.2,
        'enable_thinking': False  # Default: Fast intent detection without reasoning
    }
    if automatik_num_ctx is not None:
        followup_intent_options['num_ctx'] = automatik_num_ctx

    # Automatik tasks: Thinking is ALWAYS off (independent of user toggle)
    log_message("🧠 Followup Intent enable_thinking: False (Automatik-Task)")

    response = await llm_client.chat(
        model=automatik_model,
        messages=[{'role': 'user', 'content': prompt}],
        options=followup_intent_options
    )
    intent_raw = response.text
    # Strip thinking blocks — models like GPT-OSS always reason regardless of enable_thinking
    intent_clean = strip_thinking_blocks(intent_raw).strip()

    intent = parse_intent_from_response(intent_clean, context="cache_followup")
    log_message(f"✅ Cache-Followup Intent ({automatik_model}): {intent}")
    return intent


async def detect_vl_relevance(
    user_query: str,
    image_context: str,
    automatik_model: str,
    llm_client,
    automatik_num_ctx: Optional[int] = None,
    recent_context: str = "",
) -> Optional[int]:
    """
    Detect if a follow-up question relates to a previously uploaded image.

    Uses the currently loaded model (Automatik) for a quick classification.

    Args:
        user_query: The user's current question
        image_context: Summary of images in conversation (from build_image_context_string)
        automatik_model: LLM for classification (already loaded)
        llm_client: LLMClient instance
        automatik_num_ctx: Context size for Automatik call
        recent_context: Recent conversation messages for topic-change detection

    Returns:
        1-based image index if relevant, None if not image-related
    """
    import re
    from .prompt_loader import get_vl_relevance_check_prompt

    prompt = get_vl_relevance_check_prompt(
        user_query=user_query,
        image_context=image_context,
        recent_context=recent_context,
        lang="en",
    )

    log_message(f"📷 VL relevance check: {user_query[:60]}...")

    options: Dict = {
        'temperature': 0.2,
        'enable_thinking': False,
    }
    if automatik_num_ctx is not None:
        options['num_ctx'] = automatik_num_ctx

    response = await llm_client.chat(
        model=automatik_model,
        messages=[{'role': 'user', 'content': prompt}],
        options=options,
    )
    response_raw = response.text
    response_clean = strip_thinking_blocks(response_raw).strip().upper()

    if response_clean == "NONE":
        log_message("📷 VL relevance: NONE (not image-related)")
        return None

    match = re.match(r'IMAGE:(\d+)', response_clean)
    if match:
        image_idx = int(match.group(1))
        log_message(f"📷 VL relevance: IMAGE:{image_idx}")
        return image_idx

    log_message(f"⚠️ VL relevance unparseable: '{response_clean}' �� NONE")
    return None


def get_temperature_for_intent(intent: str) -> float:
    """
    Returns the appropriate temperature for an intent.

    Temperature values are defined in config.py:
    - INTENT_TEMPERATURE_FAKTISCH (0.2): precise, deterministic answers
    - INTENT_TEMPERATURE_GEMISCHT (0.5): general conversation
    - INTENT_TEMPERATURE_KREATIV (1.1): stories, poems, creative writing

    Args:
        intent: "FAKTISCH", "KREATIV" or "GEMISCHT"

    Returns:
        float: Temperature based on config.py constants
    """
    from .config import (
        INTENT_TEMPERATURE_FAKTISCH,
        INTENT_TEMPERATURE_GEMISCHT,
        INTENT_TEMPERATURE_KREATIV
    )

    temp_map: dict[str, float] = {
        "FAKTISCH": INTENT_TEMPERATURE_FAKTISCH,
        "KREATIV": INTENT_TEMPERATURE_KREATIV,
        "GEMISCHT": INTENT_TEMPERATURE_GEMISCHT
    }
    return temp_map.get(intent, INTENT_TEMPERATURE_FAKTISCH)  # type: ignore[no-any-return]


def get_temperature_label(intent: str) -> str:
    """
    Returns the label for an intent (for UI display)

    Args:
        intent: "FAKTISCH", "KREATIV" or "GEMISCHT"

    Returns:
        str: "factual", "creative" or "mixed"
    """
    label_map = {
        "FAKTISCH": "factual",
        "KREATIV": "creative",
        "GEMISCHT": "mixed"
    }
    return label_map.get(intent, "factual")  # Fallback: factual


