"""
Message Builder - Centralized LLM History to Messages Conversion

Converts llm_history (List[Dict]) to Ollama Messages format.
All LLM calls use llm_history exclusively (not chat_history).
"""

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .prompt_loader import get_user_name

_SPEAKER_RE = re.compile(r"\[([^\]\n]{1,40})\]: ?")


def history_speaker(content: str) -> tuple[str | None, str]:
    """The agent an llm_history entry comes from (its id) and the text after its label; ``None``
    when the label names no agent (the user, a channel's message, an automatic notice).

    Every path writes the agent's id (``formatting.assistant_history_entry``, ``[AIFRED]:``); the
    label read in lower case is the id, which also reads the display-name labels channels wrote
    until 07.11.2026 (``[AIfred]:``)."""
    from .agent_config import load_agents

    match = _SPEAKER_RE.match(content)
    if match is None:
        return None, content
    agent = match.group(1).lower()
    if agent not in load_agents():
        return None, content
    return agent, content[match.end():]


def build_messages_from_llm_history(
    llm_history: list[dict[str, str]],
    current_user_text: str = "",
    perspective: str = "aifred",
    detected_language: str | None = None,
    *,
    observer: bool = False,
) -> list[dict[str, str]]:
    """The model's messages from llm_history, as the agent ``perspective`` sees the conversation.

    Its own replies are ``assistant`` without their label (it would imitate it); everything else is
    ``user``: the user's messages under the user's name, other agents' replies under their current
    display name, channels' messages and notices as they are. ``observer`` (the judge of a debate):
    every reply, its own too, is a labelled ``user`` message. A summary of compressed history is a
    user message with a context prefix (many models allow one system message only, at the start).

    ``current_user_text`` is the question that ends the messages, with the agent's style reminder in
    front and the output language behind it.
    """
    from .agent_config import load_agents

    agents = load_agents()
    user_label = get_user_name() or "USER"
    messages: list[dict[str, str]] = []
    for msg in llm_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            messages.append({"role": "user", "content": f"[CONVERSATION CONTEXT]:\n{content}"})
            continue
        speaker, text = history_speaker(content)
        if speaker == perspective and not observer:
            messages.append({"role": "assistant", "content": text.strip()})
        elif speaker is not None:
            messages.append({"role": "user", "content": f"[{agents[speaker].display_name}]: {text}"})
        elif role == "user":
            messages.append({"role": "user", "content": f"[{user_label}]: {content}"})
        else:
            # An assistant entry without an agent label (a channel's message, an automatic notice):
            # from someone else, never the agent's own words or the user's.
            messages.append({"role": "user", "content": content})

    # Add current user message if provided
    if current_user_text:
        # Add personality reminder as prefix (v2.15.15+)
        # Reinforces agent's speech style in long conversations
        from .prompt_loader import get_language, load_personality_reminder

        # Use detected_language if provided, otherwise fall back to UI language
        reminder_lang = detected_language if detected_language else get_language()
        reminder = load_personality_reminder(perspective, lang=reminder_lang)
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


def inject_before_question(
    messages: list[dict[str, str]],
    content: str,
    position: int = -1
) -> None:
    """Flüchtigen Block als eigene Nachricht vor die Nutzerfrage haengen.

    Fuer alles, was sich von Turn zu Turn aendert, etwa Agenten-Erinnerungen.
    NICHT an den System-Prompt anhaengen — bei
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


def build_history_entry(
    agent: str,
    content: str,
    mode: str = "own_knowledge",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a chat_history entry for an agent response.

    Single Source of Truth for the history dict format.
    Used by llm_engine.py.
    The browser UI path uses _add_agent_panel() which adds extra UI concerns
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


def with_history_notes(response_clean: str, tool_notes: Sequence[str] = ()) -> str:
    """The llm_history text of an answer: the answer, one note per HTML
    preview of its code blocks, then what the turn's tools left for the next
    turn (PipelineResult.history_notes). The preview file exists only in the
    bubble — without its note the model did not know in the next turn that a
    viewable file is there (14.09.: it invented a link). Without the tool
    notes it did not know what it had researched (18.09.: it accepted "you
    did not research" after searching Kondo for Kuanda)."""
    from .formatting import html_preview_urls
    from .prompt_loader import load_prompt
    preview_notes = [load_prompt("shared/html_preview_marker", url=url) for url in html_preview_urls(response_clean)]
    return "\n\n".join(part for part in (response_clean, *preview_notes, *tool_notes) if part)


def image_marker(image_urls: Sequence[str]) -> str:
    """llm_history anchor for images of a turn. The image itself never enters
    the history — without its /_upload/ URL the model forgets on a follow-up
    that it existed; with it, vision_analyze can re-examine it."""
    from .prompt_loader import load_prompt
    return load_prompt("shared/image_marker", urls=", ".join(image_urls))


def build_autonomous_history_entry(
    channel: str, text: str, image_urls: Sequence[str] = (), speaker: str | None = None,
) -> dict[str, str]:
    """llm_history entry for an event that came in on its own (e.g. a camera alert).
    Role ``assistant`` without an agent label: every perspective sees it as a
    labelled message from someone else, never as its own words or the user's.
    ``speaker`` set: another agent speaks (named message); unset: the channel alone
    is the sender (scheduler, timer, alert — an automatic notification)."""
    from .prompt_loader import load_prompt
    body = "\n\n".join(part for part in (text, image_marker(image_urls) if image_urls else "") if part)
    if speaker:
        return {"role": "assistant", "content": load_prompt("shared/agent_message", channel=channel, speaker=speaker, text=body)}
    return {"role": "assistant", "content": load_prompt("shared/autonomous_event", channel=channel, text=body)}
