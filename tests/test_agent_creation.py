"""A new agent's prompts (agent_config.create_agent): the base prompts from prompts/<lang>/agent-template/
with its name. It can take any debate seat: the tasks belong to the seats (prompts/<lang>/debate/), it
needs no copy of its own (07.11.2026: before, the prompts of a role were copied from its holder).

Until 07.11.2026 the templates were texts in the code: a new critic got critic.txt but no tribunal.txt
(the tribunal refused it), a new agent no memory_context.txt (format_memory_context then returned
nothing: the agent never saw its memories), and an id like "../x" wrote outside prompts/."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from aifred.lib import agent_config, prompt_loader
from aifred.lib.agent_config import create_agent
from aifred.lib.debate_seats import SEAT_CRITIC, SEAT_JUDGE, SEAT_MAIN, check_seat_agent, seat_prompt

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


@pytest.fixture
def prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A prompt tree with the template, the seats' tasks and the default agents' files; agents.json with them."""
    root = tmp_path / "prompts"
    for lang in ("de", "en"):
        shutil.copytree(PROMPTS / lang / agent_config.AGENT_TEMPLATE_DIR, root / lang / agent_config.AGENT_TEMPLATE_DIR)
        shutil.copytree(PROMPTS / lang / "debate", root / lang / "debate")
        for agent in ("aifred", "sokrates", "salomo"):
            shutil.copytree(PROMPTS / lang / agent, root / lang / agent)
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", root)
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps({"agents": agent_config._default_agents()}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    return root


def test_a_new_agent_gets_the_base_prompts_with_its_name(prompts: Path) -> None:
    config = create_agent("pater", "Pater Tuck", "⛪", "Ein Mönch", "#7d8590")
    assert set(config.prompts) == {"identity", "personality", "task", "direct", "memory_context"}
    assert (prompts / "de" / "pater" / "identity.txt").read_text(encoding="utf-8").strip() == "Du bist Pater Tuck."
    assert (prompts / "en" / "pater" / "identity.txt").read_text(encoding="utf-8").strip() == "You are Pater Tuck."
    assert "{memories}" in (prompts / "de" / "pater" / "memory_context.txt").read_text(encoding="utf-8")
    assert "präzise" in (prompts / "de" / "pater" / "system_minimal.txt").read_text(encoding="utf-8")


def test_a_new_agent_takes_any_seat_with_the_seats_task(prompts: Path) -> None:
    config = create_agent("neu", "Neu", "🆕", "", "#7d8590")
    for seat in (SEAT_CRITIC, SEAT_JUDGE):
        check_seat_agent(seat, "neu")
    assert config.role == "agent"
    assert sorted(path.name for path in (prompts / "de" / "neu").iterdir()) == sorted(
        path.name for path in (prompts / "de" / agent_config.AGENT_TEMPLATE_DIR).iterdir()
    )                                                              # no copy of any seat's task
    seats = {SEAT_MAIN: "aifred", SEAT_CRITIC: "neu", SEAT_JUDGE: "salomo"}
    assert "Neu" in seat_prompt("refinement", seats, "de", round_num=2)


@pytest.mark.parametrize("agent_id", ["../draussen", "agent-template", "Groß", ""])
def test_an_id_that_is_no_prompt_folder_name_is_refused(prompts: Path, agent_id: str) -> None:
    before = sorted(path.relative_to(prompts.parent) for path in prompts.parent.rglob("*"))
    with pytest.raises(ValueError):
        create_agent(agent_id, "X", "❓", "", "#7d8590")
    assert sorted(path.relative_to(prompts.parent) for path in prompts.parent.rglob("*")) == before
