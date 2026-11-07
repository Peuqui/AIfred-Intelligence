"""The prompts of the debate roles name the agents in the roles, not AIfred, Sokrates and Salomo:
any agent can hold a role (docs/de/architecture/vue-migration/README.md, Rollentausch)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aifred.lib import agent_config, prompt_loader
from aifred.lib.agent_config import role_names, role_prompt

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
# The prompts of the roles, by the agent that ships them, and the shared description of the roles.
ROLE_PROMPTS = {"aifred": ("refinement", "defense"), "sokrates": ("critic", "tribunal"), "salomo": ("mediator", "judge")}
VARIABLES = {"round_num": 2, "critique": "Die Kritik.", "user_interjection": ""}


@pytest.fixture(autouse=True)
def agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    raw = agent_config._default_agents()
    raw["pater"] = {**raw["sokrates"], "display_name": "Pater Tuck", "aliases": [], "role": "custom"}
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps({"agents": raw}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    monkeypatch.setattr(prompt_loader, "get_user_name", lambda: "Peuqui")
    return raw


def _move_role(agents_file: Path, raw: dict[str, dict], role: str, to: str) -> None:
    for config in raw.values():
        if config.get("role") == role:
            config["role"] = "custom"
    raw[to]["role"] = role
    agents_file.write_text(json.dumps({"agents": raw}), encoding="utf-8")


@pytest.mark.parametrize("lang", ["de", "en"])
def test_every_role_prompt_loads_with_the_names_of_the_roles(lang: str) -> None:
    for agent, keys in ROLE_PROMPTS.items():
        for key in keys:
            text = role_prompt(agent, key, lang, **VARIABLES)
            assert "{" not in text, (agent, key)
    roles = prompt_loader.load_multi_agent_roles(lang)
    assert all(f"[{name}]:" in roles for name in ("AIfred", "Sokrates", "Salomo"))


def test_another_agent_in_a_role_is_named_by_the_prompts(agents: dict[str, dict]) -> None:
    _move_role(agent_config.AGENTS_FILE, agents, "critic", "pater")
    assert role_names() == {"main_agent": "AIfred", "critic_agent": "Pater Tuck", "judge_agent": "Salomo"}
    refinement = role_prompt("aifred", "refinement", "de", **VARIABLES)
    assert "Pater Tuck hat deine These kritisiert" in refinement and "Sokrates" not in refinement
    assert "Pater Tuck" in prompt_loader.get_salomo_judge_prompt(lang="de")   # the Reflex path too


@pytest.mark.parametrize("lang", ["de", "en"])
def test_no_role_prompt_names_an_agent_itself(lang: str) -> None:
    files = [PROMPTS / lang / agent / f"{key}.txt" for agent, keys in ROLE_PROMPTS.items() for key in keys]
    for path in [*files, PROMPTS / lang / "shared" / "multi_agent_roles.txt"]:
        text = path.read_text(encoding="utf-8")
        named = [name for name in ("AIfred", "Sokrates", "Salomo", "AIFRED", "SOKRATES", "SALOMO") if name in text]
        assert named == [], path
