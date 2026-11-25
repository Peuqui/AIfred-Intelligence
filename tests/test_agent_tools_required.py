"""An agent's rights are always an explicit list (Peuqui 25.11.2026): "no entry" used to mean every tool, so a
forgotten entry gave an agent tools it was never to have. Now ``tools`` is required in agents.json (an empty list =
nothing allowed until someone grants), a new agent starts with ``[]``, and no place reads "None" as "all tools"."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fake_tts_service import FakeTTSService, install_fake_service
from starlette.testclient import TestClient

from aifred.lib import agent_bundle, agent_config, agent_memory, job_notes, plugin_registry, prompt_loader
from aifred.lib.agent_config import config_from_raw, create_agent, load_agents
from aifred.lib.function_calling import Tool
from aifred.lib.plugin_base import PluginContext, load_plugin_instructions
from aifred.lib.security import TIER_WRITE_DATA
from aifred.lib.user_error import Invalid, Missing

SHIPPED_AGENTS = Path(__file__).resolve().parents[1] / "data" / "agents.json"


def _entry(**extra: Any) -> dict[str, Any]:
    return {"display_name": "Pater", "emoji": "⛪", "description": "", "role": "agent", "color": "#7d8590", **extra}


def _tool(name: str) -> Tool:
    return Tool(name=name, description=name, parameters={"type": "object"}, executor=lambda: name, tier=0)


@pytest.fixture
def agents_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agents.json"
    aifred = {**json.loads(SHIPPED_AGENTS.read_text(encoding="utf-8"))["agents"]["aifred"], "tools": []}   # real prompts: the size is measured
    path.write_text(json.dumps({"agents": {"aifred": aifred, "pater": _entry(tools=[])}}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", path)
    monkeypatch.setattr(agent_config, "_create_prompt_files", lambda _agent_id, _name: None)
    return path


def _grant(agents_file: Path, agent: str, tools: list[str]) -> None:
    raw = json.loads(agents_file.read_text(encoding="utf-8"))
    raw["agents"][agent]["tools"] = tools
    agents_file.write_text(json.dumps(raw), encoding="utf-8")


# ── Loading: a list is required ──────────────────────────────────────────


def test_an_agent_without_a_tool_list_is_refused_with_its_name() -> None:
    with pytest.raises(Invalid) as refused:
        config_from_raw(_entry())
    assert refused.value.key == "agent_tools_format"
    assert "Pater" in refused.value.message("de") and "Pater" in refused.value.message("en")


@pytest.mark.parametrize("tools", [None, "web_search", ["web_search", 3], {"web_search": True}])
def test_anything_but_a_list_of_names_is_refused(tools: object) -> None:
    with pytest.raises(Invalid):
        config_from_raw(_entry(tools=tools))


def test_an_empty_list_is_valid_and_allows_nothing() -> None:
    assert config_from_raw(_entry(tools=[])).tools == []


def test_loading_agents_stops_loudly_at_an_entry_without_a_list(agents_file: Path) -> None:
    raw = json.loads(agents_file.read_text(encoding="utf-8"))
    raw["agents"]["vergessen"] = _entry(display_name="Vergessen")
    agents_file.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Invalid, match="Vergessen"):
        load_agents()


def test_every_agent_of_the_shipped_agents_json_has_an_explicit_list() -> None:
    agents = json.loads(SHIPPED_AGENTS.read_text(encoding="utf-8"))["agents"]
    assert agents
    for agent_id, entry in agents.items():
        assert isinstance(entry.get("tools"), list), agent_id
        assert all(isinstance(name, str) for name in entry["tools"]), agent_id
        assert config_from_raw(entry).tools == entry["tools"]
    # The system agents run no tool loop of the agents' kind (vision none, the calibration its own fixed set).
    assert agents["vision"]["tools"] == [] and agents["calibration"]["tools"] == []


# ── A new agent starts with nothing ──────────────────────────────────────


def test_a_new_agent_has_no_tools_in_its_config_and_in_the_file(agents_file: Path) -> None:
    config = create_agent("neu", "Neu", "🆕", "", "#7d8590")
    assert config.tools == []
    assert json.loads(agents_file.read_text(encoding="utf-8"))["agents"]["neu"]["tools"] == []


GROUPS = [
    {"name_de": "Gedächtnis", "name_en": "Memory", "names": ["store_memory"], "tools": [{"name": "store_memory", "tier": 0}]},
    {"name_de": "Web", "name_en": "Web", "names": ["web_search", "web_fetch"],
     "tools": [{"name": "web_search", "tier": 0}, {"name": "web_fetch", "tier": 0}]},
]


@pytest.fixture
def client(agents_file: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    monkeypatch.setattr("aifred.lib.plugin_registry.tool_groups_on_offer", lambda _ctx: GROUPS)
    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_an_agent_created_over_the_api_has_every_tool_forbidden(client: TestClient) -> None:
    assert client.post("/agents", json={"id": "neu", "display_name": "Neu", "emoji": "🆕"}).status_code == 201
    rights = client.get("/agents/neu/tools").json()
    assert "all" not in rights                      # no state "every tool" any more
    assert [tool["allowed"] for group in rights["groups"] for tool in group["tools"]] == [False, False, False]


def test_the_rights_api_takes_only_a_list(client: TestClient, agents_file: Path) -> None:
    assert client.put("/agents/pater/tools", json={"allowed": None}).status_code == 422
    assert client.put("/agents/pater/tools", json={}).status_code == 422
    assert json.loads(agents_file.read_text(encoding="utf-8"))["agents"]["pater"]["tools"] == []
    assert client.put("/agents/pater/tools", json={"allowed": ["web_fetch"]}).status_code == 200
    assert json.loads(agents_file.read_text(encoding="utf-8"))["agents"]["pater"]["tools"] == ["web_fetch"]
    assert client.put("/agents/pater/tools", json={"allowed": []}).status_code == 200
    assert json.loads(agents_file.read_text(encoding="utf-8"))["agents"]["pater"]["tools"] == []


# ── The toolbox: the list decides, nothing else ──────────────────────────


class _FakeMemory:
    async def recall_context(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    def make_toolkit(self, _agent_id: str, session_id: str = "") -> agent_memory.ToolKit:
        return agent_memory.ToolKit(tools=[_tool("store_memory")])


@pytest.fixture
def toolbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Memory plus the tools of one plugin on offer, whatever the agent may use."""
    monkeypatch.setattr(agent_memory, "get_agent_memory", lambda: _FakeMemory())
    monkeypatch.setattr(plugin_registry, "collect_plugin_tools", lambda _ctx: [(None, [_tool("web_search"), _tool("web_fetch")])])


def _toolkit_names(agent: str) -> list[str]:
    _memory, toolkit = asyncio.run(agent_memory.prepare_agent_toolkit(agent, "frage", trust="owner"))
    return [tool.name for tool in toolkit.tools] if toolkit is not None else []


def test_the_toolbox_of_an_agent_with_an_empty_list_is_empty(agents_file: Path, toolbox: None) -> None:
    assert _toolkit_names("pater") == []


def test_the_toolbox_holds_exactly_the_granted_tools(agents_file: Path, toolbox: None) -> None:
    _grant(agents_file, "pater", ["web_fetch", "store_memory"])
    assert sorted(_toolkit_names("pater")) == ["store_memory", "web_fetch"]


def test_an_unknown_agent_gets_no_toolbox_at_all(agents_file: Path, toolbox: None) -> None:
    with pytest.raises(Missing):
        _toolkit_names("gibt_es_nicht")


def test_a_sub_agent_of_an_agent_with_an_empty_list_has_only_the_extra_tools(
    agents_file: Path, toolbox: None
) -> None:
    from aifred.lib import subagent_run

    ctx = PluginContext(agent_id="aifred", lang="de", session_id="s", max_tier=TIER_WRITE_DATA, source="browser", trust="owner")
    extra = _tool("report_back")
    toolkit = asyncio.run(subagent_run.build_subagent_toolkit(ctx, frozenset({0, 1, 2}), 0, "pater", "aufgabe", [extra]))
    assert toolkit is not None and [tool.name for tool in toolkit.tools] == ["report_back"]
    assert asyncio.run(subagent_run.build_subagent_toolkit(ctx, frozenset({0, 1, 2}), 0, "pater", "aufgabe")) is None


def test_the_delegation_legend_names_no_group_for_an_agent_with_an_empty_list(
    agents_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aifred.plugins.tools import subagent as subagent_plugin

    monkeypatch.setattr(plugin_registry, "collect_plugin_tools", lambda _ctx: [(None, [_tool("read_file")])])
    ctx = PluginContext(agent_id="aifred", lang="de", session_id="s", max_tier=TIER_WRITE_DATA, source="browser")
    legend = subagent_plugin.delegation_legend(ctx, subagent_plugin._parse_settings({}), 0, ["pater"])
    assert "- pater (Pater): -" in legend.splitlines()


# ── Prompt fragments only for the tools granted ──────────────────────────


def test_plugin_instructions_need_the_granted_tools_there_is_no_all(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.plugins.tools.workspace import plugin

    with pytest.raises(TypeError):
        load_plugin_instructions(plugin, "de")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        plugin.get_prompt_instructions("de")  # type: ignore[call-arg]
    assert load_plugin_instructions(plugin, "de", set()) == ""
    assert "read_file" in load_plugin_instructions(plugin, "de", {"read_file"})


def test_the_measured_prompt_follows_the_agents_list(agents_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_direct_prompt_tokens asks for the prompt before the toolkit exists: the agent's list is its tools."""
    seen: list[set[str]] = []
    real = prompt_loader.agent_system_prompt

    def spy(*args: Any, **kwargs: Any) -> str:
        seen.append(set(kwargs["tools"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(prompt_loader, "agent_system_prompt", spy)
    prompt_loader.get_direct_prompt_tokens("aifred", "de")
    _grant(agents_file, "aifred", ["web_search", "read_file"])
    prompt_loader.get_direct_prompt_tokens("aifred", "de")
    assert seen == [set(), {"web_search", "read_file"}]


# ── Notes of a job ───────────────────────────────────────────────────────


def test_an_agent_keeps_job_notes_only_with_the_notes_tools_granted(agents_file: Path) -> None:
    assert job_notes.agent_can_keep_notes("pater") is False
    _grant(agents_file, "pater", sorted(job_notes.NOTES_TOOLS))
    assert job_notes.agent_can_keep_notes("pater") is True
    assert job_notes.agent_can_keep_notes("gibt_es_nicht") is False


# ── Bundle: the list travels; none in it means nothing, said aloud ───────


@pytest.fixture
def saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fake = FakeTTSService([])
    fake.voices_dir = str(tmp_path / "voices")
    install_fake_service(monkeypatch, tmp_path, fake)
    for lang in ("de", "en"):
        (tmp_path / "prompts" / lang).mkdir(parents=True)
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", tmp_path / "prompts")
    state: dict[str, Any] = {"agents": {"codine": _entry(display_name="Codine", tools=["read_file", "nur_hier"])}, "settings": {}}
    monkeypatch.setattr(agent_bundle, "load_agents_raw", lambda: dict(state["agents"]))
    monkeypatch.setattr(agent_bundle, "save_agents_raw", lambda agents: state.update(agents=agents))
    monkeypatch.setattr(agent_bundle, "_load_settings", lambda: json.loads(json.dumps(state["settings"])))
    monkeypatch.setattr(agent_bundle, "save_settings", lambda settings: state.update(settings=settings) or True)
    return state


def _without_tools(bundle: bytes, replacement: dict[str, Any]) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(bundle))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for name in source.namelist():
            if name.endswith("config.json"):
                config = {key: value for key, value in json.loads(source.read(name)).items() if key != "tools"}
                out.writestr(name, json.dumps({**config, **replacement}))
            else:
                out.writestr(name, source.read(name))
    return buffer.getvalue()


def test_a_bundle_keeps_the_agents_list(saved: dict[str, Any]) -> None:
    bundle = agent_bundle.export_bundle(["codine"])
    saved["agents"] = {}
    agent_bundle.import_bundle(bundle)
    assert saved["agents"]["codine"]["tools"] == ["read_file", "nur_hier"]   # a tool this installation lacks stays inert


@pytest.mark.parametrize("replacement", [{}, {"tools": None}])
def test_a_bundle_without_a_list_brings_the_agent_in_without_tools_and_says_so(saved: dict[str, Any], replacement: dict[str, Any]) -> None:
    bundle = _without_tools(agent_bundle.export_bundle(["codine"]), replacement)
    saved["agents"] = {}
    ids, warnings = agent_bundle.import_bundle(bundle)
    assert ids == ["codine"] and saved["agents"]["codine"]["tools"] == []
    assert any("Codine" in warning or "codine" in warning for warning in warnings)


def test_a_bundle_with_a_list_of_something_else_is_refused(saved: dict[str, Any]) -> None:
    bundle = _without_tools(agent_bundle.export_bundle(["codine"]), {"tools": "alle"})
    saved["agents"] = {}
    with pytest.raises(Invalid):
        agent_bundle.import_bundle(bundle)
    assert saved["agents"] == {}
