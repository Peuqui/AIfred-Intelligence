"""Single source of truth for document filesystem + ChromaDB operations.

Both the workspace tool plugin (LLM-facing API) and the document-manager
UI (Reflex State mixin) call into this module — there is no second copy
of any path-resolution, index-sync, or write logic anywhere else.

Path safety, naming validation, ChromaDB-side cleanup and on-disk
modification all live here. Callers receive a uniform ``FileOpResult``
they can map to JSON (tool path) or to UI feedback (state path) without
duplicating semantics.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import DOCUMENTS_DIR

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class FileOpResult:
    """Uniform return type for every file-manager operation.

    ``success`` says whether the operation completed; ``detail`` is a
    human-readable summary suitable for both UI toasts and tool output;
    ``metadata`` carries optional structured data (counts, sizes, paths)
    that callers can serialize as needed.
    """
    success: bool
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"success": self.success, "detail": self.detail}
        out.update(self.metadata)
        return out


# ─────────────────────────────────────────────────────────────────────────
# Path safety
# ─────────────────────────────────────────────────────────────────────────


def safe_resolve(rel_path: str) -> tuple[Optional[Path], Optional[str]]:
    """Resolve a path relative to DOCUMENTS_DIR, refusing escape attempts.

    Returns ``(path, None)`` on success or ``(None, error_message)`` if
    the path tries to traverse outside the documents tree (via ``..``,
    absolute paths, or symlinks pointing elsewhere).
    """
    if not isinstance(rel_path, str):
        return None, f"Invalid path type: {type(rel_path).__name__}"
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        return None, f"Absolute paths not allowed: {rel_path!r}"

    candidate = (DOCUMENTS_DIR / rel_path).resolve()
    docs_root = DOCUMENTS_DIR.resolve()
    try:
        candidate.relative_to(docs_root)
    except ValueError:
        return None, f"Path escapes documents tree: {rel_path!r}"
    return candidate, None


def _is_safe_name(name: str) -> bool:
    """Reject names that contain path separators or are dot-special."""
    if not name or not name.strip():
        return False
    if "/" in name or "\\" in name:
        return False
    if name in (".", ".."):
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────
# Listing
# ─────────────────────────────────────────────────────────────────────────


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def list_directory(folder_rel: str = "") -> FileOpResult:
    """List files + subfolders of ``folder_rel`` (relative to documents/).

    Each item dict has: ``name, type ('file'|'folder'), size, indexed,
    chunks``. Hidden entries (leading dot) are excluded.
    """
    folder, err = safe_resolve(folder_rel) if folder_rel else (DOCUMENTS_DIR.resolve(), None)
    if err:
        return FileOpResult(False, err)
    assert folder is not None

    if not folder.exists():
        return FileOpResult(False, f"Folder not found: {folder_rel}", {"items": []})
    if not folder.is_dir():
        return FileOpResult(False, f"Not a folder: {folder_rel}", {"items": []})

    indexed_chunks: dict[str, int] = {}
    try:
        from .document_store import get_document_store
        store = get_document_store()
        if store:
            for doc in store.list_documents():
                indexed_chunks[doc["filename"]] = doc.get("total_chunks", 0)
    except Exception as exc:
        logger.warning(f"list_directory: ChromaDB unavailable, skipping index lookup: {exc}")

    dirs = sorted(
        [d for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda p: p.name.lower(),
    )
    files = sorted(
        [f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")],
        key=lambda p: p.name.lower(),
    )

    items: list[dict[str, Any]] = []
    for d in dirs:
        items.append({
            "name": d.name,
            "type": "folder",
            "size": "",
            "indexed": False,
            "chunks": 0,
        })
    for f in files:
        rel_str = str(f.relative_to(DOCUMENTS_DIR))
        chunk_count = indexed_chunks.get(rel_str) or indexed_chunks.get(f.name, 0)
        items.append({
            "name": f.name,
            "type": "file",
            "size": _format_size(f.stat().st_size),
            "indexed": chunk_count > 0,
            "chunks": chunk_count,
        })
    return FileOpResult(True, f"{len(items)} items", {"items": items})


# ─────────────────────────────────────────────────────────────────────────
# Folder operations
# ─────────────────────────────────────────────────────────────────────────


def create_folder(parent_rel: str, name: str) -> FileOpResult:
    """Create ``name`` as a subfolder of ``parent_rel``.

    Idempotent: existing folders are not an error (mirrors `mkdir -p`
    semantics, which is what the UI/agent both want).
    """
    if not _is_safe_name(name):
        return FileOpResult(False, f"Invalid folder name: {name!r}")
    parent, err = safe_resolve(parent_rel) if parent_rel else (DOCUMENTS_DIR.resolve(), None)
    if err:
        return FileOpResult(False, err)
    assert parent is not None
    target = parent / name
    target.mkdir(parents=True, exist_ok=True)
    logger.info(f"create_folder: {target.relative_to(DOCUMENTS_DIR)}")
    return FileOpResult(True, f"Created: {name}", {"path": str(target.relative_to(DOCUMENTS_DIR))})


async def delete_folder(
    parent_rel: str,
    name: str,
    *,
    recursive: bool = False,
    from_index: bool = True,
    from_disk: bool = True,
) -> FileOpResult:
    """Delete folder ``name`` from ``parent_rel`` on disk and/or in ChromaDB.

    Either side can be skipped independently (``from_disk=False`` for a
    pure index cleanup when the source folder has already been removed
    manually, ``from_index=False`` to keep the chunks for re-use).

    With ``from_disk=True`` and ``recursive=False`` the folder must be
    empty (matches the safe-by-default Unix ``rmdir`` semantics).
    """
    if not _is_safe_name(name):
        return FileOpResult(False, f"Invalid folder name: {name!r}")
    parent, err = safe_resolve(parent_rel) if parent_rel else (DOCUMENTS_DIR.resolve(), None)
    if err:
        return FileOpResult(False, err)
    assert parent is not None
    target = parent / name
    disk_exists = target.exists() and target.is_dir()

    if from_disk and not disk_exists:
        if target.exists():
            return FileOpResult(False, f"Not a folder: {name}. Use delete_file.")
        return FileOpResult(False, f"Folder not found: {name}")

    rel_target = str((target if disk_exists else target).resolve().relative_to(DOCUMENTS_DIR.resolve())) \
        if disk_exists else str(Path(parent_rel) / name) if parent_rel else name

    file_count = 0
    dir_count = 0
    if disk_exists:
        all_items = list(target.rglob("*"))
        file_count = sum(1 for p in all_items if p.is_file())
        dir_count = sum(1 for p in all_items if p.is_dir())

        if from_disk and file_count + dir_count > 0 and not recursive:
            return FileOpResult(
                False,
                f"Folder not empty ({file_count} files, {dir_count} subfolders). "
                f"Pass recursive=True to delete the entire tree.",
            )

    chunks_removed = 0
    if from_index:
        try:
            from .document_store import get_document_store
            store = get_document_store()
            if store:
                prefix = rel_target.rstrip("/") + "/"
                all_data = store._collection.get()
                if all_data and all_data.get("metadatas"):
                    matching = {
                        m["filename"] for m in all_data["metadatas"]
                        if m.get("filename", "").startswith(prefix)
                    }
                    for fn in matching:
                        chunks_removed += await store.delete_document(fn, delete_file=False)
        except Exception as exc:
            logger.warning(f"delete_folder: index cleanup partially failed: {exc}")

    actions: list[str] = []
    if from_index and chunks_removed:
        actions.append(f"index({chunks_removed} chunks)")
    if from_disk and disk_exists:
        if recursive:
            shutil.rmtree(target)
            actions.append(f"disk({file_count} files, {dir_count} subfolders)")
        else:
            target.rmdir()
            actions.append("disk(empty)")

    if not actions:
        return FileOpResult(False, f"Nothing to delete for {name}: no disk folder, no index entries")

    detail = f"Deleted {name}: {', '.join(actions)}"
    logger.info(f"delete_folder: {rel_target} ({', '.join(actions)})")
    return FileOpResult(True, detail, {
        "files_removed": file_count,
        "subfolders_removed": dir_count,
        "chunks_removed": chunks_removed,
    })


# ─────────────────────────────────────────────────────────────────────────
# File operations
# ─────────────────────────────────────────────────────────────────────────


def read_file(rel_path: str) -> FileOpResult:
    """Read a text file and return its content."""
    path, err = safe_resolve(rel_path)
    if err:
        return FileOpResult(False, err)
    assert path is not None
    if not path.exists():
        return FileOpResult(False, f"File not found: {rel_path}")
    if not path.is_file():
        return FileOpResult(False, f"Not a file: {rel_path}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return FileOpResult(False, f"File is not UTF-8 text: {rel_path}")
    return FileOpResult(True, f"Read {len(content)} chars", {"content": content})


def write_file(rel_path: str, content: str) -> FileOpResult:
    """Write/overwrite a text file. Parent directories are created."""
    path, err = safe_resolve(rel_path)
    if err:
        return FileOpResult(False, err)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info(f"write_file: {rel_path} ({len(content)} chars)")
    return FileOpResult(True, f"Wrote {len(content)} chars", {"path": rel_path})


async def delete_file(
    folder_rel: str,
    name: str,
    *,
    from_disk: bool = True,
    from_index: bool = True,
) -> FileOpResult:
    """Delete a file. Either side (disk, index) can be skipped independently."""
    if not _is_safe_name(name):
        return FileOpResult(False, f"Invalid filename: {name!r}")
    parent, err = safe_resolve(folder_rel) if folder_rel else (DOCUMENTS_DIR.resolve(), None)
    if err:
        return FileOpResult(False, err)
    assert parent is not None
    target = parent / name
    if from_disk and not target.exists():
        return FileOpResult(False, f"File not found: {name}")
    if target.exists() and target.is_dir():
        return FileOpResult(False, f"Is a folder: {name}. Use delete_folder.")

    rel_target = str(target.relative_to(DOCUMENTS_DIR))
    actions: list[str] = []
    chunks_removed = 0

    if from_index:
        try:
            from .document_store import get_document_store
            store = get_document_store()
            if store:
                chunks_removed = await store.delete_document(rel_target, delete_file=False)
                if chunks_removed == 0:
                    # Legacy uploads stored without subfolder prefix
                    chunks_removed = await store.delete_document(name, delete_file=False)
                if chunks_removed:
                    actions.append(f"index({chunks_removed} chunks)")
        except Exception as exc:
            logger.warning(f"delete_file: index cleanup failed for {name}: {exc}")

    if from_disk and target.exists():
        target.unlink()
        actions.append("disk")

    detail = f"Deleted {name}: {', '.join(actions) if actions else 'nothing'}"
    logger.info(f"delete_file: {rel_target} ({', '.join(actions)})")
    return FileOpResult(True, detail, {"chunks_removed": chunks_removed, "actions": actions})


def rename(
    folder_rel: str,
    old_name: str,
    new_name: str,
    *,
    sync_index: bool = True,
) -> FileOpResult:
    """Rename a file or folder. With ``sync_index=True`` ChromaDB chunk
    metadata is updated so the indexed document remains searchable under
    its new path.
    """
    if not _is_safe_name(new_name):
        return FileOpResult(False, f"Invalid new name: {new_name!r}")
    if old_name == new_name:
        return FileOpResult(False, "Old and new name are identical")

    parent, err = safe_resolve(folder_rel) if folder_rel else (DOCUMENTS_DIR.resolve(), None)
    if err:
        return FileOpResult(False, err)
    assert parent is not None
    old_path = parent / old_name
    new_path = parent / new_name

    if not old_path.exists():
        return FileOpResult(False, f"Source not found: {old_name}")
    if new_path.exists():
        return FileOpResult(False, f"Target exists: {new_name}")

    old_path.rename(new_path)

    chunks_updated = 0
    if sync_index and new_path.is_file():
        try:
            from .document_store import get_document_store
            store = get_document_store()
            if store:
                old_rel = str(old_path.relative_to(DOCUMENTS_DIR))
                new_rel = str(new_path.relative_to(DOCUMENTS_DIR))
                # Try canonical rel-path first, fall back to bare filename
                # (legacy uploads stored without subfolder prefix).
                for filename_query in (old_rel, old_name):
                    data = store._collection.get(where={"filename": filename_query})
                    if data and data.get("ids"):
                        for chunk_id in data["ids"]:
                            store._collection.update(
                                ids=[chunk_id],
                                metadatas=[{"filename": new_rel}],
                            )
                            chunks_updated += 1
                        break
        except Exception as exc:
            logger.warning(f"rename: index sync failed for {old_name}: {exc}")

    detail = f"Renamed {old_name} → {new_name}"
    if chunks_updated:
        detail += f" (index updated, {chunks_updated} chunks)"
    logger.info(detail)
    return FileOpResult(True, detail, {"chunks_updated": chunks_updated})


# ─────────────────────────────────────────────────────────────────────────
# Index operations (thin wrappers over document_store)
# ─────────────────────────────────────────────────────────────────────────


async def index_file(rel_path: str) -> FileOpResult:
    """Index a file into ChromaDB. Returns chunk count on success."""
    from .document_store import get_document_store
    store = get_document_store()
    if not store:
        return FileOpResult(False, "Document store not available")

    path, err = safe_resolve(rel_path)
    if err:
        return FileOpResult(False, err)
    assert path is not None
    if not path.exists():
        return FileOpResult(False, f"File not found: {rel_path}")
    if not path.is_file():
        return FileOpResult(False, f"Not a file: {rel_path}")

    chunks = await store.index_document(path, rel_path)
    detail = f"Indexed {rel_path}: {chunks} chunks"
    logger.info(detail)
    return FileOpResult(True, detail, {"chunks": chunks, "rel_path": rel_path})


async def deindex_file(rel_path: str, fallback_filename: str = "") -> FileOpResult:
    """Remove a file from ChromaDB index without touching disk."""
    from .document_store import get_document_store
    store = get_document_store()
    if not store:
        return FileOpResult(False, "Document store not available")

    chunks = await store.delete_document(rel_path, delete_file=False)
    if chunks == 0 and fallback_filename:
        chunks = await store.delete_document(fallback_filename, delete_file=False)
    detail = f"Deindexed {rel_path}: {chunks} chunks"
    logger.info(detail)
    return FileOpResult(True, detail, {"chunks_removed": chunks})


async def search_index(
    query: str,
    n_results: int = 5,
    folder: Optional[str] = None,
) -> FileOpResult:
    """Semantic search over indexed documents.

    Args:
        query: Search query.
        n_results: Max number of similarity hits (capped at
                   DOCUMENT_SEARCH_MAX_RESULTS). Each hit may bring
                   along ±DOCUMENT_SEARCH_NEIGHBOR_WINDOW neighbor chunks
                   so the model sees the full surrounding context.
        folder: If set, restrict search to this exact folder string
                (e.g. "bibel/Schlachter"). Sub-folders are not auto-included.
    """
    from .config import DOCUMENT_SEARCH_MAX_RESULTS
    from .document_store import get_document_store
    store = get_document_store()
    if not store:
        return FileOpResult(False, "Document store not available", {"results": []})

    hits = await store.search(
        query, n_results=min(n_results, DOCUMENT_SEARCH_MAX_RESULTS), folder=folder
    )
    return FileOpResult(True, f"{len(hits)} hits", {"results": hits})


def list_indexed() -> FileOpResult:
    """Return all indexed documents (filename + chunk count + timestamp)."""
    from .document_store import get_document_store
    store = get_document_store()
    if not store:
        return FileOpResult(False, "Document store not available", {"documents": []})
    docs = store.list_documents()
    return FileOpResult(True, f"{len(docs)} indexed documents", {"documents": docs})


def list_orphaned() -> FileOpResult:
    """Return indexed documents whose source file is missing on disk.

    The result groups by filename (one entry per orphaned document, not per
    chunk) so the caller can review and bulk-delete entire documents
    without scrolling through individual chunks.
    """
    from .document_store import get_document_store
    store = get_document_store()
    if not store:
        return FileOpResult(False, "Document store not available", {"orphans": []})

    docs = store.list_documents()
    docs_root = DOCUMENTS_DIR.resolve()
    orphans = [
        d for d in docs
        if not (docs_root / d["filename"]).exists()
    ]
    return FileOpResult(
        True,
        f"{len(orphans)} orphaned documents (out of {len(docs)} indexed)",
        {"orphans": orphans, "total_indexed": len(docs)},
    )
