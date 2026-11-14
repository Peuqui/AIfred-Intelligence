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

from fake_tuning import default_agents_tuning

from aifred.lib import agent_config, session_events, session_storage, turn_service
from aifred.lib.i18n import t
from aifred.lib.llm_pipeline import PipelineResult
from aifred.lib.runtime_settings import RuntimeSettings

SESSION = "e" * 32


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A session on disk, the default agents in a test agents.json, every model call replaced.

    ``world["replies"][label]`` lists what the speaker with that log label says, call by call (an
    unscripted call says "<label> spricht."); ``world["calls"]`` records ``(label, messages)``,
    ``world["temperatures"]`` ``(label, temperature)``.
    """
    from aifred.lib import (
        agent_memory,
        calibration_gate,
        llm_client,
        session_title,
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
        backend_type="ollama", backend_url="http://x", ui_language="de",
        enable_tts=False, automatik_model_id="", agent_tuning=default_agents_tuning(),
        tts_autoplay=False, tts_agents={}, max_debate_rounds=3, consensus_type="majority",
    )
    view.agent_tuning["aifred"].model_id = "qwen3:8b"
    world: dict[str, Any] = {
        "view": view, "replies": {}, "calls": [], "temperatures": [], "compressed_for": [], "block_label": None,
        "agents_file": agents_file, "memory": [],
    }

    async def intent(_view: Any, _client: Any, _text: str) -> turn_service._Intent:
        return world.get("intent") or turn_service._Intent(None, "de", {}, False, 0.0)

    async def compression(_ctx: Any, agents: list[str], _symposion: bool) -> None:
        world["compressed_for"].append(agents)

    async def toolkit(*_args: Any, **kwargs: Any) -> tuple[str, None]:
        world["memory"].append(kwargs["memory_enabled"])
        return "", None

    async def stream(_client: Any, _model: str, messages: list[dict[str, Any]], options: Any, label: str,
                     **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        world["calls"].append((label, messages))
        world["temperatures"].append((label, options.temperature))
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
    monkeypatch.setattr(session_title, "generate_session_title", title)
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


def _turn(text: str = "Ist Kaffee gesund?", source: str = "browser", trust: str | None = None) -> None:
    """``trust``: a channel's message from that sender (security.resolve_trust_label); None: the chat."""
    origin = None if trust is None else turn_service.TurnOrigin({"role": "user", "content": text}, text, 2, trust, {})

    async def scenario() -> None:
        await turn_service.run_turn(turn_service.TurnRequest(SESSION, text, source=source, origin=origin))
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
    world["intent"] = turn_service._Intent("aifred", "de", {}, False, 0.0)
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
    # The refinement is the main agent's task, after the history; the synthesis it answers is in the
    # history (the last contribution), not copied into the task a second time.
    refinement = next(messages for label, messages in world["calls"] if label == "AIfred Refinement")
    assert refinement[-1]["role"] == "user" and "Noch nicht." not in refinement[-1]["content"]
    assert any("Noch nicht." in message["content"] for message in refinement[:-1])


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


def test_in_round_1_only_critic_and_judge_vote(world: dict[str, Any]) -> None:
    """The direct answer was never asked to vote: in round 1 it does not count (until 14.11.2026 it
    counted as a no, so a unanimous consensus could never come in round 1)."""
    _mode("auto_consensus")
    world["view"].consensus_type = "unanimous"
    world["replies"] = {"Sokrates": ["Gut so. [LGTM]"], "Salomo": ["Einverstanden. [LGTM]"]}
    _turn()
    assert any("Consensus reached in round 1 (2/2 votes, unanimous)" in event["line"] for event in _events("debug"))


def test_unanimous_consensus_needs_every_vote(world: dict[str, Any]) -> None:
    _mode("auto_consensus")
    world["view"].consensus_type = "unanimous"
    world["view"].max_debate_rounds = 1
    world["replies"] = {"Sokrates": ["Gut so. [LGTM]"], "Salomo": ["Noch nicht. [WEITER]"]}
    _turn()
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
    assert (rejected[0]["reason"], rejected[0]["message"]) == ("seat", t("error_critic_unfit", agent="gibt-es-nicht"))
    assert [entry["role"] for entry in _data()["chat_history"]] == ["user"]
    assert _events("agent_started") == []                  # rejected before anyone spoke


def _switch_off(world: dict[str, Any], agent: str) -> None:
    _edit_agents(world, lambda agents: agents[agent].update(enabled=False))


def test_an_answering_agent_switched_off_rejects_the_turn_out_loud(world: dict[str, Any]) -> None:
    _switch_off(world, "sokrates")
    session_storage.update_session_config(SESSION, active_agent="sokrates")
    _turn()
    rejected = _events("turn_rejected")
    assert (rejected[0]["reason"], rejected[0]["message"]) == ("agent", t("error_agent_switched_off", agent="Sokrates"))
    assert _events("agent_started") == []                  # nobody answers in its place


def test_an_agent_switched_off_takes_no_seat(world: dict[str, Any]) -> None:
    _switch_off(world, "sokrates")
    _mode("critical_review")                                # Sokrates is the default critic
    _turn()
    rejected = _events("turn_rejected")
    assert (rejected[0]["reason"], rejected[0]["message"]) == ("seat", t("error_critic_unfit", agent="Sokrates"))
    assert _events("agent_started") == []


def test_an_agent_switched_off_leaves_the_round_table(world: dict[str, Any]) -> None:
    _switch_off(world, "salomo")
    _mode("symposion", symposion_agents=["aifred", "sokrates", "salomo"])
    world["view"].max_debate_rounds = 1
    _turn()
    assert _bubbles() == [("aifred", "symposion", 1), ("sokrates", "symposion", 1)]


# ── seats instead of names ───────────────────────────────────────────────


@pytest.fixture
def new_agent(world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Adds an agent with nothing but its persona (identity, personality): it takes a seat with the seat's task."""
    from aifred.lib import prompt_loader

    prompts = tmp_path / "prompts"
    shutil.copytree(prompt_loader.PROMPTS_DIR, prompts)
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", prompts)

    def add(agent: str, name: str) -> None:
        for lang in ("de", "en"):
            (prompts / lang / agent).mkdir()
            (prompts / lang / agent / "identity.txt").write_text(f"Du bist {name}.", encoding="utf-8")
            (prompts / lang / agent / "personality.txt").write_text(f"{name.upper()}-PERSONA", encoding="utf-8")
        _edit_agents(world, lambda agents: agents.update({agent: {
            "display_name": name, "emoji": "🧐", "description": name, "role": "agent", "color": "#7d8590",
            "prompts": {"identity": f"{agent}/identity.txt", "personality": f"{agent}/personality.txt"},
        }}))

    return add


def test_the_sessions_critic_critiques_with_the_seats_task(world: dict[str, Any], new_agent: Any) -> None:
    from aifred.lib.debate_seats import round_rules, seat_prompt

    new_agent("kritikus", "Kritikus")
    _mode("critical_review", debate_seats={"critic": "kritikus"})
    _turn()
    assert _bubbles() == [("aifred", "direct", None), ("kritikus", "critical_review", 1)]
    assert _speakers() == ["[AIFRED]", "[KRITIKUS]"]
    system = _system_prompt(world, "Kritikus")
    seats = {"main": "aifred", "critic": "kritikus", "judge": "salomo"}
    task = seat_prompt("critic", seats, "de", round_rules=round_rules("critic", True, seats, "de"), voting="")
    assert "KRITIKUS-PERSONA" in system and task in system
    assert "[LGTM]" not in task and "Salomo" not in task        # no vote and no judge in a single critique


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


def test_every_speaker_answers_with_its_own_temperature(world: dict[str, Any]) -> None:
    # None inherits AIfred's or adds to it (07.11.2026: no automatic temperature).
    _mode("tribunal")
    world["view"].max_debate_rounds = 2
    for agent, temperature in (("aifred", 0.4), ("sokrates", 0.9), ("salomo", 0.1)):
        world["view"].agent_tuning[agent].temperature = temperature
    _turn()
    assert world["temperatures"] == [
        ("AIfred", 0.4), ("Sokrates", 0.9), ("AIfred Defense", 0.4), ("Sokrates", 0.9), ("Salomo", 0.1),
    ]


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
    # The round table reads the session's history like every speaker (message_builder): the others under
    # their names, its own contributions as its own without a label (it would imitate it), the user's
    # question under the user's name, and last its style reminder and output language.
    salomo_r1 = world["calls"][1][1]
    assert salomo_r1[-2] == {"role": "user", "content": "[Sokrates]: Erst die Frage klären."}
    assert salomo_r1[-1]["content"].endswith("[AUSGABESPRACHE: DEUTSCH]")
    assert salomo_r1[1]["role"] == "user" and salomo_r1[1]["content"].startswith("[")      # the user, named
    sokrates_r2 = world["calls"][2][1]
    assert {"role": "assistant", "content": "Erst die Frage klären."} in sokrates_r2
    assert "Lücken" in sokrates_r2[-1]["content"]                # the reflection: in the last message, not the system


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


# ── recensio ────────────────────────────────────────────────────────────


def test_recensio_ends_when_the_censor_gives_lgtm(world: dict[str, Any]) -> None:
    _mode("recensio")
    world["replies"] = {
        "Sokrates": ["Befund: Zeile 12 rechnet falsch. [WEITER]", "Alles behoben. [LGTM]"],
        "AIfred Fix": ["Zeile 12 korrigiert."],
    }
    _turn("Bau mir einen Rechner.")
    # A round is a fix and its review; the first review, of the answer, belongs to no round.
    assert _bubbles() == [
        ("aifred", "direct", None),
        ("sokrates", "recensio", None),
        ("aifred", "correctura", 1),
        ("sokrates", "recensio", 1),
    ]
    chat = _data()["chat_history"]
    assert "[Recensio]" in chat[2]["content"]                     # the mode is the step: no prefix
    assert "[Recensio: Correctura R1]" in chat[3]["content"]
    assert "[Recensio R1]" in chat[4]["content"]
    assert any("Recensio passed after 1 fixes" in event["line"] for event in _events("debug"))
    # The author fixes, not the reviewer: the review is its task, after the history.
    fix = next(messages for label, messages in world["calls"] if label == "AIfred Fix")
    assert fix[-1]["role"] == "user" and "KORREKTUR 1" in fix[-1]["content"]
    assert any("Zeile 12 rechnet falsch." in message["content"] for message in fix[:-1])   # the review: in the history
    review = _system_prompt(world, "Sokrates")
    assert "CODE-PRÜFUNG" in review and "DIESE PRÜFUNG: die erste" in review and "Neu: —" in review
    # The two roles of the review, not the dialectic of three; the review in place of the everyday task.
    assert "Recensio, einer Code-Prüfung mit zwei Rollen" in review and "Antithese" not in review
    assert "Du bist Sokrates" in review                                # identity and personality stay
    fix_system = fix[0]["content"]
    assert "Recensio, einer Code-Prüfung mit zwei Rollen" in fix_system and "Antithese" not in fix_system


def test_without_lgtm_the_recensio_stops_after_max_debate_rounds(world: dict[str, Any]) -> None:
    _mode("recensio")
    world["view"].max_debate_rounds = 2
    _turn("Bau mir einen Rechner.")
    assert _bubbles() == [
        ("aifred", "direct", None),
        ("sokrates", "recensio", None),
        ("aifred", "correctura", 1), ("sokrates", "recensio", 1),
        ("aifred", "correctura", 2), ("sokrates", "recensio", 2),
    ]                                                                  # two rounds: two fixes, each re-checked
    assert any("Recensio still open after 2 rounds" in event["line"] for event in _events("debug"))


def test_the_reviewer_gets_the_files_the_author_changed(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The author's direct answer writes a file through its toolkit; the review names it."""
    from aifred.lib import agent_memory, document_changes, llm_pipeline
    from aifred.lib.function_calling import Tool, ToolKit

    documents = tmp_path / "documents"
    documents.mkdir()
    monkeypatch.setattr(document_changes, "DOCUMENTS_DIR", documents)

    def write(path: str) -> str:
        (documents / path).write_text("<html></html>", encoding="utf-8")
        return "ok"

    async def toolkit(agent: str, *_args: Any, **_kwargs: Any) -> tuple[str, ToolKit]:
        tools = [Tool(name="execute_code_write", description="x", parameters={}, executor=write, tier=2)]
        return "", ToolKit(tools=tools, _source="browser", _max_tier=4)

    scripted = llm_pipeline.run_llm_stream
    tools_of: dict[str, list[str]] = {}

    async def stream(client: Any, model: str, messages: list[dict[str, Any]], options: Any, label: str,
                     **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        tools_of[label] = [tool.name for tool in kwargs["toolkit"].tools]
        if label == "AIfred":
            await kwargs["toolkit"].execute("execute_code_write", {"path": "rechner.html"})
        async for event in scripted(client, model, messages, options, label, **kwargs):
            yield event

    monkeypatch.setattr(agent_memory, "prepare_agent_toolkit", toolkit)
    monkeypatch.setattr(llm_pipeline, "run_llm_stream", stream)
    _mode("recensio")
    world["replies"] = {"Sokrates": ["Geprüft. [LGTM]"]}
    _turn("Bau mir einen Rechner.")
    assert "Neu: rechner.html" in _system_prompt(world, "Sokrates")
    # The censor reads and tests, it writes nothing (RECENSIO_CENSOR_TOOLS); the auctor keeps its tools.
    assert tools_of == {"AIfred": ["execute_code_write"], "Sokrates": []}


# ── where the message came from ──────────────────────────────────────────


SECURITY = "SICHERHEITSHINWEIS"   # prompts/de/shared/security_boundary.txt


@pytest.mark.parametrize("mode", ["critical_review", "auto_consensus", "tribunal", "recensio"])
def test_a_strangers_message_gives_every_speaker_the_security_boundary(world: dict[str, Any], mode: str) -> None:
    """A stranger's message (<external_message>): every system prompt of the turn explains it. From 07.11.2026
    (turn_service) until 14.11.2026 no prompt builder got the source, none had it."""
    _mode(mode)
    world["view"].max_debate_rounds = 1
    _turn(source="email", trust="external")
    systems = {label: messages[0]["content"] for label, messages in world["calls"]}
    assert systems and all(SECURITY in system for system in systems.values()), [
        label for label, system in systems.items() if SECURITY not in system
    ]


def test_the_owners_message_has_no_security_boundary(world: dict[str, Any]) -> None:
    """The chat and the owner on a channel (Peuqui 14.11.2026: the restrictions are for strangers only)."""
    _mode("critical_review")
    _turn()
    _turn(source="telegram", trust="owner")
    assert world["calls"] and all(SECURITY not in messages[0]["content"] for _, messages in world["calls"])


# ── what every speaker gets ──────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["critical_review", "auto_consensus", "tribunal", "recensio"])
def test_every_speaker_ends_with_its_reminder_and_the_output_language(world: dict[str, Any], mode: str) -> None:
    """Critic, judge, censor and author alike: the last message carries the style reminder and the output
    language (until 14.11.2026 only a speaker with a question of its own got them)."""
    _mode(mode)
    world["view"].max_debate_rounds = 2
    _turn()
    for label, messages in world["calls"]:
        assert messages[-1]["role"] == "user" and messages[-1]["content"].endswith("[AUSGABESPRACHE: DEUTSCH]"), label


def test_memories_never_go_into_a_system_prompt(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """A memory block changes with every question: in the system prompt it would void the prefix cache of the
    whole history behind it (message_builder.inject_before_question). Until 14.11.2026 the judge, the
    refinement and the correctura had it there."""
    from aifred.lib import agent_memory

    async def toolkit(*_args: Any, **_kwargs: Any) -> tuple[str, None]:
        return "ERINNERUNG-X", None

    monkeypatch.setattr(agent_memory, "prepare_agent_toolkit", toolkit)
    _mode("auto_consensus")
    world["view"].max_debate_rounds = 2
    _turn()
    for label, messages in world["calls"]:
        assert "ERINNERUNG-X" not in messages[0]["content"], label
        assert messages[-2]["content"] == "ERINNERUNG-X", label          # right before the last message


def test_each_mode_describes_only_the_seats_that_speak() -> None:
    from aifred.lib import turn_debate
    from aifred.lib.prompt_loader import CRITIQUE_ROLES, DEBATE_ROLES, REVIEW_ROLES, load_prompt

    seats = {"critic": "sokrates", "judge": "salomo"}
    expected = {"critical_review": CRITIQUE_ROLES, "recensio": REVIEW_ROLES, "auto_consensus": DEBATE_ROLES,
                "tribunal": DEBATE_ROLES}
    for mode, roles in expected.items():
        assert turn_debate.plan_turn(mode, "aifred", None, [], seats).roles == roles, mode
    critique = load_prompt(CRITIQUE_ROLES, lang="de", main_agent="AIfred", critic_agent="Sokrates", user_name="USER")
    assert "Salomo" not in critique and "{judge_agent}" not in critique
