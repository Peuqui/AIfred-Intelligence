"""Agent long-term memory using ChromaDB.

Each agent gets its own ChromaDB collection for persistent memory.
Agents can write to their own collection and read from all collections.
Memory is retrieved via semantic search and injected into the agent's context.

Uses the ChromaDB server and the embedding function from embeddings.py
(shared with the document store).
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional


from .config import (
    AGENT_MEMORY_COLLECTION_MAX,
    AGENT_MEMORY_DISTANCE_THRESHOLD,
    AGENT_MEMORY_RECENT_COUNT,
    AGENT_MEMORY_RESULTS,
    AGENT_MEMORY_SUMMARY_MAX_CHARS,
    DEFAULT_OLLAMA_URL,
)
from .embeddings import OLLAMA_EMBEDDING_MODEL
from .function_calling import Tool, ToolKit
from .logging_utils import log_message
from .prompt_loader import load_shared_tool_description
from .security import TIER_READONLY, TIER_WRITE_DATA

# The agent memory tools and their tiers — one list for toolkit, agent
# editor and tool pills.
MEMORY_TOOL_TIERS: dict[str, int] = {
    "read_memory": TIER_READONLY,
    "store_memory": TIER_WRITE_DATA,
    "update_memory": TIER_WRITE_DATA,
    "delete_memory": TIER_WRITE_DATA,
}


def summary_fits_index(summary: str) -> bool:
    """Whether a summary can be an entry's line in the memory index.

    The summary is also what gets embedded for search: one line of at most
    AGENT_MEMORY_SUMMARY_MAX_CHARS characters. Shared by the memory tools and
    the memory browser.
    """
    text = summary.strip()
    return "\n" not in text and len(text) <= AGENT_MEMORY_SUMMARY_MAX_CHARS


class MemoryFullError(RuntimeError):
    """The agent's collection has reached AGENT_MEMORY_COLLECTION_MAX."""


class AgentMemory:
    """Agent memory backed by ChromaDB collections.

    One collection per agent. Write-own, read-all.
    """

    def __init__(self, host: "str | None" = None, port: "int | None" = None) -> None:
        from .chroma_client import chroma_client
        from .embeddings import OllamaEmbeddingFunction

        # None = config-Werte (CHROMA_HOST/CHROMA_PORT) — Factory-SSOT
        self._client = chroma_client(host, port)
        self._client.heartbeat()
        # Memory ops are single-shot reads/writes (recall + store_memory),
        # never bulk — query mode (CPU, keep_alive=0) is right.
        self._embed_fn = OllamaEmbeddingFunction(
            model_name=OLLAMA_EMBEDDING_MODEL,
            host=DEFAULT_OLLAMA_URL,
            mode="query",
        )
        self._collections: dict[str, Any] = {}
        log_message("AgentMemory connected to ChromaDB")

    def _collection(self, agent_id: str) -> Any:
        """Get or create a collection for an agent (cached)."""
        if agent_id not in self._collections:
            self._collections[agent_id] = self._client.get_or_create_collection(
                name=f"agent_memory_{agent_id}",
                metadata={"agent": agent_id, "embedding_model": OLLAMA_EMBEDDING_MODEL},
                embedding_function=self._embed_fn,  # type: ignore[arg-type]
            )
        return self._collections[agent_id]

    async def store(
        self, agent_id: str, content: str, memory_type: str, summary: str,
        session_id: str = "",
    ) -> str:
        """Store a memory in the agent's collection.

        The summary is used as the document (embedded for search),
        the full content goes into metadata.
        """
        col = self._collection(agent_id)

        # Full memory: refuse instead of evicting. The agent sees every entry
        # (recall_context) and merges or deletes; nothing disappears silently.
        count = col.count()
        if count >= AGENT_MEMORY_COLLECTION_MAX:
            raise MemoryFullError(
                f"Memory is full ({count} entries, limit {AGENT_MEMORY_COLLECTION_MAX}). "
                "Merge overlapping entries with update_memory or delete obsolete ones "
                "with delete_memory, then store again."
            )

        doc_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        col.add(
            ids=[doc_id],
            documents=[summary],
            metadatas=[{
                "agent_id": agent_id,
                "date": now,
                "type": memory_type,
                "summary": summary,
                "content": content,
                "session_id": session_id,
            }],
        )
        log_message(f"AgentMemory({agent_id}): stored {doc_id[:8]} [{memory_type}] {summary[:60]}")
        return f"Memory {doc_id[:8]} stored: [{memory_type}] {summary}"

    def _resolve_id(self, agent_id: str, memory_id: str) -> str:
        """Resolve a full memory ID or unique ID prefix to the stored ID.

        Raises ValueError if the prefix matches zero or multiple entries.
        """
        col = self._collection(agent_id)
        all_ids: list[str] = col.get(include=[])["ids"]
        matches = [i for i in all_ids if i.startswith(memory_id)]
        if len(matches) != 1:
            raise ValueError(
                f"Memory ID '{memory_id}' matches {len(matches)} entries — "
                "use the exact ID shown in your memory context"
            )
        return matches[0]

    async def update(
        self, agent_id: str, memory_id: str, content: str, summary: str,
        memory_type: str = "", session_id: str = "",
    ) -> str:
        """Update an existing memory (referenced by ID or unique ID prefix)."""
        col = self._collection(agent_id)
        full_id = self._resolve_id(agent_id, memory_id)

        if not memory_type:
            existing = col.get(ids=[full_id], include=["metadatas"])
            memory_type = existing["metadatas"][0].get("type", "")  # type: ignore[index]

        now = datetime.now(timezone.utc).isoformat()
        col.update(
            ids=[full_id],
            documents=[summary],
            metadatas=[{
                "agent_id": agent_id,
                "date": now,
                "type": memory_type,
                "summary": summary,
                "content": content,
                "session_id": session_id,
            }],
        )
        log_message(f"AgentMemory({agent_id}): updated {full_id[:8]} [{memory_type}] {summary[:60]}")
        return f"Memory {full_id[:8]} updated: [{memory_type}] {summary}"

    async def read(self, agent_id: str, memory_id: str) -> str:
        """Full text of one memory (referenced by ID or unique ID prefix)."""
        col = self._collection(agent_id)
        full_id = self._resolve_id(agent_id, memory_id)
        meta = col.get(ids=[full_id], include=["metadatas"])["metadatas"][0]
        return (
            f"[{full_id[:8]} | {meta.get('date', '')[:10]}, {meta.get('type', '')}] "
            f"{meta.get('summary', '')}\n\n{meta.get('content', '')}"
        )

    async def delete(self, agent_id: str, memory_id: str) -> str:
        """Delete a memory (referenced by ID or unique ID prefix)."""
        col = self._collection(agent_id)
        full_id = self._resolve_id(agent_id, memory_id)

        existing = col.get(ids=[full_id], include=["metadatas"])
        summary = existing["metadatas"][0].get("summary", "")  # type: ignore[index]
        col.delete(ids=[full_id])
        log_message(f"AgentMemory({agent_id}): deleted {full_id[:8]} {summary[:60]}")
        return f"Memory {full_id[:8]} deleted: {summary}"

    def clear(self, agent_id: str) -> int:
        """Delete every memory of an agent; returns how many were deleted."""
        col = self._collection(agent_id)
        ids = col.get(include=[])["ids"]
        if ids:
            col.delete(ids=ids)
        log_message(f"AgentMemory({agent_id}): cleared {len(ids)} memories")
        return len(ids)

    def find_by_session(self, agent_id: str, session_id: str) -> list[str]:
        """Find memory IDs for a given session_id."""
        col = self._collection(agent_id)
        if col.count() == 0:
            return []
        results = col.get(
            where={"session_id": session_id},
            include=[],
        )
        return results["ids"] if results["ids"] else []

    def update_by_session(self, agent_id: str, session_id: str, new_content: str) -> None:
        """Update existing session summary with new content."""
        ids = self.find_by_session(agent_id, session_id)
        if not ids:
            return
        col = self._collection(agent_id)
        now = datetime.now(timezone.utc).isoformat()
        col.update(
            ids=ids,
            documents=[new_content[:120]] * len(ids),
            metadatas=[{
                "agent_id": agent_id,
                "date": now,
                "type": "session_summary",
                "summary": new_content[:120],
                "content": new_content,
                "session_id": session_id,
            }] * len(ids),
        )

    async def recall(
        self, agent_id: str, query: str, n_results: int = AGENT_MEMORY_RESULTS,
    ) -> list[dict[str, Any]]:
        """Query an agent's memory by semantic similarity."""
        col = self._collection(agent_id)
        if col.count() == 0:
            return []

        results = col.query(
            query_texts=[query],
            n_results=min(n_results, col.count()),
            include=["metadatas", "distances"],
        )

        memories = []
        for i, dist in enumerate(results["distances"][0]):  # type: ignore[index]
            if dist > AGENT_MEMORY_DISTANCE_THRESHOLD:
                continue
            meta = results["metadatas"][0][i]  # type: ignore[index]
            memories.append({
                "id": results["ids"][0][i],  # type: ignore[index]
                "summary": meta.get("summary", ""),
                "content": meta.get("content", ""),
                "type": meta.get("type", ""),
                "date": meta.get("date", ""),
                "distance": dist,
            })
        return memories

    def _all_entries(self, agent_id: str) -> list[dict[str, Any]]:
        """Every memory of the agent, newest first."""
        col = self._collection(agent_id)
        if col.count() == 0:
            return []
        all_data = col.get(include=["metadatas"])
        entries = [
            {
                "id": all_data["ids"][i],
                "summary": meta.get("summary", ""),
                "content": meta.get("content", ""),
                "type": meta.get("type", ""),
                "date": meta.get("date", ""),
            }
            for i, meta in enumerate(all_data["metadatas"])  # type: ignore[union-attr]
        ]
        entries.sort(key=lambda e: e["date"], reverse=True)
        return entries

    async def recall_context(
        self, agent_id: str, query: str,
        n_semantic: int = AGENT_MEMORY_RESULTS,
        n_recent: int = AGENT_MEMORY_RECENT_COUNT,
    ) -> list[dict[str, Any]]:
        """The agent's whole memory as an index, newest first.

        Every entry is listed, so the agent sees duplicates and what to
        correct. The ``n_recent`` newest entries and the semantic hits for
        ``query`` are marked ``expanded``: their content goes into the context
        too; for the others the agent calls read_memory.
        """
        entries = self._all_entries(agent_id)
        if not entries:
            return []
        expanded = {e["id"] for e in entries[:n_recent]}
        expanded.update(m["id"] for m in await self.recall(agent_id, query, n_results=n_semantic))
        for entry in entries:
            entry["expanded"] = entry["id"] in expanded
        return entries

    def make_toolkit(self, agent_id: str, session_id: str = "") -> ToolKit:
        """Create a ToolKit with memory tools bound to a specific agent."""

        def check_summary(summary: str) -> None:
            # The summary is the entry's line in the memory index (and what is
            # embedded for search): refuse, never truncate - the model rewrites it.
            if not summary_fits_index(summary):
                raise ValueError(
                    f"summary too long ({len(summary.strip())} chars): write one short sentence "
                    f"of at most {AGENT_MEMORY_SUMMARY_MAX_CHARS} characters, on one line, "
                    "and call the tool again"
                )

        async def store_memory(content: str, memory_type: str, summary: str) -> str:
            check_summary(summary)
            return await self.store(agent_id, content, memory_type, summary, session_id=session_id)

        async def update_memory(memory_id: str, content: str, summary: str, memory_type: str = "") -> str:
            check_summary(summary)
            return await self.update(
                agent_id, memory_id, content, summary,
                memory_type=memory_type, session_id=session_id,
            )

        async def delete_memory(memory_id: str) -> str:
            return await self.delete(agent_id, memory_id)

        async def read_memory(memory_id: str) -> str:
            return await self.read(agent_id, memory_id)

        return ToolKit(tools=[
            Tool(
                name="store_memory",
                tier=MEMORY_TOOL_TIERS["store_memory"],
                owner_gated=True,
                description=load_shared_tool_description("store_memory_tool.txt"),
                parameters={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The full text to remember",
                        },
                        "memory_type": {
                            "type": "string",
                            "description": "Category (e.g. sermon, prayer, analysis, insight, code_review, counsel)",
                        },
                        "summary": {
                            "type": "string",
                            "description": f"One short sentence, at most {AGENT_MEMORY_SUMMARY_MAX_CHARS} characters, on one line - this entry's line in your memory index",
                        },
                    },
                    "required": ["content", "memory_type", "summary"],
                },
                executor=store_memory,
            ),
            Tool(
                name="update_memory",
                tier=MEMORY_TOOL_TIERS["update_memory"],
                owner_gated=True,
                description=load_shared_tool_description("update_memory_tool.txt"),
                parameters={
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "ID of the memory to update (shown in brackets in your memory context)",
                        },
                        "content": {
                            "type": "string",
                            "description": "The corrected full text (replaces the old content)",
                        },
                        "summary": {
                            "type": "string",
                            "description": f"One short sentence, at most {AGENT_MEMORY_SUMMARY_MAX_CHARS} characters, on one line - this entry's line in your memory index",
                        },
                        "memory_type": {
                            "type": "string",
                            "description": "New category (optional, keeps the existing one if omitted)",
                        },
                    },
                    "required": ["memory_id", "content", "summary"],
                },
                executor=update_memory,
            ),
            # Own-memory delete is WRITE_DATA, not WRITE_SYSTEM: update_memory
            # (tier 2) already replaces content entirely, so a higher delete
            # tier would protect nothing — and the owner should be able to
            # say "forget that" via external channels (owner tier = 2).
            Tool(
                name="delete_memory",
                tier=MEMORY_TOOL_TIERS["delete_memory"],
                owner_gated=True,
                description=load_shared_tool_description("delete_memory_tool.txt"),
                parameters={
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "ID of the memory to delete (shown in brackets in your memory context)",
                        },
                    },
                    "required": ["memory_id"],
                },
                executor=delete_memory,
            ),
            Tool(
                name="read_memory",
                tier=MEMORY_TOOL_TIERS["read_memory"],
                owner_gated=True,
                description=load_shared_tool_description("read_memory_tool.txt"),
                parameters={
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "ID of the memory to read (shown in brackets in your memory context)",
                        },
                    },
                    "required": ["memory_id"],
                },
                executor=read_memory,
            ),
        ], _agent_id=agent_id, _session_id=session_id)


def format_memory_context(
    memories: list[dict[str, Any]],
    agent_id: str = "",
    lang: str = "de",
) -> str:
    """Format retrieved memories for injection into the system prompt.

    Loads the memory_context prompt template from:
    1. prompts/{lang}/{agent_id}/memory_context.txt (agent-specific)
    2. prompts/{lang}/shared/memory_context.txt (shared fallback)
    """
    if not memories:
        return ""

    # One index line per entry; the expanded ones (newest + semantic hits)
    # carry their full content, the rest is fetched with read_memory. No
    # truncation: a cut entry looks complete to the model, and lists grow at
    # the end, so a cut would hide exactly the newest items.
    lines: list[str] = []
    for mem in memories:
        date_str = mem["date"][:10] if mem["date"] else "?"
        lines.append(f"- [{mem['id'][:8]} | {date_str}, {mem['type']}] {mem['summary']}")
        if mem["expanded"] and mem["content"] and mem["content"] != mem["summary"]:
            # Every content line indented, so a list inside the content
            # cannot pass for further index entries.
            lines.extend(f"    {line}" for line in mem["content"].splitlines() if line.strip())
    memories_text = "\n".join(lines)

    # Load prompt template (agent-specific only, no fallback)
    from pathlib import Path
    prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    template_path = prompts_dir / lang / agent_id / "memory_context.txt"

    if not template_path.exists():
        return ""

    template = template_path.read_text(encoding="utf-8").strip()
    return template.replace("{memories}", memories_text)


async def prepare_agent_toolkit(
    agent_id: str,
    user_query: str,
    lang: str = "de",
    memory_enabled: bool = True,
    research_tools_enabled: bool = True,
    state: Optional[Any] = None,
    session_id: Optional[str] = None,
    llm_history: Optional[list] = None,
    max_tier: int = 4,
    source: str = "browser",
    metadata: Optional[dict] = None,
    trust: str = "external",
) -> tuple[str, Optional["ToolKit"]]:
    """Prepare combined toolkit (memory + research tools) for an agent.

    Args:
        agent_id: Agent identifier
        user_query: User's question (for memory recall)
        lang: Language for memory context
        memory_enabled: Include memory tools (store_memory)
        research_tools_enabled: Include research tools (web_search, read_webpage)
        state: AIState for research tools (needed for forced research pipeline)
        session_id: If set, memories from this session are excluded (already in chat history)
        max_tier: Maximum security tier for tools in this context
        source: Origin of the request (browser/email/discord/cron/webhook)
        trust: Owner verdict of the sender (resolve_trust_label); gates the
            memory context and tools together with source (may_use_memory)

    Returns:
        (memory_context_str, toolkit) — context for system prompt, combined toolkit.
    """
    import time as _pat_time
    _pat_t0 = _pat_time.monotonic()
    all_tools: list[Tool] = []
    memory_tools: list[Tool] = []
    memory_ctx = ""

    # Memory tools + context — both only in owner contexts: the index holds
    # personal entries a foreign sender must not see.
    from .security import may_use_memory
    if memory_enabled and may_use_memory(source, trust):
        memory = get_agent_memory()
        if memory:
            memories = await memory.recall_context(agent_id, user_query)
            if memories:
                memory_ctx = format_memory_context(memories, agent_id=agent_id, lang=lang)
            memory_tools = memory.make_toolkit(agent_id, session_id=session_id or "").tools

    print(f"⏱️ prepare_toolkit: post-memory {_pat_time.monotonic()-_pat_t0:.2f}s", flush=True)
    # All other tools via plugin system
    if research_tools_enabled:
        from .plugin_base import PluginContext
        from .plugin_registry import collect_plugin_tools

        ctx = PluginContext(
            agent_id=agent_id,
            lang=lang,
            session_id=session_id or "",
            state=state,
            user_query=user_query,
            max_tier=max_tier,
            source=source,
            llm_history=llm_history or [],
            metadata=metadata or {},
        )

        # Tool plugins and channel plugin tools (e.g. discord_send)
        for _owner, plugin_tools in collect_plugin_tools(ctx):
            all_tools.extend(plugin_tools)
        print(f"⏱️ prepare_toolkit: post-plugin-tools {_pat_time.monotonic()-_pat_t0:.2f}s", flush=True)

        # Document-RAG-Auto-Inject wurde bewusst entfernt: Agenten suchen
        # selbst via search_documents (mit Folder-Filter). Das vermeidet
        # Anchoring-Bias und gibt dem Modell volle Recherche-Hoheit.

    # Security: filter tools by tier before building toolkit
    from .security import filter_tools_by_tier, filter_tools_for_source
    all_tools = filter_tools_for_source(filter_tools_by_tier(all_tools, max_tier), source)
    # Memory writes are gated by the owner check above, not by the tier: a
    # scheduler job runs at TIER_COMMUNICATE but is still the owner's.
    all_tools = memory_tools + all_tools

    # Per-agent tool whitelist (from agents.json)
    from .agent_config import get_agent_config
    agent_cfg = get_agent_config(agent_id)
    if agent_cfg and agent_cfg.tools is not None:
        allowed = set(agent_cfg.tools)
        all_tools = [t for t in all_tools if t.name in allowed]

    toolkit = ToolKit(
        tools=all_tools,
        _session_id=session_id or "",
        _agent_id=agent_id,
        _source=source,
        _max_tier=max_tier,
    ) if all_tools else None
    return memory_ctx, toolkit


# Singleton
_instance: Optional[AgentMemory] = None


def get_agent_memory() -> Optional[AgentMemory]:
    """Get the global AgentMemory instance. Returns None if ChromaDB unavailable."""
    global _instance
    if _instance is None:
        try:
            _instance = AgentMemory()
        except Exception as e:
            log_message(f"AgentMemory unavailable: {e}")
            return None
    return _instance
