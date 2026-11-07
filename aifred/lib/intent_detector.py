"""
Intent Detector - what the user wants from this message, in one Automatik call:

- the language of the message (prompts and the spoken reply follow it)
- the addressee: the agent the user speaks to by name, or None
- a mode switch the user asks for ("Start tribunal", "Bleib bei Pater Tuck")
- whether the message is only that command

The temperature is not its business: every agent answers with its own (agent_tuning).
"""

import re
from typing import Any

from .config import MULTI_AGENT_MODES
from .context_manager import strip_thinking_blocks
from .logging_utils import log_message
from .prompt_loader import get_intent_detection_prompt


def _parse_mode_switch(mode_field: str) -> dict[str, Any]:
    """
    Parse the MODE_SWITCH field of the intent detection output.

    Format: comma-separated key=value pairs, e.g. "multi=tribunal" or
    "agent=sokrates,multi=standard".

    ``symposion_agents`` takes a comma-separated agent list — its values
    therefore look like bare tokens to the pair-splitter
    ("multi=symposion,symposion_agents=codi,hal,rabbi"). Bare tokens
    directly following a ``symposion_agents=`` key are treated as
    continuation of that list; anywhere else they are ignored as before.

    Defensive parsing: unknown keys/values are ignored, never raises.

    Args:
        mode_field: Raw mode switch string from LLM

    Returns:
        Dict with validated config updates (keys: multi_agent_mode,
        active_agent, symposion_agents). Empty dict if nothing valid.
    """
    from .agent_config import resolve_agent_id

    updates: dict[str, Any] = {}
    if not mode_field or not mode_field.strip():
        return updates

    # Set while consuming a symposion_agents list — bare tokens append to it.
    collecting_agents: list[str] | None = None

    for pair in mode_field.split(","):
        if "=" not in pair:
            token = pair.strip().lower()
            if collecting_agents is not None and token:
                resolved = resolve_agent_id(token)
                if resolved is not None and resolved not in collecting_agents:
                    collecting_agents.append(resolved)
            continue
        collecting_agents = None
        key, _, value = pair.partition("=")
        key = key.strip().lower()
        value = value.strip().lower()
        if not key or not value:
            continue

        if key == "multi":
            if value in MULTI_AGENT_MODES:
                updates["multi_agent_mode"] = value
        elif key == "agent":
            # Resolve through ID, display_name and aliases (single source of
            # truth — same logic as the addressee parser). Absorbs LLM hiccups
            # like ``agent=HAL 9000`` and STT variants like ``agent=alfred``.
            resolved = resolve_agent_id(value)
            if resolved is not None:
                updates["active_agent"] = resolved
        elif key == "symposion_agents":
            # Ad-hoc participant list for symposion mode ("Starte Symposion
            # mit Codi, HAL und Rabbi"). Same resolver as agent= — IDs,
            # display names and STT aliases all work; invalid names are
            # dropped silently (defensive parsing, consistent with agent=).
            collecting_agents = []
            resolved = resolve_agent_id(value)
            if resolved is not None:
                collecting_agents.append(resolved)
            updates["symposion_agents"] = collecting_agents

    # All named agents failed to resolve → no usable list, drop the key
    # (an empty list would start a symposion with zero participants).
    if not updates.get("symposion_agents", True):
        del updates["symposion_agents"]
    return updates


def format_mode_switch_summary(updates: dict[str, Any], lang: str = "de") -> str:
    """Format a human-readable summary of a mode switch for confirmation messages."""
    if not updates:
        return ""
    parts: list[str] = []
    if lang == "de":
        multi_labels = {
            "standard": "Standard",
            "sokrates": "Sokrates",
            "tribunal": "Tribunal",
            "symposion": "Symposion",
            "critical_review": "Kritische Prüfung",
            "auto_consensus": "Auto-Konsens",
        }
    else:
        multi_labels = {
            "standard": "standard",
            "sokrates": "Sokrates",
            "tribunal": "Tribunal",
            "symposion": "Symposion",
            "critical_review": "critical review",
            "auto_consensus": "auto consensus",
        }
    if "multi_agent_mode" in updates:
        parts.append(f"Mode: {multi_labels.get(updates['multi_agent_mode'], updates['multi_agent_mode'])}")
    if "active_agent" in updates:
        from .agent_config import get_agent_config
        _cfg = get_agent_config(updates["active_agent"])
        _name = _cfg.display_name if _cfg else updates["active_agent"].capitalize()
        parts.append(f"Agent: {_name}")
    if "symposion_agents" in updates:
        from .agent_config import get_agent_config
        names = []
        for agent_id in updates["symposion_agents"]:
            cfg = get_agent_config(agent_id)
            names.append(cfg.display_name if cfg else agent_id.capitalize())
        label = "Teilnehmer" if lang == "de" else "Participants"
        parts.append(f"{label}: {', '.join(names)}")
    return " · ".join(parts)


def format_intent_result(addressee: str | None, language: str) -> str:
    """Format the detection result as debug string (single source of truth).

    Used by browser (add_debug), message_processor (debug), and log output.
    Resolves agent ID to display name (e.g. "pater" → "Pater Tuck").
    """
    if addressee:
        from .agent_config import get_agent_config
        cfg = get_agent_config(addressee)
        addr_display = cfg.display_name if cfg else addressee.capitalize()
    else:
        addr_display = "–"
    return f"Addressee: {addr_display}, Lang: {language.upper()}"


def _answer_field(response: str, key: str, value_pattern: str) -> str | None:
    """The value of one named field of the detector's JSON answer, read on its own: a garbled
    neighbour (a broken key, a doubled field) does not cost the others. None when absent."""
    found = re.search(rf'"{key}"\s*:\s*{value_pattern}', response)
    return found.group(1) if found else None


def parse_intent_response(response_raw: str) -> tuple[str | None, str, dict[str, Any], bool]:
    """
    Extract addressee, language, mode switch and pure-command flag.

    Expected: one JSON object ``{"language", "addressee", "mode_switch", "pure_command"}``.
    The fields are read by name one by one, not as a whole: model output is text, and one
    garbled field must not lose the rest. Examples:
        {"language": "DE", "addressee": "", "mode_switch": "", "pure_command": false}
        {"language": "EN", "addressee": "", "mode_switch": "multi=tribunal", "pure_command": true}
        {"language": "DE", "addressee": "sokrates", "mode_switch": "", "pure_command": false}

    The user message is NEVER rewritten by the detector — ``pure_command`` only classifies
    whether the message is purely a mode/config command.

    Returns:
        (addressee, language, mode_switch_updates, is_pure_command)
    """
    # Parse language (the UI language if the LLM gave none we know)
    language_part = (_answer_field(response_raw, "language", r'"([^"]*)"') or "").strip().upper()
    if language_part in ("DE", "DEUTSCH", "GERMAN"):
        language = "de"
    elif language_part in ("EN", "ENGLISH"):
        language = "en"
    else:
        from .prompt_loader import get_language
        language = get_language()
        log_message(f"⚠️ Language unknown: '{response_raw}' → UI language {language.upper()}")

    # Parse addressee — resolve_agent_id handles ID, display_name and aliases
    # in one place. Phonetic STT variations live in agents.json (aliases field).
    addressee: str | None = None
    addressee_part = (_answer_field(response_raw, "addressee", r'"([^"]*)"') or "").strip()
    if addressee_part:
        from .agent_config import resolve_agent_id
        addressee = resolve_agent_id(addressee_part)

    # Parse mode switch field (defensive — unknown values are ignored)
    mode_switch_updates = _parse_mode_switch(_answer_field(response_raw, "mode_switch", r'"([^"]*)"') or "")

    # Pure-command flag: true only when explicitly set AND a mode switch exists.
    # If no mode switch, the flag is meaningless → force False.
    is_pure_command = _answer_field(response_raw, "pure_command", r"(true|false)\b") == "true" and bool(mode_switch_updates)

    return (addressee, language, mode_switch_updates, is_pure_command)


async def detect_query_intent_and_addressee(
    user_query: str,
    automatik_model: str,
    llm_client,
    automatik_num_ctx: int | None = None
) -> tuple[str | None, str, dict[str, Any], bool, str, float]:
    """
    Detect addressee, language, mode switch and pure-command flag in one LLM call.

    Args:
        user_query: User question
        automatik_model: LLM for intent detection
        llm_client: LLMClient instance
        automatik_num_ctx: Context size for Automatik call.
            None = don't set (model keeps current context, avoids reload).
            int = explicit value (e.g. AUTOMATIK_LLM_NUM_CTX for different models).

    Returns:
        Tuple[Optional[str], str, Dict[str, Any], bool, str, float]:
            (addressee, detected_language, mode_switch_updates,
             is_pure_command, raw_response, load_time)

            - addressee: an agent id or None
            - detected_language: "de" or "en" (LLM-detected from user query)
            - mode_switch_updates: dict with config changes ({} if none)
            - is_pure_command: True iff message is ONLY a mode-switch command
            - raw_response: Raw LLM output for debugging
            - load_time: Seconds the backend spent loading the model for
              this call (0.0 = it was running). As the turn's first LLM call
              it usually carries the cold start; callers add it to the
              answer's metadata.
    """
    # Use English prompt for intent detection (universal, handles all languages)
    prompt = get_intent_detection_prompt(user_query=user_query, lang="en")

    log_message(f"🎯 Addressee+Language detection for query: {user_query[:60]}...")

    intent_options: dict = {
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

    addressee, detected_language, mode_switch, is_pure_command = parse_intent_response(response_clean)
    log_message(
        f"✅ {format_intent_result(addressee, detected_language)}, "
        f"ModeSwitch: {mode_switch or '-'}, PureCmd: {is_pure_command}, "
        f"Raw: '{response_clean}'"
    )
    return (
        addressee, detected_language, mode_switch, is_pure_command,
        response_raw, response.load_time,
    )
