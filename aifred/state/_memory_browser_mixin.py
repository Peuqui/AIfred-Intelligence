"""Memory browser mixin for AIfred state.

Handles the memory browser (ChromaDB agent memory collections),
the database tab (document index, orphan cleanup) and agent bundle
export/import.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import reflex as rx
from reflex.event import EventSpec

from ..lib.logging_utils import log_exception

if TYPE_CHECKING:
    from ..lib.file_manager import FileOpResult


def _meta_str(meta: Any, key: str, default: str = "") -> str:
    """ChromaDB-Metadata-Wert als str — verengt die Union
    ``str | int | float | SparseVector | None`` typsicher (SSOT für die
    Memory-Browser-Schleife)."""
    value = (meta or {}).get(key)
    return value if isinstance(value, str) else default


def _by_path(doc: dict[str, Any]) -> str:
    """Sort key for index lists: the document path, case-insensitive."""
    return str(doc.get("filename", "")).casefold()


async def _deindex(filename: str) -> FileOpResult:
    """Remove one document from the index only (the file stays on disk)."""
    from ..lib import file_manager as fm
    parts = filename.strip("/").rsplit("/", 1)
    parent_rel, leaf = ("", parts[0]) if len(parts) == 1 else (parts[0], parts[1])
    return await fm.delete_file(parent_rel, leaf, from_disk=False, from_index=True)



class MemoryBrowserMixin(rx.State, mixin=True):
    """Mixin for memory/database browsing and agent bundle export/import."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        agent_editor_mode: rx.Field[str]
        editor_agent_id: rx.Field[str]
        ui_language: rx.Field[str]

        def _refresh_agent_dropdown(self) -> None: ...
        @rx.event
        def add_debug(self, message: str) -> None: ...

    # Database tab: the document index, one entry per document (not per chunk)
    db_documents: rx.Field[list[dict[str, Any]]] = rx.field(default_factory=list)
    db_clear_confirm: rx.Field[bool] = rx.field(False)  # Confirmation state for clear-all

    # Orphan cleanup: indexed documents whose file is gone from disk
    db_orphans: rx.Field[list[dict[str, Any]]] = rx.field(default_factory=list)      # one entry per orphaned document (not per chunk)
    db_orphans_visible: rx.Field[bool] = rx.field(False)           # toggles the orphan section

    # Memory browser state
    memory_browser_agent: rx.Field[str] = rx.field("")  # Selected agent in memory browser ("" = overview)
    memory_browser_agent_display: rx.Field[str] = rx.field("")  # Display name of selected agent
    memory_browser_entries: rx.Field[list[dict[str, str]]] = rx.field(default_factory=list)  # Entries for selected agent
    memory_browser_collections: rx.Field[list[dict[str, str]]] = rx.field(default_factory=list)  # Collection overview
    memory_browser_filter: rx.Field[str] = rx.field("all")  # "all", "session", "agent"
    # Entry being edited in the memory browser ("" = none) and its draft
    memory_edit_id: rx.Field[str] = rx.field("")
    memory_edit_summary: rx.Field[str] = rx.field("")
    memory_edit_content: rx.Field[str] = rx.field("")
    memory_confirm_clear: rx.Field[bool] = rx.field(False)  # "Delete all" of the agent asked

    # ── Agent Bundle Export/Import ──────────────────────────────
    bundle_export_open: rx.Field[bool] = rx.field(False)
    bundle_export_selected: rx.Field[list[str]] = rx.field(default_factory=list)
    # Snapshot of all agents for the export modal — refreshed when modal opens
    bundle_all_agents: rx.Field[list[dict[str, str]]] = rx.field(default_factory=list)

    bundle_import_open: rx.Field[bool] = rx.field(False)
    bundle_import_uploaded_b64: rx.Field[str] = rx.field("")  # base64-encoded ZIP, kept in state until confirm
    bundle_import_agents: rx.Field[list[dict[str, Any]]] = rx.field(default_factory=list)  # output of peek_bundle
    bundle_import_selected: rx.Field[list[str]] = rx.field(default_factory=list)
    bundle_import_conflict: rx.Field[str] = rx.field("rename")  # abort | overwrite | rename
    bundle_import_error: rx.Field[str] = rx.field("")

    @rx.event
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

    @rx.event
    def select_memory_agent(self, label: str) -> None:
        """Select an agent in the memory browser dropdown."""
        # Find agent_id from display label (label may include count suffix)
        for col in self.memory_browser_collections:
            if col["display_name"] == label or label.startswith(col["display_name"]):
                self.browse_memory_agent(col["agent_id"])
                return

    @rx.event
    def set_memory_filter(self, filter_value: str) -> None:
        """Set the memory type filter (all/session/agent)."""
        self.memory_browser_filter = filter_value


    @rx.var(deps=["memory_browser_collections"], auto_deps=False)
    def memory_agent_dropdown_options(self) -> list[str]:
        """Agent dropdown labels with memory count."""
        return [
            f"{col['display_name']} ({col['count']})"
            for col in self.memory_browser_collections
        ]

    @rx.var(deps=["memory_browser_entries", "memory_browser_filter"], auto_deps=False)
    def filtered_memory_entries(self) -> list[dict[str, str]]:
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
            self.add_debug(f"❌ DB browse error: {result.detail}")
        docs = result.metadata.get("documents", []) if result.success else []
        self.db_documents = sorted(docs, key=_by_path)

    @rx.event
    async def db_load_documents(self) -> None:
        """Event: the database tab was opened."""
        await self._refresh_db_documents()

    @rx.event
    def confirm_clear_db(self) -> None:
        """Toggle confirmation state for clearing the document index."""
        self.db_clear_confirm = not self.db_clear_confirm

    @rx.event
    async def clear_db_index(self) -> None:
        """Remove every document from the index (files on disk stay)."""
        import asyncio
        from ..lib.document_store import get_document_store
        self.db_clear_confirm = False
        store = get_document_store()
        if store is None:
            self.add_debug("❌ Clear failed: document store not available")
            return
        removed = await asyncio.to_thread(store.clear)
        self.add_debug(f"🗑️ Document index cleared: {removed} chunks")
        await self._refresh_db_documents()
        if self.db_orphans_visible:
            await self._reload_db_orphans()

    @rx.event
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

    @rx.event
    async def db_deindex_document(self, filename: str) -> AsyncGenerator[EventSpec | None, None]:
        """Remove one document from the index (document row or orphan row)."""
        result = await _deindex(filename)
        await self._refresh_db_documents()
        if self.db_orphans_visible:
            await self._reload_db_orphans()
        if not result.success:
            self.add_debug(f"⚠️ {result.detail}")
            yield rx.toast.error(result.said(self.ui_language), duration=8000, position="top-center")

    @rx.event
    async def db_delete_all_orphans(self) -> AsyncGenerator[EventSpec | None, None]:
        """Bulk-remove every orphaned document from the index."""
        failed = []
        for orphan in list(self.db_orphans):
            filename = str(orphan.get("filename", ""))
            if filename:
                result = await _deindex(filename)
                if not result.success:
                    failed.append(result.detail)
        await self._reload_db_orphans()
        await self._refresh_db_documents()
        for detail in failed:
            self.add_debug(f"⚠️ {detail}")
            yield rx.toast.error(detail, duration=8000, position="top-center")

    def _load_memory_collections(self) -> None:
        """Load the memory overview: every agent with its entry count.

        Every configured agent may store memories, so each one is listed, also
        before its ChromaDB collection exists (count 0). Collections of agents
        deleted since stay listed: their entries are still there to read,
        edit or delete.
        """
        from ..lib.agent_memory import get_agent_memory
        memory = get_agent_memory()
        if not memory:
            self.memory_browser_collections = []
            return

        from ..lib.agent_config import get_agent_ids, get_agent_label
        from ..lib.config import AGENT_MEMORY_COLLECTION_PREFIX

        counts: dict[str, int] = {}
        try:
            for col in memory._client.list_collections():
                if col.name.startswith(AGENT_MEMORY_COLLECTION_PREFIX):
                    counts[col.name.removeprefix(AGENT_MEMORY_COLLECTION_PREFIX)] = col.count()
        except Exception as e:  # noqa: BLE001 — ChromaDB's client has no common error class; shown, logged with traceback
            log_exception("Memory browser: listing the collections", e)
            self.add_debug(f"❌ Memory browser error: {e}")

        # Agents sorted alphabetically
        self.memory_browser_collections = [
            {
                "name": f"{AGENT_MEMORY_COLLECTION_PREFIX}{agent_id}",
                "agent_id": agent_id,
                "display_name": get_agent_label(agent_id),
                "count": str(counts.get(agent_id, 0)),
            }
            for agent_id in sorted(set(get_agent_ids()) | set(counts))
        ]

    @rx.event
    def browse_memory_agent(self, agent_id: str) -> None:
        """Load all entries for a specific agent's memory collection."""
        from ..lib.agent_memory import get_agent_memory
        memory = get_agent_memory()
        if not memory:
            self.memory_browser_entries = []
            return

        self.memory_browser_agent = agent_id
        self.cancel_memory_edit()
        self.memory_confirm_clear = False
        # Resolve display name — must match dropdown format (with count)
        count = "0"
        for col_info in self.memory_browser_collections:
            if col_info["agent_id"] == agent_id:
                count = col_info["count"]
                break
        from ..lib.agent_config import get_agent_label
        self.memory_browser_agent_display = f"{get_agent_label(agent_id)} ({count})"
        entries: list[dict] = []

        # An agent without entries may not have a collection yet; reading it
        # through _collection() would create one as a side effect.
        if count == "0":
            self.memory_browser_entries = []
            return

        try:
            col = memory._collection(agent_id)

            data = col.get(include=["metadatas", "documents"])
            for i, doc_id in enumerate(data["ids"]):
                meta = data["metadatas"][i] if data["metadatas"] else {}
                doc = data["documents"][i] if data["documents"] else ""
                entries.append({
                    "id": doc_id,
                    "date": _meta_str(meta, "date")[:19],
                    "type": _meta_str(meta, "type", "unknown"),
                    "summary": _meta_str(meta, "summary", doc[:120] if doc else ""),
                    "content": _meta_str(meta, "content", doc or ""),
                    "session_id": _meta_str(meta, "session_id"),
                })
        except Exception as e:  # noqa: BLE001 — ChromaDB's client has no common error class; shown, logged with traceback
            log_exception("Memory browser: reading the entries", e)
            self.add_debug(f"❌ Memory browse error: {e}")

        entries.sort(key=lambda e: e.get("date", ""), reverse=True)
        self.memory_browser_entries = entries

    @rx.event
    def delete_memory_entry(self, entry_id: str) -> None:
        """Delete a single memory entry from the current agent's collection."""
        from ..lib.agent_memory import get_agent_memory
        memory = get_agent_memory()
        if not memory or not self.memory_browser_agent:
            return

        try:
            col = memory._collection(self.memory_browser_agent)
            col.delete(ids=[entry_id])
            self.add_debug(f"🗑️ Memory entry deleted: {entry_id[:8]}...")
        except Exception as e:  # noqa: BLE001 — ChromaDB's client has no common error class; shown, logged with traceback
            log_exception("Memory browser: delete", e)
            self.add_debug(f"❌ Delete failed: {e}")

        # Refresh collections first so browse_memory_agent reads the updated
        # count — otherwise the display value mismatches the options list.
        self._load_memory_collections()
        self.browse_memory_agent(self.memory_browser_agent)

    @rx.event
    def request_clear_memory(self) -> None:
        self.memory_confirm_clear = True

    @rx.event
    def cancel_clear_memory(self) -> None:
        self.memory_confirm_clear = False

    @rx.event
    def clear_browsed_memory(self) -> None:
        """Delete every memory entry of the browsed agent (after confirmation)."""
        from ..lib.agent_memory import get_agent_memory
        self.memory_confirm_clear = False
        memory = get_agent_memory()
        if not memory or not self.memory_browser_agent or not self.memory_browser_entries:
            return

        try:
            count = memory.clear(self.memory_browser_agent)
            self.add_debug(f"🗑️ {count} memory entries deleted ({self.memory_browser_agent})")
        except Exception as e:  # noqa: BLE001 — ChromaDB's client has no common error class; shown, logged with traceback
            log_exception("Memory browser: delete", e)
            self.add_debug(f"❌ Delete failed: {e}")

        self._load_memory_collections()
        self.browse_memory_agent(self.memory_browser_agent)

    @rx.event
    def start_memory_edit(self, entry_id: str) -> None:
        """Open one memory entry for editing in place."""
        for entry in self.memory_browser_entries:
            if entry["id"] == entry_id:
                self.memory_edit_id = entry_id
                self.memory_edit_summary = entry["summary"]
                self.memory_edit_content = entry["content"]
                return

    @rx.event
    def cancel_memory_edit(self) -> None:
        self.memory_edit_id = ""
        self.memory_edit_summary = ""
        self.memory_edit_content = ""

    @rx.event
    def set_memory_edit_summary(self, value: str) -> None:
        self.memory_edit_summary = value

    @rx.event
    def set_memory_edit_content(self, value: str) -> None:
        self.memory_edit_content = value

    @rx.event
    async def save_memory_edit(self) -> None:
        """Write the edited summary and content back to the agent's memory.

        Goes through AgentMemory.update, the same path as the agents'
        update_memory tool: the summary is re-embedded, type and session stay.
        """
        from ..lib.agent_memory import get_agent_memory, summary_fits_index
        from ..lib.config import AGENT_MEMORY_SUMMARY_MAX_CHARS

        memory = get_agent_memory()
        entry = next(
            (e for e in self.memory_browser_entries if e["id"] == self.memory_edit_id),
            None,
        )
        if not memory or not self.memory_browser_agent or entry is None:
            return

        summary = self.memory_edit_summary.strip()
        content = self.memory_edit_content.strip()
        if not summary or not content:
            self.add_debug("❌ Memory entry needs a summary and a content")
            return
        if not summary_fits_index(summary):
            self.add_debug(
                f"❌ Memory summary must be one line of at most "
                f"{AGENT_MEMORY_SUMMARY_MAX_CHARS} characters"
            )
            return

        try:
            await memory.update(
                self.memory_browser_agent,
                entry["id"],
                content,
                summary,
                memory_type=entry["type"],
                session_id=entry["session_id"],
            )
            self.add_debug(f"✏️ Memory entry updated: {entry['id'][:8]}...")
        except Exception as e:  # noqa: BLE001 — ChromaDB's client has no common error class; shown, logged with traceback
            log_exception("Memory browser: update", e)
            self.add_debug(f"❌ Update failed: {e}")
            return

        self._load_memory_collections()
        self.browse_memory_agent(self.memory_browser_agent)

    # ─────────────────────────────────────────────────────────
    # Agent Bundle Export
    # ─────────────────────────────────────────────────────────

    @rx.event
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

    @rx.event
    def close_bundle_export(self) -> None:
        self.bundle_export_open = False
        self.bundle_export_selected = []

    @rx.event
    def toggle_bundle_export_agent(self, agent_id: str) -> None:
        if agent_id in self.bundle_export_selected:
            self.bundle_export_selected = [a for a in self.bundle_export_selected if a != agent_id]
        else:
            self.bundle_export_selected = [*self.bundle_export_selected, agent_id]

    @rx.event
    def confirm_bundle_export(self):
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

    @rx.event
    def open_bundle_import(self) -> None:
        """Open the import modal in its initial empty state."""
        self.bundle_import_open = True
        self.bundle_import_uploaded_b64 = ""
        self.bundle_import_agents = []
        self.bundle_import_selected = []
        self.bundle_import_conflict = "rename"
        self.bundle_import_error = ""

    @rx.event
    async def handle_bundle_upload(self, files: list[rx.UploadFile]) -> None:
        """Reflex on_drop callback — read ZIP, peek manifest, open modal."""
        import base64
        from ..lib.agent_bundle import BundleUnreadable, peek_bundle
        from ..lib.user_error import said

        if not files:
            return

        try:
            zip_bytes = await files[0].read()
        except OSError as exc:
            self.bundle_import_error = f"Datei konnte nicht gelesen werden: {exc}"
            self.bundle_import_open = True
            return

        try:
            info = peek_bundle(zip_bytes)
        except BundleUnreadable as exc:
            self.bundle_import_error = said(exc, self.ui_language)
            self.bundle_import_open = True
            return

        self.bundle_import_uploaded_b64 = base64.b64encode(zip_bytes).decode("ascii")
        self.bundle_import_agents = info["agents"]
        self.bundle_import_selected = [a["agent_id"] for a in info["agents"]]
        self.bundle_import_conflict = "rename"
        self.bundle_import_error = ""
        self.bundle_import_open = True

    @rx.event
    def close_bundle_import(self) -> None:
        self.bundle_import_open = False
        self.bundle_import_uploaded_b64 = ""
        self.bundle_import_agents = []
        self.bundle_import_selected = []
        self.bundle_import_error = ""

    @rx.event
    def toggle_bundle_import_agent(self, agent_id: str) -> None:
        if agent_id in self.bundle_import_selected:
            self.bundle_import_selected = [a for a in self.bundle_import_selected if a != agent_id]
        else:
            self.bundle_import_selected = [*self.bundle_import_selected, agent_id]

    @rx.event
    def set_bundle_import_conflict(self, value: str) -> None:
        if value in ("abort", "overwrite", "rename"):
            self.bundle_import_conflict = value

    @rx.event
    def confirm_bundle_import(self) -> None:
        """Decode the staged bundle and write the selected agents."""
        import base64
        from ..lib.agent_bundle import import_bundle
        from ..lib.user_error import UserError, said

        if not self.bundle_import_uploaded_b64 or not self.bundle_import_selected:
            return

        try:
            zip_bytes = base64.b64decode(self.bundle_import_uploaded_b64)
            effective_ids, warnings = import_bundle(
                zip_bytes,
                selected_ids=self.bundle_import_selected,
                conflict=self.bundle_import_conflict,  # type: ignore[arg-type]
                lang=self.ui_language,
            )
        except (UserError, OSError) as exc:  # unreadable, an agent there already, or writing the agent files
            self.bundle_import_error = said(exc, self.ui_language)
            return

        for w in warnings:
            self.add_debug(f"📦 {w}")
        self.add_debug(
            f"✅ Agenten importiert: {', '.join(effective_ids)}"
        )

        self.close_bundle_import()
        self._refresh_agent_dropdown()
