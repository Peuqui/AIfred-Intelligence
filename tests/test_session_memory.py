"""„Merken“ (lib/session_memory.py, POST /api/sessions/{id}/remember): the chat summarized into the memory of every
agent that spoke in it. Until 08.11.2026 only the Reflex chat could, with its prompt in English in the code and the
conversation cut to its last 4,000 characters; system agents were left out by the name "vision" alone."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import agent_memory, llm_client, runtime_settings, session_memory, session_storage, turn_service
from aifred.lib.agent_tuning import default_agent_tuning
from aifred.lib.api.app import api_app
from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

SESSION = "c" * 32


class _Memory:
    """Stand-in for AgentMemory: per agent the entries of each session."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, str]] = {}

    def find_by_session(self, agent_id: str, session_id: str) -> list[str]:
        return [session_id] if session_id in self.entries.get(agent_id, {}) else []

    def update_by_session(self, agent_id: str, session_id: str, new_content: str) -> None:
        self.entries[agent_id][session_id] = new_content

    async def store(self, agent_id: str, content: str, memory_type: str, summary: str, session_id: str = "") -> str:
        assert memory_type == "session_summary"
        self.entries.setdefault(agent_id, {})[session_id] = content
        return "id"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    session_storage.create_empty_session(SESSION, owner="ki")
    session_storage.update_chat_data(SESSION, [
        {"role": "user", "content": "Wer war Kant?"},
        {"role": "assistant", "agent": "aifred", "agent_display_name": "AIfred",
         "content": "<details><summary>💭</summary>geheimes Nachdenken</details>Ein Philosoph aus Königsberg."},
        {"role": "assistant", "agent": "vision", "agent_display_name": "Vision", "content": "Ein Bild."},
        {"role": "user", "content": "Und sein Hauptwerk?"},
        {"role": "assistant", "agent": "sokrates", "agent_display_name": "Sokrates", "content": "Die Kritik der reinen Vernunft."},
    ], llm_history=[])
    world: dict[str, Any] = {"memory": _Memory(), "prompts": [], "answer": "Es ging um Kant und sein Hauptwerk."}

    class _Client:
        def __init__(self, **_kwargs: Any) -> None: ...

        async def chat(self, model: str, messages: list[dict[str, str]], options: dict[str, Any]) -> Any:
            world["prompts"].append(messages[0]["content"])
            return SimpleNamespace(text=world["answer"])

        async def close(self) -> None: ...

    view = runtime_settings.RuntimeSettings(
        backend_type="llamacpp", backend_url="http://x", ui_language="de", enable_tts=False, automatik_model_id="",
        agent_tuning=default_agent_tuning(), tts_autoplay=False, tts_agents={}, max_debate_rounds=3,
        consensus_type="majority",
    )
    monkeypatch.setattr(runtime_settings, "load_runtime_settings", lambda: view)
    monkeypatch.setattr(agent_memory, "get_agent_memory", lambda: world["memory"])
    monkeypatch.setattr(llm_client, "LLMClient", _Client)
    monkeypatch.setattr(turn_service, "agent_model", lambda view, agent: "test-model")
    monkeypatch.setattr(turn_service, "agent_context", lambda view, agent: (world.get("num_ctx", 32768), "test"))
    yield world
    session_storage._session_meta_cache.clear()


def test_the_summary_goes_to_the_agents_that_spoke_and_no_system_agent(world: dict[str, Any]) -> None:
    done = asyncio.run(session_memory.remember_session(SESSION))
    assert (done.agents, done.stored, done.updated) == (("AIfred", "Sokrates"), 2, 0)
    assert world["memory"].entries == {"aifred": {SESSION: world["answer"]}, "sokrates": {SESSION: world["answer"]}}
    prompt = world["prompts"][0]
    assert "Benutzer: Wer war Kant?" in prompt and "Sokrates: Die Kritik der reinen Vernunft." in prompt
    assert "geheimes Nachdenken" not in prompt and "<details>" not in prompt


def test_remembering_again_replaces_the_earlier_summary(world: dict[str, Any]) -> None:
    asyncio.run(session_memory.remember_session(SESSION))
    world["answer"] = "Neu gefasst."
    done = asyncio.run(session_memory.remember_session(SESSION))
    assert (done.stored, done.updated) == (0, 2)
    assert world["memory"].entries["aifred"][SESSION] == "Neu gefasst."


def test_a_long_chat_keeps_its_newest_part_within_the_models_context(world: dict[str, Any]) -> None:
    from aifred.lib import config
    from aifred.lib.prompt_loader import load_prompt

    # Room for the prompt, the answer and about one line: the newest line stays, the oldest goes.
    prompt_tokens = session_memory._tokens(load_prompt("utility/session_memory", lang="de", conversation=""))
    world["num_ctx"] = config.SESSION_MEMORY_NUM_PREDICT + prompt_tokens + session_memory._tokens("Sokrates: Die Kritik der reinen Vernunft.")
    asyncio.run(session_memory.remember_session(SESSION))
    prompt = world["prompts"][0]
    assert "Die Kritik der reinen Vernunft." in prompt and "Wer war Kant?" not in prompt


def test_the_api_refuses_a_chat_without_a_reply_and_a_memory_that_does_not_answer(
        world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(api_app)
    client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    assert client.post(f"/sessions/{SESSION}/remember").json()["agents"] == ["AIfred", "Sokrates"]
    monkeypatch.setattr(agent_memory, "get_agent_memory", lambda: None)
    assert client.post(f"/sessions/{SESSION}/remember").status_code == 503
    session_storage.update_chat_data(SESSION, [{"role": "user", "content": "Hallo"}], llm_history=[])
    assert client.post(f"/sessions/{SESSION}/remember").status_code == 422
