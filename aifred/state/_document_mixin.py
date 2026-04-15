"""
Document Mixin – file explorer, upload, indexing and management.

Part of the AIfred State refactoring (aifred/state/ package).
"""

import reflex as rx
from typing import Any, Dict, List


class DocumentMixin(rx.State, mixin=True):
    """Mixin for document file explorer and management."""

    # State variables
    is_uploading_document: bool = False
    document_upload_status: str = ""
    document_manager_open: bool = False
    document_preview_filename: str = ""
    document_preview_content: str = ""

    # File explorer state
    doc_current_folder: str = ""  # relative to data/documents/, empty = root
    doc_file_list: List[Dict[str, Any]] = []  # [{name, type, size, indexed, chunks}]

    # Rename state
    doc_rename_target: str = ""  # file/folder being renamed
    doc_rename_value: str = ""  # new name input

    # Delete confirmation dialog
    doc_delete_target: str = ""  # filename or foldername to delete
    doc_delete_is_folder: bool = False  # target is a folder (recursive delete)
    doc_delete_from_disk: bool = True
    doc_delete_from_index: bool = True

    # Create folder state
    doc_creating_folder: bool = False
    doc_new_folder_name: str = ""

    # ================================================================
    # MODAL OPEN / CLOSE
    # ================================================================

    def open_document_manager(self) -> None:
        """Open the document manager modal and refresh file list."""
        self.doc_current_folder = ""
        self._refresh_file_list()
        self.document_manager_open = True

    def close_document_manager(self) -> None:
        """Close the document manager modal."""
        self.document_manager_open = False
        self.document_preview_filename = ""
        self.document_preview_content = ""
        self.doc_delete_target = ""

    # ================================================================
    # FILE EXPLORER — BROWSE
    # ================================================================

    def _refresh_file_list(self) -> None:
        """Scan filesystem + check ChromaDB index status for current folder."""
        from ..lib.config import DOCUMENTS_DIR
        from ..lib.document_store import get_document_store

        folder = DOCUMENTS_DIR / self.doc_current_folder
        if not folder.exists():
            self.doc_file_list = []
            return

        # Get indexed filenames from ChromaDB
        indexed_docs: dict[str, int] = {}
        store = get_document_store()
        if store:
            for doc in store.list_documents():
                indexed_docs[doc["filename"]] = doc.get("total_chunks", 0)

        items: list[dict[str, Any]] = []

        # Directories first, then files, both sorted
        dirs = sorted([d for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")],
                       key=lambda p: p.name.lower())
        files = sorted([f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")],
                        key=lambda p: p.name.lower())

        for d in dirs:
            items.append({
                "name": d.name,
                "type": "folder",
                "size": "",
                "indexed": False,
                "chunks": 0,
            })

        for f in files:
            # Build relative path for index lookup
            rel_path = f.relative_to(DOCUMENTS_DIR)
            rel_str = str(rel_path)
            size_bytes = f.stat().st_size
            if size_bytes < 1024:
                size_str = f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"

            chunk_count = indexed_docs.get(rel_str, 0)
            # Also check just filename (old uploads without subfolder)
            if not chunk_count:
                chunk_count = indexed_docs.get(f.name, 0)

            items.append({
                "name": f.name,
                "type": "file",
                "size": size_str,
                "indexed": chunk_count > 0,
                "chunks": chunk_count,
            })

        self.doc_file_list = items
        self.add_debug(f"📂 _refresh_file_list: folder={self.doc_current_folder!r}, {len(items)} items: {[i['name'] for i in items]}")  # type: ignore[attr-defined]

    def doc_navigate_folder(self, folder_name: str) -> None:
        """Navigate into a subfolder."""
        from pathlib import PurePosixPath
        if self.doc_current_folder:
            new_path = str(PurePosixPath(self.doc_current_folder) / folder_name)
        else:
            new_path = folder_name
        self.doc_current_folder = new_path
        self._refresh_file_list()
        self.document_preview_filename = ""
        self.document_preview_content = ""

    def doc_navigate_up(self) -> None:
        """Navigate to parent folder."""
        from pathlib import PurePosixPath
        if self.doc_current_folder:
            parent = str(PurePosixPath(self.doc_current_folder).parent)
            self.doc_current_folder = "" if parent == "." else parent
        self._refresh_file_list()

    def doc_navigate_root(self) -> None:
        """Navigate to root documents folder."""
        self.doc_current_folder = ""
        self._refresh_file_list()

    # ================================================================
    # CREATE / DELETE FOLDER
    # ================================================================

    def doc_refresh(self) -> None:
        """Refresh the file list (e.g. after agent wrote files)."""
        self._refresh_file_list()

    def doc_open_create_folder(self) -> None:
        """Show inline input for new folder name."""
        self.doc_creating_folder = True
        self.doc_new_folder_name = ""

    def doc_cancel_create_folder(self) -> None:
        """Hide new-folder input."""
        self.doc_creating_folder = False
        self.doc_new_folder_name = ""

    def doc_set_new_folder_name(self, value: str) -> None:
        """Update new-folder input value."""
        self.doc_new_folder_name = value

    def doc_confirm_create_folder(self) -> None:
        """Create subfolder from doc_new_folder_name in current directory."""
        from ..lib.config import DOCUMENTS_DIR

        name = self.doc_new_folder_name.strip()
        if not name or "/" in name or "\\" in name:
            self.doc_cancel_create_folder()
            return
        target = DOCUMENTS_DIR / self.doc_current_folder / name
        target.mkdir(parents=True, exist_ok=True)
        self.doc_creating_folder = False
        self.doc_new_folder_name = ""
        self._refresh_file_list()

    def doc_open_delete_folder_dialog(self, folder_name: str) -> None:
        """Open delete confirmation for a folder (recursive)."""
        self.doc_delete_target = folder_name
        self.doc_delete_is_folder = True
        self.doc_delete_from_disk = True
        self.doc_delete_from_index = True

    # ================================================================
    # RENAME
    # ================================================================

    def doc_start_rename(self, name: str) -> None:
        """Open inline rename for a file or folder."""
        self.doc_rename_target = name
        self.doc_rename_value = name

    def doc_cancel_rename(self) -> None:
        """Cancel rename."""
        self.doc_rename_target = ""
        self.doc_rename_value = ""

    def doc_set_rename_value(self, value: str) -> None:
        """Update rename input value."""
        self.doc_rename_value = value

    def doc_confirm_rename(self) -> None:
        """Execute rename on disk (and update ChromaDB if indexed)."""
        from ..lib.config import DOCUMENTS_DIR
        from ..lib.document_store import get_document_store

        old_name = self.doc_rename_target
        new_name = self.doc_rename_value.strip()
        if not old_name or not new_name or old_name == new_name:
            self.doc_rename_target = ""
            return
        if "/" in new_name or "\\" in new_name:
            return

        old_path = DOCUMENTS_DIR / self.doc_current_folder / old_name
        new_path = DOCUMENTS_DIR / self.doc_current_folder / new_name

        if not old_path.exists() or new_path.exists():
            self.doc_rename_target = ""
            return

        # Rename on disk
        old_path.rename(new_path)

        # Update ChromaDB index if file was indexed (re-index under new name)
        if old_path.is_file():
            old_rel = str(old_path.relative_to(DOCUMENTS_DIR))
            new_rel = str(new_path.relative_to(DOCUMENTS_DIR))
            store = get_document_store()
            if store:
                # Check if old name is in index
                all_data = store._collection.get(where={"filename": old_rel})
                if all_data and all_data["ids"]:
                    # Update metadata filename on all chunks
                    for chunk_id in all_data["ids"]:
                        store._collection.update(
                            ids=[chunk_id],
                            metadatas=[{"filename": new_rel}],
                        )
                else:
                    # Try with just the filename (old uploads)
                    all_data = store._collection.get(where={"filename": old_name})
                    if all_data and all_data["ids"]:
                        for chunk_id in all_data["ids"]:
                            store._collection.update(
                                ids=[chunk_id],
                                metadatas=[{"filename": new_rel}],
                            )

        self.doc_rename_target = ""
        self.doc_rename_value = ""
        self._refresh_file_list()

    # ================================================================
    # UPLOAD
    # ================================================================

    async def handle_document_upload(self, files: List[rx.UploadFile]) -> None:  # type: ignore[override]
        """Handle document file upload — save to current folder."""
        from ..lib.config import DOCUMENT_ALLOWED_EXTENSIONS, DOCUMENT_MAX_FILE_SIZE_MB, DOCUMENTS_DIR
        from ..lib.i18n import t
        from pathlib import Path

        self.is_uploading_document = True
        self.document_upload_status = ""
        yield  # type: ignore[misc]

        try:
            target_dir = DOCUMENTS_DIR / self.doc_current_folder
            target_dir.mkdir(parents=True, exist_ok=True)

            for file in files:
                filename = file.filename or "unknown.txt"
                suffix = Path(filename).suffix.lower()

                # Validate extension
                if suffix not in DOCUMENT_ALLOWED_EXTENSIONS:
                    self.document_upload_status = t(
                        "doc_upload_invalid_type", lang=self.ui_language,  # type: ignore[attr-defined]
                        filename=filename, allowed=", ".join(DOCUMENT_ALLOWED_EXTENSIONS),
                    )
                    self.add_debug(f"\u26a0\ufe0f Document rejected: {filename} (type {suffix})")  # type: ignore[attr-defined]
                    yield  # type: ignore[misc]
                    continue

                # Read content
                content = await file.read()

                # Validate size (0 = no limit)
                size_mb = len(content) / (1024 * 1024)
                if DOCUMENT_MAX_FILE_SIZE_MB > 0 and size_mb > DOCUMENT_MAX_FILE_SIZE_MB:
                    self.document_upload_status = t(
                        "doc_upload_too_large", lang=self.ui_language,  # type: ignore[attr-defined]
                        filename=filename, max_mb=DOCUMENT_MAX_FILE_SIZE_MB,
                    )
                    yield  # type: ignore[misc]
                    continue

                # Save to disk (in current folder)
                file_path = target_dir / filename
                file_path.write_bytes(content)

                self.document_upload_status = t(
                    "doc_upload_saved", lang=self.ui_language,  # type: ignore[attr-defined]
                    filename=filename,
                )
                self.add_debug(f"\U0001f4c4 Document uploaded: {filename}")  # type: ignore[attr-defined]
                yield  # type: ignore[misc]

            # Refresh file list
            self._refresh_file_list()

        except Exception as e:
            self.document_upload_status = f"\u274c {e}"
            self.add_debug(f"\u274c Document upload failed: {e}")  # type: ignore[attr-defined]

        finally:
            self.is_uploading_document = False
            import asyncio
            await asyncio.sleep(4)
            self.document_upload_status = ""
            yield  # type: ignore[misc]

    # ================================================================
    # INDEX / DEINDEX
    # ================================================================

    async def doc_index_file(self, filename: str) -> None:
        """Index a file into ChromaDB."""
        from ..lib.config import DOCUMENTS_DIR
        from ..lib.document_store import get_document_store
        from ..lib.i18n import t

        store = get_document_store()
        if not store:
            self.document_upload_status = t("doc_upload_no_store", lang=self.ui_language)  # type: ignore[attr-defined]
            return

        file_path = DOCUMENTS_DIR / self.doc_current_folder / filename
        if not file_path.exists():
            return

        # Use relative path as document name
        rel_path = str(file_path.relative_to(DOCUMENTS_DIR))

        self.document_upload_status = t(
            "doc_upload_indexing", lang=self.ui_language,  # type: ignore[attr-defined]
            filename=filename,
        )
        yield  # type: ignore[misc]

        chunks = await store.index_document(file_path, rel_path)

        self.document_upload_status = t(
            "doc_upload_success", lang=self.ui_language,  # type: ignore[attr-defined]
            filename=filename, chunks=chunks,
        )
        self.add_debug(f"\u2705 Indexed: {rel_path} ({chunks} chunks)")  # type: ignore[attr-defined]
        self._refresh_file_list()
        yield  # type: ignore[misc]

        import asyncio
        await asyncio.sleep(4)
        self.document_upload_status = ""
        yield  # type: ignore[misc]

    async def doc_deindex_file(self, filename: str) -> None:
        """Remove a file from ChromaDB index (file stays on disk)."""
        from ..lib.config import DOCUMENTS_DIR
        from ..lib.document_store import get_document_store
        from ..lib.i18n import t

        store = get_document_store()
        if not store:
            return

        file_path = DOCUMENTS_DIR / self.doc_current_folder / filename
        rel_path = str(file_path.relative_to(DOCUMENTS_DIR))

        count = await store.delete_document(rel_path, delete_file=False)
        # Also try just filename (old uploads)
        if count == 0:
            count = await store.delete_document(filename, delete_file=False)

        self.document_upload_status = t(
            "doc_deindex_success", lang=self.ui_language,  # type: ignore[attr-defined]
            filename=filename, chunks=count,
        )
        self.add_debug(f"\U0001f4e4 Deindexed: {filename} ({count} chunks)")  # type: ignore[attr-defined]
        self._refresh_file_list()
        yield  # type: ignore[misc]

        import asyncio
        await asyncio.sleep(4)
        self.document_upload_status = ""
        yield  # type: ignore[misc]

    # ================================================================
    # DELETE — with confirmation dialog
    # ================================================================

    def doc_open_delete_dialog(self, filename: str) -> None:
        """Open delete confirmation for a file."""
        self.doc_delete_target = filename
        self.doc_delete_is_folder = False
        self.doc_delete_from_disk = True
        self.doc_delete_from_index = True

    def doc_close_delete_dialog(self) -> None:
        """Close delete confirmation."""
        self.doc_delete_target = ""
        self.doc_delete_is_folder = False

    def doc_toggle_delete_disk(self, _val: bool) -> None:
        """Toggle 'delete from disk' checkbox."""
        self.doc_delete_from_disk = not self.doc_delete_from_disk

    def doc_toggle_delete_index(self, _val: bool) -> None:
        """Toggle 'delete from index' checkbox."""
        self.doc_delete_from_index = not self.doc_delete_from_index

    async def doc_confirm_delete(self) -> None:
        """Execute delete with selected options (file or folder)."""
        import shutil
        from ..lib.config import DOCUMENTS_DIR
        from ..lib.document_store import get_document_store
        from ..lib.i18n import t

        target_name = self.doc_delete_target
        is_folder = self.doc_delete_is_folder
        if not target_name:
            return

        target_path = DOCUMENTS_DIR / self.doc_current_folder / target_name
        rel_path = str(target_path.relative_to(DOCUMENTS_DIR))
        actions: list[str] = []

        # Delete from index
        if self.doc_delete_from_index:
            store = get_document_store()
            if store:
                if is_folder:
                    # Remove all indexed files whose path starts with this folder
                    prefix = rel_path.rstrip("/") + "/"
                    total = 0
                    all_data = store._collection.get()
                    if all_data and all_data.get("metadatas"):
                        matching_files = {
                            m["filename"] for m in all_data["metadatas"]
                            if m.get("filename", "").startswith(prefix)
                        }
                        for fn in matching_files:
                            total += await store.delete_document(fn, delete_file=False)
                    actions.append(f"Index ({total} chunks)")
                else:
                    count = await store.delete_document(rel_path, delete_file=False)
                    if count == 0:
                        count = await store.delete_document(target_name, delete_file=False)
                    actions.append(f"Index ({count} chunks)")

        # Delete from disk
        if self.doc_delete_from_disk and target_path.exists():
            if is_folder:
                shutil.rmtree(target_path)
            else:
                target_path.unlink()
            actions.append("Disk")

        action_str = " + ".join(actions) if actions else "nothing"
        self.document_upload_status = t(
            "doc_delete_done", lang=self.ui_language,  # type: ignore[attr-defined]
            filename=target_name, actions=action_str,
        )
        self.add_debug(f"\U0001f5d1\ufe0f Deleted {target_name}: {action_str}")  # type: ignore[attr-defined]

        self.doc_delete_target = ""
        self.doc_delete_is_folder = False
        self._refresh_file_list()
        yield  # type: ignore[misc]

        import asyncio
        await asyncio.sleep(4)
        self.document_upload_status = ""
        yield  # type: ignore[misc]

    # ================================================================
    # PREVIEW
    # ================================================================

    def preview_document(self, filename: str) -> None:
        """Load document content for preview in the modal."""
        from ..lib.config import DOCUMENTS_DIR
        from ..lib.document_store import PARSERS

        file_path = DOCUMENTS_DIR / self.doc_current_folder / filename
        if not file_path.exists():
            self.document_preview_filename = filename
            self.document_preview_content = "\u274c File not found"
            return

        suffix = file_path.suffix.lower()
        parser = PARSERS.get(suffix)
        if not parser:
            self.document_preview_filename = filename
            self.document_preview_content = f"\u274c No parser for {suffix}"
            return

        try:
            text = parser(file_path)
            if len(text) > 10_000:
                text = text[:10_000] + f"\n\n... ({len(text) - 10_000} chars truncated)"
            self.document_preview_filename = filename
            self.document_preview_content = text
        except Exception as e:
            self.document_preview_filename = filename
            self.document_preview_content = f"\u274c {e}"

    def close_document_preview(self) -> None:
        """Close the document preview."""
        self.document_preview_filename = ""
        self.document_preview_content = ""
