"""The server turn (lib/turn_service.py) without a model: the LLM calls are stand-ins, the session
file, event hub, routing, bubble building, cancellation and title run for real."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from fake_reading import FakeReading

from aifred.lib import audio_processing, reply_speech, session_events, session_storage, turn_service
from aifred.lib.agent_tuning import default_agent_tuning
from aifred.lib.llm_pipeline import PipelineResult
from aifred.lib.runtime_settings import RuntimeSettings

SESSION = "d" * 32


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A session on disk and every model call replaced; ``world["chunks"]`` is what the model streams."""
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

    view = RuntimeSettings(
        backend_type="ollama", backend_url="http://x", temperature_mode="auto", ui_language="de",
        enable_tts=False, automatik_model_id="", agent_tuning=default_agent_tuning(),
        tts_autoplay=False, tts_agents={}, max_debate_rounds=3, consensus_type="majority",
    )
    view.agent_tuning["aifred"].model_id = "qwen3:8b"
    world: dict[str, Any] = {"chunks": [{"type": "content", "text": "Hallo "}, {"type": "content", "text": "Peuqui."}],
                             "titles": [], "block": None, "view": view}

    async def intent(_view: Any, _client: Any, _text: str) -> turn_service._Intent:
        return world.get("intent") or turn_service._Intent("FAKTISCH", None, "de", {}, False, 0.0)

    async def no_compression(*_args: Any) -> None:
        return None

    async def toolkit(*_args: Any, **_kwargs: Any) -> tuple[str, None]:
        return "", None

    async def stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        text = ""
        for chunk in world["chunks"]:
            text += chunk["text"]
            yield chunk
            if world["block"] is not None:
                world["streamed"].set()
                await world["block"].wait()
        yield {"type": "pipeline_result", "result": PipelineResult(text=text, answer_text=text, text_clean=text)}

    async def title(user_text: str, reply: str, *_args: Any, **_kwargs: Any) -> str:
        world["titles"].append((user_text, reply))
        return "Begrüßung"

    class _Client:
        def __init__(self, **_kwargs: Any) -> None: ...
        async def close(self) -> None: ...

    monkeypatch.setattr(runtime_settings, "load_runtime_settings", lambda: view)
    monkeypatch.setattr(calibration_gate, "is_calibration_active", lambda: world.get("calibrating", False))
    monkeypatch.setattr(turn_service, "_detect_intent", intent)
    monkeypatch.setattr(turn_service, "_compress", no_compression)
    monkeypatch.setattr(agent_memory, "prepare_agent_toolkit", toolkit)
    monkeypatch.setattr(llm_pipeline, "run_llm_stream", stream)
    monkeypatch.setattr(llm_client, "LLMClient", _Client)
    monkeypatch.setattr(llm_engine, "generate_session_title", title)
    monkeypatch.setattr(context_utils, "get_agent_num_ctx", lambda *_args: (32768, "test"))
    yield world
    session_storage._session_meta_cache.clear()
    session_events._channels.pop(SESSION, None)
    reply_speech._readings.pop(SESSION, None)


def _kinds() -> list[str]:
    return [event.kind for event in session_events.replay_after(SESSION, 0) if event.kind != "debug"]


def _data() -> dict[str, Any]:
    session = session_storage.load_session(SESSION)
    assert session is not None
    data: dict[str, Any] = session["data"]
    return data


async def _turn(text: str) -> None:
    await turn_service.run_turn(turn_service.TurnRequest(SESSION, text))
    await asyncio.sleep(0)   # the title task


def test_a_turn_saves_both_entries_and_tells_the_clients(world: dict[str, Any]) -> None:
    asyncio.run(_turn("Hallo AIfred"))
    data = _data()
    assert [entry["role"] for entry in data["chat_history"]] == ["user", "assistant"]
    assert data["chat_history"][0]["content"] == "Hallo AIfred"
    assert "Hallo Peuqui." in data["chat_history"][1]["content"]
    assert data["llm_history"][0]["content"].endswith("Hallo AIfred")       # stamped user turn
    assert data["llm_history"][1]["content"] == "[AIFRED]: Hallo Peuqui."
    kinds = _kinds()
    assert "token" in kinds
    assert [kind for kind in kinds if kind not in ("token", "session_title")] == [
        "turn_started", "agent_started", "message_added", "turn_finished",
    ]
    finished = [e for e in session_events.replay_after(SESSION, 0) if e.kind == "turn_finished"]
    assert finished[0].data["status"] == "done"


def test_the_first_exchange_gets_a_title(world: dict[str, Any]) -> None:
    asyncio.run(_turn("Hallo AIfred"))
    assert world["titles"] == [(_data()["llm_history"][0]["content"], "Hallo Peuqui.")]
    assert "session_title" in _kinds()


def test_a_turn_during_calibration_is_rejected_and_saves_nothing(world: dict[str, Any]) -> None:
    world["calibrating"] = True
    asyncio.run(_turn("Hallo"))
    assert _data()["chat_history"] == []
    assert _kinds() == ["turn_rejected", "turn_finished"]


def test_a_mode_the_server_does_not_offer_is_refused(world: dict[str, Any]) -> None:
    # devils_advocate is no longer offered (config.MULTI_AGENT_MODES); an old session may still hold it.
    session_storage.update_session_config(SESSION, multi_agent_mode="devils_advocate")
    asyncio.run(_turn("Hallo"))
    rejected = [e for e in session_events.replay_after(SESSION, 0) if e.kind == "turn_rejected"]
    assert rejected[0].data["reason"] == "mode"
    assert [entry["role"] for entry in _data()["chat_history"]] == ["user"]


def test_an_address_sticks_to_the_session(world: dict[str, Any]) -> None:
    world["intent"] = turn_service._Intent("FAKTISCH", "sokrates", "de", {}, False, 0.0)
    asyncio.run(_turn("Sokrates, was meinst du?"))
    assert session_storage.get_session_config(SESSION)["active_agent"] == "sokrates"
    assert _data()["llm_history"][1]["content"].startswith("[SOKRATES]: ")
    assert "session_config" in _kinds()


def test_a_pure_mode_switch_is_confirmed_without_an_answer(world: dict[str, Any]) -> None:
    world["intent"] = turn_service._Intent("FAKTISCH", None, "de", {"active_agent": "salomo"}, True, 0.0)
    asyncio.run(_turn("Wechsle zu Salomo"))
    entry = _data()["chat_history"][-1]
    assert (entry["agent"], entry["mode"]) == ("system", "mode_switch")
    assert "agent_started" not in _kinds()


def test_a_cancelled_turn_keeps_the_streamed_text(world: dict[str, Any]) -> None:
    async def scenario() -> None:
        world["block"] = asyncio.Event()
        world["streamed"] = asyncio.Event()
        task = turn_service.start_turn(turn_service.TurnRequest(SESSION, "Erzähl"))
        await world["streamed"].wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    reply = _data()["chat_history"][-1]
    assert reply["role"] == "assistant" and reply["content"].startswith("Hallo ")
    finished = [e for e in session_events.replay_after(SESSION, 0) if e.kind == "turn_finished"]
    assert finished[0].data["status"] == "cancelled"


def test_a_message_waits_behind_a_running_turn_and_starts_after_it(world: dict[str, Any]) -> None:
    seen: list[str] = []

    async def scenario() -> None:
        world["block"] = asyncio.Event()
        world["streamed"] = asyncio.Event()
        async with session_events.subscribe(SESSION) as (_, queue):   # a client that stays connected
            assert turn_service.submit_turn(turn_service.TurnRequest(SESSION, "Erste Frage")) is False
            await world["streamed"].wait()
            assert turn_service.submit_turn(turn_service.TurnRequest(SESSION, "Zweite Frage")) is True
            world["block"].set()
            world["block"] = None
            while len([kind for kind in seen if kind == "turn_finished"]) < 2:
                seen.append((await asyncio.wait_for(queue.get(), timeout=5)).kind)

    asyncio.run(scenario())
    assert [e["content"] for e in _data()["chat_history"] if e["role"] == "user"] == ["Erste Frage", "Zweite Frage"]
    assert [e["role"] for e in _data()["chat_history"]] == ["user", "assistant", "user", "assistant"]
    assert seen.index("message_queued") < seen.index("turn_finished")   # queued while the first one ran


@pytest.fixture
def speaking(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replies are read aloud; the reading is a stand-in that is over when the text is."""
    world["view"].enable_tts = True
    world["view"].tts_autoplay = True
    FakeReading.made.clear()
    monkeypatch.setattr(FakeReading, "ends_on_finish", True)
    monkeypatch.setattr(reply_speech, "BrowserReading", FakeReading)
    monkeypatch.setattr(audio_processing, "save_audio_to_session", lambda urls, sid: f"/_upload/audio/{sid}/reply.wav")
    return world


def test_a_reply_read_aloud_gets_its_audio_when_the_reading_is_over(speaking: dict[str, Any]) -> None:
    async def scenario() -> None:
        await _turn("Hallo AIfred")
        await reply_speech._readings[SESSION].settled()

    asyncio.run(scenario())
    assert FakeReading.made[0].fed == ["Hallo ", "Peuqui."]
    reply = _data()["chat_history"][-1]
    assert reply["has_audio"] and reply["metadata"]["audio_urls"] == [f"/_upload/audio/{SESSION}/reply.wav"]
    updated = [e for e in session_events.replay_after(SESSION, 0) if e.kind == "message_updated"]
    assert updated[0].data["entry"]["timestamp"] == reply["timestamp"]


def test_a_cancelled_reply_is_not_read_to_its_end(speaking: dict[str, Any]) -> None:
    async def scenario() -> None:
        speaking["block"] = asyncio.Event()
        speaking["streamed"] = asyncio.Event()
        task = turn_service.start_turn(turn_service.TurnRequest(SESSION, "Erzähl"))
        await speaking["streamed"].wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    reading = FakeReading.made[0]
    assert reading.aborted and not reading.finished
    assert not _data()["chat_history"][-1]["has_audio"]          # the interrupted bubble keeps no audio
