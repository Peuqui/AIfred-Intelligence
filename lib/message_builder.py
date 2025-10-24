"""
Message Builder - Centralized History-to-Messages Conversion

Konvertiert Gradio Chat History zu Ollama Messages Format und
entfernt Timing-Informationen aus den Anzeigen.

Vorher: 6+ duplizierte Code-Stellen mit jeweils 10-15 Zeilen
Nachher: 1 zentrale Funktion mit robustem Pattern Matching
"""

from typing import List, Dict, Optional, Tuple


def build_messages_from_history(
    history: List[Tuple[str, str]],
    current_user_text: str,
    max_turns: Optional[int] = None
) -> List[Dict[str, str]]:
    """
    Konvertiert Gradio-History zu Ollama-Messages Format

    Entfernt Timing-Info wie "(STT: 2.5s)", "(Inferenz: 1.3s)", "(Agent: 45.2s)"
    aus User- und AI-Nachrichten.

    Args:
        history: Gradio Chat History [[user_msg, ai_msg], ...]
        current_user_text: Aktuelle User-Nachricht
        max_turns: Optional - Nur letzte N Turns verwenden (None = alle)

    Returns:
        list: Ollama Messages Format [{'role': 'user', 'content': '...'}, ...]

    Examples:
        >>> history = [
        ...     ["Hallo (STT: 2.5s)", "Hi! (Inferenz: 1.3s)"],
        ...     ["Was ist 2+2? (Agent: 45.2s)", "4 (Inferenz: 0.8s)"]
        ... ]
        >>> msgs = build_messages_from_history(history, "Danke!")
        >>> msgs
        [
            {'role': 'user', 'content': 'Hallo'},
            {'role': 'assistant', 'content': 'Hi!'},
            {'role': 'user', 'content': 'Was ist 2+2?'},
            {'role': 'assistant', 'content': '4'},
            {'role': 'user', 'content': 'Danke!'}
        ]
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
        # Bereinige User-Nachricht
        clean_user = user_turn
        for pattern in timing_patterns:
            if pattern in clean_user:
                # Schneide alles ab dem ersten Timing-Pattern ab
                clean_user = clean_user.split(pattern)[0]

        # Bereinige AI-Nachricht
        clean_ai = ai_turn
        for pattern in timing_patterns:
            if pattern in clean_ai:
                # Schneide alles ab dem ersten Timing-Pattern ab
                clean_ai = clean_ai.split(pattern)[0]

        # Füge bereinigte Messages hinzu
        messages.extend([
            {'role': 'user', 'content': clean_user},
            {'role': 'assistant', 'content': clean_ai}
        ])

    # Füge aktuelle User-Nachricht hinzu
    messages.append({'role': 'user', 'content': current_user_text})

    return messages
