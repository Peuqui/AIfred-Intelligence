"""
Agent Configuration Module

Manages agent definitions (identity, prompts, toggles)
via a JSON configuration file at data/agents.json.

Each agent has:
- display_name, emoji, description, role
- prompts: mapping of prompt layer names to file paths (relative to prompts/{lang}/)
- toggles: default states for personality/reasoning/thinking

Sampling parameters (top_k, top_p, etc.) are NOT stored here —
they come from the llama-swap YAML config per model at runtime.
"""

import json
from dataclasses import dataclass, field, asdict
from typing import Optional

from .config import DATA_DIR

AGENTS_FILE = DATA_DIR / "agents.json"

# Valid agent roles
# "system" agents (calibration, future helpers) are not selectable as
# chat partners — they back internal workflows and only expose
# prompt + model in the editor.
VALID_ROLES = ("main", "critic", "judge", "custom", "system")


@dataclass
class AgentConfig:
    """Configuration for a single agent."""

    display_name: str
    emoji: str
    description: str
    role: str  # "main" | "critic" | "judge" | "custom" | "system"

    # Prompt file paths relative to prompts/{lang}/
    # Keys: "identity", "personality", "task", "reminder", + role-specific
    prompts: dict[str, str] = field(default_factory=dict)

    # Default toggle states
    toggles: dict[str, bool] = field(default_factory=lambda: {
        "personality": True,
        "reasoning": False,
        "thinking": True,
    })

    # Tool whitelist — None means all tools allowed
    tools: Optional[list[str]] = None

    # Cloud-API model identifier — only meaningful for system agents that
    # call out to a Cloud LLM (e.g. calibration agent uses Qwen).
    # Empty/None means "use the workflow's default".
    model: str = ""

    # STT phonetic aliases — names the agent should also respond to when
    # detected by Whisper/Vosk/etc. Always lowercase, no leading/trailing
    # whitespace. Example for "AIfred": ["alfred", "ai fred", "eifred"].
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _default_agents() -> dict[str, dict]:
    """Return the 3 default agents as raw dicts."""
    return {
        "aifred": {
            "display_name": "AIfred",
            "aliases": ["alfred", "ai fred", "eifred", "ai-fred"],
            "emoji": "\U0001f3a9",
            "description": "Gentleman-Berater und KI-Butler",
            "role": "main",
            "prompts": {
                "identity": "aifred/identity.txt",
                "personality": "aifred/personality.txt",
                "reminder": "aifred/reminder.txt",
                "task": "aifred/system_minimal.txt",
                "direct": "aifred/direct.txt",
                "refinement": "aifred/refinement.txt",
                "defense": "aifred/defense.txt",
                "rag": "aifred/system_rag.txt",
            },
            "toggles": {
                "personality": True,
                "reasoning": False,
                "thinking": True,
            },
        },
        "sokrates": {
            "display_name": "Sokrates",
            "aliases": ["socrates"],
            "emoji": "\U0001f3db\ufe0f",
            "description": "Scharfsinniger Philosoph und Kritiker",
            "role": "critic",
            "prompts": {
                "identity": "sokrates/identity.txt",
                "personality": "sokrates/personality.txt",
                "reminder": "sokrates/reminder.txt",
                "task": "sokrates/system_minimal.txt",
                "direct": "sokrates/direct.txt",
                "critic": "sokrates/critic.txt",
                "tribunal": "sokrates/tribunal.txt",
            },
            "toggles": {
                "personality": True,
                "reasoning": False,
                "thinking": True,
            },
        },
        "salomo": {
            "display_name": "Salomo",
            "aliases": ["salomon", "solomon"],
            "emoji": "\U0001f451",
            "description": "Weiser Richter und Synthesist",
            "role": "judge",
            "prompts": {
                "identity": "salomo/identity.txt",
                "personality": "salomo/personality.txt",
                "reminder": "salomo/reminder.txt",
                "task": "salomo/system_minimal.txt",
                "direct": "salomo/direct.txt",
                "mediator": "salomo/mediator.txt",
                "judge": "salomo/judge.txt",
            },
            "toggles": {
                "personality": True,
                "reasoning": False,
                "thinking": True,
            },
        },
        "vision": {
            "display_name": "Vision",
            "emoji": "\U0001f4f7",
            "description": "Vision Language Model fuer Bildanalyse",
            "role": "main",
            "prompts": {
                "identity": "vision/identity.txt",
                "personality": "aifred/personality.txt",
                "task": "vision/vision_ocr.txt",
                "task_qa": "vision/vision_qa.txt",
                "memory_context": "vision/memory_context.txt",
            },
            "toggles": {
                "personality": True,
                "reasoning": False,
                "thinking": True,
            },
        },
    }


def _dict_to_config(data: dict) -> AgentConfig:
    """Convert a raw dict to an AgentConfig instance."""
    raw_aliases = data.get("aliases") or []
    aliases = [str(a).strip().lower() for a in raw_aliases if isinstance(a, str) and a.strip()]
    return AgentConfig(
        display_name=data["display_name"],
        emoji=data["emoji"],
        description=data["description"],
        role=data["role"],
        prompts=data.get("prompts", {}),
        toggles=data.get("toggles", {}),
        tools=data.get("tools"),
        model=data.get("model", ""),
        aliases=aliases,
    )


def load_agents() -> dict[str, AgentConfig]:
    """Load agent configurations from data/agents.json.

    If the file doesn't exist, creates it with default agents.
    """
    if not AGENTS_FILE.exists():
        save_agents_raw(_default_agents())

    with open(AGENTS_FILE, "r", encoding="utf-8") as f:
        raw: dict = json.load(f)

    agents_data = raw.get("agents", raw)
    return {
        agent_id: _dict_to_config(agent_data)
        for agent_id, agent_data in agents_data.items()
    }


def load_agents_raw() -> dict[str, dict]:
    """Load agent configurations as raw dicts (for UI serialization)."""
    if not AGENTS_FILE.exists():
        save_agents_raw(_default_agents())

    with open(AGENTS_FILE, "r", encoding="utf-8") as f:
        raw: dict = json.load(f)

    agents_data: dict[str, dict] = raw.get("agents", raw)
    return agents_data


def save_agents_raw(agents: dict[str, dict]) -> None:
    """Save agent configurations from raw dicts."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(AGENTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"agents": agents}, f, indent=2, ensure_ascii=False)


def save_agents(agents: dict[str, AgentConfig]) -> None:
    """Save agent configurations to data/agents.json."""
    raw = {agent_id: config.to_dict() for agent_id, config in agents.items()}
    save_agents_raw(raw)


def get_agent_ids() -> list[str]:
    """Return list of configured agent IDs."""
    agents = load_agents_raw()
    return list(agents.keys())


def get_agent_emoji(agent_id: str) -> str:
    """Get the emoji for an agent, or a generic fallback."""
    agents = load_agents_raw()
    agent = agents.get(agent_id)
    if agent:
        return str(agent.get("emoji", "\U0001f916"))
    return "\U0001f916"


def get_agent_label(agent_id: str) -> str:
    """Get 'emoji display_name' for an agent (for debug messages etc.)."""
    agents = load_agents_raw()
    agent = agents.get(agent_id)
    if agent:
        return f"{agent.get('emoji', '\U0001f916')} {agent.get('display_name', agent_id.capitalize())}"
    return agent_id.capitalize()


def get_agent_config(agent_id: str) -> Optional[AgentConfig]:
    """Get config for a specific agent, or None if not found."""
    agents = load_agents()
    return agents.get(agent_id)


def resolve_agent_id(name_or_alias: str) -> Optional[str]:
    """Map a user-supplied name to a canonical agent id.

    Tries (in order):
      1. exact match against agent ID (lowercase)
      2. exact match against display_name (case-insensitive)
      3. exact match against any alias (case-insensitive)

    Used by the intent-detection parser, the mode-switch parser, and any
    other code path that needs to resolve a noisy STT-transcribed name
    (e.g. "Hey Alfred" → "aifred", "HAL 9000" → "hal").

    Returns the canonical agent id, or None if nothing matched.
    """
    if not name_or_alias:
        return None
    needle = name_or_alias.strip().lower()
    if not needle:
        return None
    agents = load_agents_raw()
    if needle in agents:
        return needle
    for aid, adata in agents.items():
        if needle == str(adata.get("display_name", "")).strip().lower():
            return aid
        for alias in adata.get("aliases") or []:
            if isinstance(alias, str) and needle == alias.strip().lower():
                return aid
    return None


def create_agent(
    agent_id: str,
    display_name: str,
    emoji: str,
    description: str,
    role: str = "custom",
) -> AgentConfig:
    """Create a new agent and save to config.

    Also creates prompt template files for the agent.

    Args:
        agent_id: Unique identifier (lowercase, no spaces)
        display_name: Human-readable name
        emoji: Agent emoji character
        description: Short description
        role: Agent role ("main", "critic", "judge", "custom")

    Returns:
        The newly created AgentConfig

    Raises:
        ValueError: If agent_id already exists or role is invalid
    """
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of {VALID_ROLES}")

    agents = load_agents_raw()
    if agent_id in agents:
        raise ValueError(f"Agent '{agent_id}' already exists")

    # Build prompt paths for the new agent
    prompts = {
        "identity": f"{agent_id}/identity.txt",
        "personality": f"{agent_id}/personality.txt",
        "task": f"{agent_id}/system_minimal.txt",
        "direct": f"{agent_id}/direct.txt",
        "memory_context": f"{agent_id}/memory_context.txt",
    }

    # Add role-specific prompts
    if role == "critic":
        prompts["critic"] = f"{agent_id}/critic.txt"
    elif role == "judge":
        prompts["mediator"] = f"{agent_id}/mediator.txt"
        prompts["judge"] = f"{agent_id}/judge.txt"

    config = AgentConfig(
        display_name=display_name,
        emoji=emoji,
        description=description,
        role=role,
        prompts=prompts,
    )

    # Create prompt template files
    _create_prompt_files(agent_id, display_name, role)

    # Save to config
    agents[agent_id] = config.to_dict()
    save_agents_raw(agents)

    return config


def delete_agent(agent_id: str) -> None:
    """Delete an agent from config and remove prompt files from disk.

    Raises:
        ValueError: If agent is a default agent or doesn't exist
    """
    import shutil
    from .prompt_loader import PROMPTS_DIR

    if agent_id in ("aifred", "sokrates", "salomo", "vision"):
        raise ValueError(f"Cannot delete default agent '{agent_id}'")

    agents = load_agents_raw()
    if agent_id not in agents:
        raise ValueError(f"Agent '{agent_id}' not found")

    # Get prompt directory name from config before deleting
    config = agents[agent_id]
    prompt_dirs: set[str] = set()
    for prompt_path in config.get("prompts", {}).values():
        # prompt_path is like "codi/identity.txt" → directory is "codi"
        parts = prompt_path.split("/")
        if len(parts) >= 2:
            prompt_dirs.add(parts[0])

    del agents[agent_id]
    save_agents_raw(agents)

    # Remove prompt files from disk (both languages)
    for lang in ("de", "en"):
        for prompt_dir in prompt_dirs:
            full_path = PROMPTS_DIR / lang / prompt_dir
            if full_path.exists() and full_path.is_dir():
                shutil.rmtree(full_path)
                from .logging_utils import log_message
                log_message(f"🗑️ Deleted prompt directory: {full_path}")


def update_agent(agent_id: str, updates: dict) -> AgentConfig:
    """Update an agent's configuration.

    Args:
        agent_id: Agent to update
        updates: Dict with fields to update (shallow merge)

    Returns:
        Updated AgentConfig

    Raises:
        ValueError: If agent doesn't exist
    """
    agents = load_agents_raw()
    if agent_id not in agents:
        raise ValueError(f"Agent '{agent_id}' not found")

    agents[agent_id].update(updates)
    save_agents_raw(agents)

    return _dict_to_config(agents[agent_id])


def _create_prompt_files(agent_id: str, display_name: str, role: str) -> None:
    """Create template prompt files for a new agent in both languages."""
    from .config import PROJECT_ROOT

    prompts_dir = PROJECT_ROOT / "prompts"

    # Role-specific templates
    role_templates = _get_role_templates(display_name, role)

    for lang in ("de", "en"):
        agent_dir = prompts_dir / lang / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)

        templates = role_templates.get(lang, role_templates.get("de", {}))

        for filename, content in templates.items():
            filepath = agent_dir / filename
            if not filepath.exists():
                filepath.write_text(content, encoding="utf-8")


def _get_role_templates(display_name: str, role: str) -> dict[str, dict[str, str]]:
    """Get prompt templates for a given role.

    Returns:
        Dict mapping lang -> {filename: content}
    """
    templates: dict[str, dict[str, str]] = {
        "de": {
            "identity.txt": f"Du bist {display_name}.",
            "personality.txt": "",
            "system_minimal.txt": (
                "Beantworte die Frage des Benutzers praezise und hilfreich.\n"
                "Heute ist {current_weekday}, der {current_date}, {current_time} Uhr.\n"
                "Das aktuelle Jahr ist {current_year}."
            ),
            "direct.txt": (
                "Beantworte die Frage des Benutzers direkt und hilfreich.\n"
                "Heute ist {current_weekday}, der {current_date}, {current_time} Uhr."
            ),
        },
        "en": {
            "identity.txt": f"You are {display_name}.",
            "personality.txt": "",
            "system_minimal.txt": (
                "Answer the user's question precisely and helpfully.\n"
                "Today is {current_weekday}, {current_date}, {current_time}.\n"
                "The current year is {current_year}."
            ),
            "direct.txt": (
                "Answer the user's question directly and helpfully.\n"
                "Today is {current_weekday}, {current_date}, {current_time}."
            ),
        },
    }

    # Add role-specific templates
    if role == "critic":
        templates["de"]["critic.txt"] = (
            "Analysiere die Antwort kritisch. Finde Schwaechen, Luecken "
            "und moegliche Verbesserungen. Runde {round_num}."
        )
        templates["en"]["critic.txt"] = (
            "Critically analyze the response. Find weaknesses, gaps "
            "and possible improvements. Round {round_num}."
        )
    elif role == "judge":
        templates["de"]["mediator.txt"] = (
            "Synthetisiere die verschiedenen Perspektiven zu einem "
            "ausgewogenen Urteil. Runde {round_num}."
        )
        templates["de"]["judge.txt"] = (
            "Fasse die Debatte zusammen und sprich ein abschliessendes Urteil."
        )
        templates["en"]["mediator.txt"] = (
            "Synthesize the different perspectives into a balanced "
            "judgment. Round {round_num}."
        )
        templates["en"]["judge.txt"] = (
            "Summarize the debate and deliver a final verdict."
        )

    return templates
