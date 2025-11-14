"""
Message Builder - Centralized History-to-Messages Conversion

Konvertiert Gradio Chat History zu Ollama Messages Format und
entfernt Timing-Informationen aus den Anzeigen.

Vorher: 6+ duplizierte Code-Stellen mit jeweils 10-15 Zeilen
Nachher: 1 zentrale Funktion mit robustem Pattern Matching
"""

import re
from typing import List, Dict, Optional, Tuple
from datetime import datetime


def build_messages_from_history(
    history: List[Tuple[str, str]],
    current_user_text: str,
    max_turns: Optional[int] = None,
    include_summaries: bool = True
) -> List[Dict[str, str]]:
    """
    Konvertiert Gradio-History zu Ollama-Messages Format

    Entfernt Timing-Info, HTML-Tags und Metadata aus User- und AI-Nachrichten:
    - Timing-Patterns: "(STT: 2.5s)", "(Inferenz: 1.3s)", "(Agent: 45.2s)"
    - HTML-Metadata: <span style="...">( Inferenz: ... )</span>
    - Thinking-Collapsibles: <details>...</details>

    Behandelt History-Summaries als System-Messages:
    - Summary-Format: ("", "[📊 Komprimiert: X Messages]\\n{summary}")
    - Wird zu: {'role': 'system', 'content': summary}

    Args:
        history: Gradio Chat History [[user_msg, ai_msg], ...]
        current_user_text: Aktuelle User-Nachricht
        max_turns: Optional - Nur letzte N Turns verwenden (None = alle)
        include_summaries: Summaries als System-Messages einbinden (default: True)

    Returns:
        list: Ollama Messages Format [{'role': 'user', 'content': '...'}, ...]

    Examples:
        >>> history = [
        ...     ["", "[📊 Komprimiert: 6 Messages]\\nUser fragte nach Wetter..."],
        ...     ["Hallo (STT: 2.5s)", "Hi! (Inferenz: 1.3s)"],
        ...     ["Was ist 2+2? (Agent: 45.2s)", "4 (Inferenz: 0.8s)"]
        ... ]
        >>> msgs = build_messages_from_history(history, "Danke!")
        >>> msgs[0]
        {'role': 'system', 'content': '[📊 Komprimiert: 6 Messages]\\nUser fragte nach Wetter...'}
    """
    messages = []

    # Liste aller bekannten Timing-Patterns (robust gegen neue Patterns)
    timing_patterns = [
        " (STT:",          # Speech-to-Text Zeit
        " (Agent:",        # Agent Research Zeit
        " (Inferenz:",     # LLM Inference Zeit
        " (TTS:",          # Text-to-Speech Zeit
        " (Entscheidung:", # Automatik Decision Zeit
    ]

    # Begrenze History falls gewünscht (z.B. nur letzte 3 Turns)
    history_to_process = history[-max_turns:] if max_turns else history

    # Verarbeite History
    for user_turn, ai_turn in history_to_process:
        # Erkenne Summary-Einträge: ("", "[📊 Komprimiert: ...")
        is_summary = (user_turn == "" and
                     ai_turn.startswith("[📊 Komprimiert:") and
                     include_summaries)

        if is_summary:
            # Summary als System-Message hinzufügen
            messages.append({'role': 'system', 'content': ai_turn})
            continue

        # Normale User/AI Messages bereinigen
        # Bereinige User-Nachricht
        clean_user = user_turn
        for pattern in timing_patterns:
            if pattern in clean_user:
                # Schneide alles ab dem ersten Timing-Pattern ab
                clean_user = clean_user.split(pattern)[0]

        # Bereinige AI-Nachricht (entferne HTML-Tags UND Text-Metadata)
        clean_ai = ai_turn

        # 1. Entferne Thinking-Collapsibles (<details>...</details>)
        clean_ai = re.sub(r'<details[^>]*>.*?</details>', '', clean_ai, flags=re.DOTALL)

        # 2. Entferne Metadata-Spans (<span style="...">( Inferenz: ... )</span>)
        clean_ai = re.sub(r'<span[^>]*>\s*\([^)]+\)\s*</span>', '', clean_ai, flags=re.DOTALL)

        # 3. Fallback: Entferne verbleibende Text-Metadata (falls HTML-Tags fehlen)
        for pattern in timing_patterns:
            if pattern in clean_ai:
                # Schneide alles ab dem ersten Timing-Pattern ab
                clean_ai = clean_ai.split(pattern)[0]

        # 4. Cleanup: Entferne mehrfache Leerzeilen und Whitespace
        clean_ai = re.sub(r'\n\n+', '\n\n', clean_ai.strip())

        # Füge bereinigte Messages hinzu
        messages.extend([
            {'role': 'user', 'content': clean_user},
            {'role': 'assistant', 'content': clean_ai}
        ])

    # Füge aktuelle User-Nachricht hinzu
    messages.append({'role': 'user', 'content': current_user_text})

    return messages


def build_system_prompt(language: str = "de") -> Dict[str, str]:
    """
    Build system prompt with current date and time

    This ensures the LLM knows the current date/time for temporal queries
    like "aktuelle Ereignisse", "neueste News", etc.

    Args:
        language: "de" or "en" (default: "de")

    Returns:
        dict: {'role': 'system', 'content': '...'}

    Examples:
        >>> prompt = build_system_prompt("de")
        >>> "Aktuelles Datum" in prompt['content']
        True
    """
    now = datetime.now()

    if language == "de":
        date_str = now.strftime("%d.%m.%Y")  # 15.11.2025
        time_str = now.strftime("%H:%M")     # 14:30
        weekday = now.strftime("%A")         # Monday

        # Translate weekday to German
        weekday_map = {
            "Monday": "Montag", "Tuesday": "Dienstag", "Wednesday": "Mittwoch",
            "Thursday": "Donnerstag", "Friday": "Freitag",
            "Saturday": "Samstag", "Sunday": "Sonntag"
        }
        weekday_de = weekday_map.get(weekday, weekday)

        content = f"""Du bist ein hilfreicher AI-Assistent.

WICHTIGE ZEITANGABEN:
- Aktuelles Datum: {weekday_de}, {date_str}
- Aktuelle Uhrzeit: {time_str} Uhr
- Jahr: {now.year}

Nutze diese Informationen für zeitbezogene Fragen (z.B. "Was ist heute?", "Welches Jahr haben wir?", "Aktuelle Ereignisse")."""

    else:  # English
        date_str = now.strftime("%Y-%m-%d")  # 2025-11-15
        time_str = now.strftime("%H:%M")     # 14:30
        weekday = now.strftime("%A")         # Monday

        content = f"""You are a helpful AI assistant.

IMPORTANT TIME INFORMATION:
- Current date: {weekday}, {date_str}
- Current time: {time_str}
- Year: {now.year}

Use this information for time-related queries (e.g., "What's today's date?", "What year is it?", "Current events")."""

    return {'role': 'system', 'content': content}
