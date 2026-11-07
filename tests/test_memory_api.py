"""The agents' memory over the API (Vue memory page, lib/api/memory.py): every agent with its count (also one without
memories and the collection of an agent deleted since), the entries, one edited through AgentMemory.update (type and
session stay, the summary limit holds), one or all deleted; nothing reads an agent without a collection (that would
create one). The memory is a stand-in."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient


class _Memory:
    """Stand-in for AgentMemory: collections by agent, ``read`` records which ones were opened."""

    def __init__(self) -> None:
        self.collections: dict[str, list[dict[str, Any]]] = {
            "sokrates": [
                {"id": "a1", "date": "2026-11-07T10:00", "type": "insight", "summary": "Kaffee", "content": "Mag Kaffee.", "session_id": "s1"},
                {"id": "a0", "date": "2026-11-06T10:00", "type": "fact", "summary": "Tee", "content": "Mag Tee.", "session_id": ""},
            ],
            "geloescht": [{"id": "g1", "date": "2026-10-01", "type": "fact", "summary": "alt", "content": "alt", "session_id": ""}],
        }
        self.opened: list[str] = []
        self.updates: list[tuple[Any, ...]] = []

    def counts(self) -> dict[str, int]:
        return {agent: len(entries) for agent, entries in self.collections.items()}

    def entries(self, agent_id: str) -> list[dict[str, Any]]:
        self.opened.append(agent_id)
        return [dict(entry) for entry in self.collections.setdefault(agent_id, [])]

    async def update(self, agent_id: str, memory_id: str, content: str, summary: str, memory_type: str = "", session_id: str = "") -> str:
        self.updates.append((agent_id, memory_id, memory_type, session_id))
        entry = next(entry for entry in self.collections[agent_id] if entry["id"] == memory_id)
        entry.update(summary=summary, content=content, date="2026-11-07T20:00")
        return "updated"

    async def delete(self, agent_id: str, memory_id: str) -> str:
        self.collections[agent_id] = [entry for entry in self.collections[agent_id] if entry["id"] != memory_id]
        return "deleted"

    def clear(self, agent_id: str) -> int:
        count = len(self.collections[agent_id])
        self.collections[agent_id] = []
        return count


@pytest.fixture
def memory(monkeypatch: pytest.MonkeyPatch) -> _Memory:
    fake = _Memory()
    monkeypatch.setattr("aifred.lib.agent_memory.get_agent_memory", lambda: fake)
    return fake


@pytest.fixture
def client(memory: _Memory) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_every_agent_is_listed_with_its_count(client: TestClient, memory: _Memory) -> None:
    agents = {agent["agent_id"]: agent for agent in client.get("/memory").json()}
    assert agents["sokrates"]["count"] == 2 and agents["aifred"]["count"] == 0          # none yet: listed with 0
    assert agents["geloescht"] == {"agent_id": "geloescht", "display_name": "geloescht", "emoji": "🗂️", "count": 1}
    assert client.get("/memory/aifred").json() == [] and "aifred" not in memory.opened  # no collection created


def test_an_entry_is_edited_with_its_type_and_session_kept(client: TestClient, memory: _Memory) -> None:
    edited = client.patch("/memory/sokrates/a1", json={"summary": " Kaffee schwarz ", "content": "Trinkt ihn schwarz."}).json()
    assert (edited["summary"], edited["content"]) == ("Kaffee schwarz", "Trinkt ihn schwarz.")
    assert memory.updates == [("sokrates", "a1", "insight", "s1")]
    assert client.patch("/memory/sokrates/a1", json={"summary": "zwei\nZeilen", "content": "x"}).status_code == 422
    assert client.patch("/memory/sokrates/fehlt", json={"summary": "x", "content": "x"}).status_code == 404


def test_one_or_all_entries_are_deleted(client: TestClient, memory: _Memory) -> None:
    assert client.delete("/memory/sokrates/a0").status_code == 204
    assert [entry["id"] for entry in client.get("/memory/sokrates").json()] == ["a1"]
    assert client.delete("/memory/sokrates/a0").status_code == 404
    assert client.delete("/memory/sokrates").json() == {"deleted": 1}
    assert client.delete("/memory/aifred").json() == {"deleted": 0} and "aifred" not in memory.collections


def test_without_chromadb_the_page_says_so(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aifred.lib.agent_memory.get_agent_memory", lambda: None)
    assert client.get("/memory").status_code == 503
