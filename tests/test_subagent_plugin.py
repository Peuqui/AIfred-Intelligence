"""Tests for the sub-agent plugin — delegate_task as a tool of the tool loop."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

import aifred.lib.subagent_run as run
import aifred.plugins.tools.subagent as sub
from aifred.lib.bubble import (
    KIND_COLLAPSIBLE,
    KIND_SANDBOX_HTML,
    KIND_SANDBOX_IMAGE,
    KIND_SOURCES,
    KIND_VISION_IMAGES,
    KIND_VLM_OUTPUT,
    BubbleArtifact,
    code_section,
    text_section,
    thinking_section,
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
    monkeypatch.setattr(instance, "_load_settings", dict)
    return instance


@pytest.fixture
def ctx():
    return PluginContext(
        agent_id="aifred",
        lang="de",
        session_id="test_session",
        max_tier=TIER_WRITE_DATA,
        source="telegram",
        trust="owner",
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
        assert plugin_display_name(plugin, "de") == "Sub-Agentes"  # Latin, in every language (Peuqui 19.11.2026)
        assert plugin_display_name(plugin, "en") == "Sub-Agentes"

    def test_delegate_tool_is_readonly(self, plugin, ctx):
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        assert set(tools) == {sub.TOOL_NAME, sub.PLAN_TOOL_NAME}   # the plan tool: tests/test_run_task_plan_tool.py
        assert tools[sub.TOOL_NAME].tier == TIER_READONLY
        props = tools[sub.TOOL_NAME].parameters["properties"]
        assert set(props) == {"task", "expected_result"}

    def test_no_tool_at_max_depth(self, plugin, ctx):
        # Default depth 1: a sub-agent (depth 1) must not see delegate_task.
        ctx.metadata[run.DEPTH_METADATA_KEY] = 1
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

    def test_a_read_only_channel_says_so_in_the_description(self, plugin, ctx):
        """Echo: channel tier 1, sub-agents 0 and 2 → only reading reaches them."""
        note = "SUB-AGENTS CAN ONLY READ"
        assert note not in plugin.get_tools(ctx)[0].description          # tier 2: they may write
        ctx.max_tier = TIER_COMMUNICATE
        assert note in plugin.get_tools(ctx)[0].description

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
        import aifred.lib.plugin_registry as registry
        from aifred.lib import agent_config, plugin_base

        workspace, channel, sandbox = (SimpleNamespace(key=k) for k in ("ws", "tg", "sb"))
        seen_depth = []

        def fake_collect(sub_ctx):
            seen_depth.append(sub_ctx.metadata[run.DEPTH_METADATA_KEY])
            return [
                (workspace, [_tool("read_file", TIER_READONLY), _tool("delete_file", TIER_WRITE_SYSTEM)]),
                (channel, [_tool("telegram_send", TIER_COMMUNICATE)]),
                (sandbox, [_tool("execute_code", TIER_WRITE_DATA)]),
            ]

        agents = {
            "codine": SimpleNamespace(tools=["read_file", "telegram_send"], display_name="Codine"),
            "hal": SimpleNamespace(tools=["read_file", "execute_code"], display_name="HAL 9000"),
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

    def test_candidates_are_all_main_agents_but_caller_and_system(self, ctx, monkeypatch):
        from aifred.lib import agent_config

        # "twin" shares the caller's model and whitelist: its persona alone
        # makes it a candidate; "schlaefer" is switched off.
        def agent(role: str, enabled: bool = True) -> dict[str, Any]:
            return {"display_name": "x", "emoji": "x", "description": "", "role": role, "color": "#7d8590", "tools": [], "enabled": enabled}

        agents = {"aifred": agent("agent"), "codine": agent("agent"), "twin": agent("agent"),
                  "schlaefer": agent("agent", enabled=False), "calibration": agent(agent_config.ROLE_SYSTEM)}
        monkeypatch.setattr(agent_config, "load_agents_raw", lambda: agents)
        assert sub.delegation_candidates(ctx) == ["codine", "twin"]


class TestToolkit:
    def test_inherits_source_and_tier_and_filters_tiers(self, ctx, monkeypatch):
        seen: dict[str, Any] = {}

        async def fake_prepare(agent_id, user_query, **kwargs):
            seen.update(kwargs, agent_id=agent_id)
            return "", ToolKit(tools=[
                _tool("read_file", TIER_READONLY),
                _tool("telegram_send", TIER_COMMUNICATE),
                _tool("write_file", TIER_WRITE_DATA),
                _tool("delete_file", TIER_WRITE_SYSTEM),
            ])

        monkeypatch.setattr(run, "prepare_agent_toolkit", fake_prepare)
        settings = sub._parse_settings({})
        kit = _run(run.build_subagent_toolkit(ctx, settings.allowed_tiers, 0, "aifred", "tue etwas"))
        assert [t.name for t in kit.tools] == ["read_file", "write_file"]
        assert kit._source == "telegram" and kit._max_tier == TIER_WRITE_DATA
        assert seen["memory_enabled"] is False
        assert seen["source"] == "telegram" and seen["max_tier"] == TIER_WRITE_DATA
        # The caller's trust decides which plugins offer tools (the documents folder only to the owner); without it
        # the sub-agent of a scheduled job had no read_file and never found the job's notes (24./25.11.2026).
        assert seen["trust"] == "owner"
        assert seen["metadata"] == {"chat_id": 7, run.DEPTH_METADATA_KEY: 1}
        assert seen["llm_history"] == []

    def test_no_tools_left_means_no_toolkit(self, ctx, monkeypatch):
        async def fake_prepare(agent_id, user_query, **kwargs):
            return "", ToolKit(tools=[_tool("telegram_send", TIER_COMMUNICATE)])

        monkeypatch.setattr(run, "prepare_agent_toolkit", fake_prepare)
        assert _run(run.build_subagent_toolkit(ctx, sub._parse_settings({}).allowed_tiers, 0, "aifred", "x")) is None


class TestRun:
    @pytest.fixture(autouse=True)
    def fake_runtime(self, monkeypatch):
        async def fake_prepare(agent_id, user_query, **kwargs):
            return "", ToolKit(tools=[_tool("read_file", TIER_READONLY)])

        monkeypatch.setattr(run, "prepare_agent_toolkit", fake_prepare)
        monkeypatch.setattr(
            run, "resolve_run_params",
            lambda agent_id: run.RunParams("llamacpp", "http://x", "test-model", 8192, 0.5),
        )
        from aifred.backends.base import LLMOptions
        monkeypatch.setattr(run, "build_llm_options", lambda state, agent, model, t, n: LLMOptions(temperature=t, num_ctx=n))

        class FakeClient:
            def __init__(self, **kwargs):
                self.closed = False

            async def close(self):
                self.closed = True

        monkeypatch.setattr(run, "LLMClient", FakeClient)

        captured: dict[str, Any] = {}

        async def fake_stream(client, model, messages, options, label, *, source, toolkit=None, compression=None):
            captured.update(model=model, messages=messages, toolkit=toolkit, label=label, compression=compression)
            yield {"type": "thinking", "text": "erst lesen"}
            yield {"type": "tool_call", "name": "read_file", "arguments": '{"path": "a.txt"}'}
            yield {"type": "tool_progress", "message": "lese …"}
            yield {"type": "tool_result", "name": "read_file", "result": "INHALT VON A"}
            yield {"type": "tool_round", "text": "", "calls": [
                {"name": "read_file", "arguments": '{"path": "a.txt"}', "result": "Ausgeführt."}]}
            yield {"type": "content", "text": "Der Bericht: "}
            yield {"type": "content", "text": "alles gut."}
            yield {"type": "pipeline_result", "result": self.pipeline_result}

        self.pipeline_result = SimpleNamespace(
            text_clean="Der Bericht: alles gut.", artifacts=[],
            debug_msg="✅ Codine (Sub-Agent) done (12,0s, 600 tok, 51,3 tok/s)",
            metrics={"work": {"prefill_tokens": 900, "prefill_s": 1.5, "decode_tokens": 600, "decode_s": 10.0}},
            metadata_dict={"ttft": 1.5, "tokens_per_sec": 51.3, "inference_time": 12.0,
                           "source": "Codine (Sub-Agent) (test-model)", "backend_type": "llamacpp"},
        )
        monkeypatch.setattr(run, "run_llm_stream", fake_stream)
        self.captured = captured

    def test_report_is_result_and_transcript_is_an_artifact(self, plugin, ctx):
        tool = plugin.get_tools(ctx)[0]
        events = _run(_collect(tool.executor(task="Lies a.txt", expected_result="Zusammenfassung")))
        kinds = [next(iter(e)) for e in events]
        assert kinds.count("result") == 1 and kinds[-1] == "result"
        assert kinds.count("artifacts") == 1
        # The sub-agent's inference goes up so the caller's footer sums it
        (work,) = [e["work"] for e in events if "work" in e]
        # Its done line reaches the debug console like a main agent's
        assert any("done (" in e.get("progress", "") for e in events)
        assert work == self.pipeline_result.metrics["work"]
        assert "progress" in kinds
        result = next(e for e in events if "result" in e)["result"]
        assert result == "Der Bericht: alles gut."
        (transcript,) = next(e for e in events if "artifacts" in e)["artifacts"]
        assert transcript["kind"] == KIND_COLLAPSIBLE
        assert "Lies a.txt" in transcript["data"]["title"]
        assert transcript["data"]["icon"] == run.SUBAGENT_ICON
        sections = transcript["data"]["sections"]
        assert "content" not in transcript["data"]
        # The thinking is a section of its own, not a "[DENKEN]" line in the text
        assert [s["text"] for s in sections if s["kind"] == "thinking"] == ["erst lesen"]
        full_text = "\n".join(s["text"] for s in sections)
        pieces = ("erst lesen", "read_file", 'path: "a.txt"', "INHALT VON A", "Der Bericht: alles gut.")
        positions = [full_text.index(piece) for piece in pieces]
        assert positions == sorted(positions)  # in the order the run happened
        # The main agents' performance line, with model and backend, kept apart
        # from the transcript text and rendered italic in parentheses.
        footer = transcript["data"]["footer"]
        assert "TTFT" in footer and "test-model" in footer and "[llamacpp]" in footer
        assert "TTFT" not in full_text
        from aifred.lib.formatting import build_tool_collapsible
        html = build_tool_collapsible(transcript["data"])
        assert '<div style="padding: 0.6em 0 0 1em; font-style: italic;">( ' in html and " )</div>" in html

    def test_the_rounds_of_the_subagent_are_not_written_into_the_sessions_history(self, plugin, ctx, monkeypatch):
        # Its loop hands on a tool_round like every loop; only the turn service writes those into a session, and it
        # never sees the sub-agent's stream.
        from aifred.lib import turn_service

        def written(*_args, **_kwargs):
            raise AssertionError("the sub-agent wrote into the session")

        monkeypatch.setattr(turn_service, "_append", written)
        tool = plugin.get_tools(ctx)[0]
        events = _run(_collect(tool.executor(task="Lies a.txt", expected_result="Zusammenfassung")))
        assert not any("tool_round" in str(event) for event in events)
        assert next(e for e in events if "result" in e)["result"] == "Der Bericht: alles gut."

    def test_artifacts_of_the_subagent_turn_go_up(self, plugin, ctx):
        from aifred.lib.sandbox import SANDBOX_HTML_URL_MARKER, SANDBOX_IMAGE_URL_MARKER
        self.pipeline_result.artifacts = [
            BubbleArtifact(KIND_COLLAPSIBLE, {"title": "inner", "sections": [text_section("tief")]}, offset=3),
            BubbleArtifact(KIND_SOURCES, {"urls": [{"url": "https://example.org", "success": True}]}),
            BubbleArtifact(KIND_SANDBOX_HTML, {"url": "/_upload/sandbox_output/s/page.html"}),
            BubbleArtifact(KIND_SANDBOX_IMAGE, {"url": "/_upload/sandbox_output/s/plot.png"}),
            BubbleArtifact(KIND_VISION_IMAGES, {"urls": ["/_upload/vigilantia/a.jpg"], "alt": "Tür"}),
        ]

        tool = plugin.get_tools(ctx)[0]
        events = _run(_collect(tool.executor(task="Lies a.txt", expected_result="Zusammenfassung")))

        forwarded = next(e for e in events if "artifacts" in e)["artifacts"]
        assert [a["kind"] for a in forwarded] == [
            KIND_COLLAPSIBLE, KIND_COLLAPSIBLE, KIND_SOURCES, KIND_SANDBOX_HTML, KIND_SANDBOX_IMAGE,
            KIND_VISION_IMAGES,
        ]
        assert "Lies a.txt" in forwarded[0]["data"]["title"]  # own transcript first
        lines = next(e for e in events if "result" in e)["result"].splitlines()
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
        # Its own loop compresses with its own model, in the caller's language
        compression = self.captured["compression"]
        assert (compression.model, compression.language) == ("test-model", ctx.lang)

    def test_other_main_agent_brings_identity_and_personality(self, plugin, ctx, monkeypatch):
        from aifred.lib import prompt_loader
        monkeypatch.setattr(plugin, "_load_settings", lambda: {sub.DELEGATE_OTHERS_KEY: "1"})
        monkeypatch.setattr(sub, "delegation_candidates", lambda c: ["codine"])
        monkeypatch.setattr(sub, "delegation_legend", lambda c, s, d, cands: "LEGEND")
        monkeypatch.setattr(prompt_loader, "load_identity", lambda agent, lang=None: f"IDENTITY:{agent}")
        monkeypatch.setattr(prompt_loader, "load_personality", lambda agent, lang=None: f"PERSONALITY:{agent}")
        tool = plugin.get_tools(ctx)[0]

        _run(_collect(tool.executor(task="x", expected_result="y", agent="codine")))
        system = self.captured["messages"][0]["content"]
        # Same layer order as a normal turn, the frame in the task slot.
        pieces = ("IDENTITY:codine", "SUB-AGENT", "PERSONALITY:codine")
        positions = [system.index(piece) for piece in pieces]
        assert positions == sorted(positions)

        # Without ``agent`` the sub-agent stays a worker without persona.
        _run(_collect(tool.executor(task="x", expected_result="y")))
        system = self.captured["messages"][0]["content"]
        assert "IDENTITY:" not in system and "PERSONALITY:" not in system

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
            yield {"artifacts": [{"kind": KIND_COLLAPSIBLE, "data": {"title": "T", "sections": [text_section("C")]}}]}
            yield {"result": "R"}

        kit = ToolKit(
            tools=[Tool(name="t", description="t", parameters={"type": "object"}, executor=executor)],
            _source="browser", _max_tier=4,
        )
        events = _run(_collect(kit.execute_streaming("t", {"task": "x"})))
        assert [e["type"] for e in events] == ["tool_progress", "tool_artifacts", "tool_result"]
        assert events[1]["artifacts"] == [{"kind": KIND_COLLAPSIBLE, "data": {"title": "T", "sections": [text_section("C")]}}]
        assert events[2]["result"] == "R"

    def test_transcript_shows_code_arguments_in_full(self):
        code = "html = open('fibonacci.html', 'w')\n" + "x = 1\n" * 100
        line = run.format_transcript_call("TOOL", "execute_code", json.dumps({"code": code, "description": "d"}))
        assert line.startswith("[TOOL] execute_code\ncode:\n")
        assert code in line and 'description: "d"' in line
        # Arguments that are not JSON are shown as they came.
        assert run.format_transcript_call("TOOL", "t", "{kaputt") == "[TOOL] t({kaputt)"

    def test_collapsible_html_escapes(self):
        from aifred.lib.formatting import build_tool_collapsible
        html = build_tool_collapsible({"title": "🤝 <x>", "sections": [text_section("a < b\n```code```")]})
        assert "<details" in html and "&lt;x&gt;" in html and "a &lt; b" in html

    def test_collapsible_icon_in_accent_colour(self):
        from aifred.lib.formatting import build_tool_collapsible
        from aifred.theme import COLORS
        html = build_tool_collapsible({"icon": "⇄", "title": "Sub-Agent", "sections": [text_section("x")]})
        assert f'<span style="color: {COLORS["primary"]};">⇄</span> Sub-Agent</summary>' in html

    def test_collapsible_text_is_in_normal_font(self):
        from aifred.lib.formatting import build_tool_collapsible
        html = build_tool_collapsible({"title": "T", "sections": [text_section("Lies a.txt")]})
        # The protocol is prose to read, not code: the body font, no typewriter face, no smaller size.
        assert "monospace" not in html and "font-size: 0.95em" not in html
        assert "white-space: pre-wrap" in html

    def test_collapsible_code_section_keeps_the_typewriter_face(self):
        from aifred.lib.formatting import build_tool_collapsible
        html = build_tool_collapsible({"title": "T", "sections": [code_section("x = 1\nprint(x)")]})
        assert "font-family: monospace" in html and "x = 1&#10;print(x)" in html

    def test_collapsible_thinking_section_is_a_nested_thinking_block(self):
        from aifred.lib.formatting import build_tool_collapsible, format_thinking_process, get_ui_locale
        from aifred.lib.config import get_xml_tag_config
        html = build_tool_collapsible({"title": "T", "sections": [
            text_section("vorher"), thinking_section("erst <b>lesen</b>\n\nund & denken"), text_section("nachher"),
        ]})
        # An own <details> inside the outer one, in the same markup as the main agent's thinking block
        assert html.count("<details") == 2 and html.count("</details>") == 2
        think = get_xml_tag_config(get_ui_locale())["think"]
        assert f"{think['icon']} {think['label']}</summary>" in html and f'<div class="{think["class"]}" style="white-space: pre-wrap;' in html
        main_summary = format_thinking_process("<think>x</think>").splitlines()[1]
        assert main_summary in html
        # Content escaped, line breaks as the entity, no blank line anywhere (Markdown must stay off)
        assert "erst &lt;b&gt;lesen&lt;/b&gt;&#10;&#10;und &amp; denken" in html
        assert "\n\n" not in html and "monospace" not in html
        # pre-wrap must not show the markup's own line breaks as empty lines at the edges of the thinking
        assert 'word-break: break-word;">erst &lt;b&gt;' in html and "und &amp; denken</div>" in html
        # The order of the sections is kept
        order = [html.index(m) for m in ("vorher", "erst &lt;b&gt;", "nachher")]
        assert order == sorted(order)

    def test_collapsible_unknown_section_kind_is_refused(self):
        from aifred.lib.formatting import build_tool_collapsible
        with pytest.raises(ValueError, match="section kind"):
            build_tool_collapsible({"title": "T", "sections": [{"kind": "unbekannt", "text": "x"}]})

    def test_source_code_block_keeps_the_typewriter_face(self, tmp_path, monkeypatch):
        from aifred.lib import bubble, sandbox
        page = tmp_path / "page.html"
        page.write_text("<p>hi</p>\n", encoding="utf-8")
        monkeypatch.setattr(sandbox, "sandbox_output_path", lambda url: page)
        html = bubble._source_block("/_upload/sandbox_output/s/page.html")
        assert "font-family: monospace" in html and "&lt;p&gt;hi&lt;/p&gt;" in html

    def test_collapsible_content_stays_literal_text(self):
        from aifred.lib.formatting import build_tool_collapsible
        html = build_tool_collapsible({"title": "T", "sections": [text_section("a\n\n| x | y |\n|---|---|\n\n**b**")]})
        # One HTML block without any blank line, so Markdown never switches back
        # on inside it; line breaks travel as the &#10; entity.
        assert "\n\n" not in html and "a&#10;&#10;| x | y |" in html
        # No <pre>: the Reflex UI rendered <pre> with its code-block component, which
        # needed a <code> child and crashed the whole page (until 08.11.2026).
        assert "<pre" not in html
        # No second scroll box inside the <details>.
        assert "max-height" not in html

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
            BubbleArtifact(KIND_COLLAPSIBLE, {"title": "TRANSKRIPT", "sections": [text_section("x")]}, offset=offset),
            BubbleArtifact(KIND_VISION_IMAGES, {"urls": ["/_upload/vigilantia/a.jpg"], "alt": "Tür"}, offset=offset),
        ]
        html = render_bubble(text, artifacts)
        order = [html.index(m) for m in ("erst", "Ich delegiere.", "TRANSKRIPT", "vigilantia/a.jpg", "dann", "Ergebnis.")]
        assert order == sorted(order)
        # The history reference at the top is not shown a second time.
        assert html.count("vigilantia/a.jpg") == 1

    def test_bubble_shows_thinking_and_results_for_every_channel(self):
        from aifred.lib.bubble import render_bubble
        text = "<think>intern</think>Antwort."
        artifacts = [
            BubbleArtifact(KIND_VLM_OUTPUT, {"body": "VLM sagt"}, offset=0),
            BubbleArtifact(KIND_SANDBOX_IMAGE, {"url": "/_upload/sandbox_output/s/plot.png"}, offset=len(text)),
        ]
        html = render_bubble(text, artifacts)
        assert "intern" in html and "VLM sagt" in html
        assert "Antwort." in html and "plot.png" in html


def test_subagent_inherits_reasoning_from_its_caller() -> None:
    from aifred.backends.base import LLMOptions
    own = LLMOptions(temperature=0.3, enable_thinking=False, reasoning_effort=None, top_k=10)
    caller = LLMOptions(temperature=1.0, enable_thinking=True, reasoning_effort="medium", top_k=40)
    merged = run.inherit_reasoning(own, caller)
    # Thinking depth from the main agent, sampling from the target agent
    assert (merged.enable_thinking, merged.reasoning_effort) == (True, "medium")
    assert (merged.temperature, merged.top_k) == (0.3, 10)
