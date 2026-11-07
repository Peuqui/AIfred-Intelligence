"""Agents switched on and off, and deleted (agents page, lib/agent_config.py): the cards, the switch and the
deletion over the API; an agent switched off vanishes from every choice (summary, addressing, the intent
detection's list, the sub-agent's targets) and whatever names it is refused out loud: a scheduled job, a channel
message (a trigger or a wake word). The base agent is never switched off or deleted, a system agent neither; Sokrates and Salomo
are deleted only once Reflex is gone. agents.json is a temporary one."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import agent_config, session_storage

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
    assert cards["aifred"]["base"] and "base agent" in cards["aifred"]["delete_refusal"]
    assert "Reflex" in cards["sokrates"]["delete_refusal"] and "Reflex" in cards["salomo"]["delete_refusal"]
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
    assert refused.status_code == 422 and "base agent" in refused.json()["detail"]
    assert client.patch("/agents/vision", json={"enabled": False}).status_code == 422
    assert client.patch("/agents/gibt_es_nicht", json={"enabled": False}).status_code == 404


def test_deleting_takes_the_agent_away_and_refuses_with_the_reason(client: TestClient) -> None:
    assert client.delete(f"/agents/{PROBE}").status_code == 204
    assert PROBE not in [card["id"] for card in client.get("/agents").json()]
    for agent, reason in (("aifred", "base agent"), ("sokrates", "Reflex"), ("vision", "system agent")):
        refused = client.delete(f"/agents/{agent}")
        assert refused.status_code == 422 and reason in refused.json()["detail"]
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


def test_a_channel_message_for_an_agent_switched_off_is_refused_out_loud(
    agents_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aifred.lib import message_processor as mp
    from aifred.lib.envelope import InboundMessage

    _switch_off(agents_file, "sokrates")
    session = "f" * 32
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    session_storage.create_empty_session(session, owner="ki")
    notifications: list[str] = []

    async def detected(_text: str) -> tuple[str, str, dict[str, Any], float]:
        return "aifred", "de", {}, 0.0

    async def engine(**_kwargs: Any) -> None:
        raise AssertionError("no agent may answer in its place")

    monkeypatch.setattr(mp, "routing_table", SimpleNamespace(get_route=lambda *_args: SimpleNamespace(session_id=session)))
    monkeypatch.setattr("aifred.lib.calibration_gate.is_calibration_active", lambda: False)
    monkeypatch.setattr("aifred.lib.security.resolve_tier_for_sender", lambda *_args: 0)
    monkeypatch.setattr("aifred.lib.plugin_registry.get_channel", lambda _channel: None)
    monkeypatch.setattr(mp, "channel_display_label", lambda _channel: "Webhook")
    monkeypatch.setattr(mp, "detect_target_agent_via_llm", detected)
    monkeypatch.setattr(mp, "_call_engine", engine)
    monkeypatch.setattr(mp, "write_hub_notification", lambda *_args, status: notifications.append(status))
    message = InboundMessage(
        channel="webhook", channel_id="trigger", sender="Agent-Orc", text="Was sagst du dazu?",
        timestamp=datetime.now(UTC), metadata={"wake_agent": "sokrates"},   # as a trigger or a wake word names it
    )
    assert asyncio.run(mp.process_inbound(message)) is None
    assert notifications[-1] == "error"
    stored = session_storage.load_session(session)
    assert stored is not None and stored["data"]["chat_history"][-1]["content"].endswith("Was sagst du dazu?")
    session_storage._session_meta_cache.clear()
