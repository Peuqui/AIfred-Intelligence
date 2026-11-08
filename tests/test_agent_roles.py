"""Only the roles agent and system (agent_config.VALID_ROLES); the roles of the Reflex time (main, critic, judge,
custom) are turned into "agent" once, by scripts/migrate_agent_roles.py, with a copy of the file beside it. Who
critiques and judges is a seat of the session since 08.11.2026, not a property of the agent."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from aifred.lib import agent_config
from aifred.lib.user_error import Invalid

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_agent_roles.py"


def _script():
    spec = importlib.util.spec_from_file_location("migrate_agent_roles", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry(role: str) -> dict:
    return {"display_name": role.title(), "emoji": "🙂", "description": "", "role": role}


def test_an_old_role_is_refused_with_the_way_out() -> None:
    with pytest.raises(Invalid) as refused:
        agent_config.config_from_raw(_entry("critic"))
    assert refused.value.key == "agent_role_unknown"
    assert "migrate_agent_roles" in refused.value.message("de")


def test_the_script_turns_the_old_roles_into_agent_and_keeps_a_copy(tmp_path: Path) -> None:
    path = tmp_path / "agents.json"
    original = {"agents": {"aifred": _entry("main"), "sokrates": _entry("critic"), "pater": _entry("custom"), "vision": _entry("system")}}
    path.write_text(json.dumps(original), encoding="utf-8")
    assert sorted(_script().migrate(path)) == ["aifred", "pater", "sokrates"]
    roles = {agent: entry["role"] for agent, entry in json.loads(path.read_text(encoding="utf-8"))["agents"].items()}
    assert roles == {"aifred": "agent", "sokrates": "agent", "pater": "agent", "vision": "system"}
    backups = list(tmp_path.glob("agents.json.bak-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8")) == original
    assert _script().migrate(path) == [] and len(list(tmp_path.glob("agents.json.bak-*"))) == 1   # nothing left to do


def test_the_script_changes_nothing_on_a_role_it_does_not_know(tmp_path: Path) -> None:
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"agents": {"x": _entry("wizard"), "y": _entry("main")}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        _script().migrate(path)
    assert json.loads(path.read_text(encoding="utf-8"))["agents"]["y"]["role"] == "main"
