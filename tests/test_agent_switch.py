"""Agents switched on and off, and deleted (agents page, lib/agent_config.py): the cards, the switch and the
deletion over the API; an agent switched off vanishes from every choice (summary, addressing, the intent
detection's list, the sub-agent's targets) and whatever names it is refused out loud: a scheduled job, a channel
message (a trigger or a wake word; tests/test_turn_service.py, as a turn). The base agent is never switched off or deleted, a system agent neither; Sokrates and Salomo
are deleted only once Reflex is gone. agents.json is a temporary one."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import agent_config
from aifred.lib.i18n import t

PROBE = "probe_agent"   # an agent of this test; its prompt folder does not exist


@pytest.fixture
def agents_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agents.json"
    agents = agent_config._default_agents()
    agents[PROBE] = {**agents["sokrates"], "display_name": "Probe", "aliases": [], "prompts": {"identity": f"{PROBE}/identity.txt"}}
    path.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", path)
    return path


@pytest.fixture
def client(agents_file: Path) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def _switch_off(agents_file: Path, agent: str) -> None:
    raw = json.loads(agents_file.read_text(encoding="utf-8"))
    raw["agents"][agent]["enabled"] = False
    agents_file.write_text(json.dumps(raw), encoding="utf-8")


def test_the_cards_show_every_chat_agent_and_what_may_be_deleted(client: TestClient) -> None:
    cards = {card["id"]: card for card in client.get("/agents").json()}
    assert "vision" not in cards and "calibration" not in cards                 # system agents
    assert cards["aifred"]["base"] and cards["aifred"]["delete_refusal"] == t("error_base_agent_stays", agent="AIfred")
    assert cards["sokrates"]["delete_refusal"] == t("error_agent_needed_by_reflex", agent="Sokrates")
    assert cards["salomo"]["delete_refusal"] == t("error_agent_needed_by_reflex", agent="Salomo")
    assert cards[PROBE]["delete_refusal"] is None and all(card["enabled"] for card in cards.values())


def test_an_agent_switched_off_vanishes_from_every_choice(client: TestClient) -> None:
    from aifred.lib.plugin_base import PluginContext
    from aifred.lib.prompt_loader import get_intent_detection_prompt
    from aifred.plugins.tools.subagent import delegation_candidates

    card = client.patch("/agents/sokrates", json={"enabled": False}).json()
    assert card["enabled"] is False
    assert "sokrates" not in [agent["id"] for agent in client.get("/agents/summary").json()]
    assert agent_config.resolve_agent_id("Sokrates") is None and agent_config.resolve_agent_id("socrates") is None
    assert "- sokrates" not in get_intent_detection_prompt("Hallo", lang="en")
    assert "sokrates" not in delegation_candidates(PluginContext(agent_id="aifred", lang="de", session_id=""))
    assert client.get("/agents").json()[1]["id"] == "sokrates"                   # the page still shows it
    client.patch("/agents/sokrates", json={"enabled": True})
    assert agent_config.resolve_agent_id("socrates") == "sokrates"


def test_the_base_agent_and_a_system_agent_stay_on(client: TestClient) -> None:
    refused = client.patch("/agents/aifred", json={"enabled": False})
    assert (refused.status_code, refused.json()["detail"]) == (422, t("error_base_agent_stays_on", agent="AIfred"))
    assert client.patch("/agents/vision", json={"enabled": False}).status_code == 422
    assert client.patch("/agents/gibt_es_nicht", json={"enabled": False}).status_code == 404


def test_deleting_takes_the_agent_away_and_refuses_with_the_reason(client: TestClient) -> None:
    assert client.delete(f"/agents/{PROBE}").status_code == 204
    assert PROBE not in [card["id"] for card in client.get("/agents").json()]
    for agent, key in (("aifred", "base_agent_stays"), ("sokrates", "agent_needed_by_reflex"), ("vision", "system_agent_stays")):
        refused = client.delete(f"/agents/{agent}")
        expected = t(f"error_{key}", agent=agent_config.agent_display_name(agent))
        assert (refused.status_code, refused.json()["detail"]) == (422, expected)
    assert client.delete(f"/agents/{PROBE}").status_code == 404


def test_a_job_of_an_agent_switched_off_fails_out_loud(agents_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import scheduler

    _switch_off(agents_file, "sokrates")

    async def must_not_run(_message: Any) -> None:
        raise AssertionError("no answer may be asked for")

    monkeypatch.setattr("aifred.lib.message_processor.process_inbound", must_not_run)
    job = SimpleNamespace(name="Morgengruß", payload={"message": "Guten Morgen", "agent": "sokrates"}, job_id="j1")
    with pytest.raises(RuntimeError, match="'sokrates' is switched off"):
        asyncio.run(scheduler._execute_job(job))  # type: ignore[arg-type]  # a stand-in with the job's fields
