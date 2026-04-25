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

from pathlib import Path
from typing import Optional

# Base directory for prompts (relative to project root)
PROMPTS_DIR = Path(__file__).parent.parent.parent / 'prompts'

# Global language setting (synced with UI language)
_current_language = "de"  # "de" or "en" (synced from ui_language)

# Global user name (set once when settings are loaded)
_current_user_name = ""

# Global user gender for salutation (male/female)
_current_user_gender = "male"

# Global personality toggle states (loaded from settings)
# Dynamically populated from agents.json via _init_toggle_dicts()
_personality_enabled: dict[str, bool] = {}

# Global reasoning toggle states (loaded from settings)
_reasoning_enabled: dict[str, bool] = {}

def _init_toggle_dicts() -> None:
    """Initialize toggle dicts from agents.json defaults.

    Called once at module load to populate the dicts with all configured agents.
    Afterwards, sync_*_from_settings() overrides with persisted values.

    Note: thinking toggles are NOT stored here — they are read directly from
    the Reflex State (self.{agent}_thinking) to avoid stale module-level globals.
    """
    global _personality_enabled, _reasoning_enabled
    from .agent_config import load_agents

    agents = load_agents()
    for agent_id, config in agents.items():
        _personality_enabled.setdefault(agent_id, config.toggles.get("personality", True))
        _reasoning_enabled.setdefault(agent_id, config.toggles.get("reasoning", False))


# Populate on module load
_init_toggle_dicts()

# Cache for system prompt token counts (populated at startup)
# Format: {"aifred": {"de": tokens, "en": tokens}, "sokrates": {...}, ...}
_system_prompt_token_cache: dict[str, dict[str, int]] = {}


def set_user_name(name: str):
    """Set the global user name for prompts"""
    global _current_user_name
    _current_user_name = name.strip() if name else ""


def get_user_name() -> str:
    """Get the current user name"""
    return _current_user_name


def set_user_gender(gender: str):
    """Set the global user gender for salutation (male/female)"""
    global _current_user_gender
    _current_user_gender = gender if gender in ("male", "female") else "male"


def get_user_gender() -> str:
    """Get the current user gender"""
    return _current_user_gender




def set_personality_enabled(agent: str, enabled: bool):
    """
    Set personality toggle state for an agent.

    Args:
        agent: Agent ID (e.g. "aifred", "sokrates", "salomo", or any custom agent)
        enabled: True to enable personality style, False for factual responses
    """
    global _personality_enabled
    _personality_enabled[agent] = enabled


def get_personality_enabled(agent: str) -> bool:
    """
    Get personality toggle state for an agent.

    Args:
        agent: Agent name ("aifred", "sokrates", "salomo")

    Returns:
        True if personality is enabled, False otherwise
    """
    return _personality_enabled.get(agent, True)


def set_reasoning_enabled(agent: str, enabled: bool):
    """
    Set reasoning toggle state for an agent.

    Args:
        agent: Agent ID (e.g. "aifred", "sokrates", "salomo", or any custom agent)
        enabled: True to enable chain-of-thought reasoning
    """
    global _reasoning_enabled
    _reasoning_enabled[agent] = enabled


def get_reasoning_enabled(agent: str) -> bool:
    """
    Get reasoning toggle state for an agent.

    Args:
        agent: Agent name ("aifred", "sokrates", "salomo")

    Returns:
        True if reasoning is enabled, False otherwise
    """
    return _reasoning_enabled.get(agent, False)


def _resolve_prompt_file(agent: str, prompt_key: str, lang: Optional[str] = None) -> Optional[Path]:
    """
    Resolve prompt file path for an agent via agent_config.

    Looks up the agent's prompts dict for the given key (e.g. "identity",
    "personality", "reminder"). This allows cross-references like Vision
    using "aifred/personality.txt" for AIfred's personality.

    Args:
        agent: Agent identifier (e.g. "aifred", "vision", or any custom agent)
        prompt_key: Key into the agent's prompts dict
        lang: Language code, defaults to current language

    Returns:
        Resolved Path if the file exists, None otherwise
    """
    if lang is None:
        lang = _current_language

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


def load_reasoning(agent: str, lang: Optional[str] = None) -> str:
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
        lang = _current_language

    reasoning_file = PROMPTS_DIR / lang / "utility" / "reasoning.txt"

    if not reasoning_file.exists():
        return ""

    return reasoning_file.read_text(encoding="utf-8").strip()


def load_identity(agent: str, lang: Optional[str] = None) -> str:
    """
    Load identity prompt for an agent (always loaded).

    Identity defines WHO the agent is - resolved via agent_config,
    so agents can use custom paths (e.g. "vision/identity.txt").

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


def load_personality(agent: str, lang: Optional[str] = None) -> str:
    """
    Load personality prompt for an agent.

    Personality defines HOW the agent speaks (style) - resolved via
    agent_config, so agents can reference other agents' personalities
    (e.g. Vision using "aifred/personality.txt").

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


def load_personality_reminder(agent: str, lang: Optional[str] = None) -> str:
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


def set_language(lang: str):
    """
    Set the global language for prompts.

    This is synced with ui_language from state.py.

    Args:
        lang: "de" or "en"
    """
    global _current_language
    if lang in ["de", "en"]:
        _current_language = lang
    else:
        raise ValueError(f"Unsupported language: {lang}. Use 'de' or 'en'")


def get_language() -> str:
    """Get the current language setting"""
    return _current_language


def load_tool_description(filename: str) -> str:
    """Load a tool description from prompts/shared/.

    Args:
        filename: File name (e.g. 'email_tool.txt')

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    path = PROMPTS_DIR / "shared" / filename
    if not path.exists():
        raise FileNotFoundError(f"Tool description not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_prompt(
    prompt_name: str,
    lang: Optional[str] = None,
    user_text: Optional[str] = None,
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
        lang: Language override ("de" or "en", or None for current setting)
        user_text: User text (passed through to kwargs for template formatting)
        **kwargs: Keyword arguments for string formatting

    Returns:
        Formatted prompt string with placeholders replaced

    Raises:
        FileNotFoundError: If prompt file doesn't exist
        KeyError: If required placeholders are missing
    """
    from datetime import datetime

    # Determine language
    if lang is None:
        lang = _current_language

    # Load from language-specific directory only (no fallback)
    prompt_file = PROMPTS_DIR / lang / f"{prompt_name}.txt"

    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {prompt_file}\n"
            f"Expected language: {lang}\n"
            f"Available prompts: {list_available_prompts()}"
        )

    # Load prompt file
    with open(prompt_file, 'r', encoding='utf-8') as f:
        prompt_template = f.read()

    # ============================================================
    # BUILD STANDARD PLACEHOLDERS (date/time/user)
    # ============================================================
    now = datetime.now()

    # German weekday translation
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

    # Build upcoming days reference (helps LLMs with date arithmetic)
    from datetime import timedelta
    upcoming_days: list[str] = []
    for offset in range(1, 8):
        d = now + timedelta(days=offset)
        if lang == "de":
            wd = weekday_map.get(d.strftime("%A"), d.strftime("%A"))
            upcoming_days.append(f"{wd} {d.strftime('%d.%m.%Y')}")
        else:
            upcoming_days.append(f"{d.strftime('%A')} {d.strftime('%Y-%m-%d')}")
    upcoming_week = ", ".join(upcoming_days)

    # EPIM lookup data (loaded once, cached in DB singleton)
    epim_categories = ""
    epim_todolists = ""
    epim_notetrees = ""
    epim_calendars = ""
    try:
        from .config import EPIM_ENABLED
        if EPIM_ENABLED:
            from ..plugins.tools.epim.db import get_epim_db
            _epim = get_epim_db()
            if _epim:
                epim_categories = ", ".join(str(c["name"]) for c in _epim.get_categories())
                epim_todolists = ", ".join(str(t["name"]) for t in _epim.get_todolists())
                epim_notetrees = ", ".join(str(n["name"]) for n in _epim.get_notetrees())
                epim_calendars = ", ".join(str(c["name"]) for c in _epim.get_calendars())
    except Exception:
        pass  # EPIM not available — placeholders stay empty

    # Standard placeholders - always available
    current_year_int = now.year
    standard_placeholders = {
        'current_year': str(current_year_int),
        'current_date': current_date,
        'current_time': now.strftime('%H:%M:%S'),
        'current_weekday': weekday,
        'upcoming_week': upcoming_week,
        'epim_categories': epim_categories,
        'epim_todolists': epim_todolists,
        'epim_notetrees': epim_notetrees,
        'epim_calendars': epim_calendars,
        'previous_years': f"{current_year_int - 2} oder {current_year_int - 1}",  # e.g., "2024 oder 2025"
        'user_name': _current_user_name if _current_user_name else "",
        'user_gender': ("männlich" if _current_user_gender == "male" else "weiblich") if lang == "de" else _current_user_gender,
    }

    # Merge standard placeholders with kwargs (kwargs override standard)
    all_placeholders = {**standard_placeholders, **kwargs}

    # Merge user_text into placeholders if not already there
    if user_text and 'user_text' not in all_placeholders:
        all_placeholders['user_text'] = user_text

    # NOTE: User name/gender prefix is injected in _merge_prompt_layers() (Layer 0),
    # NOT here. This prevents the prefix from appearing once per layer (5-6x).

    # Format prompt with all placeholders
    try:
        return prompt_template.format(**all_placeholders)
    except KeyError as e:
        raise KeyError(
            f"Missing placeholder in prompt '{prompt_name}': {e}\n"
            f"Provided kwargs: {list(kwargs.keys())}\n"
            f"Standard placeholders: {list(standard_placeholders.keys())}"
        )


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

    return sorted(list(prompts))


# ============================================================
# Generic Agent Prompt Loader (dynamic agents)
# ============================================================

def get_agent_system_prompt(
    agent_id: str,
    prompt_key: str = "task",
    lang: Optional[str] = None,
    multi_agent: bool = False,
    memory: bool = True,
    **kwargs,
) -> str:
    """
    Load system prompt for any configured agent.

    Uses agent_config.json to resolve prompt file paths, then merges
    through the 6-layer system (Identity + Reasoning + [MultiAgent] + Task + [Memory] + Personality).

    Args:
        agent_id: Agent identifier (e.g. "aifred", "sokrates", or any custom agent)
        prompt_key: Key into the agent's prompts dict (default "task" = system_minimal)
        lang: Language code (de/en), defaults to current language
        multi_agent: If True, include multi-agent roles explanation
        memory: If True, include memory instructions (False in incognito mode)
        **kwargs: Extra placeholders for the prompt template

    Returns:
        Merged system prompt string
    """
    from .agent_config import get_agent_config

    config = get_agent_config(agent_id)
    if config is None:
        raise ValueError(f"Unknown agent: {agent_id}")

    prompt_path = config.prompts.get(prompt_key)
    if prompt_path is None:
        raise ValueError(
            f"Agent '{agent_id}' has no prompt for key '{prompt_key}'. "
            f"Available: {list(config.prompts.keys())}"
        )

    # Strip .txt suffix if present (load_prompt adds it)
    prompt_name = prompt_path.removesuffix(".txt")

    task_prompt = load_prompt(prompt_name, lang=lang, **kwargs)
    return _merge_prompt_layers(
        agent_id, task_prompt, lang,
        multi_agent=multi_agent, memory=memory,
        tools=kwargs.get('tools', True),
        user_name=kwargs.get('user_name'), user_gender=kwargs.get('user_gender'),
        source=kwargs.get('source', 'browser'),
    )


def register_agent_toggles(agent_id: str, toggles: dict[str, bool]) -> None:
    """Register toggle states for a new agent in the prompt loader.

    Called when a new agent is created at runtime.
    """
    global _personality_enabled, _reasoning_enabled
    _personality_enabled[agent_id] = toggles.get("personality", True)
    _reasoning_enabled[agent_id] = toggles.get("reasoning", False)


def unregister_agent_toggles(agent_id: str) -> None:
    """Remove toggle states for a deleted agent."""
    _personality_enabled.pop(agent_id, None)
    _reasoning_enabled.pop(agent_id, None)


# ============================================================
# Convenience functions for frequently used prompts
# ============================================================

def get_intent_detection_prompt(user_query: str, lang: Optional[str] = None) -> str:
    """Load intent detection prompt with dynamic agent list + descriptions + aliases."""
    from .agent_config import load_agents_raw
    agents = load_agents_raw()
    agent_lines: list[str] = []
    for aid, adata in agents.items():
        # Skip vision agent (not user-addressable)
        if aid == "vision":
            continue
        name = adata.get("display_name", aid)
        desc = adata.get("description", "")
        aliases = [str(a).strip().lower() for a in adata.get("aliases") or [] if isinstance(a, str) and a.strip()]
        name_part = f"{aid}" if aid == name.lower() else f"{aid} (name: {name})"
        alias_part = f" [also: {', '.join(aliases)}]" if aliases else ""
        if desc:
            agent_lines.append(f"- {name_part}{alias_part}: {desc}")
        else:
            agent_lines.append(f"- {name_part}{alias_part}")
    agent_list = "\n".join(agent_lines)
    return load_prompt(
        'automatik/intent_detection', lang=lang,
        user_query=user_query, agent_list=agent_list,
    )


def get_vl_relevance_check_prompt(
    user_query: str,
    image_context: str,
    recent_context: str = "",
    lang: Optional[str] = None,
) -> str:
    """Load VL relevance check prompt for image follow-up detection."""
    return load_prompt(
        'automatik/vl_relevance_check',
        lang=lang,
        user_query=user_query,
        image_context=image_context,
        recent_context=recent_context if recent_context else "(no prior messages)",
    )


def get_research_decision_prompt(
    user_text: str,
    has_images: bool = False,
    vision_json: Optional[dict] = None,
    lang: Optional[str] = None
) -> str:
    """
    Load combined research decision prompt (Decision-Making + Query-Optimization).

    This consolidates two LLM calls into one:
    1. Decides if web research is needed
    2. If yes, generates 3 optimized search queries

    Output format is JSON:
    - {"web": false} if no research needed
    - {"web": true, "queries": ["q1", "q2", "q3"]} if research needed

    Args:
        user_text: User query text
        has_images: Whether the message includes image(s)
        vision_json: Structured data extracted from images by Vision-LLM
        lang: Language override

    Returns:
        Formatted research decision prompt
    """
    # Build image context string
    if has_images:
        if lang == "en":
            image_context = "\n\n⚠️ USER ATTACHED IMAGE(S) - This is an image analysis task!"
        else:  # German (default)
            image_context = "\n\n⚠️ BENUTZER HAT BILD(ER) ANGEHÄNGT - Dies ist eine Bildanalyse-Aufgabe!"
    else:
        image_context = ""

    # Build Vision JSON context string
    if vision_json:
        import json
        vision_json_context = f"""

STRUKTURIERTE DATEN AUS BILD:
```json
{json.dumps(vision_json, ensure_ascii=False, indent=2)}
```

Diese Daten wurden automatisch aus dem Bild extrahiert."""
    else:
        vision_json_context = ""

    return load_prompt(
        'automatik/research_decision',
        lang=lang,
        user_text=user_text,
        image_context=image_context,
        vision_json_context=vision_json_context
    )


def get_query_generation_prompt(
    user_text: str,
    has_images: bool = False,
    vision_json: Optional[dict] = None,
    lang: Optional[str] = None
) -> str:
    """
    Load query generation prompt (ONLY queries, NO web decision).

    Used in explicit web search modes (quick/deep) where the user has
    already decided that web search is needed. This prompt ONLY generates
    3 optimized search queries without deciding if search is necessary.

    Output format is JSON:
    - {"queries": ["q1", "q2", "q3"]}

    Args:
        user_text: User query text
        has_images: Whether the message includes image(s)
        vision_json: Structured data extracted from images by Vision-LLM
        lang: Language override

    Returns:
        Formatted query generation prompt
    """
    # Build image context string
    if has_images:
        if lang == "en":
            image_context = "\n\n⚠️ USER ATTACHED IMAGE(S) - This is an image analysis task!"
        else:  # German (default)
            image_context = "\n\n⚠️ BENUTZER HAT BILD(ER) ANGEHÄNGT - Dies ist eine Bildanalyse-Aufgabe!"
    else:
        image_context = ""

    # Build Vision JSON context string
    if vision_json:
        import json
        vision_json_context = f"""

STRUKTURIERTE DATEN AUS BILD:
```json
{json.dumps(vision_json, ensure_ascii=False, indent=2)}
```

Diese Daten wurden automatisch aus dem Bild extrahiert."""
    else:
        vision_json_context = ""

    return load_prompt(
        'automatik/query_generation',
        lang=lang,
        user_text=user_text,
        image_context=image_context,
        vision_json_context=vision_json_context
    )


def get_followup_intent_prompt(original_query: str, followup_query: str, lang: Optional[str] = None) -> str:
    """Load followup intent detection prompt"""
    return load_prompt(
        'automatik/followup_intent_detection',
        lang=lang,
        original_query=original_query,
        followup_query=followup_query
    )


def load_multi_agent_roles(lang: Optional[str] = None) -> str:
    """
    Load shared multi-agent roles description.

    This explains the three-agent system (AIfred, Sokrates, Salomo) and
    history labels. Used in all multi-agent modes (not in direct modes).

    Args:
        lang: Language code (de/en), defaults to current language

    Returns:
        Multi-agent roles prompt text
    """
    return load_prompt('shared/multi_agent_roles', lang=lang)


def load_memory_instructions(lang: Optional[str] = None) -> str:
    """Load shared memory instructions for agents with long-term memory."""
    return load_prompt('shared/memory_instructions', lang=lang)


def _merge_prompt_layers(
    agent: str,
    task_prompt: str,
    lang: Optional[str] = None,
    multi_agent: bool = False,
    memory: bool = True,
    user_name: Optional[str] = None,
    user_gender: Optional[str] = None,
    tools: bool = False,
    rag_context: Optional[str] = None,
    source: str = "browser",
) -> str:
    """
    Merge prompt layers in correct order.

    Layer system:
    0. User prefix (WHO is the user) - if user_name is set
    1. Identity (WHO am I) - always loaded
    2. Reasoning (HOW do I think) - toggleable via settings
    3. Multi-Agent Roles (WHO are the others) - only in multi-agent modes
    4. Task prompt (WHAT should I do) - situational
    5. Anti-hallucination (STAY HONEST) - always loaded
    6. RAG context (RESEARCH RESULTS) - when research data available
    7. Tool instructions (USE TOOLS) - when tools available
    8. Memory instructions (REMEMBER) - when memory active (not incognito)
    9. Personality (HOW do I speak) - toggleable via settings, LAST for priority

    Args:
        agent: Agent name ("aifred", "sokrates", "salomo", or custom agent ID)
        task_prompt: The task-specific prompt (already loaded with timestamp)
        lang: Language code (de/en), defaults to current language
        multi_agent: If True, include shared/multi_agent_roles.txt (for debate modes)
        memory: If True, include memory instructions (False in incognito mode)
        user_name: User's display name (fallback: global _current_user_name)
        user_gender: User's gender "male"/"female" (fallback: global _current_user_gender)
        tools: If True, include tool usage instructions
        rag_context: Research context string to inject (from web search)

    Returns:
        Merged prompt string with all applicable layers
    """
    if lang is None:
        lang = get_language()

    # Resolve user name/gender: explicit parameter > global cache
    name = user_name if user_name is not None else _current_user_name
    gender = user_gender if user_gender is not None else _current_user_gender

    parts = []

    # Layer 0: User prefix (once, at the top — NOT per-layer)
    if name:
        user_prefix = load_prompt('shared/user_prefix', lang=lang, user_name=name, user_gender=gender)
        if user_prefix:
            parts.append(user_prefix)

    # Layer 1: Identity (always)
    identity = load_identity(agent, lang)
    if identity:
        parts.append(identity)

    # Layer 2: Reasoning (if enabled) - before task prompt
    reasoning = load_reasoning(agent, lang)
    if reasoning:
        parts.append(reasoning)

    # Layer 3: Multi-Agent Roles (only in multi-agent modes)
    if multi_agent:
        roles = load_multi_agent_roles(lang)
        if roles:
            parts.append(roles)

    # Layer 4: Task prompt (always)
    parts.append(task_prompt)

    # Layer 5: Security boundary (only for external channel contexts)
    if source != "browser":
        sec_boundary = load_prompt('shared/security_boundary', lang=lang)
        if sec_boundary:
            parts.append(sec_boundary)

    # Layer 6: RAG context (when research results available)
    if rag_context:
        rag_instructions = load_prompt('shared/rag_context', lang=lang, context=rag_context)
        parts.append(rag_instructions)

    # Layer 7: Memory instructions (when memory active)
    if memory:
        mem_instructions = load_memory_instructions(lang)
        if mem_instructions:
            parts.append(mem_instructions)

    # Layer 8: Personality (if enabled)
    personality = load_personality(agent, lang)
    if personality:
        parts.append(personality)

    # Layer 9: Tool instructions — near the end so LLM prioritizes tool use
    if tools:
        tool_instructions = load_prompt('shared/tool_instructions', lang=lang)
        if tool_instructions:
            parts.append(tool_instructions)

        # Plugin-specific instructions (dynamic)
        from .plugin_registry import discover_tools
        for p in discover_tools():
            if p.is_available():
                instr = p.get_prompt_instructions(lang)
                if instr:
                    parts.append(instr)

    # Layer 10: Anti-hallucination (always, LAST — recency bias for date grounding)
    anti_halluc = load_prompt('shared/anti_hallucination', lang=lang)
    if anti_halluc:
        parts.append(anti_halluc)

    return "\n\n".join(parts)


def get_system_rag_prompt(
    context: str, user_text: str = "", agent_id: str = "aifred",
    lang: Optional[str] = None,
    user_name: Optional[str] = None, user_gender: Optional[str] = None,
) -> str:
    """
    Load system prompt with RAG context via shared layer merging.

    Uses the agent's normal task prompt (direct.txt) + shared/rag_context.txt layer.
    RAG context is injected as Layer 6, shared across all agents.
    """
    task_prompt = load_prompt(f'{agent_id}/direct', lang=lang)
    return _merge_prompt_layers(
        agent_id, task_prompt, lang,
        tools=True,
        user_name=user_name, user_gender=user_gender,
        rag_context=context,
    )


# Cache metadata prompt removed - will be replaced with Vector DB embeddings


def get_vision_ocr_prompt(lang: Optional[str] = None) -> str:
    """Load Vision-LLM OCR prompt (timestamp injected automatically by load_prompt)"""
    return load_prompt('vision/vision_ocr', lang=lang)


def get_vision_templateless_ocr_prompt(lang: Optional[str] = None) -> str:
    """
    Load Vision-LLM OCR prompt for template-less models (DeepSeek-OCR, etc.)

    Note: No timestamp injection for template-less models (keeps prompt minimal)
    """
    if lang is None:
        lang = _current_language

    prompt_file = PROMPTS_DIR / lang / "vision" / "vision_templateless_ocr.txt"
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read().strip()


def get_vision_templateless_default_prompt(lang: Optional[str] = None) -> str:
    """
    Load default Vision prompt for template-less models.

    Uses the same prompt as vision_ocr.txt - the difference is only
    in how it's injected (as user content vs. system prompt).

    Note: No timestamp injection for template-less models (keeps prompt minimal)
    """
    if lang is None:
        lang = _current_language

    # Use vision_ocr.txt for both template and non-template models
    # (same content, different injection method)
    prompt_file = PROMPTS_DIR / lang / "vision" / "vision_ocr.txt"
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read().strip()


def get_cache_metadata_prompt(sources_preview: str, lang: Optional[str] = None) -> str:
    """
    Load cache metadata generation prompt.

    Used to generate a concise summary of cached research sources
    for later cache hit decisions.

    Args:
        sources_preview: Preview text of research sources
        lang: Language code (de/en), defaults to current language

    Returns:
        Formatted prompt with sources inserted
    """
    if lang is None:
        lang = _current_language

    prompt_file = PROMPTS_DIR / lang / "automatik" / "cache_metadata.txt"
    with open(prompt_file, 'r', encoding='utf-8') as f:
        template = f.read().strip()

    return template.format(sources_preview=sources_preview)


# ============================================================
# Sokrates Multi-Agent Prompts
# ============================================================

def get_sokrates_critic_prompt(round_num: int = 1, lang: Optional[str] = None) -> str:
    """
    Load Sokrates Critic prompt for User-as-Judge and Auto-Consensus modes.

    Args:
        round_num: Current debate round (1, 2, 3, ...)
        lang: Language code (de/en), defaults to current language

    Returns:
        Sokrates critic system prompt with timestamp prefix and round number
    """
    return load_prompt('sokrates/critic', lang=lang, round_num=round_num)


def get_sokrates_devils_advocate_prompt(lang: Optional[str] = None) -> str:
    """
    Load Sokrates Devil's Advocate prompt for Pro/Contra analysis.

    Args:
        lang: Language code (de/en), defaults to current language

    Returns:
        Sokrates devil's advocate system prompt with timestamp prefix
    """
    return load_prompt('sokrates/devils_advocate', lang=lang)


def get_aifred_refinement_prompt(
    critique: str,
    user_interjection: str = "",
    lang: Optional[str] = None,
    round_num: int = 2
) -> str:
    """
    Load AIfred Refinement prompt (when responding to Sokrates' critique).

    Args:
        critique: Sokrates' critique text
        user_interjection: Optional user interjection during debate
        lang: Language code (de/en), defaults to current language
        round_num: Current debate round (default 2, since refinement starts at R2)

    Returns:
        Formatted refinement prompt with critique and timestamp prefix
    """
    return load_prompt(
        'aifred/refinement',
        lang=lang,
        critique=critique,
        user_interjection=user_interjection,
        round_num=round_num
    )



# ============================================================
# Tribunal Mode Prompts (Adversarial Debate)
# ============================================================

def get_sokrates_tribunal_prompt(round_num: int = 1, lang: Optional[str] = None) -> str:
    """
    Load Sokrates Tribunal prompt for adversarial debate mode.

    In Tribunal mode, Sokrates acts as prosecutor/opponent rather than coach.
    He attacks AIfred's position directly, and Salomo judges at the end.

    Args:
        round_num: Current debate round (1, 2, 3, ...)
        lang: Language code (de/en), defaults to current language

    Returns:
        Sokrates tribunal system prompt with round number
    """
    return load_prompt('sokrates/tribunal', lang=lang, round_num=round_num)


def get_aifred_defense_prompt(
    critique: str,
    user_interjection: str = "",
    lang: Optional[str] = None,
    round_num: int = 2
) -> str:
    """
    Load AIfred Defense prompt for Tribunal mode.

    In Tribunal mode, AIfred can choose to DEFEND his position or REVISE.
    This differs from refinement.txt where AIfred must always acknowledge
    Sokrates' critique.

    Args:
        critique: Sokrates' critique text
        user_interjection: Optional user interjection during debate
        lang: Language code (de/en), defaults to current language
        round_num: Current debate round (default 2, since defense starts at R2)

    Returns:
        Formatted defense prompt with critique and round number
    """
    return load_prompt(
        'aifred/defense',
        lang=lang,
        critique=critique,
        user_interjection=user_interjection,
        round_num=round_num
    )


# ============================================================
# Agent Direct & System Prompts (generic, works for all agents)
# ============================================================

def get_agent_direct_prompt(
    agent_id: str, lang: Optional[str] = None, memory: bool = True,
    user_name: Optional[str] = None, user_gender: Optional[str] = None,
    tools: bool = False, rag_context: Optional[str] = None,
) -> str:
    """Load direct response prompt for any agent via layer merging.

    Works for both default agents (aifred, sokrates, salomo) and custom agents.
    Loads {agent_id}/direct.txt as task prompt, merges with all applicable layers.
    """
    task_prompt = load_prompt(f'{agent_id}/direct', lang=lang)
    return _merge_prompt_layers(
        agent_id, task_prompt, lang, memory=memory,
        user_name=user_name, user_gender=user_gender,
        tools=tools, rag_context=rag_context,
    )


def get_salomo_mediator_prompt(round_num: int = 1, lang: Optional[str] = None) -> str:
    """
    Load Salomo Mediator prompt for Auto-Consensus mode (Trialog).

    Salomo synthesizes AIfred's answer and Sokrates' critique,
    and decides whether to give LGTM.

    Args:
        round_num: Current debate round (1, 2, 3, ...)
        lang: Language code (de/en), defaults to current language

    Returns:
        Salomo mediator system prompt with round number
    """
    return load_prompt('salomo/mediator', lang=lang, round_num=round_num)


def get_salomo_judge_prompt(lang: Optional[str] = None) -> str:
    """
    Load Salomo Judge prompt for Tribunal mode.

    Salomo delivers a final verdict after AIfred and Sokrates have debated.

    Args:
        lang: Language code (de/en), defaults to current language

    Returns:
        Salomo judge system prompt
    """
    return load_prompt('salomo/judge', lang=lang)


# ============================================================
# System Prompt Token Cache (v2.14.0+)
# ============================================================

def _estimate_tokens(text: str) -> int:
    """Estimate tokens from text (3.5 chars/token for German/mixed)."""
    return int(len(text) / 3.5) if text else 0


def init_system_prompt_cache() -> dict[str, dict[str, int]]:
    """
    Initialize cache with all system prompt token counts.

    Called once at application startup to pre-calculate all prompt sizes.
    This avoids repeated file reads and token estimations during runtime.

    Returns:
        dict: The populated cache {agent: {lang: tokens}}
    """
    global _system_prompt_token_cache
    from .logging_utils import log_message

    _system_prompt_token_cache = {}

    from .agent_config import load_agents_raw

    agents = load_agents_raw()
    for lang in ["de", "en"]:
        for agent_id in agents:
            try:
                prompt = get_agent_system_prompt(agent_id, "task", lang=lang, multi_agent=True)
                if agent_id not in _system_prompt_token_cache:
                    _system_prompt_token_cache[agent_id] = {}
                _system_prompt_token_cache[agent_id][lang] = _estimate_tokens(prompt)
            except Exception as e:
                log_message(f"⚠️ Failed to cache prompts for {agent_id}/{lang}: {e}")

    # Log cache summary
    for agent, langs in _system_prompt_token_cache.items():
        de_tokens = langs.get("de", 0)
        en_tokens = langs.get("en", 0)
        log_message(f"📊 Prompt token estimate: {agent} = {de_tokens} tok (de), {en_tokens} tok (en)")

    return _system_prompt_token_cache


def get_system_prompt_tokens(agent: str, lang: str = "de") -> int:
    """
    Get cached system prompt token count for an agent.

    Args:
        agent: Agent name ("aifred", "sokrates", "salomo")
        lang: Language code ("de" or "en")

    Returns:
        int: Cached token count, or 0 if not cached
    """
    if not _system_prompt_token_cache:
        # Cache not initialized - use fallback estimation
        return 0
    return _system_prompt_token_cache.get(agent, {}).get(lang, 0)


def get_max_system_prompt_tokens(multi_agent_mode: str = "standard", lang: str = "de") -> int:
    """
    Get the maximum system prompt tokens for the current mode.

    For compression checks, we need the worst-case (largest) prompt size
    across all agents that will be called.

    Args:
        multi_agent_mode: "standard", "critical_review", "auto_consensus", "tribunal", "devils_advocate"
        lang: Language code ("de" or "en")

    Returns:
        int: Maximum token count across relevant agents
    """
    if not _system_prompt_token_cache:
        return 0

    aifred_tokens = get_system_prompt_tokens("aifred", lang)

    if multi_agent_mode == "standard":
        return aifred_tokens

    sokrates_tokens = get_system_prompt_tokens("sokrates", lang)

    if multi_agent_mode in ["auto_consensus", "tribunal"]:
        salomo_tokens = get_system_prompt_tokens("salomo", lang)
        return max(aifred_tokens, sokrates_tokens, salomo_tokens)

    # critical_review, devils_advocate
    return max(aifred_tokens, sokrates_tokens)