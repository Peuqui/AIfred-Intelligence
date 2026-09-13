"""Tests for the sub-agent plugin — delegate_task as a tool of the tool loop."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import aifred.plugins.tools.subagent as sub
from aifred.lib.bubble import (
    KIND_COLLAPSIBLE,
    KIND_SANDBOX_HTML,
    KIND_SANDBOX_IMAGE,
    KIND_SOURCES,
    KIND_VISION_IMAGES,
    KIND_VLM_OUTPUT,
    BubbleArtifact,
)
from aifred.lib.function_calling import Tool, ToolKit
from aifred.lib.plugin_base import PluginContext, plugin_display_name
from aifred.lib.security import (
    TIER_COMMUNICATE,
    TIER_READONLY,
    TIER_WRITE_DATA,
    TIER_WRITE_SYSTEM,
)


@pytest.fixture
def plugin(monkeypatch):
    """Plugin with defaults only: the settings.json on disk must not leak
    into the tests, whatever the gear icon last saved."""
    instance = sub.SubAgentPlugin()
    monkeypatch.setattr(instance, "_load_settings", lambda: {})
    return instance


@pytest.fixture
def ctx():
    return PluginContext(
        agent_id="aifred",
        lang="de",
        session_id="test_session",
        max_tier=TIER_WRITE_DATA,
        source="telegram",
        metadata={"chat_id": 7},
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _collect(agen):
    return [item async for item in agen]


def _tool(name: str, tier: int) -> Tool:
    return Tool(name=name, description=name, parameters={"type": "object"}, executor=lambda: name, tier=tier)


class TestPluginShape:
    def test_name_equals_folder(self, plugin):
        assert plugin.name == "subagent"
        assert plugin_display_name(plugin, "de") == "Sub-Agenten"
        assert plugin_display_name(plugin, "en") == "Sub-Agents"

    def test_delegate_tool_is_readonly(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        assert set(tools) == {sub.TOOL_NAME}
        assert tools[sub.TOOL_NAME].tier == TIER_READONLY
        props = tools[sub.TOOL_NAME].parameters["properties"]
        assert set(props) == {"task", "expected_result"}

    def test_no_tool_at_max_depth(self, plugin, ctx):
        # Default depth 1: a sub-agent (depth 1) must not see delegate_task.
        ctx.metadata[sub.DEPTH_METADATA_KEY] = 1
        assert plugin.get_tools(ctx) == []

    def test_agent_parameter_only_with_setting_and_candidates(self, plugin, ctx, monkeypatch):
        monkeypatch.setattr(plugin, "_load_settings", lambda: {sub.DELEGATE_OTHERS_KEY: "1"})
        monkeypatch.setattr(sub, "delegation_candidates", lambda c: ["codine"])
        monkeypatch.setattr(sub, "delegation_legend", lambda c, s, d, cands: "LEGEND")
        props = plugin.get_tools(ctx)[0].parameters["properties"]
        assert props["agent"]["enum"] == ["codine"]
        assert props["agent"]["description"].endswith("LEGEND")
        # Setting on, but nobody differs from the caller: parameter absent.
        monkeypatch.setattr(sub, "delegation_candidates", lambda c: [])
        props = plugin.get_tools(ctx)[0].parameters["properties"]
        assert "agent" not in props

    def test_default_settings(self, plugin):
        s = plugin.settings()
        assert s.allowed_tiers == frozenset({TIER_READONLY, TIER_WRITE_DATA})
        assert s.max_depth == 1
        assert s.delegate_to_other_agents is False

    def test_ui_status(self, plugin):
        assert "Sub-Agent" in plugin.get_ui_status(sub.TOOL_NAME, {"task": "x\ny"}, "de")
        assert plugin.get_ui_status("calculate", {}, "de") == ""


class TestDelegationLegend:
    def test_groups_follow_whitelist_tiers_and_plugin_texts(self, ctx, monkeypatch):
        import aifred.lib.agent_config as agent_config
        import aifred.lib.plugin_base as plugin_base
        import aifred.lib.plugin_registry as registry

        workspace, channel, sandbox = (SimpleNamespace(key=k) for k in ("ws", "tg", "sb"))
        seen_depth = []

        def fake_collect(sub_ctx):
            seen_depth.append(sub_ctx.metadata[sub.DEPTH_METADATA_KEY])
            return [
                (workspace, [_tool("read_file", TIER_READONLY), _tool("delete_file", TIER_WRITE_SYSTEM)]),
                (channel, [_tool("telegram_send", TIER_COMMUNICATE)]),
                (sandbox, [_tool("execute_code", TIER_WRITE_DATA)]),
            ]

        agents = {
            "codine": SimpleNamespace(tools=["read_file", "telegram_send"], display_name="Codine"),
            "hal": SimpleNamespace(tools=None, display_name="HAL 9000"),
        }
        monkeypatch.setattr(registry, "collect_plugin_tools", fake_collect)
        monkeypatch.setattr(agent_config, "get_agent_config", agents.get)
        monkeypatch.setattr(plugin_base, "plugin_display_name", lambda o, lang: f"{o.key}-{lang}")
        monkeypatch.setattr(plugin_base, "plugin_description", lambda o, lang: f"about {o.key}")

        legend = sub.delegation_legend(ctx, sub._parse_settings({}), 0, ["codine", "hal"])

        assert seen_depth == [1]  # built from the sub-agent's own depth
        lines = legend.splitlines()
        # Tier 1 (sending) is outside the default allowed tiers, so the channel
        # never shows up; codine's whitelist hides the sandbox.
        assert "- codine (Codine): ws-de" in lines
        assert "- hal (HAL 9000): ws-de, sb-de" in lines
        assert "- ws-de: about ws" in lines and "- sb-de: about sb" in lines
        assert not any("tg" in line for line in lines)

    def test_system_agents_are_no_candidates(self, ctx, monkeypatch):
        import aifred.lib.agent_config as agent_config

        roles = {"aifred": "main", "codine": "custom", "calibration": agent_config.ROLE_SYSTEM}
        monkeypatch.setattr(agent_config, "get_agent_ids", lambda: list(roles))
        monkeypatch.setattr(
            agent_config, "get_agent_config", lambda aid: SimpleNamespace(role=roles[aid]),
        )
        monkeypatch.setattr(sub, "_agent_model_and_tools", lambda aid, state: (aid, None))
        assert sub.delegation_candidates(ctx) == ["codine"]


class TestToolkit:
    def test_inherits_source_and_tier_and_filters_tiers(self, ctx, monkeypatch):
        seen = {}

        async def fake_prepare(agent_id, user_query, **kwargs):
            seen.update(kwargs, agent_id=agent_id)
            return "", ToolKit(tools=[
                _tool("read_file", TIER_READONLY),
                _tool("telegram_send", TIER_COMMUNICATE),
                _tool("write_file", TIER_WRITE_DATA),
                _tool("delete_file", TIER_WRITE_SYSTEM),
            ])

        monkeypatch.setattr(sub, "prepare_agent_toolkit", fake_prepare)
        settings = sub._parse_settings({})
        kit = _run(sub.build_subagent_toolkit(ctx, settings, 0, "aifred", "tue etwas"))
        assert [t.name for t in kit.tools] == ["read_file", "write_file"]
        assert kit._source == "telegram" and kit._max_tier == TIER_WRITE_DATA
        assert seen["memory_enabled"] is False
        assert seen["source"] == "telegram" and seen["max_tier"] == TIER_WRITE_DATA
        assert seen["metadata"] == {"chat_id": 7, sub.DEPTH_METADATA_KEY: 1}
        assert seen["llm_history"] == []

    def test_no_tools_left_means_no_toolkit(self, ctx, monkeypatch):
        async def fake_prepare(agent_id, user_query, **kwargs):
            return "", ToolKit(tools=[_tool("telegram_send", TIER_COMMUNICATE)])

        monkeypatch.setattr(sub, "prepare_agent_toolkit", fake_prepare)
        assert _run(sub.build_subagent_toolkit(ctx, sub._parse_settings({}), 0, "aifred", "x")) is None


class TestRun:
    @pytest.fixture(autouse=True)
    def fake_runtime(self, monkeypatch):
        async def fake_prepare(agent_id, user_query, **kwargs):
            return "", ToolKit(tools=[_tool("read_file", TIER_READONLY)])

        monkeypatch.setattr(sub, "prepare_agent_toolkit", fake_prepare)
        monkeypatch.setattr(
            sub, "resolve_run_params",
            lambda agent_id, state: sub.RunParams("llamacpp", "http://x", "test-model", 8192, 0.5),
        )
        monkeypatch.setattr(sub, "build_llm_options", lambda state, agent, t, n: {"temperature": t, "num_ctx": n})

        class FakeClient:
            def __init__(self, **kwargs):
                self.closed = False

            async def close(self):
                self.closed = True

        monkeypatch.setattr(sub, "LLMClient", FakeClient)

        captured = {}

        async def fake_stream(client, model, messages, options, label, toolkit=None, retry=False):
            captured.update(model=model, messages=messages, toolkit=toolkit, label=label)
            yield {"type": "thinking", "text": "erst lesen"}
            yield {"type": "tool_call", "name": "read_file", "arguments": '{"path": "a.txt"}'}
            yield {"type": "tool_progress", "message": "lese …"}
            yield {"type": "tool_result", "name": "read_file", "result": "INHALT VON A"}
            yield {"type": "content", "text": "Der Bericht: "}
            yield {"type": "content", "text": "alles gut."}
            yield {"type": "pipeline_result", "result": self.pipeline_result}

        self.pipeline_result = SimpleNamespace(text_clean="Der Bericht: alles gut.", artifacts=[])
        monkeypatch.setattr(sub, "run_llm_stream", fake_stream)
        self.captured = captured

    def test_report_is_result_and_transcript_is_an_artifact(self, plugin, ctx):
        tool = plugin.get_tools(ctx)[0]
        events = _run(_collect(tool.executor(task="Lies a.txt", expected_result="Zusammenfassung")))
        kinds = [next(iter(e)) for e in events]
        assert kinds.count("result") == 1 and kinds[-1] == "result"
        assert kinds.count("artifacts") == 1
        assert "progress" in kinds
        result = [e for e in events if "result" in e][0]["result"]
        assert result == "Der Bericht: alles gut."
        (transcript,) = [e for e in events if "artifacts" in e][0]["artifacts"]
        assert transcript["kind"] == KIND_COLLAPSIBLE
        assert "Lies a.txt" in transcript["data"]["title"]
        pieces = ("erst lesen", "read_file", 'path: "a.txt"', "INHALT VON A", "Der Bericht: alles gut.")
        positions = [transcript["data"]["content"].index(piece) for piece in pieces]
        assert positions == sorted(positions)  # in the order the run happened

    def test_artifacts_of_the_subagent_turn_go_up(self, plugin, ctx):
        from aifred.lib.sandbox import SANDBOX_HTML_URL_MARKER, SANDBOX_IMAGE_URL_MARKER
        self.pipeline_result.artifacts = [
            BubbleArtifact(KIND_COLLAPSIBLE, {"title": "inner", "content": "tief"}, offset=3),
            BubbleArtifact(KIND_SOURCES, {"urls": [{"url": "https://example.org", "success": True}]}),
            BubbleArtifact(KIND_SANDBOX_HTML, {"url": "/_upload/sandbox_output/s/page.html"}),
            BubbleArtifact(KIND_SANDBOX_IMAGE, {"url": "/_upload/sandbox_output/s/plot.png"}),
            BubbleArtifact(KIND_VISION_IMAGES, {"urls": ["/_upload/vigilantia/a.jpg"], "alt": "Tür"}),
        ]

        tool = plugin.get_tools(ctx)[0]
        events = _run(_collect(tool.executor(task="Lies a.txt", expected_result="Zusammenfassung")))

        forwarded = [e for e in events if "artifacts" in e][0]["artifacts"]
        assert [a["kind"] for a in forwarded] == [
            KIND_COLLAPSIBLE, KIND_COLLAPSIBLE, KIND_SOURCES, KIND_SANDBOX_HTML, KIND_SANDBOX_IMAGE,
            KIND_VISION_IMAGES,
        ]
        assert "Lies a.txt" in forwarded[0]["data"]["title"]  # own transcript first
        lines = [e for e in events if "result" in e][0]["result"].splitlines()
        # Markers lead the report, so a capped result keeps them.
        assert lines[0] == f"{SANDBOX_HTML_URL_MARKER}/_upload/sandbox_output/s/page.html"
        assert lines[1] == f"{SANDBOX_IMAGE_URL_MARKER}/_upload/sandbox_output/s/plot.png"
        assert lines[2] == "Der Bericht: alles gut."

    def test_subagent_prompt_has_no_persona_and_carries_the_task(self, plugin, ctx):
        tool = plugin.get_tools(ctx)[0]
        _run(_collect(tool.executor(task="Lies a.txt", expected_result="Zusammenfassung")))
        messages = self.captured["messages"]
        assert [m["role"] for m in messages] == ["system", "user"]
        assert "Sub-Agent" in messages[0]["content"] or "SUB-AGENT" in messages[0]["content"]
        assert "Butler" not in messages[0]["content"]
        assert "Lies a.txt" in messages[1]["content"] and "Zusammenfassung" in messages[1]["content"]
        assert self.captured["model"] == "test-model"
        assert [t.name for t in self.captured["toolkit"].tools] == ["read_file"]

    def test_unknown_delegation_target_is_refused(self, plugin, ctx, monkeypatch):
        monkeypatch.setattr(plugin, "_load_settings", lambda: {sub.DELEGATE_OTHERS_KEY: "1"})
        monkeypatch.setattr(sub, "delegation_candidates", lambda c: ["codine"])
        tool = plugin.get_tools(ctx)[0]
        events = _run(_collect(tool.executor(task="x", expected_result="y", agent="sokrates")))
        assert len(events) == 1
        assert "error" in json.loads(events[0]["result"])


class TestToolLoopIntegration:
    def test_toolkit_forwards_artifacts_event(self):
        async def executor(task: str):
            yield {"progress": "p"}
            yield {"artifacts": [{"kind": KIND_COLLAPSIBLE, "data": {"title": "T", "content": "C"}}]}
            yield {"result": "R"}

        kit = ToolKit(
            tools=[Tool(name="t", description="t", parameters={"type": "object"}, executor=executor)],
            _source="browser", _max_tier=4,
        )
        events = _run(_collect(kit.execute_streaming("t", {"task": "x"})))
        assert [e["type"] for e in events] == ["tool_progress", "tool_artifacts", "tool_result"]
        assert events[1]["artifacts"] == [{"kind": KIND_COLLAPSIBLE, "data": {"title": "T", "content": "C"}}]
        assert events[2]["result"] == "R"

    def test_transcript_shows_code_arguments_in_full(self):
        code = "html = open('fibonacci.html', 'w')\n" + "x = 1\n" * 100
        line = sub.format_transcript_call("TOOL", "execute_code", json.dumps({"code": code, "description": "d"}))
        assert line.startswith("[TOOL] execute_code\ncode:\n")
        assert code in line and 'description: "d"' in line
        # Arguments that are not JSON are shown as they came.
        assert sub.format_transcript_call("TOOL", "t", "{kaputt") == "[TOOL] t({kaputt)"

    def test_collapsible_html_escapes(self):
        from aifred.lib.formatting import build_tool_collapsible
        html = build_tool_collapsible({"title": "🤝 <x>", "content": "a < b\n```code```"})
        assert "<details" in html and "&lt;x&gt;" in html and "a &lt; b" in html

    def test_anchor_offsets_survive_post_processing_shifts(self):
        from aifred.lib.llm_pipeline import _ARTIFACT_ANCHOR, resolve_artifact_anchors
        streamed = "<think>a</think>vorher" + _ARTIFACT_ANCHOR.format(0) + "<think>b</think>nachher"
        # A prepend during post-processing moves the anchor along with the text.
        text, offsets = resolve_artifact_anchors("BILD\n\n" + streamed)
        assert "\x00" not in text
        assert text[offsets[0]:].startswith("<think>b</think>nachher")
        assert text[:offsets[0]].endswith("vorher")

    def test_bubble_orders_thinking_text_and_artifacts_by_turn(self):
        from aifred.lib.bubble import render_bubble
        text = "![Tür](/_upload/vigilantia/a.jpg)\n\n<think>erst</think>Ich delegiere.<think>dann</think>Ergebnis."
        offset = text.index("<think>dann")
        artifacts = [
            BubbleArtifact(KIND_COLLAPSIBLE, {"title": "TRANSKRIPT", "content": "x"}, offset=offset),
            BubbleArtifact(KIND_VISION_IMAGES, {"urls": ["/_upload/vigilantia/a.jpg"], "alt": "Tür"}, offset=offset),
        ]
        html = render_bubble(text, artifacts)
        order = [html.index(m) for m in ("erst", "Ich delegiere.", "TRANSKRIPT", "vigilantia/a.jpg", "dann", "Ergebnis.")]
        assert order == sorted(order)
        # The history reference at the top is not shown a second time.
        assert html.count("vigilantia/a.jpg") == 1

    def test_hub_view_hides_tags_but_keeps_results(self):
        from aifred.lib.bubble import render_bubble
        text = "<think>intern</think>Antwort."
        artifacts = [
            BubbleArtifact(KIND_VLM_OUTPUT, {"body": "VLM sagt"}, offset=0),
            BubbleArtifact(KIND_SANDBOX_IMAGE, {"url": "/_upload/sandbox_output/s/plot.png"}, offset=len(text)),
        ]
        html = render_bubble(text, artifacts, show_tags=False)
        assert "intern" not in html and "VLM sagt" not in html
        assert "Antwort." in html and "plot.png" in html
