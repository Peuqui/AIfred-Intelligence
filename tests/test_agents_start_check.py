"""The start check of the server (25.11.2026): no built-in agents, nothing creates an agents.json. The file loads and
holds the base agent and the system agents the code needs, or the server does not start. The real data/agents.json
of the repository passes."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from agent_fixtures import raw_test_agents

from aifred.lib import agent_choices, agent_config, config, server_startup
from aifred.lib.calibration import ai_agent
from aifred.lib.runtime_settings import RuntimeSettings


def _write(path: Path, agents: dict[str, dict]) -> None:
    path.write_text(json.dumps({"agents": agents}), encoding="utf-8")


@pytest.fixture
def agents_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agents.json"
    monkeypatch.setattr(agent_config, "AGENTS_FILE", path)
    return path


def _required_agents() -> dict[str, dict]:
    agents = raw_test_agents()
    agents["calibration"] = {**agents["vision"], "display_name": "Calibration", "prompts": {"system": "calibration/system.txt"}}
    return agents


def test_a_missing_agents_file_is_an_error_and_is_not_created(agents_file: Path) -> None:
    with pytest.raises(FileNotFoundError, match=re.escape(str(agents_file))):
        agent_config.load_agents_raw()
    assert not agents_file.exists()


def test_the_check_passes_with_the_base_agent_and_the_system_agents(agents_file: Path) -> None:
    _write(agents_file, _required_agents())
    agent_config.check_required_agents()


def test_the_check_refuses_a_missing_agents_file(agents_file: Path) -> None:
    with pytest.raises(FileNotFoundError, match=re.escape(str(agents_file))):
        agent_config.check_required_agents()


@pytest.mark.parametrize("missing", [agent_config.BASE_AGENT, *agent_config.REQUIRED_AGENT_ROLES])
def test_the_check_refuses_a_missing_required_agent_and_names_it_and_the_file(agents_file: Path, missing: str) -> None:
    agents = _required_agents()
    del agents[missing]
    _write(agents_file, agents)
    with pytest.raises(RuntimeError, match=re.escape(missing)) as error:
        agent_config.check_required_agents()
    assert str(agents_file) in str(error.value)


def test_the_check_refuses_an_entry_that_does_not_load(agents_file: Path) -> None:
    agents = _required_agents()
    del agents["aifred"]["tools"]
    _write(agents_file, agents)
    with pytest.raises(Exception, match="tools"):
        agent_config.check_required_agents()


def test_the_required_agents_are_the_base_agent_and_the_systems_vision_and_calibration_with_their_roles() -> None:
    assert agent_config.REQUIRED_AGENT_ROLES == {"aifred": "agent", "vision": "system", "calibration": "system"}
    assert agent_config.BASE_AGENT == "aifred"


@pytest.mark.parametrize(("agent_id", "wrong_role", "expected_role"),
                         [("aifred", "system", "agent"), ("vision", "agent", "system"), ("calibration", "agent", "system")])
def test_the_check_refuses_a_required_agent_with_the_wrong_role_and_names_both_roles(
    agents_file: Path, agent_id: str, wrong_role: str, expected_role: str,
) -> None:
    agents = _required_agents()
    agents[agent_id]["role"] = wrong_role
    _write(agents_file, agents)
    with pytest.raises(RuntimeError) as error:
        agent_config.check_required_agents()
    assert f"{agent_id} has the role '{wrong_role}', expected '{expected_role}'" in str(error.value)
    assert str(agents_file) in str(error.value)


def test_the_agents_file_of_the_repository_passes_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_config, "AGENTS_FILE", Path(__file__).resolve().parents[1] / "data" / "agents.json")
    agent_config.check_required_agents()


def test_the_server_does_not_start_without_the_required_agents(agents_file: Path) -> None:
    async def enter() -> None:
        async with server_startup.agents_check_lifespan():
            pass

    _write(agents_file, raw_test_agents())   # no calibration
    with pytest.raises(RuntimeError, match="calibration"):
        asyncio.run(enter())


def test_the_agents_check_is_part_of_the_servers_lifespans() -> None:
    assert server_startup.agents_check_lifespan in server_startup.SERVER_LIFESPANS


def test_the_base_agent_is_defined_once() -> None:
    assert not hasattr(config, "BASE_AGENT")
    assert [path.name for path in (Path(agent_config.__file__).parent).rglob("*.py")
            if re.search(r"^BASE_AGENT\s*=", path.read_text(encoding="utf-8"), re.MULTILINE)] == ["agent_config.py"]


# What the start check rules out, the code no longer tolerates: an agents.json without the entry is an error, not a default.

def _without(agents_file: Path, agent_id: str) -> None:
    agents = _required_agents()
    del agents[agent_id]
    _write(agents_file, agents)


def _view() -> RuntimeSettings:
    from fake_tuning import default_agents_tuning

    return RuntimeSettings(
        backend_type="llamacpp", backend_url="http://llama-swap", ui_language="de",
        enable_tts=False, automatik_model_id="", agent_tuning=default_agents_tuning(), tts_autoplay=False, tts_agents={},
        max_debate_rounds=3, consensus_type="majority",
    )


def test_the_calibration_options_need_the_calibration_agent(agents_file: Path) -> None:
    from aifred.lib.calibration.service import calibration_options

    _without(agents_file, "calibration")
    with pytest.raises(KeyError, match="calibration"):
        calibration_options()


def test_the_calibration_prompt_comes_from_the_calibration_agents_entry_alone(agents_file: Path) -> None:
    agents = _required_agents()
    del agents["calibration"]["prompts"]["system"]
    _write(agents_file, agents)
    with pytest.raises(KeyError, match="system"):
        ai_agent._build_system_prompt("m", 1.0, 4096, 32, [], 192, False, [])


def test_the_models_in_use_need_the_vision_agent(agents_file: Path) -> None:
    _without(agents_file, "vision")
    with pytest.raises(KeyError, match="vision"):
        agent_choices.models_in_use(_view(), "de")


def test_the_quick_choices_need_the_vision_agent(agents_file: Path) -> None:
    _without(agents_file, "vision")
    with pytest.raises(KeyError, match="vision"):
        asyncio.run(agent_choices.quick_choices(_view(), {}, "de"))
