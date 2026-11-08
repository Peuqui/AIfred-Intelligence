"""
Agent Configuration Module

Manages agent definitions (identity, prompts, toggles)
via a JSON configuration file at data/agents.json.

Each agent has:
- display_name, emoji, description, role
- prompts: mapping of prompt layer names to file paths (relative to prompts/{lang}/)
- toggles: default states for personality/reasoning/thinking
- enabled: switched on (an agent switched off vanishes from every choice, see selectable_agents)

Sampling parameters (top_k, top_p, etc.) are NOT stored here —
they come from the llama-swap YAML config per model at runtime.
"""

import json
import re
from collections.abc import Collection
from dataclasses import asdict, dataclass, field

from .config import DATA_DIR
from .user_error import Invalid, Missing, UserError

AGENTS_FILE = DATA_DIR / "agents.json"

# What an agent is: an agent a chat addresses, or a "system" agent (vision, calibration, future helpers)
# that backs an internal workflow and only exposes prompt + model in the editor. Who critiques and judges
# in the debate modes is no property of the agent: the session's seats (lib/debate_seats.py).
ROLE_AGENT = "agent"
ROLE_SYSTEM = "system"
VALID_ROLES = (ROLE_AGENT, ROLE_SYSTEM)

# The base agent: it names AIfred, is never deleted or switched off, and always has a model of its own
# (Peuqui 07.11.2026). Its display name can change, its id cannot.
BASE_AGENT = "aifred"


# An agent's id names its prompt folder (prompts/<lang>/<id>/); the template folder has a hyphen, so no id is it.
AGENT_ID_PATTERN = r"[a-z0-9_]+"
AGENT_TEMPLATE_DIR = "agent-template"


@dataclass
class AgentConfig:
    """Configuration for a single agent."""

    display_name: str
    emoji: str
    description: str
    role: str  # "agent" | "system" (VALID_ROLES)

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

    # Switched on: an agent switched off vanishes from every choice (selectable_agents); a turn, job or
    # channel that names it is refused out loud. Not written into agents.json until first switched.
    enabled: bool = True

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
            "role": "agent",
            "prompts": {
                "identity": "aifred/identity.txt",
                "personality": "aifred/personality.txt",
                "reminder": "aifred/reminder.txt",
                "task": "aifred/system_minimal.txt",
                "direct": "aifred/direct.txt",
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
            "role": "agent",
            "prompts": {
                "identity": "sokrates/identity.txt",
                "personality": "sokrates/personality.txt",
                "reminder": "sokrates/reminder.txt",
                "task": "sokrates/system_minimal.txt",
                "direct": "sokrates/direct.txt",
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
            "role": "agent",
            "prompts": {
                "identity": "salomo/identity.txt",
                "personality": "salomo/personality.txt",
                "reminder": "salomo/reminder.txt",
                "task": "salomo/system_minimal.txt",
                "direct": "salomo/direct.txt",
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
            "role": ROLE_SYSTEM,   # backs the image workflow, no chat partner (as every agents.json has it)
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


def config_from_raw(data: dict) -> AgentConfig:
    """The AgentConfig of an entry of agents.json; KeyError names a required field it lacks (display_name, emoji,
    description, role), Invalid a role other than VALID_ROLES."""
    raw_aliases = data.get("aliases") or []
    aliases = [str(a).strip().lower() for a in raw_aliases if isinstance(a, str) and a.strip()]
    if data["role"] not in VALID_ROLES:
        # The roles of the Reflex time (main, critic, judge, custom): scripts/migrate_agent_roles.py turns them into "agent".
        raise Invalid("agent_role_unknown", agent=data["display_name"], role=data["role"])
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
        enabled=data.get("enabled", True),
    )


def load_agents() -> dict[str, AgentConfig]:
    """Load agent configurations from data/agents.json (load_agents_raw reads the file)."""
    return {agent_id: config_from_raw(agent_data) for agent_id, agent_data in load_agents_raw().items()}


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


def selectable_agents() -> dict[str, AgentConfig]:
    """The agents a user, a debate seat, a symposion, a job or an address can choose: no system agent
    (vision, calibration) and none switched off. The one list for every choice."""
    return {agent_id: config for agent_id, config in load_agents().items()
            if config.role != ROLE_SYSTEM and config.enabled}


def set_agent_enabled(agent_id: str, enabled: bool) -> AgentConfig:
    """Switch an agent on or off. The base agent and the system agents are never switched off.

    Raises:
        Missing: the agent does not exist; Invalid: it is the base agent or a system agent
    """
    config = get_agent_config(agent_id)
    if config is None:
        raise Missing("unknown_agent", agent=agent_id)
    if not enabled and agent_id == BASE_AGENT:
        raise Invalid("base_agent_stays_on", agent=config.display_name)
    if config.role == ROLE_SYSTEM:
        raise Invalid("system_agent_no_switch", agent=config.display_name)
    return update_agent(agent_id, {"enabled": enabled})


def agent_display_name(agent_id: str) -> str:
    """The name an agent speaks and signs with; its id if it no longer exists."""
    agent = get_agent_config(agent_id)
    return agent.display_name if agent else agent_id


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
    (e.g. "Hey Alfred" → "aifred", "HAL 9000" → "hal"). Only selectable
    agents: one switched off, or a system agent, cannot be addressed.

    Returns the canonical agent id, or None if nothing matched.
    """
    if not name_or_alias:
        return None
    needle = name_or_alias.strip().lower()
    if not needle:
        return None
    agents = selectable_agents()
    if needle in agents:
        return needle
    for aid, config in agents.items():
        if needle == config.display_name.strip().lower() or needle in config.aliases:
            return aid
    return None


def create_agent(
    agent_id: str,
    display_name: str,
    emoji: str,
    description: str,
) -> AgentConfig:
    """Create a new agent and save to config.

    Also creates prompt template files for the agent.

    Args:
        agent_id: Unique identifier (lowercase, no spaces)
        display_name: Human-readable name
        emoji: Agent emoji character
        description: Short description

    Returns:
        The newly created AgentConfig

    Raises:
        Invalid: If agent_id already exists or is no valid id
    """
    if not re.fullmatch(AGENT_ID_PATTERN, agent_id):
        # The id names the agent's prompt folder: nothing outside prompts/<lang>/, never the template.
        raise Invalid("agent_id_format", agent=agent_id)

    agents = load_agents_raw()
    if agent_id in agents:
        raise Invalid("agent_exists", agent=agent_id)
    if prompt_folder_taken(agent_id, agents):
        raise Invalid("agent_id_taken", agent=agent_id)

    # Build prompt paths for the new agent
    prompts = {
        "identity": f"{agent_id}/identity.txt",
        "personality": f"{agent_id}/personality.txt",
        "task": f"{agent_id}/system_minimal.txt",
        "direct": f"{agent_id}/direct.txt",
        "memory_context": f"{agent_id}/memory_context.txt",
    }

    config = AgentConfig(
        display_name=display_name,
        emoji=emoji,
        description=description,
        role=ROLE_AGENT,
        prompts=prompts,
    )

    # Create prompt template files
    _create_prompt_files(agent_id, display_name)

    # Save to config
    agents[agent_id] = config.to_dict()
    save_agents_raw(agents)

    return config


def deletion_refusal(agent_id: str) -> UserError | None:
    """Why the agent cannot be deleted, None when it can. The base agent never is, nor a system agent
    (it backs an internal workflow), nor one that sits on a seat of the settings' default (a new chat would start
    every debate with an agent that is gone). Since 08.11.2026 Sokrates and Salomo can be deleted like any other."""
    from .debate_seats import default_seats
    from .i18n import t
    from .prompt_loader import get_language

    config = get_agent_config(agent_id)
    if config is None:
        return Missing("unknown_agent", agent=agent_id)
    if agent_id == BASE_AGENT:
        return Invalid("base_agent_stays", agent=config.display_name)
    if config.role == ROLE_SYSTEM:
        return Invalid("system_agent_stays", agent=config.display_name)
    for seat, holder in default_seats().items():
        if holder == agent_id:
            return Invalid("agent_on_default_seat", agent=config.display_name, seat=t(f"debate_seat_{seat}", lang=get_language()))
    return None


def delete_agent(agent_id: str) -> None:
    """Delete an agent from config and remove prompt files from disk.

    Raises:
        UserError: the agent cannot be deleted (deletion_refusal says why)
    """
    import shutil

    from .logging_utils import log_message
    from .prompt_loader import PROMPTS_DIR, prompt_languages

    refusal = deletion_refusal(agent_id)
    if refusal is not None:
        raise refusal
    agents = load_agents_raw()
    del agents[agent_id]
    save_agents_raw(agents)

    # Only its own folder: a prompt layer may point elsewhere (Vision's personality is AIfred's file); until
    # 08.11.2026 every folder a layer named went, an imported agent renamed on a conflict took the original's.
    for lang in prompt_languages():
        folder = PROMPTS_DIR / lang / agent_id
        if folder.is_dir():
            shutil.rmtree(folder)
            log_message(f"🗑️ Deleted prompt directory: {folder}")


def prompt_folder_taken(agent_id: str, agents: Collection[str]) -> bool:
    """A folder ``prompts/<lang>/<agent_id>`` exists that belongs to none of ``agents`` (the ids there are: shared,
    utility, debate, the Automatik's …). An agent owns the folder named by its id: one of that id would write its
    prompts into the other folder, and deleting it would take that folder with it."""
    from .prompt_loader import PROMPTS_DIR, prompt_languages

    return agent_id not in agents and any((PROMPTS_DIR / lang / agent_id).exists() for lang in prompt_languages())


def update_agent(agent_id: str, updates: dict) -> AgentConfig:
    """Update an agent's configuration.

    Args:
        agent_id: Agent to update
        updates: Dict with fields to update (shallow merge)

    Returns:
        Updated AgentConfig

    Raises:
        Missing: If agent doesn't exist
    """
    agents = load_agents_raw()
    if agent_id not in agents:
        raise Missing("unknown_agent", agent=agent_id)

    agents[agent_id].update(updates)
    save_agents_raw(agents)

    return config_from_raw(agents[agent_id])


def _create_prompt_files(agent_id: str, display_name: str) -> None:
    """The prompt files of a new agent in every language, existing ones are kept: the base prompts from
    ``prompts/<lang>/agent-template/`` (its name filled in). The tasks of the debate seats belong to
    the seats (prompts/<lang>/debate/), not to an agent."""
    from .prompt_loader import PROMPTS_DIR

    for lang in ("de", "en"):
        agent_dir = PROMPTS_DIR / lang / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        files = {
            path.name: path.read_text(encoding="utf-8").replace("{display_name}", display_name)
            for path in (PROMPTS_DIR / lang / AGENT_TEMPLATE_DIR).glob("*.txt")
        }
        for filename, content in files.items():
            filepath = agent_dir / filename
            if not filepath.exists():
                filepath.write_text(content, encoding="utf-8")
