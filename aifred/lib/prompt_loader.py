"""
Prompt Loader Module with i18n Support

Loads prompts from language-specific directories (de/ or en/).
Language is detected by LLM-based Intent Detection (see intent_detector.py).
No fallbacks - prompts must exist in both languages.

Personality System (v2.15.3+):
- Each agent has a personality.txt file with their speech style
- Personality can be toggled on/off per agent via settings
- When enabled, personality is appended after identity + task prompts
"""

from collections.abc import Collection
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .settings import persisted_settings

# Base directory for prompts (relative to project root)
PROMPTS_DIR = Path(__file__).parent.parent.parent / 'prompts'

# Persona values (language, user name/gender, personality/reasoning toggles)
# come from the persisted settings — one truth for browser, scheduler and hub,
# independent of whether a browser session has loaded since the last restart.

def get_user_name() -> str:
    """User name from the persisted settings."""
    return str(persisted_settings()["user_name"]).strip()


def get_user_gender() -> str:
    """User gender for salutation ("male"/"female") from the persisted settings."""
    return str(persisted_settings()["user_gender"])


def _agent_toggle(agent: str, feature: str, default: bool) -> bool:
    """Per-agent toggle: the saved tuning, or the agent's agents.json default
    while the agent has no saved tuning yet (new custom agent)."""
    saved = persisted_settings().get("agent_tuning", {}).get(agent, {})
    if feature in saved:
        return bool(saved[feature])
    from .agent_config import get_agent_config
    config = get_agent_config(agent)
    return bool(config.toggles.get(feature, default)) if config else default


def get_personality_enabled(agent: str) -> bool:
    """Personality toggle of an agent (persisted settings)."""
    return _agent_toggle(agent, "personality", default=True)


def get_reasoning_enabled(agent: str) -> bool:
    """Reasoning toggle of an agent (persisted settings)."""
    return _agent_toggle(agent, "reasoning", default=False)


def _resolve_prompt_file(agent: str, prompt_key: str, lang: str | None = None) -> Path | None:
    """
    Resolve prompt file path for an agent via agent_config.

    Looks up the agent's prompts dict for the given key (e.g. "identity",
    "personality", "reminder"). This allows cross-references like Vision
    using "aifred/personality.txt" for AIfred's personality.

    Args:
        agent: Agent identifier (e.g. "aifred", "sokrates", or any custom agent)
        prompt_key: Key into the agent's prompts dict
        lang: Language code, defaults to current language

    Returns:
        Resolved Path if the file exists, None otherwise
    """
    if lang is None:
        lang = prompt_language()

    from .agent_config import get_agent_config
    config = get_agent_config(agent)

    if config is None:
        return None

    rel_path = config.prompts.get(prompt_key)
    if rel_path is None:
        return None

    full_path = PROMPTS_DIR / lang / rel_path
    if full_path.exists():
        return full_path
    return None


def load_reasoning(agent: str, lang: str | None = None) -> str:
    """
    Load reasoning prompt for an agent (if enabled).

    Reasoning is a shared prompt (utility/reasoning.txt), not agent-specific.

    Args:
        agent: Agent identifier
        lang: Language code ("de" or "en"), defaults to current language

    Returns:
        Reasoning prompt text, or empty string if not enabled
    """
    if not get_reasoning_enabled(agent):
        return ""

    if lang is None:
        lang = prompt_language()

    reasoning_file = PROMPTS_DIR / lang / "utility" / "reasoning.txt"

    if not reasoning_file.exists():
        return ""

    return reasoning_file.read_text(encoding="utf-8").strip()


def load_identity(agent: str, lang: str | None = None) -> str:
    """
    Load identity prompt for an agent (always loaded).

    Identity defines WHO the agent is - resolved via agent_config,
    so agents can use custom paths (e.g. a new agent's "<id>/identity.txt").

    Args:
        agent: Agent identifier
        lang: Language code, defaults to current language

    Returns:
        Identity prompt text, or empty string if not found
    """
    identity_file = _resolve_prompt_file(agent, "identity", lang)
    if identity_file is None:
        return ""

    return identity_file.read_text(encoding="utf-8").strip()


def load_personality(agent: str, lang: str | None = None) -> str:
    """
    Load personality prompt for an agent.

    Personality defines HOW the agent speaks (style) - resolved via
    agent_config, so agents can reference other agents' personalities
    (e.g. an agent using another agent's "aifred/personality.txt").

    Args:
        agent: Agent identifier
        lang: Language code, defaults to current language

    Returns:
        Personality prompt text, or empty string if not found/disabled
    """
    if not get_personality_enabled(agent):
        return ""

    personality_file = _resolve_prompt_file(agent, "personality", lang)
    if personality_file is None:
        return ""

    return personality_file.read_text(encoding="utf-8").strip()


def load_personality_reminder(agent: str, lang: str | None = None) -> str:
    """
    Load short personality reminder for user-message prefix.

    Used to remind the LLM of the agent's speech style in long conversations.
    Resolved via agent_config for cross-agent references.

    Args:
        agent: Agent identifier
        lang: Language code, defaults to current language

    Returns:
        Short reminder text (e.g., "[STIL: Britischer Butler]"), or empty string
    """
    if not get_personality_enabled(agent):
        return ""

    reminder_file = _resolve_prompt_file(agent, "reminder", lang)
    if reminder_file is None:
        return ""

    return reminder_file.read_text(encoding="utf-8").strip()


def prompt_languages() -> list[str]:
    """The languages there are prompts in: the folders under prompts/ (de, en)."""
    return sorted(entry.name for entry in PROMPTS_DIR.iterdir() if entry.is_dir() and not entry.name.startswith("."))


def prompt_path(lang: str, relative: str) -> Path | None:
    """The file ``prompts/<lang>/<relative>``, or None: a language that is no folder under prompts/,
    an empty path, or one that leaves prompts/<lang>/. The language arrives from a client, the path
    from agents.json, which a bundle import fills; the one place both are checked."""
    from .audio_sources import safe_subpath

    if lang not in prompt_languages() or not relative:
        return None
    return safe_subpath(PROMPTS_DIR / lang, relative)


def get_language() -> str:
    """UI language ("de"/"en") from the persisted settings."""
    return str(persisted_settings()["ui_language"])


# The language of the turn being answered (set by turn_service after the intent detection): every prompt a model
# reads in it, tool results and notices too, speaks it. Task-local like tool_output_cap.budget_var.
turn_language: ContextVar[str | None] = ContextVar("turn_language", default=None)


def prompt_language() -> str:
    """The language of a prompt loaded without one: the turn's, outside a turn the interface's."""
    return turn_language.get() or get_language()


def _build_standard_placeholders(lang: str) -> dict:
    """Build the always-available date/time/user placeholder dict.

    Single source of truth for both :func:`load_prompt` (prompt-tree files) and
    :func:`render_standard_placeholders` (plugin instruction fragments), so a
    plugin prompt like the EPIM intro can carry ``{upcoming_week}`` instead
    of a hardcoded, quickly-stale snapshot.
    """
    from datetime import datetime, timedelta
    now = datetime.now()

    weekday_map = {
        "Monday": "Montag", "Tuesday": "Dienstag", "Wednesday": "Mittwoch",
        "Thursday": "Donnerstag", "Friday": "Freitag",
        "Saturday": "Samstag", "Sunday": "Sonntag"
    }

    if lang == "de":
        weekday = weekday_map.get(now.strftime("%A"), now.strftime("%A"))
        current_date = f"{weekday}, {now.strftime('%d.%m.%Y')}"
    else:
        weekday = now.strftime("%A")
        current_date = f"{weekday}, {now.strftime('%Y-%m-%d')}"

    # Build upcoming days reference (helps LLMs with date arithmetic): today and the 7 days after it, so
    # "morgen" and "nächsten <Wochentag>" (each weekday once after today) both resolve from the list
    upcoming_days: list[str] = []
    for offset in range(8):
        d = now + timedelta(days=offset)
        if lang == "de":
            wd = weekday_map.get(d.strftime("%A"), d.strftime("%A"))
            upcoming_days.append(f"{wd} {d.strftime('%d.%m.%Y')}")
        else:
            upcoming_days.append(f"{d.strftime('%A')} {d.strftime('%Y-%m-%d')}")
    upcoming_week = ", ".join(upcoming_days)

    current_year_int = now.year
    return {
        'current_year': str(current_year_int),
        'current_date': current_date,
        'current_time': now.strftime('%H:%M:%S'),
        'current_weekday': weekday,
        'upcoming_week': upcoming_week,
        'previous_years': f"{current_year_int - 2} oder {current_year_int - 1}",  # e.g., "2024 oder 2025"
        'user_name': get_user_name(),
        'user_gender': ("männlich" if get_user_gender() == "male" else "weiblich") if lang == "de" else get_user_gender(),
    }


def render_standard_placeholders(text: str, lang: str) -> str:
    """Substitute standard placeholders in free text (e.g. plugin instruction
    fragments loaded outside the prompt tree).

    Uses literal ``{key}`` replacement — NOT ``str.format`` — so stray braces in
    the text can't raise. Unknown placeholders are left untouched.
    """
    for key, value in _build_standard_placeholders(lang).items():
        text = text.replace("{" + key + "}", str(value))
    return text


def load_prompt(
    prompt_name: str,
    lang: str | None = None,
    user_text: str | None = None,
    **kwargs
) -> str:
    """
    Load a prompt from a file with language support.

    Provides automatic placeholder replacement for date/time values:
    - {current_year} → "2025"
    - {current_date} → "Montag, 02.01.2025" (DE) or "Monday, 2025-01-02" (EN)
    - {current_time} → "14:30:45"
    - {current_weekday} → "Montag" (DE) or "Monday" (EN)
    - {user_name} → User's configured name (if set)

    Args:
        prompt_name: Name of the prompt file (without .txt extension)
        lang: Language ("de" or "en"); None: the turn's language (prompt_language)
        user_text: User text (passed through to kwargs for template formatting)
        **kwargs: Keyword arguments for string formatting

    Returns:
        Formatted prompt string with placeholders replaced

    Raises:
        FileNotFoundError: If prompt file doesn't exist
        KeyError: If required placeholders are missing
    """
    # Determine language
    if lang is None:
        lang = prompt_language()

    # Load from language-specific directory only (no fallback)
    prompt_file = PROMPTS_DIR / lang / f"{prompt_name}.txt"

    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {prompt_file}\n"
            f"Expected language: {lang}\n"
            f"Available prompts: {list_available_prompts()}"
        )
    if user_text and 'user_text' not in kwargs:
        kwargs['user_text'] = user_text
    return fill_prompt(prompt_file, lang, **kwargs)


def fill_prompt(prompt_file: Path, lang: str, **kwargs: Any) -> str:
    """The prompt file with its placeholders filled: the standard ones (date, time, user; kwargs override them)
    and ``kwargs``. One place for every prompt file, the tree's (load_prompt) and a plugin's
    (plugin_base.load_plugin_prompt).

    Raises:
        KeyError: a placeholder of the file is given nowhere
    """
    prompt_template = prompt_file.read_text(encoding="utf-8")
    standard_placeholders = _build_standard_placeholders(lang)
    all_placeholders = {**standard_placeholders, **kwargs}

    # NOTE: User name/gender prefix is injected in _merge_prompt_layers() (Layer 0),
    # NOT here. This prevents the prefix from appearing once per layer (5-6x).
    try:
        return prompt_template.format(**all_placeholders)
    except KeyError as e:
        raise KeyError(
            f"Missing placeholder in prompt '{prompt_file}': {e}\n"
            f"Provided kwargs: {list(kwargs.keys())}\n"
            f"Standard placeholders: {list(standard_placeholders.keys())}"
        ) from e


def list_available_prompts() -> list:
    """
    List all available prompts across all languages

    Returns:
        List of all available prompt names (without .txt)
    """
    if not PROMPTS_DIR.exists():
        return []

    prompts: set[str] = set()

    # Check language directories only (no root directory)
    for lang_dir in ['de', 'en']:
        lang_path = PROMPTS_DIR / lang_dir
        if lang_path.exists():
            prompts.update(p.stem for p in lang_path.glob('*.txt'))

    return sorted(prompts)


# ============================================================
# The system prompt of an agent (one builder for every path)
# ============================================================

# The descriptions of the seats: the dialectic of three (critic, judge), the critical review of two (author,
# critic) and the code review of two (auctor and censor, no antithesis).
DEBATE_ROLES = "shared/multi_agent_roles"
CRITIQUE_ROLES = "shared/multi_agent_roles_critique"
REVIEW_ROLES = "shared/multi_agent_roles_review"


def agent_system_prompt(
    agent_id: str | None,
    situation: str,
    lang: str,
    *,
    answer_channel: str,
    external: bool,
    tools: Collection[str] | None,
    memory: bool,
    seats: dict[str, str] | None = None,
    roles: str = DEBATE_ROLES,
    answers_user: bool = True,
) -> str:
    """The system prompt of an agent, on every path: the direct answer and every channel
    (``situation`` = shared/conversation_roles), a seat of a debate (its task), the symposion (its
    rules), a sub-agent (shared/subagent_frame). The agent's own files are only identity, personality
    and reminder; what it is to do comes from the situation.

    ``answer_channel``: where the answer arrives (a job's delivery channel, else the message's): the
    formatting rules come where it is shown, not spoken or plain text. ``external``: the message is not the owner's (security.in_owner_context: a
    stranger, a webhook's payload): it adds the security boundary. ``tools``: the names of the
    tools the call really has — only theirs get instructions; None while the toolkit is not built
    yet (measuring the prompt before a turn: the agent's whitelist). ``memory``: the session is not
    incognito. ``seats``: a debate's seats, described by ``roles``. ``agent_id`` None: a sub-agent
    without a persona (no identity, reasoning, personality). ``answers_user`` False: the answer goes
    to someone else than the user (a sub-agent's caller, a peer on AI-Connect) — no user prefix, output
    format, formatting rules, announcing or asking back.
    """
    return _merge_prompt_layers(
        agent_id, situation, lang, seats=seats, memory=memory, answer_channel=answer_channel, external=external, roles=roles,
        tools=tools, answers_user=answers_user,
    )


def conversation_situation(lang: str) -> str:
    """The situation of an agent that answers the user (directly, addressed by name, in a channel)."""
    return load_prompt("shared/conversation_roles", lang=lang)


# ============================================================
# Convenience functions for frequently used prompts
# ============================================================

# The Automatik LLM is no agent: its prompts are the files of this folder (intent detection, URL ranking), in English
# only, since it is always asked in English.
AUTOMATIK_PROMPT_DIR = "automatik"


def automatik_prompts() -> dict[str, str]:
    """The Automatik LLM's prompts as prompt layers: key → path under prompts/<lang>/, from every language's folder."""
    keys = sorted({path.stem for lang in prompt_languages() for path in (PROMPTS_DIR / lang / AUTOMATIK_PROMPT_DIR).glob("*.txt")})
    return {key: f"{AUTOMATIK_PROMPT_DIR}/{key}.txt" for key in keys}


def automatik_prompt_languages() -> list[str]:
    """The languages the Automatik's prompts are in (English: it is always asked in English; a file in another
    language would never be loaded)."""
    return [lang for lang in prompt_languages() if (PROMPTS_DIR / lang / AUTOMATIK_PROMPT_DIR).is_dir()]


def get_intent_detection_prompt(user_query: str, lang: str | None = None) -> str:
    """Load intent detection prompt with dynamic agent list + descriptions + aliases: the selectable
    agents only (no system agent, none switched off — they cannot be addressed)."""
    from .agent_config import selectable_agents
    agent_lines: list[str] = []
    for aid, config in selectable_agents().items():
        name = config.display_name
        name_part = f"{aid}" if aid == name.lower() else f"{aid} (name: {name})"
        alias_part = f" [also: {', '.join(config.aliases)}]" if config.aliases else ""
        if config.description:
            agent_lines.append(f"- {name_part}{alias_part}: {config.description}")
        else:
            agent_lines.append(f"- {name_part}{alias_part}")
    agent_list = "\n".join(agent_lines)
    return load_prompt(
        f'{AUTOMATIK_PROMPT_DIR}/intent_detection', lang=lang,
        user_query=user_query, agent_list=agent_list,
    )


def load_multi_agent_roles(seats: dict[str, str], lang: str | None = None, roles: str = DEBATE_ROLES) -> str:
    """Who sits in which seat and the history labels (``roles``: DEBATE_ROLES or REVIEW_ROLES).
    Used in all multi-agent modes (not in direct modes)."""
    from .debate_seats import seat_names
    from .message_builder import history_user_label
    return load_prompt(roles, lang=lang, user_name=history_user_label(), **seat_names(seats))


def _renders_markdown(answer_channel: str) -> bool:
    """The answer, arriving on ``answer_channel``, is shown as Markdown (browser, text channels, a job's review) — not
    spoken or shown as plain text (a channel with ``markdown_output = False``: the Echo puck, Telegram)."""
    from .plugin_registry import get_channel

    channel = get_channel(answer_channel)
    return channel is None or channel.markdown_output


def answer_situation(source: str, lang: str) -> tuple[str, bool]:
    """The situation of the agent answering a turn from ``source`` and whether it answers the user: the
    channel's own (AI-Connect: another assistant, not the user), else the user's conversation."""
    from .plugin_registry import get_channel

    channel = get_channel(source)
    own = channel.situation(lang) if channel is not None else None
    return (own, False) if own is not None else (conversation_situation(lang), True)


def load_memory_instructions(lang: str | None = None) -> str:
    """Load shared memory instructions for agents with long-term memory."""
    return load_prompt('shared/memory_instructions', lang=lang)


def _merge_prompt_layers(
    agent: str | None,
    task_prompt: str,
    lang: str | None = None,
    seats: dict[str, str] | None = None,
    memory: bool = True,
    answer_channel: str = "browser",
    external: bool = False,
    roles: str = DEBATE_ROLES,
    tools: Collection[str] | None = None,
    answers_user: bool = True,
) -> str:
    """
    Merge prompt layers in correct order.

    Layer system (agent None: no layers 1, 2, 9; answers_user False: no layers 0, 5, 6, no announcing and no asking back):
    0. User prefix (WHO is the user) - if user_name is set
    1. Identity (WHO am I, with the agent's role) - always loaded
    2. Reasoning (HOW do I think) - toggleable per agent
    3. Multi-Agent Roles (WHO are the others) - only with seats
    4. Situation (WHAT should I do here) - answer the user, a seat's task, the symposion, the sub-agent's frame
    5. Output format - always
    6. Formatting rules - where the answer is shown, not spoken
    7. Security boundary - only for a message from a sender who is not the owner (external)
    8. Memory instructions - session not incognito and memory tools at hand
    9. Personality (HOW do I speak) - toggleable per agent
    10. Tool and plugin instructions - for the tools the call has (announcing them: answering the user only)
    11. Disciplines and asking back - always, LAST

    Args:
        agent: Agent name ("aifred", "sokrates", "salomo", or custom agent ID); None for a sub-agent without persona
        task_prompt: The task-specific prompt (already loaded with timestamp)
        lang: Language code (de/en), defaults to current language
        seats: The debate's seats: with them, shared/multi_agent_roles.txt names who sits where
        memory: If True, include memory instructions (False in incognito mode)

    Returns:
        Merged prompt string with all applicable layers
    """
    if lang is None:
        lang = prompt_language()

    parts = []

    # Layer 0: User prefix (once, at the top — NOT per-layer). Name and gender (translated for the language)
    # come from the standard placeholders.
    if answers_user and get_user_name():
        user_prefix = load_prompt('shared/user_prefix', lang=lang)
        if user_prefix:
            parts.append(user_prefix)

    # Layer 1: Identity (always)
    identity = load_identity(agent, lang) if agent else ""
    if identity:
        parts.append(identity)

    # Layer 2: Reasoning (if enabled) - before task prompt
    reasoning = load_reasoning(agent, lang) if agent else ""
    if reasoning:
        parts.append(reasoning)

    # Layer 3: Multi-Agent Roles (only in multi-agent modes)
    if seats is not None:
        roles_prompt = load_multi_agent_roles(seats, lang, roles)
        if roles_prompt:
            parts.append(roles_prompt)

    # Layer 4: The situation (what the agent is to do here: answer the user, a seat's task, the symposion)
    if task_prompt:
        parts.append(task_prompt)

    # Layer 5: Output format (no name header) — on every path to the user
    if answers_user:
        parts.append(load_prompt('shared/output_format', lang=lang))

    # Layer 6: Formatting rules where the answer is shown (browser, text channels), not where it is spoken
    if answers_user and _renders_markdown(answer_channel):
        parts.append(load_prompt('shared/formatting', lang=lang))

    # Layer 7: Security boundary — a stranger's message; the owner's own (any channel, a job) needs none
    if external:
        parts.append(load_prompt('shared/security_boundary', lang=lang))

    # Layer 8: Memory instructions — when the session keeps memory and the call has the memory tools
    if memory and (tools is None or "store_memory" in tools):
        parts.append(load_memory_instructions(lang))

    # Layer 9: Personality (if enabled)
    personality = load_personality(agent, lang) if agent else ""
    if personality:
        parts.append(personality)

    # Layer 10: Tool instructions — near the end so the model prioritizes tool use. The plugins'
    # instructions only for the tools the call really has (``tools``); before the toolkit is built
    # (None) for the agent's whitelist. Every plugin decides itself which fragments it gives
    # (load_plugin_instructions) and gives "" when none of its tools is there.
    from .agent_config import get_agent_config
    from .plugin_registry import discover_tools
    if tools is None:
        assert agent is not None, "a sub-agent's prompt is built with its granted tools"
        config = get_agent_config(agent)
        allowed = set(config.tools) if (config and config.tools is not None) else None
    else:
        allowed = set(tools)
    if allowed is None or allowed:
        parts.append(load_prompt('shared/tool_instructions', lang=lang))
        if answers_user:   # announcing a tool call is said to the user; a sub-agent speaks to nobody
            parts.append(load_prompt('shared/tool_announce', lang=lang))
        for plugin in discover_tools():
            if not plugin.is_available():
                continue
            instructions = plugin.get_prompt_instructions(lang, allowed)
            if instructions:
                parts.append(instructions)

    # Layer 11: Disciplines (date grounding, quotes, currency) and asking back on decisions — LAST
    # (recency bias for the date grounding)
    parts.append(load_prompt('shared/disciplines', lang=lang))
    if answers_user:
        parts.append(load_prompt('shared/ask_back', lang=lang))

    return "\n\n".join(parts)


def get_vision_ir_context_prompt(lang: str | None = None) -> str:
    """Kontext-Baustein für Infrarot-/Graustufen-Aufnahmen: verhindert, dass
    das VLM IR-Helligkeiten als reale Farben beschreibt ("helles T-Shirt").
    In ``prompts/{lang}/vision/vision_ir_context.txt``."""
    if lang is None:
        lang = prompt_language()
    prompt_file = PROMPTS_DIR / lang / "vision" / "vision_ir_context.txt"
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read().strip()


def get_vision_identity_context_prompt(
    names: list[str], lang: str | None = None
) -> str:
    """Identitäts-Kontext für VLM-Beschreibungen: die sicher erkannten
    Personen als Fakt voranstellen, damit das VLM sie beim Namen nennt.
    SSoT für Alert-Beschreibung UND Live-Teleprompter. In
    ``prompts/{lang}/vision/vision_identity_context.txt`` ({names})."""
    if lang is None:
        lang = prompt_language()
    prompt_file = PROMPTS_DIR / lang / "vision" / "vision_identity_context.txt"
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read().strip().replace("{names}", ", ".join(names))


def get_vision_headcount_context_prompt(
    count: int, lang: str | None = None
) -> str:
    """Personenzahl-Kontext für VLM-Beschreibungen: die von YOLO gezählten
    Personen als Fakt mitgeben, damit das VLM auch verdeckte/abgewandte
    Personen im Hintergrund beschreibt statt nur die vorderste. In
    ``prompts/{lang}/vision/vision_headcount_context.txt`` ({count})."""
    if lang is None:
        lang = prompt_language()
    prompt_file = PROMPTS_DIR / lang / "vision" / "vision_headcount_context.txt"
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read().strip().replace("{count}", str(count))


def get_vision_continuous_prompt(lang: str | None = None) -> str:
    """Prompt für die Dauer-Beschreibung des Live-Teleprompters. In
    ``prompts/{lang}/vision/vision_continuous.txt``."""
    if lang is None:
        lang = prompt_language()
    prompt_file = PROMPTS_DIR / lang / "vision" / "vision_continuous.txt"
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read().strip()


def get_vision_event_single_prompt(lang: str | None = None) -> str:
    """Prompt für die Einzelbild-Analyse eines gespeicherten Vision-Events
    (Casus-Button). In ``prompts/{lang}/vision/vision_event_single.txt``."""
    if lang is None:
        lang = prompt_language()
    prompt_file = PROMPTS_DIR / lang / "vision" / "vision_event_single.txt"
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read().strip()


def get_vision_event_sequence_prompt(lang: str | None = None) -> str:
    """Prompt für die Sequenz-Analyse eines Vorkommnisses (mehrere Keyframes
    in Zeitreihenfolge — Szene + was sich verändert). Genutzt vom Cluster-/
    Bulk-Describe. In ``prompts/{lang}/vision/vision_event_sequence.txt``."""
    if lang is None:
        lang = prompt_language()
    prompt_file = PROMPTS_DIR / lang / "vision" / "vision_event_sequence.txt"
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read().strip()


def get_vision_event_summary_prompt(lang: str | None = None) -> str:
    """Prompt für die Zusammenfassung eines ganzen Vorkommnisses am Burst-Ende
    (kurzer Ablauf ohne Bildnummern — die Bilder zeigt der Film). In
    ``prompts/{lang}/vision/vision_event_summary.txt``."""
    if lang is None:
        lang = prompt_language()
    prompt_file = PROMPTS_DIR / lang / "vision" / "vision_event_summary.txt"
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read().strip()


# ============================================================
# System Prompt Size (SSOT fuer die Kontextfuellung)
# ============================================================

def get_direct_prompt_tokens(
    agent: str,
    lang: str = "de",
    *,
    memory: bool = True,
    situation: str | None = None,
) -> int:
    """Tokens des System-Prompts, den dieser Agent im nächsten Zug abschickt, bevor sein Werkzeugkasten
    gebaut ist (Anleitungen nach seiner Whitelist, also die Obergrenze). ``situation``: die des ersten
    Sprechers (das Symposion hat seine Regeln), sonst die Antwort an den Benutzer."""
    from .context_manager import count_tokens_with_tokenizer
    prompt = agent_system_prompt(
        agent, situation if situation is not None else conversation_situation(lang), lang,
        answer_channel="browser", external=False, tools=None, memory=memory,
    )
    return count_tokens_with_tokenizer(prompt)
