"""The tasks of the debate seats (prompts/<lang>/debate/, lib/debate_seats.py) name the agents in the seats,
never AIfred, Sokrates or Salomo themselves: any agent can take a seat. 07.11.2026: critic and judge were
tags on the agents, the tasks copies in the holders' prompt folders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aifred.lib import agent_config, prompt_loader
from aifred.lib.debate_seats import SEAT_CRITIC, SEAT_JUDGE, SEAT_MAIN, seat_names, seat_prompt

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
SEAT_TASKS = ("critic", "tribunal", "refinement", "defense", "mediator", "judge")
VARIABLES = {"round_num": 2, "critique": "Die Kritik.", "user_interjection": ""}
DEFAULT_SEATS = {SEAT_MAIN: "aifred", SEAT_CRITIC: "sokrates", SEAT_JUDGE: "salomo"}


@pytest.fixture(autouse=True)
def agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    raw = agent_config._default_agents()
    raw["pater"] = {**raw["sokrates"], "display_name": "Pater Tuck", "aliases": []}
    raw["codine"] = {**raw["sokrates"], "display_name": "Codine", "aliases": []}
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps({"agents": raw}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    monkeypatch.setattr(prompt_loader, "get_user_name", lambda: "Peuqui")
    return raw


@pytest.mark.parametrize("lang", ["de", "en"])
def test_every_seat_task_loads_with_the_names_of_the_seats(lang: str) -> None:
    for key in SEAT_TASKS:
        assert "{" not in seat_prompt(key, DEFAULT_SEATS, lang, **VARIABLES), key
    roles = prompt_loader.load_multi_agent_roles(DEFAULT_SEATS, lang)
    assert all(f"[{name}]:" in roles for name in ("AIfred", "Sokrates", "Salomo"))


def test_the_agents_in_the_seats_are_named_by_the_tasks() -> None:
    # Code review: Codine wrote the draft, Pater Tuck reviews it, Salomo judges.
    seats = {SEAT_MAIN: "codine", SEAT_CRITIC: "pater", SEAT_JUDGE: "salomo"}
    assert seat_names(seats) == {"main_agent": "Codine", "critic_agent": "Pater Tuck", "judge_agent": "Salomo"}
    refinement = seat_prompt("refinement", seats, "de", **VARIABLES)
    assert "Pater Tuck hat deine These kritisiert" in refinement and "Sokrates" not in refinement
    roles = prompt_loader.load_multi_agent_roles(seats, "de")
    assert "[Codine]:" in roles and "AIfred" not in roles


@pytest.mark.parametrize("lang", ["de", "en"])
def test_no_seat_task_names_an_agent_itself(lang: str) -> None:
    files = [PROMPTS / lang / "debate" / f"{key}.txt" for key in SEAT_TASKS]
    assert sorted(path.name for path in (PROMPTS / lang / "debate").iterdir()) == sorted(path.name for path in files)
    for path in [*files, PROMPTS / lang / "shared" / "multi_agent_roles.txt"]:
        text = path.read_text(encoding="utf-8")
        named = [name for name in ("AIfred", "Sokrates", "Salomo", "AIFRED", "SOKRATES", "SALOMO") if name in text]
        assert named == [], path
