"""A new agent's prompts (agent_config.create_agent): the base prompts from prompts/<lang>/agent-template/
with its name, and every prompt of its debate role, copied from the agent that holds the role.

Until 07.11.2026 the templates were texts in the code: a new critic got critic.txt but no tribunal.txt
(the tribunal refused it), a new agent no memory_context.txt (format_memory_context then returned
nothing: the agent never saw its memories), and an id like "../x" wrote outside prompts/."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from aifred.lib import agent_config, prompt_loader
from aifred.lib.agent_config import create_agent, role_prompt_name
from aifred.lib.turn_debate import _DEBATE_ROLES, role_prompt_keys

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


@pytest.fixture
def prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A prompt tree with the template and the role holders' files; agents.json with the default agents."""
    root = tmp_path / "prompts"
    for lang in ("de", "en"):
        shutil.copytree(PROMPTS / lang / agent_config.AGENT_TEMPLATE_DIR, root / lang / agent_config.AGENT_TEMPLATE_DIR)
        for agent in ("aifred", "sokrates", "salomo"):
            shutil.copytree(PROMPTS / lang / agent, root / lang / agent)
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", root)
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps({"agents": agent_config._default_agents()}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    return root


def test_a_new_agent_gets_the_base_prompts_with_its_name(prompts: Path) -> None:
    config = create_agent("pater", "Pater Tuck", "⛪", "Ein Mönch")
    assert set(config.prompts) == {"identity", "personality", "task", "direct", "memory_context"}
    assert (prompts / "de" / "pater" / "identity.txt").read_text(encoding="utf-8").strip() == "Du bist Pater Tuck."
    assert (prompts / "en" / "pater" / "identity.txt").read_text(encoding="utf-8").strip() == "You are Pater Tuck."
    assert "{memories}" in (prompts / "de" / "pater" / "memory_context.txt").read_text(encoding="utf-8")
    assert "präzise" in (prompts / "de" / "pater" / "system_minimal.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("role", ["main", "critic", "judge"])
def test_a_new_agent_in_a_role_has_every_prompt_its_role_needs_in_every_mode(prompts: Path, role: str) -> None:
    holder = agent_config.agent_for_role(role)
    create_agent("neu", "Neu", "🆕", "", role=role)
    for roles in _DEBATE_ROLES.values():
        for key in roles.get(role, ()):
            role_prompt_name("neu", key)                         # the tribunal refused a critic without "tribunal"
    for key in role_prompt_keys(role):
        for lang in ("de", "en"):
            copied = (prompts / lang / "neu" / f"{key}.txt").read_text(encoding="utf-8")
            assert copied == (prompts / lang / f"{role_prompt_name(holder, key)}.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("agent_id", ["../draussen", "agent-template", "Groß", ""])
def test_an_id_that_is_no_prompt_folder_name_is_refused(prompts: Path, agent_id: str) -> None:
    before = sorted(path.relative_to(prompts.parent) for path in prompts.parent.rglob("*"))
    with pytest.raises(ValueError):
        create_agent(agent_id, "X", "❓", "")
    assert sorted(path.relative_to(prompts.parent) for path in prompts.parent.rglob("*")) == before
