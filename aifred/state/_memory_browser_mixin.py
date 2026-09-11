"""Memory browser mixin for AIfred state.

Handles the memory browser (ChromaDB agent memory collections),
the database tab (document index, orphan cleanup) and agent bundle
export/import.
"""

from __future__ import annotations

from typing import Any, Dict, List

import reflex as rx


def _meta_str(meta: Any, key: str, default: str = "") -> str:
    """ChromaDB-Metadata-Wert als str — verengt die Union
    ``str | int | float | SparseVector | None`` typsicher (SSOT für die
    Memory-Browser-Schleife)."""
    value = (meta or {}).get(key)
    return value if isinstance(value, str) else default


def _by_path(doc: Dict[str, Any]) -> str:
    """Sort key for index lists: the document path, case-insensitive."""
    return str(doc.get("filename", "")).casefold()


async def _deindex(filename: str) -> None:
    """Remove one document from the index only (the file stays on disk)."""
    from ..lib import file_manager as fm
    parts = filename.strip("/").rsplit("/", 1)
    parent_rel, leaf = ("", parts[0]) if len(parts) == 1 else (parts[0], parts[1])
    await fm.delete_file(parent_rel, leaf, from_disk=False, from_index=True)



class MemoryBrowserMixin(rx.State, mixin=True):
    """Mixin for memory/database browsing and agent bundle export/import."""

    # Database tab: the document index, one entry per document (not per chunk)
    db_documents: List[Dict[str, Any]] = []
    db_clear_confirm: bool = False  # Confirmation state for clear-all

    # Orphan cleanup: indexed documents whose file is gone from disk
    db_orphans: List[Dict[str, Any]] = []      # one entry per orphaned document (not per chunk)
    db_orphans_visible: bool = False           # toggles the orphan section

    # Memory browser state
    memory_browser_agent: str = ""  # Selected agent in memory browser ("" = overview)
    memory_browser_agent_display: str = ""  # Display name of selected agent
    memory_browser_entries: List[Dict[str, str]] = []  # Entries for selected agent
    memory_browser_collections: List[Dict[str, str]] = []  # Collection overview
    memory_browser_filter: str = "all"  # "all", "session", "agent"

    # ── Agent Bundle Export/Import ──────────────────────────────
    bundle_export_open: bool = False
    bundle_export_selected: List[str] = []
    # Snapshot of all agents for the export modal — refreshed when modal opens
    bundle_all_agents: List[Dict[str, str]] = []

    bundle_import_open: bool = False
    bundle_import_uploaded_b64: str = ""  # base64-encoded ZIP, kept in state until confirm
    bundle_import_agents: List[Dict[str, Any]] = []  # output of peek_bundle
    bundle_import_selected: List[str] = []
    bundle_import_conflict: str = "rename"  # abort | overwrite | rename
    bundle_import_error: str = ""

    def open_memory_browser(self) -> None:
        """Switch to memory browser view, pre-select AIfred."""
        self.agent_editor_mode = "memory"
        self.memory_browser_filter = "all"
        self._load_memory_collections()

        # Pre-select AIfred's memory (or first available agent)
        agent_collections = self.memory_browser_collections
        if agent_collections:
            # Prefer AIfred
            aifred_col = next((c for c in agent_collections if c["agent_id"] == "aifred"), None)
            pick = aifred_col or agent_collections[0]
            self.browse_memory_agent(pick["agent_id"])

    def select_memory_agent(self, label: str) -> None:
        """Select an agent in the memory browser dropdown."""
        # Find agent_id from display label (label may include count suffix)
        for col in self.memory_browser_collections:
            if col["display_name"] == label or label.startswith(col["display_name"]):
                self.browse_memory_agent(col["agent_id"])
                return

    def set_memory_filter(self, filter_value: str) -> None:
        """Set the memory type filter (all/session/agent)."""
        self.memory_browser_filter = filter_value


    @rx.var(deps=["memory_browser_collections"], auto_deps=False)
    def memory_agent_dropdown_options(self) -> List[str]:
        """Agent dropdown labels with memory count."""
        return [
            f"{col['display_name']} ({col['count']})"
            for col in self.memory_browser_collections
        ]

    @rx.var(deps=["memory_browser_entries", "memory_browser_filter"], auto_deps=False)
    def filtered_memory_entries(self) -> List[Dict[str, str]]:
        """Memory entries filtered by type (all/session/agent)."""
        if self.memory_browser_filter == "all":
            return self.memory_browser_entries
        elif self.memory_browser_filter == "session":
            return [e for e in self.memory_browser_entries if e.get("type") == "session_summary"]
        else:  # "agent" — everything the agent stored itself
            return [e for e in self.memory_browser_entries if e.get("type") != "session_summary"]

    async def _refresh_db_documents(self) -> None:
        """Load the document index, sorted by path (paged Chroma read, ~2 s
        for 46k chunks — in a thread, not on the event loop)."""
        import asyncio
        from ..lib import file_manager as fm
        result = await asyncio.to_thread(fm.list_indexed)
        if not result.success:
            self.add_debug(f"❌ DB browse error: {result.detail}")  # type: ignore[attr-defined]
        docs = result.metadata.get("documents", []) if result.success else []
        self.db_documents = sorted(docs, key=_by_path)

    async def db_load_documents(self) -> None:
        """Event: the database tab was opened."""
        await self._refresh_db_documents()

    def confirm_clear_db(self) -> None:
        """Toggle confirmation state for clearing the document index."""
        self.db_clear_confirm = not self.db_clear_confirm

    async def clear_db_index(self) -> None:
        """Remove every document from the index (files on disk stay)."""
        import asyncio
        from ..lib.document_store import get_document_store
        self.db_clear_confirm = False
        store = get_document_store()
        if store is None:
            self.add_debug("❌ Clear failed: document store not available")  # type: ignore[attr-defined]
            return
        removed = await asyncio.to_thread(store.clear)
        self.add_debug(f"🗑️ Document index cleared: {removed} chunks")  # type: ignore[attr-defined]
        await self._refresh_db_documents()
        if self.db_orphans_visible:
            await self._reload_db_orphans()

    async def db_toggle_orphans(self) -> None:
        """Toggle the orphan-cleanup section."""
        self.db_orphans_visible = not self.db_orphans_visible
        if self.db_orphans_visible:
            await self._reload_db_orphans()

    async def _reload_db_orphans(self) -> None:
        import asyncio
        from ..lib import file_manager as fm
        result = await asyncio.to_thread(fm.list_orphaned)
        orphans = result.metadata.get("orphans", []) if result.success else []
        self.db_orphans = sorted(orphans, key=_by_path)

    async def db_deindex_document(self, filename: str) -> None:
        """Remove one document from the index (document row or orphan row)."""
        await _deindex(filename)
        await self._refresh_db_documents()
        if self.db_orphans_visible:
            await self._reload_db_orphans()

    async def db_delete_all_orphans(self) -> None:
        """Bulk-remove every orphaned document from the index."""
        for orphan in list(self.db_orphans):
            filename = str(orphan.get("filename", ""))
            if filename:
                await _deindex(filename)
        await self._reload_db_orphans()
        await self._refresh_db_documents()

    def _load_memory_collections(self) -> None:
        """Load overview of all ChromaDB agent memory collections."""
        from ..lib.agent_memory import get_agent_memory
        memory = get_agent_memory()
        if not memory:
            self.memory_browser_collections = []
            return

        from ..lib.agent_config import get_agent_config

        collections = []
        try:
            for col in memory._client.list_collections():
                if col.name.startswith("agent_memory_"):
                    agent_id = col.name.removeprefix("agent_memory_")
                    cfg = get_agent_config(agent_id)
                    display_name = f"{cfg.emoji} {cfg.display_name}" if cfg else agent_id.capitalize()
                    collections.append({
                        "name": col.name,
                        "agent_id": agent_id,
                        "display_name": display_name,
                        "count": str(col.count()),
                    })
        except Exception as e:
            self.add_debug(f"❌ Memory browser error: {e}")  # type: ignore[attr-defined]

        # Agents sorted alphabetically
        self.memory_browser_collections = sorted(
            collections,
            key=lambda c: c["agent_id"],
        )

    def browse_memory_agent(self, agent_id: str) -> None:
        """Load all entries for a specific agent's memory collection."""
        from ..lib.agent_memory import get_agent_memory
        memory = get_agent_memory()
        if not memory:
            self.memory_browser_entries = []
            return

        self.memory_browser_agent = agent_id
        # Resolve display name — must match dropdown format (with count)
        count = "0"
        for col_info in self.memory_browser_collections:
            if col_info["agent_id"] == agent_id:
                count = col_info["count"]
                break
        from ..lib.agent_config import get_agent_config
        cfg = get_agent_config(agent_id)
        name = f"{cfg.emoji} {cfg.display_name}" if cfg else agent_id.capitalize()
        self.memory_browser_agent_display = f"{name} ({count})"
        entries: list[dict] = []

        try:
            col = memory._collection(agent_id)

            if col.count() == 0:
                self.memory_browser_entries = []
                return

            data = col.get(include=["metadatas", "documents"])
            for i, doc_id in enumerate(data["ids"]):
                meta = data["metadatas"][i] if data["metadatas"] else {}  # type: ignore[index]
                doc = data["documents"][i] if data["documents"] else ""  # type: ignore[index]
                entries.append({
                    "id": doc_id,
                    "date": _meta_str(meta, "date")[:19],
                    "type": _meta_str(meta, "type", "unknown"),
                    "summary": _meta_str(meta, "summary", doc[:120] if doc else ""),
                    "content": _meta_str(meta, "content", doc or ""),
                    "session_id": _meta_str(meta, "session_id"),
                })
        except Exception as e:
            self.add_debug(f"❌ Memory browse error: {e}")  # type: ignore[attr-defined]

        entries.sort(key=lambda e: e.get("date", ""), reverse=True)
        self.memory_browser_entries = entries

    def delete_memory_entry(self, entry_id: str) -> None:
        """Delete a single memory entry from the current agent's collection."""
        from ..lib.agent_memory import get_agent_memory
        memory = get_agent_memory()
        if not memory or not self.memory_browser_agent:
            return

        try:
            col = memory._collection(self.memory_browser_agent)
            col.delete(ids=[entry_id])
            self.add_debug(f"🗑️ Memory entry deleted: {entry_id[:8]}...")  # type: ignore[attr-defined]
        except Exception as e:
            self.add_debug(f"❌ Delete failed: {e}")  # type: ignore[attr-defined]

        # Refresh collections first so browse_memory_agent reads the updated
        # count — otherwise the display value mismatches the options list.
        self._load_memory_collections()
        self.browse_memory_agent(self.memory_browser_agent)

    # ─────────────────────────────────────────────────────────
    # Agent Bundle Export
    # ─────────────────────────────────────────────────────────

    def open_bundle_export(self) -> None:
        """Open the export modal — preselect the currently edited agent."""
        from ..lib.agent_config import load_agents_raw
        raw = load_agents_raw()
        self.bundle_all_agents = [
            {
                "agent_id": aid,
                "display_name": data.get("display_name", aid),
                "emoji": data.get("emoji", ""),
            }
            for aid, data in raw.items()
        ]
        preselect = [self.editor_agent_id] if self.editor_agent_id in raw else []
        self.bundle_export_selected = preselect
        self.bundle_export_open = True

    def close_bundle_export(self) -> None:
        self.bundle_export_open = False
        self.bundle_export_selected = []

    def toggle_bundle_export_agent(self, agent_id: str) -> None:
        if agent_id in self.bundle_export_selected:
            self.bundle_export_selected = [a for a in self.bundle_export_selected if a != agent_id]
        else:
            self.bundle_export_selected = [*self.bundle_export_selected, agent_id]

    def confirm_bundle_export(self):  # type: ignore[no-untyped-def]
        """Trigger a browser download via the /api/agents/export endpoint."""
        if not self.bundle_export_selected:
            return
        ids_param = ",".join(self.bundle_export_selected)
        url = f"/api/agents/export?ids={ids_param}"
        self.bundle_export_open = False
        self.bundle_export_selected = []
        yield rx.call_script(f"window.location.href = {url!r}")

    # ─────────────────────────────────────────────────────────
    # Agent Bundle Import
    # ─────────────────────────────────────────────────────────

    def open_bundle_import(self) -> None:
        """Open the import modal in its initial empty state."""
        self.bundle_import_open = True
        self.bundle_import_uploaded_b64 = ""
        self.bundle_import_agents = []
        self.bundle_import_selected = []
        self.bundle_import_conflict = "rename"
        self.bundle_import_error = ""

    async def handle_bundle_upload(self, files: list) -> None:  # type: ignore[no-untyped-def]
        """Reflex on_drop callback — read ZIP, peek manifest, open modal."""
        import base64
        from ..lib.agent_bundle import peek_bundle

        if not files:
            return

        try:
            zip_bytes = await files[0].read()
        except Exception as exc:
            self.bundle_import_error = f"Datei konnte nicht gelesen werden: {exc}"
            self.bundle_import_open = True
            return

        try:
            info = peek_bundle(zip_bytes)
        except Exception as exc:
            self.bundle_import_error = f"Kein gültiges Agent-Bundle: {exc}"
            self.bundle_import_open = True
            return

        self.bundle_import_uploaded_b64 = base64.b64encode(zip_bytes).decode("ascii")
        self.bundle_import_agents = info["agents"]
        self.bundle_import_selected = [a["agent_id"] for a in info["agents"]]
        self.bundle_import_conflict = "rename"
        self.bundle_import_error = ""
        self.bundle_import_open = True

    def close_bundle_import(self) -> None:
        self.bundle_import_open = False
        self.bundle_import_uploaded_b64 = ""
        self.bundle_import_agents = []
        self.bundle_import_selected = []
        self.bundle_import_error = ""

    def toggle_bundle_import_agent(self, agent_id: str) -> None:
        if agent_id in self.bundle_import_selected:
            self.bundle_import_selected = [a for a in self.bundle_import_selected if a != agent_id]
        else:
            self.bundle_import_selected = [*self.bundle_import_selected, agent_id]

    def set_bundle_import_conflict(self, value: str) -> None:
        if value in ("abort", "overwrite", "rename"):
            self.bundle_import_conflict = value

    def confirm_bundle_import(self) -> None:
        """Decode the staged bundle and write the selected agents."""
        import base64
        from ..lib.agent_bundle import import_bundle

        if not self.bundle_import_uploaded_b64 or not self.bundle_import_selected:
            return

        try:
            zip_bytes = base64.b64decode(self.bundle_import_uploaded_b64)
            effective_ids, warnings = import_bundle(
                zip_bytes,
                selected_ids=self.bundle_import_selected,
                conflict=self.bundle_import_conflict,  # type: ignore[arg-type]
            )
        except FileExistsError as exc:
            self.bundle_import_error = str(exc)
            return
        except Exception as exc:
            self.bundle_import_error = f"Import fehlgeschlagen: {exc}"
            return

        for w in warnings:
            self.add_debug(f"📦 {w}")  # type: ignore[attr-defined]
        self.add_debug(  # type: ignore[attr-defined]
            f"✅ Agenten importiert: {', '.join(effective_ids)}"
        )

        self.close_bundle_import()
        self._refresh_agent_dropdown()
