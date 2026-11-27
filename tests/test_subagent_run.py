"""SubagentSession (lib/subagent_run): extra tools, follow-up in the same run, access to the toolkit."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

import aifred.lib.subagent_run as run
from aifred.lib.agent_config import get_agent_config
from aifred.lib.bubble import SECTION_TEXT, SECTION_THINKING
from aifred.lib import tool_output_cap
from aifred.backends.base import LLMOptions
from aifred.lib.function_calling import Tool, ToolKit
from aifred.lib.plugin_base import PluginContext
from aifred.lib.security import TIER_READONLY, TIER_WRITE_DATA, TIER_WRITE_SYSTEM


def _tool(name: str, tier: int) -> Tool:
    return Tool(name=name, description=name, parameters={"type": "object"}, executor=lambda: name, tier=tier)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _collect(agen):
    return [item async for item in agen]


@pytest.fixture
def ctx():
    return PluginContext(agent_id="aifred", lang="de", session_id="s", max_tier=TIER_WRITE_DATA, source="browser")


@pytest.fixture
def fake_runtime(monkeypatch):
    """Stand-ins for the toolkit source and the model; records what the model gets in every call."""
    calls: list[dict[str, Any]] = []
    answers = ["Erster Bericht.", "Nachgereichter Bericht."]

    async def fake_prepare(agent_id, user_query, **kwargs):
        return "", ToolKit(tools=[_tool("read_file", TIER_READONLY), _tool("delete_file", TIER_WRITE_SYSTEM)])

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def close(self):
            pass

    async def fake_stream(client, model, messages, options, label, *, source, toolkit=None, compression=None):
        calls.append({"messages": list(messages), "toolkit": toolkit})
        # Like the real loop: it appends its own rounds to the list it is given.
        messages.append({"role": "assistant", "content": "interne Runde"})
        text = answers[len(calls) - 1]
        yield {"type": "content", "text": text}
        yield {"type": "pipeline_result", "result": SimpleNamespace(
            text_clean=text, artifacts=[], debug_msg="done",
            metrics={"work": {"prefill_tokens": 1, "prefill_s": 1.0, "decode_tokens": 1, "decode_s": 1.0}},
            metadata_dict={"ttft": 1.0, "tokens_per_sec": 1.0, "inference_time": 1.0, "source": "x", "backend_type": "llamacpp"},
        )}

    monkeypatch.setattr(run, "prepare_agent_toolkit", fake_prepare)
    monkeypatch.setattr(run, "resolve_run_params", lambda agent_id: run.RunParams("llamacpp", "http://x", "m", 8192, 0.5))
    monkeypatch.setattr(run, "build_llm_options", lambda state, agent, model, t, n: LLMOptions(temperature=t, num_ctx=n))
    monkeypatch.setattr(run, "LLMClient", FakeClient)
    monkeypatch.setattr(run, "run_llm_stream", fake_stream)
    return calls


def _session(ctx, **kwargs) -> run.SubagentSession:
    return run.SubagentSession(
        ctx, allowed_tiers=frozenset({TIER_READONLY, TIER_WRITE_DATA}), depth=0, target_agent="aifred",
        task="Suche", expected_result="Liste", **kwargs,
    )


class TestExtraTools:
    def test_extra_tools_join_the_toolkit_whatever_the_tiers(self, ctx, fake_runtime):
        extra = _tool("plan_record", TIER_WRITE_SYSTEM)  # outside the allowed tiers
        session = _session(ctx, extra_tools=[extra])
        _run(_collect(session.run()))
        assert [t.name for t in fake_runtime[0]["toolkit"].tools] == ["read_file", "plan_record"]
        assert session.toolkit is not None and extra in session.toolkit.tools

    def test_extra_tools_alone_make_a_toolkit(self, ctx, fake_runtime, monkeypatch):
        async def nothing(agent_id, user_query, **kwargs):
            return "", None

        monkeypatch.setattr(run, "prepare_agent_toolkit", nothing)
        session = _session(ctx, extra_tools=[_tool("plan_record", TIER_READONLY)])
        _run(_collect(session.run()))
        assert session.toolkit is not None
        assert [t.name for t in session.toolkit.tools] == ["plan_record"]

    def test_without_extra_tools_the_tier_filter_alone_decides(self, ctx, fake_runtime):
        session = _session(ctx)
        _run(_collect(session.run()))
        assert session.toolkit is not None
        assert [t.name for t in session.toolkit.tools] == ["read_file"]


class TestFollowUp:
    def test_follow_up_continues_with_the_same_toolkit_and_the_report(self, ctx, fake_runtime):
        session = _session(ctx)
        first = _run(_collect(session.run()))
        second = _run(_collect(session.follow_up("Es fehlt Position 3.")))

        assert next(e for e in first if "result" in e)["result"] == "Erster Bericht."
        assert next(e for e in second if "result" in e)["result"] == "Nachgereichter Bericht."
        # The same ToolKit instance, not a rebuilt one
        assert fake_runtime[0]["toolkit"] is fake_runtime[1]["toolkit"] is session.toolkit
        messages = fake_runtime[1]["messages"]
        assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
        assert messages[0]["content"] == fake_runtime[0]["messages"][0]["content"]
        assert messages[1]["content"] == fake_runtime[0]["messages"][1]["content"]
        # The report, not the loop's inner rounds
        assert messages[2]["content"] == "Erster Bericht."
        assert messages[3]["content"] == "Es fehlt Position 3."

    def test_follow_up_yields_the_same_kinds_of_events_and_its_own_transcript(self, ctx, fake_runtime):
        session = _session(ctx)
        _run(_collect(session.run()))
        events = _run(_collect(session.follow_up("Nochmal")))
        kinds = [next(iter(e)) for e in events]
        assert kinds.count("result") == 1 and kinds[-1] == "result"
        assert kinds.count("artifacts") == 1 and kinds.count("work") == 1
        (transcript,) = next(e for e in events if "artifacts" in e)["artifacts"]
        texts = [section["text"] for section in transcript["data"]["sections"]]
        # The order stays in the follow-up's own transcript too, its task first
        assert texts[0] == "NACHFORDERUNG:\nNochmal"
        assert "[BERICHT]\nNachgereichter Bericht." in texts

    def test_a_second_follow_up_sees_both_earlier_reports(self, ctx, fake_runtime, monkeypatch):
        session = _session(ctx)
        _run(_collect(session.run()))
        _run(_collect(session.follow_up("eins")))
        run_answers = ["x", "y", "Dritter Bericht."]
        monkeypatch.setattr(run, "run_llm_stream", _third_round_stream(fake_runtime, run_answers))
        _run(_collect(session.follow_up("zwei")))
        roles_and_text = [(m["role"], m["content"]) for m in fake_runtime[2]["messages"][2:]]
        assert roles_and_text == [
            ("assistant", "Erster Bericht."), ("user", "eins"),
            ("assistant", "Nachgereichter Bericht."), ("user", "zwei"),
        ]

    def test_follow_up_after_an_empty_report_keeps_the_roles_alternating(self, ctx, fake_runtime, monkeypatch):
        answers = ["", "Nachgereichter Bericht."]

        async def stream(client, model, messages, options, label, *, source, toolkit=None, compression=None):
            fake_runtime.append({"messages": list(messages), "toolkit": toolkit})
            yield {"type": "pipeline_result", "result": SimpleNamespace(
                text_clean=answers[len(fake_runtime) - 1], artifacts=[], debug_msg="done",
                metrics={"work": {"prefill_tokens": 1, "prefill_s": 1.0, "decode_tokens": 1, "decode_s": 1.0}},
                metadata_dict={"ttft": 1.0, "tokens_per_sec": 1.0, "inference_time": 1.0, "source": "x", "backend_type": "llamacpp"},
            )}

        monkeypatch.setattr(run, "run_llm_stream", stream)
        session = _session(ctx)
        first = _run(_collect(session.run()))
        assert "error" in json.loads(next(e for e in first if "result" in e)["result"])
        _run(_collect(session.follow_up("Es fehlt Position 3.")))
        assert [m["role"] for m in fake_runtime[1]["messages"]] == ["system", "user", "assistant", "user"]

    def test_the_budget_of_a_follow_up_counts_the_whole_history(self, ctx, fake_runtime, monkeypatch):
        budgets: list[int] = []
        real_compute = tool_output_cap.compute_budget

        def spy(effective_ctx, sys_tok, hist_tok, mem_tok=0, tools_tok=0):
            budgets.append(hist_tok)
            return real_compute(effective_ctx, sys_tok, hist_tok, mem_tok, tools_tok)

        monkeypatch.setattr(tool_output_cap, "compute_budget", spy)
        session = _session(ctx)
        _run(_collect(session.run()))
        _run(_collect(session.follow_up("kurz")))
        first_hist, follow_up_hist = budgets
        # task + earlier report + the new text, not the new text alone
        assert follow_up_hist > first_hist

    def test_follow_up_before_run_is_an_error(self, ctx, fake_runtime):
        with pytest.raises(RuntimeError):
            _run(_collect(_session(ctx).follow_up("x")))


def _third_round_stream(calls, answers):
    async def stream(client, model, messages, options, label, *, source, toolkit=None, compression=None):
        calls.append({"messages": list(messages), "toolkit": toolkit})
        yield {"type": "pipeline_result", "result": SimpleNamespace(
            text_clean=answers[2], artifacts=[], debug_msg="done",
            metrics={"work": {"prefill_tokens": 1, "prefill_s": 1.0, "decode_tokens": 1, "decode_s": 1.0}},
            metadata_dict={"ttft": 1.0, "tokens_per_sec": 1.0, "inference_time": 1.0, "source": "x", "backend_type": "llamacpp"},
        )}
    return stream


class TestToolkitAccess:
    def test_toolkit_is_none_before_run(self, ctx):
        assert _session(ctx).toolkit is None

    def test_unknown_agent_is_refused_without_building_anything(self, ctx, fake_runtime):
        session = run.SubagentSession(
            ctx, allowed_tiers=frozenset({TIER_READONLY}), depth=0, target_agent="gibtsnicht", task="x", expected_result="y",
        )
        events = _run(_collect(session.run()))
        assert len(events) == 1 and "error" in events[0]["result"]
        assert session.toolkit is None and fake_runtime == []


def _scripted(monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]], tools: list[Tool]) -> None:
    """The sub-agent's model says exactly ``events`` (then the report) with the given tools in its toolkit."""
    async def prepare(agent_id, user_query, **kwargs):
        return "", ToolKit(tools=tools)

    async def stream(client, model, messages, options, label, *, source, toolkit=None, compression=None):
        for event in events:
            yield event
        yield {"type": "pipeline_result", "result": SimpleNamespace(
            text_clean="Fertig.", artifacts=[], debug_msg="done",
            metrics={"work": {"prefill_tokens": 1, "prefill_s": 1.0, "decode_tokens": 1, "decode_s": 1.0}},
            metadata_dict={"ttft": 1.0, "tokens_per_sec": 1.0, "inference_time": 1.0, "source": "x", "backend_type": "llamacpp"},
        )}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(run, "prepare_agent_toolkit", prepare)
    monkeypatch.setattr(run, "resolve_run_params", lambda agent_id: run.RunParams("llamacpp", "http://x", "m", 8192, 0.5))
    monkeypatch.setattr(run, "build_llm_options", lambda state, agent, model, t, n: LLMOptions(temperature=t, num_ctx=n))
    monkeypatch.setattr(run, "LLMClient", FakeClient)
    monkeypatch.setattr(run, "run_llm_stream", stream)


def _display_name(agent_id: str) -> str:
    config = get_agent_config(agent_id)
    assert config is not None
    return config.display_name


def _sections(session: run.SubagentSession) -> list[dict[str, str]]:
    events = _run(_collect(session.run()))
    (transcript,) = next(e for e in events if "artifacts" in e)["artifacts"]
    sections: list[dict[str, str]] = transcript["data"]["sections"]
    return sections


class TestTranscriptSections:
    def test_the_task_stays_the_first_section_and_the_thinking_is_a_section_of_its_own(self, ctx, monkeypatch):
        _scripted(monkeypatch, [
            {"type": "thinking", "text": "ich suche zuerst"},
            {"type": "content", "text": "Ich suche jetzt."},
            {"type": "tool_call", "name": "read_file", "arguments": '{"path": "a.txt"}'},
            {"type": "tool_result", "name": "read_file", "result": "INHALT"},
        ], [_tool("read_file", TIER_READONLY)])
        sections = _sections(_session(ctx))
        assert sections[0] == {"kind": SECTION_TEXT, "text": "AUFGABE:\nSuche\n\nERWARTETES ERGEBNIS:\nListe"}
        assert [section["kind"] for section in sections] == [
            SECTION_TEXT, SECTION_THINKING, SECTION_TEXT, SECTION_TEXT, SECTION_TEXT, SECTION_TEXT,
        ]
        assert sections[1]["text"] == "ich suche zuerst"
        assert [section["text"] for section in sections[2:]] == [
            f"[{_display_name('aifred')}]\nIch suche jetzt.", "[WERKZEUG] read_file\npath: \"a.txt\"", "[ERGEBNIS]\nINHALT", "[BERICHT]\nFertig.",
        ]
        # No marker line for the thinking: the nested block carries its label
        assert not any("DENKEN" in section["text"] for section in sections)

    def test_the_results_of_the_research_tools_are_not_in_the_transcript_but_their_calls_are(self, ctx, monkeypatch):
        research = {"type": "tool_result", "result": "SEITENINHALT " * 1000}
        _scripted(monkeypatch, [
            {"type": "tool_call", "name": "web_search", "arguments": '{"queries": ["preis xyz"]}'},
            {**research, "name": "web_search"},
            {"type": "tool_call", "name": "web_fetch", "arguments": '{"url": "https://shop.example/a"}'},
            {**research, "name": "web_fetch"},
            {"type": "tool_call", "name": "plan_record", "arguments": '{"id": "p1"}'},
            {"type": "tool_result", "name": "plan_record", "result": "Eintrag gespeichert."},
            {"type": "tool_call", "name": "read_file", "arguments": '{"path": "x"}'},
            {"type": "tool_result", "name": "read_file", "result": json.dumps({"error": "nicht gefunden"})},
        ], [
            Tool(name="web_search", description="", parameters={"type": "object"}, executor=lambda: "", tier=TIER_READONLY,
                 transcript_without_result=True),
            Tool(name="web_fetch", description="", parameters={"type": "object"}, executor=lambda: "", tier=TIER_READONLY,
                 transcript_without_result=True),
            _tool("plan_record", TIER_READONLY), _tool("read_file", TIER_READONLY),
        ])
        texts = [section["text"] for section in _sections(_session(ctx))]
        assert not any("SEITENINHALT" in text for text in texts)
        # The calls with their arguments stay, so the queries and addresses can be read
        assert '[WERKZEUG] web_search\nqueries: ["preis xyz"]' in texts
        assert '[WERKZEUG] web_fetch\nurl: "https://shop.example/a"' in texts
        # What the other tools say, errors included, stays as before
        assert "[ERGEBNIS]\nEintrag gespeichert." in texts
        assert '[ERGEBNIS]\n{"error": "nicht gefunden"}' in texts
        assert sum(text.startswith("[ERGEBNIS]") for text in texts) == 2

    def test_the_research_tools_of_the_plugin_say_themselves_that_their_result_is_no_transcript_text(self):
        from aifred.plugins.tools.research import get_research_tools
        tools = get_research_tools(lang="de", llm_history=[], session_id="s")
        assert {tool.name: tool.transcript_without_result for tool in tools} == {"web_search": True, "web_fetch": True}

    def test_read_file_says_itself_that_the_file_content_is_no_transcript_text(self):
        # A job's notes (113 KB on 25.11.2026) stood in full in every part's bubble; the call with the file name stays.
        from aifred.lib.plugin_base import PluginContext
        from aifred.plugins.tools.workspace import WorkspacePlugin
        tools = WorkspacePlugin().get_tools(PluginContext(agent_id="aifred", lang="de", session_id="s", source="browser"))
        flags = {tool.name: tool.transcript_without_result for tool in tools}
        assert flags["read_file"] is True
        assert flags["list_files"] is False

    def test_a_result_of_a_tool_the_toolkit_does_not_hold_stays(self, ctx, monkeypatch):
        _scripted(monkeypatch, [
            {"type": "tool_call", "name": "read_file", "arguments": "{}"},
            {"type": "tool_result", "name": "read_file", "result": "INHALT"},
        ], [_tool("read_file", TIER_READONLY)])
        assert "[ERGEBNIS]\nINHALT" in [section["text"] for section in _sections(_session(ctx))]
