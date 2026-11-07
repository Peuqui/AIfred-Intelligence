"""The agents' long-term memory for the Vue app's memory page (lib/agent_memory.py): how many memories each agent
has, its entries, one edited (through AgentMemory.update, the path of the agents' own update_memory tool: the
summary is embedded anew, type and session stay), one or all deleted. 503 while ChromaDB is not there."""

from typing import TYPE_CHECKING

from fastapi import HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from .app import api_app

if TYPE_CHECKING:
    from ..agent_memory import AgentMemory


class MemoryAgent(BaseModel):
    agent_id: str
    display_name: str
    emoji: str
    count: int


class MemoryEntry(BaseModel):
    id: str
    date: str
    type: str
    summary: str
    content: str
    session_id: str


class MemoryEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1)
    content: str = Field(min_length=1)


class MemoryCleared(BaseModel):
    deleted: int


def _memory() -> "AgentMemory":
    from ..agent_memory import get_agent_memory

    memory = get_agent_memory()
    if memory is None:
        raise HTTPException(status_code=503, detail="Agent memory not available (ChromaDB not running?)")
    return memory


def _entries(agent_id: str) -> list[MemoryEntry]:
    memory = _memory()
    # An agent without a collection has no entries; reading one would create it.
    if agent_id not in memory.counts():
        return []
    return [MemoryEntry(**entry) for entry in memory.entries(agent_id)]


@api_app.get("/memory", response_model=list[MemoryAgent], tags=["Memory"])
async def memory_agents() -> list[MemoryAgent]:
    """Every agent with its number of memories: the agents of agents.json (also those without a memory yet,
    and system agents) and collections of agents deleted since."""
    from ..agent_config import load_agents

    counts = _memory().counts()
    agents = load_agents()
    return [
        MemoryAgent(
            agent_id=agent_id,
            display_name=agents[agent_id].display_name if agent_id in agents else agent_id,
            emoji=agents[agent_id].emoji if agent_id in agents else "🗂️",
            count=counts.get(agent_id, 0),
        )
        for agent_id in sorted(set(agents) | set(counts))
    ]


@api_app.get("/memory/{agent_id}", response_model=list[MemoryEntry], tags=["Memory"])
async def memory_entries(agent_id: str) -> list[MemoryEntry]:
    """An agent's memories, newest first."""
    return _entries(agent_id)


@api_app.patch("/memory/{agent_id}/{memory_id}", response_model=MemoryEntry, tags=["Memory"])
async def memory_edit(agent_id: str, memory_id: str, edit: MemoryEdit) -> MemoryEntry:
    """Rewrite a memory's summary (one line, the index's limit) and content; 422 for a summary too long."""
    from ..agent_memory import summary_fits_index
    from ..config import AGENT_MEMORY_SUMMARY_MAX_CHARS

    summary, content = edit.summary.strip(), edit.content.strip()
    if not summary_fits_index(summary):
        raise HTTPException(status_code=422, detail=f"The summary must be one line of at most {AGENT_MEMORY_SUMMARY_MAX_CHARS} characters")
    entry = next((entry for entry in _entries(agent_id) if entry.id == memory_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No memory '{memory_id}' of '{agent_id}'")
    await _memory().update(agent_id, memory_id, content, summary, memory_type=entry.type, session_id=entry.session_id)
    log_message(f"🧠 Memory {memory_id[:8]} of '{agent_id}' edited")
    return next(entry for entry in _entries(agent_id) if entry.id == memory_id)


@api_app.delete("/memory/{agent_id}/{memory_id}", status_code=204, tags=["Memory"])
async def memory_delete(agent_id: str, memory_id: str) -> Response:
    """Delete one memory."""
    if all(entry.id != memory_id for entry in _entries(agent_id)):
        raise HTTPException(status_code=404, detail=f"No memory '{memory_id}' of '{agent_id}'")
    await _memory().delete(agent_id, memory_id)
    return Response(status_code=204)


@api_app.delete("/memory/{agent_id}", response_model=MemoryCleared, tags=["Memory"])
async def memory_clear(agent_id: str) -> MemoryCleared:
    """Delete every memory of an agent."""
    memory = _memory()
    deleted = memory.clear(agent_id) if agent_id in memory.counts() else 0
    return MemoryCleared(deleted=deleted)
