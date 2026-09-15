"""Agent memory: full index in the context, read_memory, no silent dedup or eviction."""

import asyncio

import pytest

from aifred.lib import agent_memory
from aifred.lib.agent_memory import AgentMemory, MemoryFullError, format_memory_context


class FakeCollection:
    """In-memory stand-in for a ChromaDB collection (exact-match "semantics")."""

    def __init__(self):
        self.rows: dict[str, tuple[str, dict]] = {}

    def count(self):
        return len(self.rows)

    def add(self, ids, documents, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, m)

    def update(self, ids, documents, metadatas):
        self.add(ids, documents, metadatas)

    def delete(self, ids):
        for i in ids:
            del self.rows[i]

    def get(self, ids=None, include=None, where=None):
        keys = ids if ids is not None else list(self.rows)
        return {"ids": keys, "metadatas": [self.rows[k][1] for k in keys]}

    def query(self, query_texts, n_results, include):
        hits = [k for k, (doc, _) in self.rows.items() if query_texts[0] in doc][:n_results]
        return {
            "ids": [hits],
            "metadatas": [[self.rows[k][1] for k in hits]],
            "distances": [[0.1 for _ in hits]],
        }


@pytest.fixture
def memory():
    mem = object.__new__(AgentMemory)
    mem._collections = {"pater": FakeCollection()}
    return mem


def _store(memory, summary, content="", session_id=""):
    return asyncio.run(memory.store("pater", content or summary, "counsel", summary, session_id=session_id))


def test_similar_summary_creates_a_new_entry(memory):
    _store(memory, "Psalm-Liste Morgen-Mail")
    _store(memory, "Psalm-Liste Morgen-Mail")
    assert memory._collections["pater"].count() == 2


def test_full_memory_refuses_instead_of_evicting(memory, monkeypatch):
    monkeypatch.setattr(agent_memory, "AGENT_MEMORY_COLLECTION_MAX", 2)
    _store(memory, "eins")
    _store(memory, "zwei")
    with pytest.raises(MemoryFullError, match="update_memory"):
        _store(memory, "drei")
    assert memory._collections["pater"].count() == 2


def test_index_lists_all_and_expands_recent_and_hits(memory):
    for i in range(4):
        _store(memory, f"Eintrag {i}", content=f"Inhalt {i} ausführlich", session_id="same-session")
    entries = asyncio.run(memory.recall_context("pater", "Eintrag 0", n_semantic=1, n_recent=1))
    assert len(entries) == 4  # current-session entries are not hidden
    expanded = {e["summary"] for e in entries if e["expanded"]}
    assert expanded == {"Eintrag 3", "Eintrag 0"}  # newest + semantic hit


def test_format_shows_content_only_for_expanded(tmp_path, monkeypatch):
    entries = [
        {"id": "aaaaaaaa-1", "date": "2026-09-15T10:00", "type": "counsel",
         "summary": "offen", "content": "Details offen", "expanded": True},
        {"id": "bbbbbbbb-2", "date": "2026-09-14T10:00", "type": "counsel",
         "summary": "zu", "content": "Details zu", "expanded": False},
    ]
    text = format_memory_context(entries, agent_id="pater", lang="de")
    assert "[aaaaaaaa | 2026-09-15, counsel] offen" in text
    assert "Details offen" in text
    assert "[bbbbbbbb | 2026-09-14, counsel] zu" in text
    assert "Details zu" not in text


def test_multiline_content_stays_indented_under_its_entry():
    entries = [{"id": "aaaaaaaa-1", "date": "2026-09-15", "type": "counsel", "summary": "Psalmen",
                "content": "Liste:\n- Psalm 46\n- Psalm 63", "expanded": True}]
    text = format_memory_context(entries, agent_id="pater", lang="de")
    assert "\n    - Psalm 46\n    - Psalm 63" in text
    assert "\n- Psalm" not in text


def test_read_returns_full_content(memory):
    _store(memory, "Psalm-Liste", content="Psalm 143, Psalm 63 " + "x" * 800)
    entry_id = next(iter(memory._collections["pater"].rows))
    text = asyncio.run(memory.read("pater", entry_id[:8]))
    assert text.startswith(f"[{entry_id[:8]} |")
    assert text.endswith("x" * 800)


def test_read_memory_tool_is_readonly_and_owner_gated(memory):
    tools = {t.name: t for t in memory.make_toolkit("pater").tools}
    assert set(tools) == set(agent_memory.MEMORY_TOOL_TIERS)
    assert tools["read_memory"].tier == 0 and tools["read_memory"].owner_gated


def test_summary_limit_refuses_long_or_multiline(memory):
    tools = {t.name: t for t in memory.make_toolkit("pater").tools}
    store = tools["store_memory"].executor
    with pytest.raises(ValueError, match="at most 160"):
        asyncio.run(store(content="x", memory_type="counsel", summary="a" * 161))
    with pytest.raises(ValueError, match="one line"):
        asyncio.run(store(content="x", memory_type="counsel", summary="Satz eins.\nSatz zwei."))
    assert memory._collections["pater"].count() == 0
    asyncio.run(store(content="x", memory_type="counsel", summary="a" * 160))
    assert memory._collections["pater"].count() == 1
