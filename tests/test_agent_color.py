"""An agent's colour (09.11.2026): the stripe at the left of its chat bubbles and its name there. Every agent has one
(#rrggbb, agents.json "color"); the persona changes it, a new agent starts with AGENT_DEFAULT_COLOR, and the chat
reads every agent's, switched off and system agents too (a history shows their answers). agents.json is a
temporary one."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import agent_config
from aifred.lib.config import AGENT_DEFAULT_COLOR
from aifred.lib.user_error import Invalid


@pytest.fixture
def agents_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agents.json"
    agents = agent_config._default_agents()
    agents["sokrates"]["enabled"] = False
    path.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", path)
    monkeypatch.setattr(agent_config, "_create_prompt_files", lambda _agent_id, _name: None)
    return path


@pytest.fixture
def client(agents_file: Path) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def _entry() -> dict[str, str]:
    return {"display_name": "X", "emoji": "🙂", "description": "", "role": "agent"}


def test_an_agent_without_a_colour_is_refused() -> None:
    with pytest.raises(KeyError, match="color"):
        agent_config.config_from_raw(_entry())


@pytest.mark.parametrize("color", ["orange", "#e670", "#e67700ff", "e67700"])
def test_a_colour_that_is_no_rrggbb_is_refused(color: str) -> None:
    with pytest.raises(Invalid):
        agent_config.config_from_raw({**_entry(), "color": color})


def test_the_chat_reads_every_agents_colour_switched_off_and_system_agents_too(client: TestClient) -> None:
    colors = client.get("/agents/colors").json()
    assert colors == {agent_id: entry["color"] for agent_id, entry in agent_config._default_agents().items()}
    assert "sokrates" in colors and "vision" in colors


def test_the_persona_changes_the_colour_and_refuses_one_that_is_no_rrggbb(client: TestClient, agents_file: Path) -> None:
    assert client.patch("/agents/salomo/persona", json={"color": "#123abc"}).json()["color"] == "#123abc"
    assert json.loads(agents_file.read_text(encoding="utf-8"))["agents"]["salomo"]["color"] == "#123abc"
    assert client.patch("/agents/salomo/persona", json={"color": "gold"}).status_code == 422
    assert client.get("/agents/salomo/persona").json()["color"] == "#123abc"


def test_a_new_agent_starts_with_the_default_colour(client: TestClient) -> None:
    created = client.post("/agents", json={"id": "neu", "display_name": "Neu", "emoji": "🆕"})
    assert created.status_code == 201 and created.json()["color"] == AGENT_DEFAULT_COLOR
