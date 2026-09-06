"""
Message Builder - Centralized LLM History to Messages Conversion

Converts llm_history (List[Dict]) to Ollama Messages format.
All LLM calls use llm_history exclusively (not chat_history).
"""

from typing import Any, List, Dict, Optional
from datetime import datetime

from .prompt_loader import get_user_name


def build_messages_from_llm_history(
    llm_history: List[Dict[str, str]],
    current_user_text: str = "",
    perspective: str = "aifred",  # REQUIRED: "sokrates", "aifred", "salomo", "observer"
    detected_language: Optional[str] = None  # Language from Intent Detection ("de" or "en")
) -> List[Dict[str, str]]:
    """
    Build LLM messages directly from llm_history (v2.13.0+).

    This is the ONLY function for building LLM messages.
    llm_history is already in the correct format - no parsing or cleaning needed!

    Advantages:
    - No regex parsing needed (llm_history is pre-cleaned)
    - No marker detection (speaker labels already applied)
    - Fast and reliable
    - Summaries as user messages with [CONVERSATION CONTEXT] prefix
    - All labels preserved for agent identification

    Args:
        llm_history: List of {"role": "user/assistant/system", "content": "..."}
        current_user_text: Current user message to append
        perspective: REQUIRED - Agent perspective for role transformation
            - "aifred": AIfred speaking - his messages as 'assistant', others as 'user'
            - "sokrates": Sokrates speaking - his messages as 'assistant', others as 'user'
            - "salomo": Salomo speaking - his messages as 'assistant', others as 'user'
            - "observer": Neutral observer - all as 'user' with labels

    Returns:
        list: Messages in Ollama format [{"role": "...", "content": "..."}, ...]

    Note:
        Agent labels are handled per perspective:
        - Own labels are STRIPPED from assistant messages (prevents LLM from imitating them)
        - Other agents' labels are PRESERVED in user messages (allows referencing their statements)
    """
    if not llm_history:
        messages: list[dict[str, str]] = []
    else:
        # Multi-Agent perspective transformation
        messages = []
        perspective_lower = perspective.lower()
        user_label = get_user_name() or "USER"

        for msg in llm_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                # Summaries as user message with context prefix
                # (many models only allow one system message at the beginning)
                messages.append({"role": "user", "content": f"[CONVERSATION CONTEXT]:\n{content}"})
                continue

            # Detect speaker from content labels
            # ALL agent responses have labels: [AIFRED]:, [SOKRATES]:, [SALOMO]:
            is_sokrates = content.startswith("[SOKRATES]:")
            is_aifred = content.startswith("[AIFRED]:")
            is_salomo = content.startswith("[SALOMO]:")
            is_user = role == "user" and not (is_sokrates or is_aifred or is_salomo)

            if perspective_lower == "observer":
                # Observer sees everything as 'user' with labels
                if is_user:
                    messages.append({"role": "user", "content": f"[{user_label}]: {content}"})
                else:
                    # All agent messages as 'user' (keep their labels)
                    messages.append({"role": "user", "content": content})

            elif perspective_lower == "sokrates":
                if is_sokrates:
                    # Sokrates sees his own messages as 'assistant' (label STRIPPED!)
                    # Important: Remove [SOKRATES]: prefix to prevent LLM from imitating it
                    clean_content = content[12:].strip() if content.startswith("[SOKRATES]: ") else content
                    messages.append({"role": "assistant", "content": clean_content})
                elif is_user:
                    messages.append({"role": "user", "content": f"[{user_label}]: {content}"})
                else:
                    # Others (AIfred, Salomo) as 'user' (keep their labels)
                    messages.append({"role": "user", "content": content})

            elif perspective_lower == "aifred":
                if is_aifred:
                    # AIfred sees his own messages as 'assistant' (label STRIPPED!)
                    # Important: Remove [AIFRED]: prefix to prevent LLM from imitating it
                    clean_content = content[10:].strip() if content.startswith("[AIFRED]: ") else content
                    messages.append({"role": "assistant", "content": clean_content})
                elif is_user:
                    messages.append({"role": "user", "content": f"[{user_label}]: {content}"})
                else:
                    # Others (Sokrates, Salomo) as 'user' (keep their labels)
                    messages.append({"role": "user", "content": content})

            elif perspective_lower == "salomo":
                if is_salomo:
                    # Salomo sees his own messages as 'assistant' (label STRIPPED!)
                    # Important: Remove [SALOMO]: prefix to prevent LLM from imitating it
                    clean_content = content[10:].strip() if content.startswith("[SALOMO]: ") else content
                    messages.append({"role": "assistant", "content": clean_content})
                elif is_user:
                    messages.append({"role": "user", "content": f"[{user_label}]: {content}"})
                else:
                    # Others (Sokrates, AIfred) as 'user' (keep their labels)
                    messages.append({"role": "user", "content": content})

            else:
                # Unknown perspective - use as-is
                messages.append(msg.copy())

    # Add current user message if provided
    if current_user_text:
        # Add personality reminder as prefix (v2.15.15+)
        # Reinforces agent's speech style in long conversations
        from .prompt_loader import load_personality_reminder, get_language

        # Determine agent name: perspective if set, otherwise "aifred" (default)
        agent_name = perspective.lower() if perspective else "aifred"
        if agent_name == "observer":
            agent_name = "salomo"  # Observer is Salomo's perspective

        # Use detected_language if provided, otherwise fall back to UI language
        reminder_lang = detected_language if detected_language else get_language()
        reminder = load_personality_reminder(agent_name, lang=reminder_lang)
        if reminder:
            current_user_text = f"{reminder}\n\n{current_user_text}"

        # Language enforcement suffix (Recency Bias: last tokens before generation
        # have strongest influence → overrides language drift from English content)
        lang_suffixes = {
            "de": "\n\n[AUSGABESPRACHE: DEUTSCH]",
            "en": "\n\n[OUTPUT LANGUAGE: ENGLISH]",
        }
        lang_suffix = lang_suffixes.get(reminder_lang, "")
        if lang_suffix:
            current_user_text = f"{current_user_text}{lang_suffix}"

        messages.append({"role": "user", "content": current_user_text})

    return messages


def inject_rag_context(
    messages: List[Dict[str, str]],
    rag_context: str,
    position: int = -1
) -> None:
    """
    Inject RAG context as user message into messages list.

    Modifies the list in-place by inserting a user message with
    previously researched context.

    Args:
        messages: List of message dicts to modify
        rag_context: The RAG context string to inject
        position: Where to insert (-1 = before last message, i.e., before user's question)

    Example:
        >>> messages = [{"role": "system", "content": "..."}, {"role": "user", "content": "Frage"}]
        >>> inject_rag_context(messages, "Recherche-Ergebnisse hier")
        >>> len(messages)
        3  # Context message was inserted
    """
    rag_message = {
        'role': 'user',
        'content': f"""[ADDITIONAL CONTEXT FROM PREVIOUS RESEARCH]:

{rag_context}

Use this information IN ADDITION to your training knowledge when relevant to the current question."""
    }
    messages.insert(position, rag_message)


def inject_before_question(
    messages: List[Dict[str, str]],
    content: str,
    position: int = -1
) -> None:
    """Flüchtigen Block als eigene Nachricht vor die Nutzerfrage haengen.

    Fuer alles, was sich von Turn zu Turn aendert: Agenten-Erinnerungen,
    erzwungene Web-Recherche. NICHT an den System-Prompt anhaengen — bei
    Erinnerungen etwa ist der Abruf FRAGENABHAENGIG
    (``prepare_agent_toolkit(agent, user_query, ...)``), der Block aendert
    sich also von Turn zu Turn oder faellt ganz weg. Am System-Prompt
    haengend verschiebt er die vordersten Token jeder Anfrage und entwertet
    den Praefix-Cache fuer den GESAMTEN Verlauf dahinter — dieselbe Falle
    wie der Zeitstempel vor d3c4e9c6. Gemessen am 2026-09-01 mit
    DeepSeek-V4-Flash: 32.842 neu gerechnete Token fuer einen
    13.325-Token-Prompt, TTFT 102 s.

    Auch der RAG-Wrapper ist hier falsch — der behauptet
    "Recherche-Ergebnisse aus dem Internet", was fuer Erinnerungen nicht
    stimmt. Deshalb ein eigener, schlichter Rahmen.
    """
    messages.insert(position, {"role": "user", "content": content})


def inject_vision_json_context(
    messages: List[Dict[str, str]],
    vision_json: dict,
    position: int = -1
) -> None:
    """
    Inject Vision JSON context as user message into messages list.

    Modifies the list in-place by inserting a user message with
    extracted image data.

    Args:
        messages: List of message dicts to modify
        vision_json: The extracted JSON from Vision-LLM
        position: Where to insert (-1 = before last message)

    Example:
        >>> messages = [{"role": "user", "content": "Was steht im Bild?"}]
        >>> inject_vision_json_context(messages, {"text": "Hello World"})
        >>> len(messages)
        2  # Vision context message was inserted
    """
    import json

    vision_message = {
        'role': 'user',
        'content': f"""[PREVIOUS IMAGE EXTRACTION (STRUCTURED DATA)]:

```json
{json.dumps(vision_json, ensure_ascii=False, indent=2)}
```

This data was extracted from an image. Use it for your answer."""
    }
    messages.insert(position, vision_message)


def build_history_entry(
    agent: str,
    content: str,
    mode: str = "own_knowledge",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a chat_history entry for an agent response.

    Single Source of Truth for the history dict format.
    Used by llm_engine.py and research pipeline (cache_handler, context_builder).
    The browser UI path uses add_agent_panel() which adds extra UI concerns
    (markers, TTS, session save) on top of the same structure.

    Args:
        agent: Agent identifier ("aifred", "sokrates", "pater", etc.)
        content: Formatted response content (with thinking HTML, metadata display)
        mode: Response mode ("own_knowledge", "web_research", "session_cache", etc.)
        metadata: Optional metadata dict (TTFT, inference_time, tokens_per_sec, etc.)
    """
    from .agent_config import get_agent_config, get_agent_emoji

    cfg = get_agent_config(agent)
    return {
        "role": "assistant",
        "content": content,
        "agent": agent,
        "agent_display_name": cfg.display_name if cfg else agent.capitalize(),
        "agent_emoji": get_agent_emoji(agent),
        "mode": mode,
        "round_num": 0,
        "metadata": metadata or {},
        "timestamp": datetime.now().isoformat(),
    }


def user_turn_stamp() -> str:
    """Timestamp of the current user turn: ``[YYYY-MM-DD Wkd HH:MM]``.

    Single Source of Truth for the format that prompts/*/shared/disciplines.txt
    promises the model ("every user message carries its timestamp"). Taken
    ONCE per turn and handed to every :func:`stamp_user_turn` call of that
    turn, so the live message and the history entry never disagree — not even
    across a minute boundary.
    """
    now = datetime.now()
    # Feste englische Kuerzel statt %a: locale-unabhaengig und damit
    # reproduzierbar, egal welche LANG-Variable der Dienst erbt.
    weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[now.weekday()]
    return f"[{now.strftime('%Y-%m-%d')} {weekday} {now.strftime('%H:%M')}]"


def stamp_user_turn(content: str, stamp: str) -> str:
    """Prefix the LLM-facing user text with the turn's timestamp.

    The stamped text is what the model sees live AND what llm_history keeps:
    the model knows date and time from the very first turn (no stamp = it
    invents one, the prompt promised it), and the later history entry is
    byte-identical to what was sent, which keeps the prefix cache intact.

    Why here and not in the system prompt: the system prompt is rebuilt and
    prepended on EVERY request, so a clock in it changes the very first
    tokens each time and invalidates the cached KV state of the entire
    conversation behind it (~28 s of needless prefill per follow-up turn on
    a 30k history). Stamped per turn, the prefix stays byte-identical, and
    the model additionally learns WHEN each earlier turn happened — which a
    single session-start timestamp could never express.
    """
    return f"{stamp} {content}"


def build_llm_history_entry(agent: str, response_clean: str) -> Dict[str, str]:
    """Build an llm_history entry with agent speaker tag.

    Single Source of Truth for the [Agent]: prefix format. Uses the
    agent's display_name (e.g. "Codine") rather than its raw ID
    uppercased ("CODI") — the model parrots back whatever it sees in
    its history, so showing "Codine" gives it the correct self-name
    instead of teaching it to sign as the lookup key. Falls back to
    a capitalized ID when no agent config is available.
    """
    from .agent_config import get_agent_config
    cfg = get_agent_config(agent)
    label = cfg.display_name if cfg else agent.capitalize()
    return {"role": "assistant", "content": f"[{label}]: {response_clean}"}
