"""The server turn (lib/turn_service.py) without a model: the LLM calls are stand-ins, the session
file, event hub, routing, bubble building, cancellation and title run for real."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from fake_reading import FakeReading

from aifred.lib import (
    audio_processing,
    reply_speech,
    session_events,
    session_storage,
    turn_images,
    turn_service,
    vision_routing,
    vision_utils,
)
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
        backend_type="ollama", backend_url="http://x", ui_language="de",
        enable_tts=False, automatik_model_id="", agent_tuning=default_agent_tuning(),
        tts_autoplay=False, tts_agents={}, max_debate_rounds=3, consensus_type="majority",
    )
    view.agent_tuning["aifred"].model_id = "qwen3:8b"
    world: dict[str, Any] = {"chunks": [{"type": "content", "text": "Hallo "}, {"type": "content", "text": "Peuqui."}],
                             "titles": [], "block": None, "view": view, "requests": [], "options": [], "title_calls": [],
                             "memory": []}

    async def intent(_view: Any, _client: Any, _text: str) -> turn_service._Intent:
        return world.get("intent") or turn_service._Intent(None, "de", {}, False, 0.0)

    async def no_compression(*_args: Any) -> None:
        return None

    async def toolkit(*_args: Any, **kwargs: Any) -> tuple[str, None]:
        world["memory"].append(kwargs["memory_enabled"])
        return "", None

    async def stream(*args: Any, **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        world["requests"].append(args[2])     # the messages of the call
        world["options"].append(args[3])
        text = ""
        for chunk in world["chunks"]:
            text += chunk["text"]
            yield chunk
            if world["block"] is not None:
                world["streamed"].set()
                await world["block"].wait()
        yield {"type": "pipeline_result", "result": PipelineResult(text=text, answer_text=text, text_clean=text)}

    async def title(user_text: str, reply: str, *_args: Any, **kwargs: Any) -> str:
        world["titles"].append((user_text, reply))
        world["title_calls"].append((kwargs["model_override"], kwargs["num_ctx_override"]))
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


def test_the_title_comes_from_the_model_that_just_answered(world: dict[str, Any]) -> None:
    world["view"].agent_tuning["sokrates"].model_id = "qwen3:4b"
    world["intent"] = turn_service._Intent("sokrates", "de", {}, False, 0.0)
    asyncio.run(_turn("Sokrates, hallo"))
    assert world["title_calls"] == [("qwen3:4b", 32768)]        # loaded already, nothing reloads


def test_each_agent_thinks_as_its_own_row_says(world: dict[str, Any]) -> None:
    # Peuqui 07.11.2026: AIfred's thinking choice does not apply to the other agents.
    world["view"].agent_tuning["aifred"].thinking_by_model = {"qwen3:8b": "on"}
    world["view"].agent_tuning["sokrates"].thinking_by_model = {"qwen3:8b": "off"}
    world["intent"] = turn_service._Intent("sokrates", "de", {}, False, 0.0)
    asyncio.run(_turn("Sokrates, was meinst du?"))
    world["intent"] = turn_service._Intent("aifred", "de", {}, False, 0.0)
    asyncio.run(_turn("AIfred, und du?"))
    assert [options.enable_thinking for options in world["options"]] == [False, True]


def test_an_incognito_session_answers_without_the_agents_memory(world: dict[str, Any]) -> None:
    asyncio.run(_turn("Hallo"))
    session_storage.update_session_config(SESSION, incognito=True)
    asyncio.run(_turn("Und jetzt?"))
    assert world["memory"] == [True, False]
    system_prompts = [request[0]["content"] for request in world["requests"]]
    assert len(system_prompts[1]) < len(system_prompts[0])        # no memory layer in the prompt
    assert any("Incognito" in line for line in _data()["debug_messages"])


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
    world["intent"] = turn_service._Intent("sokrates", "de", {}, False, 0.0)
    asyncio.run(_turn("Sokrates, was meinst du?"))
    assert session_storage.get_session_config(SESSION)["active_agent"] == "sokrates"
    assert _data()["llm_history"][1]["content"].startswith("[SOKRATES]: ")
    assert "session_config" in _kinds()


def test_a_pure_mode_switch_is_confirmed_without_an_answer(world: dict[str, Any]) -> None:
    world["intent"] = turn_service._Intent(None, "de", {"active_agent": "salomo"}, True, 0.0)
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


# ── Images ───────────────────────────────────────────────────────────────


@pytest.fixture
def picture(world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """An image uploaded for the next message; the description is a stand-in."""
    directory = tmp_path / "upload" / "images"
    monkeypatch.setattr(vision_utils, "UPLOAD_IMAGES_DIR", directory)
    (directory / SESSION).mkdir(parents=True)
    (directory / SESSION / "hund.jpg").write_bytes(b"\xff\xd8bild")
    world["described"] = []

    async def describe(images: Any, question: str, lang: str, model: str, _settings: Any) -> str:
        world["described"].append((model, question))
        return "Ein brauner Hund auf einer Wiese."

    monkeypatch.setattr(turn_images, "describe_images", describe)
    return f"/_upload/images/{SESSION}/hund.jpg"


def _seeing(monkeypatch: pytest.MonkeyPatch, model: str | None) -> None:
    """``model`` describes the images; None: the model that answers sees them itself."""
    monkeypatch.setattr(
        vision_routing, "chat_describer", lambda main: vision_routing.Describer(model or main, evicts_chat_model=False),
    )


def _image_turn(text: str, url: str) -> None:
    async def scenario() -> None:
        await turn_service.run_turn(turn_service.TurnRequest(SESSION, text, images=(url,)))
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_an_agent_that_sees_gets_the_image_in_its_request_and_the_history_only_its_url(
    picture: str, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seeing(monkeypatch, None)
    _image_turn("Was ist das?", picture)
    question = world["requests"][0][-1]["content"]
    assert [part["type"] for part in question] == ["image_url", "text"]
    assert "Was ist das?" in question[1]["text"] and picture in question[1]["text"]
    assert world["described"] == []
    user, reply = _data()["llm_history"]
    assert picture in user["content"] and "base64" not in user["content"]
    assert reply["content"] == "[AIFRED]: Hallo Peuqui."
    assert '<img src="' + picture in _data()["chat_history"][0]["content"]
    assert _data()["chat_history"][0]["metadata"]["images"] == [{"name": "hund.jpg", "url": picture}]


def test_a_model_that_cannot_see_answers_from_a_description_in_the_user_turn(
    picture: str, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seeing(monkeypatch, "qwen3-vl:8b")
    _image_turn("Was ist das?", picture)
    assert world["described"] == [("qwen3-vl:8b", "Was ist das?")]
    question = world["requests"][0][-1]["content"]
    assert isinstance(question, str) and "Ein brauner Hund auf einer Wiese." in question
    assert "Ein brauner Hund auf einer Wiese." in _data()["llm_history"][0]["content"]   # later turns know it too


def test_a_debate_works_from_one_description_even_when_the_answering_model_sees(
    picture: str, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seeing(monkeypatch, None)
    session_storage.update_session_config(SESSION, multi_agent_mode="critical_review")
    _image_turn("Was ist das?", picture)
    assert len(world["described"]) == 1
    assert all(isinstance(request[-1]["content"], str) for request in world["requests"])


def test_without_a_model_that_can_see_the_message_goes_on_and_the_user_is_told(
    picture: str, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def nobody(_main: str) -> vision_routing.Describer:
        raise vision_routing.NoVisionModelError("no vision LLM is configured")

    monkeypatch.setattr(vision_routing, "chat_describer", nobody)
    _image_turn("", picture)
    assert "notice" in _kinds() and "message_added" in _kinds()
    assert picture in _data()["llm_history"][0]["content"]


def test_an_image_of_another_session_is_refused(picture: str, world: dict[str, Any]) -> None:
    _image_turn("Was ist das?", f"/_upload/images/{'f' * 32}/fremd.jpg")
    assert _kinds() == ["turn_rejected", "turn_finished"]


# ── A message from outside the chat: channels, jobs, alerts (message_processor.process_inbound) ──


class _Channel:
    """A channel plugin as process_inbound uses it; what it sends is kept."""

    speaker_is_user = True
    always_reply = True
    emoji = "✈️"

    def __init__(self) -> None:
        self.sent: list[Any] = []

    def build_context(self, message: Any) -> str:
        return f"[Telegram from {message.sender}]\n\n{message.text}"

    def build_reply_metadata(self, _message: Any) -> dict[str, Any]:
        return {"chat_id": 42}

    async def send_reply(self, outbound: Any, _original: Any) -> None:
        self.sent.append(outbound)


@pytest.fixture
def channel(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> _Channel:
    """The session is the channel's (routing table); tier 1, the owner writes; the toolkit's arguments
    are kept in ``world["toolkit"]``. Speech is on: a channel's reply must still not be read in the browser."""
    from dataclasses import replace
    from types import SimpleNamespace

    from aifred.lib import agent_memory, message_processor, runtime_settings, security

    plugin = _Channel()
    world["toolkit"] = []

    async def toolkit(*_args: Any, **kwargs: Any) -> tuple[str, None]:
        world["toolkit"].append(kwargs)
        return "", None

    async def no_reading(*_args: Any) -> None:
        raise AssertionError("a channel speaks its reply itself")

    view = replace(world["view"], enable_tts=True, tts_autoplay=True)
    world["view"] = view
    monkeypatch.setattr(runtime_settings, "load_runtime_settings", lambda: view)
    monkeypatch.setattr(agent_memory, "prepare_agent_toolkit", toolkit)
    monkeypatch.setattr(reply_speech, "begin", no_reading)
    monkeypatch.setattr("aifred.lib.plugin_registry.get_channel", lambda _name: plugin)
    monkeypatch.setattr(message_processor, "routing_table", SimpleNamespace(get_route=lambda *_args: SimpleNamespace(session_id=SESSION)))
    monkeypatch.setattr(message_processor, "channel_display_label", lambda _channel: "Telegram")
    monkeypatch.setattr(message_processor, "_mapped_user_name", lambda *_args: None)
    monkeypatch.setattr(message_processor, "write_hub_notification", lambda *_args, status: world.setdefault("hub", []).append(status))
    monkeypatch.setattr(security, "resolve_tier_for_sender", lambda *_args: 1)
    monkeypatch.setattr(security, "resolve_trust_label", lambda *_args: "owner")
    return plugin


def _inbound(text: str, **metadata: Any) -> Any:
    from datetime import UTC, datetime

    from aifred.lib.envelope import InboundMessage

    return InboundMessage(channel="telegram", channel_id="chat-1", sender="Peuqui", text=text,
                          timestamp=datetime.now(UTC), metadata=metadata)


def _process(message: Any) -> Any:
    from aifred.lib.message_processor import process_inbound

    async def scenario() -> Any:
        outbound = await process_inbound(message)
        await asyncio.sleep(0)   # the title task
        return outbound

    return asyncio.run(scenario())


def test_a_channel_message_is_the_chats_turn_within_its_senders_rights(world: dict[str, Any], channel: _Channel) -> None:
    outbound = _process(_inbound("Wie wird das Wetter?", note="vom Handy"))
    data = _data()
    bubble, question = data["chat_history"][0], data["llm_history"][0]["content"]
    assert bubble["role"] == "user" and bubble["content"].endswith("Wie wird das Wetter?")
    assert "<external_message" not in bubble["content"]
    # The fence stays in the history: it protects every later turn that reads it.
    assert '<external_message sender="Peuqui" channel="telegram" trust="owner">' in question
    assert question.endswith("[Telegram from Peuqui]\n\nWie wird das Wetter?\n</external_message>")
    rights = world["toolkit"][0]
    assert (rights["max_tier"], rights["trust"], rights["source"], rights["metadata"]) == (1, "owner", "telegram", {"note": "vom Handy"})
    assert channel.sent == [outbound]
    assert outbound.text == "Hallo Peuqui." and outbound.metadata == {"chat_id": 42, "session_id": SESSION}
    assert "token" in _kinds() and world["hub"][-1] == "done"     # a tab on the session follows live


def test_a_wake_word_pins_the_agent_whoever_the_text_addresses(world: dict[str, Any], channel: _Channel) -> None:
    world["intent"] = turn_service._Intent("sokrates", "de", {}, False, 0.0)
    message = _inbound("Sokrates, was meinst du?", wake_agent="salomo")
    outbound = _process(message)
    assert _data()["llm_history"][1]["content"].startswith("[SALOMO]: ")
    assert message.target_agent == "salomo"                     # the channel speaks with his voice
    assert outbound.text == "— Salomo —\n\nHallo Peuqui."
    assert session_storage.get_session_config(SESSION)["active_agent"] == "salomo"


def test_a_channel_message_without_an_answer_returns_nothing_and_the_toast_says_so(
    world: dict[str, Any], channel: _Channel,
) -> None:
    session_storage.update_session_config(SESSION, multi_agent_mode="devils_advocate")   # refused by the turn
    assert _process(_inbound("Hallo")) is None
    assert channel.sent == [] and world["hub"][-1] == "error"


def test_a_reply_without_text_is_no_reply_and_nothing_goes_out(world: dict[str, Any], channel: _Channel) -> None:
    world["chunks"] = []                    # the model wrote nothing (its tool did the work)
    assert _process(_inbound("Spiel Musik")) is None
    assert channel.sent == [] and world["hub"][-1] == "error"


def test_a_channel_message_for_an_agent_switched_off_is_refused_out_loud(
    world: dict[str, Any], channel: _Channel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from aifred.lib import agent_config

    agents = agent_config._default_agents()
    agents["sokrates"]["enabled"] = False
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", path)
    assert _process(_inbound("Was sagst du dazu?", wake_agent="sokrates")) is None   # as a trigger or a wake word names it
    rejected = [event for event in session_events.replay_after(SESSION, 0) if event.kind == "turn_rejected"]
    assert rejected[0].data["reason"] == "agent"
    assert world["requests"] == [] and channel.sent == []          # no agent answers in his place
    assert world["hub"][-1] == "error"
    assert _data()["chat_history"][-1]["content"].endswith("Was sagst du dazu?")


def test_every_speaker_of_a_turn_goes_out_named_when_several_spoke() -> None:
    from aifred.lib.message_processor import outbound_text
    from aifred.lib.turn_service import TurnReply

    alone = [TurnReply("aifred", "Gern.", silent=False)]
    debate = [TurnReply("aifred", "Ja.", silent=False), TurnReply("sokrates", "Warum?", silent=False)]
    switched = [TurnReply("system", "✅ **Tribunal**", silent=False), TurnReply("salomo", "So sei es.", silent=False)]
    assert outbound_text(alone) == "Gern."
    assert outbound_text(debate) == "— AIfred —\n\nJa.\n\n— Sokrates —\n\nWarum?"
    assert outbound_text(switched) == "✅ **Tribunal**\n\n— Salomo —\n\nSo sei es."   # a confirmation speaks for nobody
