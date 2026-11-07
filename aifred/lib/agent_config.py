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
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import DATA_DIR

AGENTS_FILE = DATA_DIR / "agents.json"

# Valid agent roles
# "system" agents (calibration, future helpers) are not selectable as
# chat partners — they back internal workflows and only expose
# prompt + model in the editor.
# main, critic and judge are the parts of the multi-agent modes (lib/turn_debate.py): the agent
# that holds a role speaks it, with the role's prompts from its own prompt mapping.
ROLE_MAIN = "main"
ROLE_CRITIC = "critic"
ROLE_JUDGE = "judge"
ROLE_SYSTEM = "system"
VALID_ROLES = (ROLE_MAIN, ROLE_CRITIC, ROLE_JUDGE, "custom", ROLE_SYSTEM)


# An agent's id names its prompt folder (prompts/<lang>/<id>/); the template folder has a hyphen, so no id is it.
AGENT_ID_PATTERN = r"[a-z0-9_]+"
AGENT_TEMPLATE_DIR = "agent-template"


class AgentRoleError(LookupError):
    """No agent holds a role, or the agent of a role lacks a prompt the role needs."""


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
    tools: list[str] | None = None

    # Cloud-API provider + model identifier — only meaningful for system
    # agents that call out to a Cloud LLM (e.g. the calibration agent).
    # Both resolve through the shared CLOUD_API_PROVIDERS SSOT; provider
    # defaults to "qwen" (DashScope). Empty model means "not configured".
    cloud_provider: str = "qwen"
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
                "task": "vision/task_adaptive.txt",
                "memory_context": "vision/memory_context.txt",
            },
            "toggles": {
                "personality": True,
                "reasoning": False,
                "thinking": False,
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
        cloud_provider=data.get("cloud_provider", "qwen"),
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


def get_agent_config(agent_id: str) -> AgentConfig | None:
    """Get config for a specific agent, or None if not found."""
    agents = load_agents()
    return agents.get(agent_id)


def agent_display_name(agent_id: str) -> str:
    """The name an agent speaks and signs with; its id if it no longer exists."""
    agent = get_agent_config(agent_id)
    return agent.display_name if agent else agent_id


def agent_for_role(role: str) -> str:
    """The agent that speaks ``role`` (main, critic, judge): the first one in agents.json with it."""
    for agent_id, config in load_agents().items():
        if config.role == role:
            return agent_id
    raise AgentRoleError(f"No agent has the role '{role}' (data/agents.json)")


def role_names() -> dict[str, str]:
    """The display names of the agents in the debate roles, as the role prompts name them:
    ``{main_agent}``, ``{critic_agent}``, ``{judge_agent}``. Any agent can hold a role, so no prompt
    names one itself."""
    agents = load_agents()
    return {f"{role}_agent": agents[agent_for_role(role)].display_name for role in (ROLE_MAIN, ROLE_CRITIC, ROLE_JUDGE)}


def role_prompt_name(agent_id: str, key: str) -> str:
    """The agent's prompt ``key`` as load_prompt names it: its path in the agent's prompt mapping
    without ``.txt``."""
    config = get_agent_config(agent_id)
    if config is None:
        raise AgentRoleError(f"Unknown agent: {agent_id}")
    path = config.prompts.get(key)
    if path is None:
        raise AgentRoleError(
            f"Agent '{agent_id}' (role {config.role}) has no prompt '{key}' in data/agents.json"
        )
    return path.removesuffix(".txt")


def role_prompt(agent_id: str, key: str, lang: str, **variables: Any) -> str:
    """The agent's prompt ``key`` (e.g. "critic", "refinement") in ``lang``, its placeholders filled:
    the names of the roles (``role_names``) and ``variables``."""
    from .prompt_loader import load_prompt

    return load_prompt(role_prompt_name(agent_id, key), lang=lang, **role_names(), **variables)


# Generic fallback used when an agent has no per-engine voice default.
# Empty voice means "let the TTS engine pick its own default"; no speed/pitch
# means "not set" (nothing is sent, the service's tuning of the voice applies).
_GENERIC_TTS_VOICE_DEFAULT: dict[str, str | bool] = {
    "voice": "",
    "enabled": True,
}


def get_tts_voice_default(agent_id: str, engine: str) -> dict[str, str | bool]:
    """Return the default TTS voice config for ``(agent_id, engine)``.

    Reads from the ``tts_voices.<engine>`` block on the agent's entry in
    ``data/agents.json``. Agents without a ``tts_voices`` block, or with
    no entry for the requested engine, get the generic fallback (empty
    voice → engine decides). Caller may further override via user
    settings.
    """
    agents = load_agents_raw()
    cfg = agents.get(agent_id) or {}
    voice = cfg.get("tts_voices", {}).get(engine)
    if isinstance(voice, dict):
        return dict(voice)
    return dict(_GENERIC_TTS_VOICE_DEFAULT)


def get_tts_voice_defaults_for_engine(engine: str) -> dict[str, dict[str, str | bool]]:
    """Return all per-agent voice defaults for a TTS engine.

    Skips agents that have no entry for this engine. Mirrors the
    per-engine sub-dict shape of the old ``TTS_AGENT_VOICE_DEFAULTS``,
    so callers iterating ``defaults.get(agent_id)`` keep working.
    """
    result: dict[str, dict[str, str | bool]] = {}
    for aid, cfg in load_agents_raw().items():
        voice = cfg.get("tts_voices", {}).get(engine) if isinstance(cfg, dict) else None
        if isinstance(voice, dict):
            result[aid] = dict(voice)
    return result


def resolve_agent_id(name_or_alias: str) -> str | None:
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
    if not re.fullmatch(AGENT_ID_PATTERN, agent_id):
        # The id names the agent's prompt folder: nothing outside prompts/<lang>/, never the template.
        raise ValueError(f"Invalid agent id '{agent_id}': lowercase letters, digits and _ only")

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

    # The prompts of its debate role (turn_debate._DEBATE_ROLES): every mode it can speak in
    from .turn_debate import role_prompt_keys

    for key in role_prompt_keys(role):
        prompts[key] = f"{agent_id}/{key}.txt"

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

    from .agent_settings import CANONICAL_AGENTS
    from .prompt_loader import PROMPTS_DIR
    if agent_id in CANONICAL_AGENTS:
        raise ValueError(f"Cannot delete default agent '{agent_id}'")

    agents = load_agents_raw()
    if agent_id not in agents:
        raise ValueError(f"Agent '{agent_id}' not found")

    # Get prompt directory name from config before deleting
    config = agents[agent_id]
    prompt_dirs: set[str] = set()
    for prompt_path in config.get("prompts", {}).values():
        # prompt_path is like "<agent_id>/identity.txt" → first segment is the dir
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
    """The prompt files of a new agent in every language, existing ones are kept: the base prompts from
    ``prompts/<lang>/agent-template/`` (its name filled in), the prompts of its debate role copied from
    the agent that holds the role now (they name the roles' agents, never one of their own)."""
    from .prompt_loader import PROMPTS_DIR
    from .turn_debate import role_prompt_keys

    holder = agent_for_role(role) if role_prompt_keys(role) else None
    for lang in ("de", "en"):
        agent_dir = PROMPTS_DIR / lang / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        files = {
            path.name: path.read_text(encoding="utf-8").replace("{display_name}", display_name)
            for path in (PROMPTS_DIR / lang / AGENT_TEMPLATE_DIR).glob("*.txt")
        }
        if holder is not None:
            for key in role_prompt_keys(role):
                files[f"{key}.txt"] = (PROMPTS_DIR / lang / f"{role_prompt_name(holder, key)}.txt").read_text(encoding="utf-8")
        for filename, content in files.items():
            filepath = agent_dir / filename
            if not filepath.exists():
                filepath.write_text(content, encoding="utf-8")
