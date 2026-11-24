"""SubagentSession (lib/subagent_run): extra tools, follow-up in the same run, access to the toolkit."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import aifred.lib.subagent_run as run
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
        assert "NACHFORDERUNG:\nNochmal" in transcript["data"]["content"]
        assert "Nachgereichter Bericht." in transcript["data"]["content"]

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
