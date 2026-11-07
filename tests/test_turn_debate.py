"""The multi-agent modes of the server turn (lib/turn_debate.py) without a model: the LLM calls are
stand-ins that answer per speaker, the session file, event hub, seats and agents from a test agents.json, the
prompts, bubbles, votes and cancellation run for real."""

import asyncio
import json
import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from aifred.lib import agent_config, session_events, session_storage, turn_service
from aifred.lib.agent_tuning import default_agent_tuning
from aifred.lib.llm_pipeline import PipelineResult
from aifred.lib.runtime_settings import RuntimeSettings

SESSION = "e" * 32


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A session on disk, the default agents in a test agents.json, every model call replaced.

    ``world["replies"][label]`` lists what the speaker with that log label says, call by call (an
    unscripted call says "<label> spricht."); ``world["calls"]`` records ``(label, messages)``.
    """
    from aifred.lib import (
        agent_memory,
        calibration_gate,
        llm_client,
        llm_engine,
        llm_pipeline,
        runtime_settings,
    )
    from aifred.lib.research import context_utils

    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    session_storage.save_session(SESSION, {"data": {"chat_history": [], "llm_history": []}}, owner="ki")
    session_events._channels.pop(SESSION, None)
    agents_file = tmp_path / "agents.json"
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    agents_file.write_text(json.dumps({"agents": agent_config._default_agents()}), encoding="utf-8")

    view = RuntimeSettings(
        backend_type="ollama", backend_url="http://x", temperature_mode="auto", ui_language="de",
        enable_tts=False, automatik_model_id="", agent_tuning=default_agent_tuning(),
        tts_autoplay=False, tts_agents={}, max_debate_rounds=3, consensus_type="majority",
    )
    view.agent_tuning["aifred"].model_id = "qwen3:8b"
    world: dict[str, Any] = {
        "view": view, "replies": {}, "calls": [], "compressed_for": [], "block_label": None,
        "agents_file": agents_file, "memory": [],
    }

    async def intent(_view: Any, _client: Any, _text: str) -> turn_service._Intent:
        return world.get("intent") or turn_service._Intent("FAKTISCH", None, "de", {}, False, 0.0)

    async def compression(_ctx: Any, agents: list[str]) -> None:
        world["compressed_for"].append(agents)

    async def toolkit(*_args: Any, **kwargs: Any) -> tuple[str, None]:
        world["memory"].append(kwargs["memory_enabled"])
        return "", None

    async def stream(_client: Any, _model: str, messages: list[dict[str, Any]], _options: Any, label: str,
                     **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        world["calls"].append((label, messages))
        scripted = world["replies"].get(label) or []
        text = scripted.pop(0) if scripted else f"{label} spricht."
        yield {"type": "content", "text": text}
        if label == world["block_label"]:
            world["streamed"].set()
            await asyncio.Event().wait()   # until the turn is cancelled
        yield {"type": "pipeline_result", "result": PipelineResult(text=text, answer_text=text, text_clean=text)}

    async def title(*_args: Any, **_kwargs: Any) -> str:
        return "Debatte"

    class _Client:
        def __init__(self, **_kwargs: Any) -> None: ...
        async def close(self) -> None: ...

    monkeypatch.setattr(runtime_settings, "load_runtime_settings", lambda: view)
    monkeypatch.setattr(calibration_gate, "is_calibration_active", lambda: False)
    monkeypatch.setattr(turn_service, "_detect_intent", intent)
    monkeypatch.setattr(turn_service, "_compress", compression)
    monkeypatch.setattr(agent_memory, "prepare_agent_toolkit", toolkit)
    monkeypatch.setattr(llm_pipeline, "run_llm_stream", stream)
    monkeypatch.setattr(llm_client, "LLMClient", _Client)
    monkeypatch.setattr(llm_engine, "generate_session_title", title)
    monkeypatch.setattr(context_utils, "get_agent_num_ctx", lambda *_args: (32768, "test"))
    yield world
    session_storage._session_meta_cache.clear()
    session_events._channels.pop(SESSION, None)


def _mode(mode: str, **config: Any) -> None:
    session_storage.update_session_config(SESSION, multi_agent_mode=mode, **config)


def _edit_agents(world: dict[str, Any], change: Any) -> None:
    raw = json.loads(world["agents_file"].read_text(encoding="utf-8"))
    change(raw["agents"])
    world["agents_file"].write_text(json.dumps(raw), encoding="utf-8")


def _data() -> dict[str, Any]:
    session = session_storage.load_session(SESSION)
    assert session is not None
    data: dict[str, Any] = session["data"]
    return data


def _bubbles() -> list[tuple[str, str, int | None]]:
    """(agent, mode, round) of every reply in the chat."""
    return [(e["agent"], e["mode"], e["round_num"]) for e in _data()["chat_history"] if e["role"] == "assistant"]


def _speakers() -> list[str]:
    """The speaker label of every reply in the llm history."""
    return [e["content"].split("]:")[0] + "]" for e in _data()["llm_history"] if e["role"] == "assistant"]


def _events(kind: str) -> list[dict[str, Any]]:
    return [event.data for event in session_events.replay_after(SESSION, 0) if event.kind == kind]


def _turn(text: str = "Ist Kaffee gesund?") -> None:
    async def scenario() -> None:
        await turn_service.run_turn(turn_service.TurnRequest(SESSION, text))
        await asyncio.sleep(0)   # the title task

    asyncio.run(scenario())


def _system_prompt(world: dict[str, Any], label: str) -> str:
    content: str = next(messages for call_label, messages in world["calls"] if call_label == label)[0]["content"]
    return content


# ── critical_review ──────────────────────────────────────────────────────


def test_critical_review_answers_then_the_critic_critiques_once(world: dict[str, Any]) -> None:
    _mode("critical_review")
    _turn()
    assert _bubbles() == [("aifred", "direct", None), ("sokrates", "critical_review", 1)]
    assert _speakers() == ["[AIFRED]", "[SOKRATES]"]
    assert world["compressed_for"] == [["aifred", "sokrates"]]
    critique = _data()["chat_history"][-1]["content"]
    assert "[Kritische Prüfung R1]" in critique          # the mode is the step: no prefix
    assert [e["agent"] for e in _events("agent_started")] == ["aifred", "sokrates"]
    assert _events("turn_finished")[0]["status"] == "done"


def test_an_incognito_debate_recalls_no_memory_for_any_speaker(world: dict[str, Any]) -> None:
    _mode("critical_review")
    session_storage.update_session_config(SESSION, incognito=True)
    _turn()
    assert world["memory"] and not any(world["memory"])          # the answer and every debate member


def test_addressing_the_main_agent_asks_for_its_answer_alone(world: dict[str, Any]) -> None:
    _mode("critical_review")
    world["intent"] = turn_service._Intent("FAKTISCH", "aifred", "de", {}, False, 0.0)
    _turn("AIfred, ist Kaffee gesund?")
    assert _bubbles() == [("aifred", "direct", None)]


# ── auto_consensus ───────────────────────────────────────────────────────


def test_auto_consensus_ends_when_the_votes_reach_consensus(world: dict[str, Any]) -> None:
    _mode("auto_consensus")
    world["replies"] = {
        "Sokrates": ["Zu einseitig.", "Jetzt passt es. [LGTM]"],
        "Salomo": ["Noch nicht. [WEITER]", "Ausgewogen. [LGTM]"],
        "AIfred Refinement": ["Verbessert: in Maßen gesund. [LGTM]"],
    }
    _turn()
    assert _bubbles() == [
        ("aifred", "direct", None),
        ("sokrates", "critical_review", 1), ("salomo", "synthesis", 1),
        ("aifred", "refinement", 2),
        ("sokrates", "critical_review", 2), ("salomo", "synthesis", 2),
    ]                                                      # 3/3 in round 2: no round 3
    assert _speakers() == ["[AIFRED]", "[SOKRATES]", "[SALOMO]", "[AIFRED]", "[SOKRATES]", "[SALOMO]"]
    assert world["compressed_for"] == [["aifred", "sokrates", "salomo"]]
    chat = _data()["chat_history"]
    assert "[Auto-Konsens: Synthese R1]" in chat[3]["content"]
    assert "[Auto-Konsens: Verfeinerung R2]" in chat[4]["content"]
    assert any("Consensus reached in round 2" in event["line"] for event in _events("debug"))
    # The refinement answers the synthesis: it is the main agent's task, after the history.
    refinement = next(messages for label, messages in world["calls"] if label == "AIfred Refinement")
    assert refinement[-1]["role"] == "user" and "Noch nicht." in refinement[-1]["content"]


def test_without_consensus_auto_consensus_stops_after_max_debate_rounds(world: dict[str, Any]) -> None:
    _mode("auto_consensus")
    world["view"].max_debate_rounds = 2
    _turn()
    assert _bubbles() == [
        ("aifred", "direct", None),
        ("sokrates", "critical_review", 1), ("salomo", "synthesis", 1),
        ("aifred", "refinement", 2),
        ("sokrates", "critical_review", 2), ("salomo", "synthesis", 2),
    ]                                                      # no refinement after the last round
    assert any("No consensus after 2 rounds" in event["line"] for event in _events("debug"))


def test_unanimous_consensus_needs_all_three_votes(world: dict[str, Any]) -> None:
    _mode("auto_consensus")
    world["view"].consensus_type = "unanimous"
    world["view"].max_debate_rounds = 1
    world["replies"] = {"Sokrates": ["Gut so. [LGTM]"], "Salomo": ["Einverstanden. [LGTM]"]}
    _turn()
    # 2 of 3 (the direct answer did not vote): a majority, but not unanimous.
    assert any("No consensus after 1 rounds" in event["line"] for event in _events("debug"))


# ── tribunal ─────────────────────────────────────────────────────────────


def test_tribunal_alternates_attack_and_defense_and_the_judge_speaks_last(world: dict[str, Any]) -> None:
    _mode("tribunal")
    world["view"].max_debate_rounds = 2
    _turn()
    assert _bubbles() == [
        ("aifred", "direct", None),
        ("sokrates", "tribunal", 1), ("aifred", "tribunal", 2),
        ("sokrates", "tribunal", 2),
        ("salomo", "verdict", 2),
    ]
    assert _speakers() == ["[AIFRED]", "[SOKRATES]", "[AIFRED]", "[SOKRATES]", "[SALOMO]"]
    chat = _data()["chat_history"]
    assert "[Tribunal R1]" in chat[2]["content"]
    assert "[Tribunal: Urteil R2]" in chat[-1]["content"]
    # The judge reads the whole debate as an observer: every contribution as a labelled user turn.
    verdict = next(messages for label, messages in world["calls"] if label == "Salomo")
    assert all(message["role"] in ("system", "user") for message in verdict)
    assert any(message["content"].startswith("[Sokrates]: ") for message in verdict)   # under his display name


def test_a_seat_whose_agent_is_gone_rejects_the_turn(world: dict[str, Any]) -> None:
    _mode("tribunal", debate_seats={"critic": "gibt-es-nicht"})
    _turn()
    rejected = _events("turn_rejected")
    assert rejected[0]["reason"] == "seat" and "'gibt-es-nicht'" in rejected[0]["message"]
    assert [entry["role"] for entry in _data()["chat_history"]] == ["user"]
    assert _events("agent_started") == []                  # rejected before anyone spoke


# ── seats instead of names ───────────────────────────────────────────────


@pytest.fixture
def new_agent(world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Adds an agent with nothing but its persona (identity, task, direct): it takes a seat with the seat's task."""
    from aifred.lib import prompt_loader

    prompts = tmp_path / "prompts"
    shutil.copytree(prompt_loader.PROMPTS_DIR, prompts)
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", prompts)

    def add(agent: str, name: str) -> None:
        for lang in ("de", "en"):
            (prompts / lang / agent).mkdir()
            (prompts / lang / agent / "identity.txt").write_text(f"Du bist {name}.", encoding="utf-8")
            (prompts / lang / agent / "task.txt").write_text(f"{name.upper()}-PERSONA", encoding="utf-8")
            (prompts / lang / agent / "direct.txt").write_text(f"{name} antwortet direkt.", encoding="utf-8")
        _edit_agents(world, lambda agents: agents.update({agent: {
            "display_name": name, "emoji": "🧐", "description": name, "role": "agent",
            "prompts": {"identity": f"{agent}/identity.txt", "task": f"{agent}/task.txt", "direct": f"{agent}/direct.txt"},
        }}))

    return add


def test_the_sessions_critic_critiques_with_the_seats_task(world: dict[str, Any], new_agent: Any) -> None:
    from aifred.lib.debate_seats import seat_prompt

    new_agent("kritikus", "Kritikus")
    _mode("critical_review", debate_seats={"critic": "kritikus"})
    _turn()
    assert _bubbles() == [("aifred", "direct", None), ("kritikus", "critical_review", 1)]
    assert _speakers() == ["[AIFRED]", "[KRITIKUS]"]
    system = _system_prompt(world, "Kritikus")
    seats = {"main": "aifred", "critic": "kritikus", "judge": "salomo"}
    assert "KRITIKUS-PERSONA" in system and seat_prompt("critic", seats, "de", round_num=1) in system


def test_the_author_of_the_answer_defends_it_not_aifred(world: dict[str, Any], new_agent: Any) -> None:
    # Code review and refactoring: who wrote the draft answers the critique (07.11.2026: AIfred did).
    new_agent("autorin", "Autorin")
    _mode("tribunal", active_agent="autorin")
    world["view"].max_debate_rounds = 2
    _turn()
    assert _bubbles() == [
        ("autorin", "direct", None),
        ("sokrates", "tribunal", 1), ("autorin", "tribunal", 2),
        ("sokrates", "tribunal", 2),
        ("salomo", "verdict", 2),
    ]
    assert "Autorin" in _system_prompt(world, "Sokrates")                    # the critic knows whom it attacks


def test_the_temperature_offset_belongs_to_the_agent(world: dict[str, Any]) -> None:
    from aifred.lib.agent_settings import resolve_agent_temperature

    view = world["view"]
    view.agent_tuning["aifred"].temperature = 0.5
    view.agent_tuning["sokrates"].temperature_offset = 0.2
    assert resolve_agent_temperature(view, "sokrates") == pytest.approx(0.7)
    assert resolve_agent_temperature(view, "salomo") == pytest.approx(0.5 + view.agent_tuning["salomo"].temperature_offset)
    view.temperature_mode = "manual"
    assert resolve_agent_temperature(view, "sokrates") == view.agent_tuning["sokrates"].temperature


# ── symposion ────────────────────────────────────────────────────────────


def test_symposion_lets_the_selected_agents_speak_in_turn_each_round(world: dict[str, Any]) -> None:
    _mode("symposion", symposion_agents=["sokrates", "salomo"])
    world["view"].max_debate_rounds = 2
    world["replies"] = {"Sokrates": ["Erst die Frage klären.", "Nachtrag."]}
    _turn()
    assert _bubbles() == [
        ("sokrates", "symposion", 1), ("salomo", "symposion", 1),
        ("sokrates", "symposion", 2), ("salomo", "symposion", 2),
    ]                                                      # no direct answer before the round table
    assert _speakers() == ["[SOKRATES]", "[SALOMO]", "[SOKRATES]", "[SALOMO]"]
    assert world["compressed_for"] == [["sokrates", "salomo"]]
    assert "[Symposion R2]" in _data()["chat_history"][-1]["content"]
    salomo_r1 = world["calls"][1][1]
    assert salomo_r1[-1] == {"role": "user", "content": "[Sokrates]: Erst die Frage klären."}
    sokrates_r2 = world["calls"][2][1]
    assert {"role": "assistant", "content": "[Sokrates]: Erst die Frage klären."} in sokrates_r2


def test_symposion_with_one_agent_is_its_direct_answer(world: dict[str, Any]) -> None:
    _mode("symposion", symposion_agents=["salomo"])
    _turn()
    assert _bubbles() == [("salomo", "direct", None)]


# ── cancellation ─────────────────────────────────────────────────────────


def test_a_cancelled_debate_keeps_the_streamed_critique(world: dict[str, Any]) -> None:
    _mode("critical_review")
    world["block_label"] = "Sokrates"
    world["replies"] = {"Sokrates": ["Halb gedacht"]}

    async def scenario() -> None:
        world["streamed"] = asyncio.Event()
        task = turn_service.start_turn(turn_service.TurnRequest(SESSION, "Ist Kaffee gesund?"))
        await world["streamed"].wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    reply = _data()["chat_history"][-1]
    assert reply["agent"] == "sokrates" and reply["content"].startswith("Halb gedacht")
    assert _speakers() == ["[AIFRED]", "[SOKRATES]"]
    assert _events("turn_finished")[0]["status"] == "cancelled"
