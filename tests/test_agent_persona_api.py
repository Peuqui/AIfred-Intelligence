"""An agent's persona, prompts and rights over the API (agents page, part 2): name, emoji, description and aliases
(addressing follows them), a prompt layer's text per language (nothing outside prompts/<lang>/), the tools granted
(a list keeps what it held for tools of a plugin switched off), and a new agent from the template. agents.json and
the prompts folder are temporary ones."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import agent_config, prompt_loader

GROUPS = [
    {"name_de": "Gedächtnis", "name_en": "Memory", "names": ["store_memory"], "tools": [{"name": "store_memory", "tier": 0}]},
    {"name_de": "Web", "name_en": "Web", "names": ["web_search", "web_fetch"],
     "tools": [{"name": "web_search", "tier": 0}, {"name": "web_fetch", "tier": 0}]},
]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path]]:
    agents_file = tmp_path / "agents.json"
    agents = agent_config._default_agents()
    agents["schelm"] = {**agents["sokrates"], "display_name": "Schelm", "prompts": {"identity": "../../geheim.txt"}}
    agents_file.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    prompts = tmp_path / "prompts"
    for lang in ("de", "en"):
        shutil.copytree(prompt_loader.PROMPTS_DIR / lang / agent_config.AGENT_TEMPLATE_DIR, prompts / lang / agent_config.AGENT_TEMPLATE_DIR)
        (prompts / lang / "sokrates").mkdir(parents=True)
    (prompts / "de" / "sokrates" / "identity.txt").write_text("Ich bin Sokrates.", encoding="utf-8")
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", prompts)
    monkeypatch.setattr("aifred.lib.plugin_registry.tool_groups_on_offer", lambda _ctx: GROUPS)
    yield agents_file, prompts


@pytest.fixture
def client(world: tuple[Path, Path]) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def _saved(agents_file: Path, agent: str) -> dict[str, Any]:
    entry: dict[str, Any] = json.loads(agents_file.read_text(encoding="utf-8"))["agents"][agent]
    return entry


def test_the_persona_changes_and_addressing_follows_the_aliases(client: TestClient) -> None:
    persona = client.get("/agents/sokrates/persona").json()
    assert persona["display_name"] == "Sokrates" and "identity" in persona["prompt_keys"] and persona["languages"] == ["de", "en"]
    changed = client.patch("/agents/sokrates/persona", json={"emoji": "🦉", "aliases": [" Sokratis ", "", "SOCRATES"]}).json()
    assert (changed["emoji"], changed["aliases"]) == ("🦉", ["sokratis", "socrates"])
    assert agent_config.resolve_agent_id("Sokratis") == "sokrates"
    assert client.patch("/agents/sokrates/persona", json={"display_name": ""}).status_code == 422
    assert client.get("/agents/vision/persona").status_code == 404                # a system agent


def test_a_prompt_layer_is_read_and_written_per_language(client: TestClient, world: tuple[Path, Path]) -> None:
    _agents_file, prompts = world
    assert client.get("/agents/sokrates/prompts/de/identity").json() == {"text": "Ich bin Sokrates.", "exists": True}
    assert client.get("/agents/sokrates/prompts/en/identity").json() == {"text": "", "exists": False}
    written = client.put("/agents/sokrates/prompts/en/identity", json={"text": "I am Socrates."}).json()
    assert written["exists"] and (prompts / "en" / "sokrates" / "identity.txt").read_text() == "I am Socrates."
    assert client.get("/agents/sokrates/prompts/fr/identity").status_code == 422     # no such language
    assert client.get("/agents/sokrates/prompts/de/gibt_es_nicht").status_code == 404


def test_a_prompt_path_out_of_the_prompts_folder_is_refused(client: TestClient, world: tuple[Path, Path]) -> None:
    _agents_file, prompts = world
    assert client.put("/agents/schelm/prompts/de/identity", json={"text": "x"}).status_code == 422
    assert not (prompts.parent / "geheim.txt").exists()


def test_rights_grant_every_tool_or_a_list_that_keeps_tools_not_on_offer(client: TestClient, world: tuple[Path, Path]) -> None:
    agents_file, _prompts = world
    rights = client.get("/agents/sokrates/tools").json()
    assert rights["all"] and [group["name"] for group in rights["groups"]] == ["Gedächtnis", "Web"]
    raw = json.loads(agents_file.read_text(encoding="utf-8"))
    raw["agents"]["sokrates"]["tools"] = ["web_search", "nextcloud_read"]      # nextcloud: plugin switched off
    agents_file.write_text(json.dumps(raw), encoding="utf-8")
    changed = client.put("/agents/sokrates/tools", json={"allowed": ["store_memory", "web_fetch"]}).json()
    assert not changed["all"]
    assert _saved(agents_file, "sokrates")["tools"] == ["nextcloud_read", "store_memory", "web_fetch"]
    web = next(group for group in changed["groups"] if group["name"] == "Web")
    assert [(tool["name"], tool["allowed"]) for tool in web["tools"]] == [("web_search", False), ("web_fetch", True)]
    assert client.put("/agents/sokrates/tools", json={"allowed": None}).json()["all"]
    assert _saved(agents_file, "sokrates")["tools"] is None


def test_a_new_agent_gets_the_template_prompts(client: TestClient, world: tuple[Path, Path]) -> None:
    _agents_file, prompts = world
    card = client.post("/agents", json={"id": "kritikus", "display_name": "Kritikus", "emoji": "🧐"})
    assert card.status_code == 201 and card.json()["enabled"] and card.json()["delete_refusal"] is None
    identity = client.get("/agents/kritikus/prompts/de/identity").json()
    assert identity["exists"] and "Kritikus" in identity["text"]                  # the name filled in
    assert (prompts / "en" / "kritikus").is_dir()
    assert client.post("/agents", json={"id": "kritikus", "display_name": "X", "emoji": "x"}).status_code == 422
    assert client.post("/agents", json={"id": "../raus", "display_name": "X", "emoji": "x"}).status_code == 422
