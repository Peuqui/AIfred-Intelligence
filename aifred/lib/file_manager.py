"""Single source of truth for document filesystem + ChromaDB operations.

Both the workspace tool plugin (LLM-facing API) and the document-manager
UI (the document-manager API) call into this module — there is no second copy
of any path-resolution, index-sync, or write logic anywhere else.

Path safety, naming validation, ChromaDB-side cleanup and on-disk
modification all live here. Callers receive a uniform ``FileOpResult``
they can map to JSON (tool path) or to UI feedback (state path) without
duplicating semantics.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DOCUMENTS_DIR
from .formatting import format_stamp
from .user_error import Invalid, Unavailable, UserError

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class FileOpResult:
    """Uniform return type for every file-manager operation.

    ``success`` says whether the operation completed; ``detail`` is a
    human-readable summary in English, as the tools give it to the model;
    ``metadata`` carries optional structured data (counts, sizes, paths)
    that callers can serialize as needed; ``problem`` is a refusal's reason,
    which a page words in its language (``said``).
    """
    success: bool
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)
    problem: UserError | None = None

    def said(self, lang: str) -> str:
        """The outcome as a page shows it: a refusal in the UI language, anything else as ``detail`` reads."""
        return self.problem.message(lang) if self.problem else self.detail


def _refused(problem: UserError, metadata: dict[str, Any] | None = None) -> FileOpResult:
    return FileOpResult(False, str(problem), metadata or {}, problem)


# ─────────────────────────────────────────────────────────────────────────
# Path safety
# ─────────────────────────────────────────────────────────────────────────


def safe_resolve(rel_path: str) -> tuple[Path | None, Invalid | None]:
    """Resolve a path relative to DOCUMENTS_DIR, refusing escape attempts.

    Returns ``(path, None)`` on success or ``(None, refusal)`` if the path
    tries to traverse outside the documents tree (via ``..``, absolute
    paths, or symlinks pointing elsewhere); ``str(refusal)`` is the reason
    in English, as a tool gives it to the model.
    """
    if not isinstance(rel_path, str):
        return None, Invalid("path_type", type=type(rel_path).__name__)
    if rel_path.startswith(("/", "\\")):
        return None, Invalid("path_absolute", path=rel_path)

    candidate = (DOCUMENTS_DIR / rel_path).resolve()
    docs_root = DOCUMENTS_DIR.resolve()
    try:
        candidate.relative_to(docs_root)
    except ValueError:
        return None, Invalid("path_escapes", path=rel_path)
    return candidate, None


def _is_safe_name(name: str) -> bool:
    """Reject names that contain path separators or are dot-special."""
    if not name or not name.strip():
        return False
    if "/" in name or "\\" in name:
        return False
    return name not in (".", "..")


# ─────────────────────────────────────────────────────────────────────────
# Listing
# ─────────────────────────────────────────────────────────────────────────


def format_size(size_bytes: int) -> str:
    """File size for the document browser (locale-aware, format_number)."""
    from .formatting import format_number

    if size_bytes < 1024:
        return f"{format_number(size_bytes)} B"
    if size_bytes < 1024 * 1024:
        return f"{format_number(size_bytes / 1024, 1)} KB"
    return f"{format_number(size_bytes / (1024 * 1024), 1)} MB"


def chunks_per_document() -> dict[str, int]:
    """Chunk count of every indexed document; empty while ChromaDB is unavailable,
    so a file listing still works without the index."""
    chunks: dict[str, int] = {}
    try:
        from .document_store import get_document_store
        store = get_document_store()
        if store:
            for doc in store.list_documents():
                chunks[doc["filename"]] = doc.get("total_chunks", 0)
    except Exception as exc:  # ChromaDB raises a bare Exception for HTTP errors: no chunk counts, logged
        logger.warning(f"chunks_per_document: ChromaDB unavailable, skipping index lookup: {exc}", exc_info=True)
    return chunks


def list_directory(folder_rel: str = "") -> FileOpResult:
    """List files + subfolders of ``folder_rel`` (relative to documents/).

    Each item dict has: ``name, type ('file'|'folder'), size, created,
    modified, indexed, chunks, indexable, file_count``. Hidden entries (leading dot) are excluded.
    """
    from .document_store import PARSERS

    folder, err = safe_resolve(folder_rel) if folder_rel else (DOCUMENTS_DIR.resolve(), None)
    if err:
        return _refused(err)
    assert folder is not None

    if not folder.exists():
        return _refused(Invalid("folder_not_found", path=folder_rel), {"items": []})
    if not folder.is_dir():
        return _refused(Invalid("not_a_folder", path=folder_rel), {"items": []})

    indexed_chunks = chunks_per_document()

    dirs = sorted(
        [d for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda p: p.name.lower(),
    )
    files = sorted(
        [f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")],
        key=lambda p: p.name.lower(),
    )

    # "created" is ``st_ctime``: Linux has no creation time in ``stat()``,
    # it is the inode-change time. For this folder that is the moment the
    # file landed here (written once by a tool or an upload), which is
    # exactly what "created" should mean to the user. It only drifts from
    # that if the file is later renamed or its permissions change.
    items: list[dict[str, Any]] = []
    for d in dirs:
        # Count files recursively so the user can tell at a glance whether
        # a sub-folder is empty without clicking into it.
        try:
            file_count = sum(1 for p in d.rglob("*") if p.is_file())
        except OSError:
            file_count = 0
        d_stat = d.stat()
        items.append({
            "name": d.name,
            "type": "folder",
            "size": "",
            "created": format_stamp(d_stat.st_ctime),
            "modified": format_stamp(d_stat.st_mtime),
            "indexed": False,
            "chunks": 0,
            "indexable": False,
            "file_count": file_count,
        })
    for f in files:
        rel_str = str(f.relative_to(DOCUMENTS_DIR))
        chunk_count = indexed_chunks.get(rel_str) or indexed_chunks.get(f.name, 0)
        st = f.stat()
        items.append({
            "name": f.name,
            "type": "file",
            "size": format_size(st.st_size),
            "created": format_stamp(st.st_ctime),
            "modified": format_stamp(st.st_mtime),
            "indexed": chunk_count > 0,
            "chunks": chunk_count,
            "indexable": f.suffix.lower() in PARSERS,
            "file_count": 0,  # only meaningful for folders, kept for schema consistency
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
        return _refused(Invalid("invalid_folder_name", name=name))
    parent, err = safe_resolve(parent_rel) if parent_rel else (DOCUMENTS_DIR.resolve(), None)
    if err:
        return _refused(err)
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
        return _refused(Invalid("invalid_folder_name", name=name))
    parent, err = safe_resolve(parent_rel) if parent_rel else (DOCUMENTS_DIR.resolve(), None)
    if err:
        return _refused(err)
    assert parent is not None
    target = parent / name
    disk_exists = target.exists() and target.is_dir()

    if target.exists() and not disk_exists:
        # path exists but is a file, not a folder
        return _refused(Invalid("folder_is_no_file", name=name))
    # A missing disk folder must NOT abort here: the index cleanup below
    # still has to run. The actions check at the end reports the case
    # where neither a disk folder nor index entries were found.

    rel_target = str(target.resolve().relative_to(DOCUMENTS_DIR.resolve())) \
        if disk_exists else str(Path(parent_rel) / name) if parent_rel else name

    file_count = 0
    dir_count = 0
    if disk_exists:
        all_items = list(target.rglob("*"))
        file_count = sum(1 for p in all_items if p.is_file())
        dir_count = sum(1 for p in all_items if p.is_dir())

        if from_disk and file_count + dir_count > 0 and not recursive:
            return _refused(Invalid("folder_not_empty", files=file_count, folders=dir_count))

    chunks_removed = 0
    if from_index:
        from .document_store import get_document_store
        store = get_document_store()
        if store is None:
            return _refused(Unavailable("stays_index_unavailable", name=name))
        prefix = rel_target.rstrip("/") + "/"
        try:
            documents = await asyncio.to_thread(store.list_documents)
            for document in documents:
                if document["filename"].startswith(prefix):
                    chunks_removed += await store.delete_document(document["filename"], delete_file=False)
        except Exception as exc:  # noqa: BLE001 — the folder stays: gone from disk, the search would keep finding it
            logger.warning(f"delete_folder: index cleanup failed for {name}: {exc}")
            return _refused(Unavailable("stays_index_failed", name=name, reason=str(exc)))

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
        return _refused(Invalid("nothing_to_delete_folder", name=name))

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
        return _refused(err)
    assert path is not None
    if not path.exists():
        return _refused(Invalid("file_not_found", path=rel_path))
    if not path.is_file():
        return _refused(Invalid("not_a_file", path=rel_path))
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _refused(Invalid("not_utf8", path=rel_path))
    return FileOpResult(True, f"Read {len(content)} chars", {"content": content})


def write_file(rel_path: str, content: str) -> FileOpResult:
    """Write/overwrite a text file. Parent directories are created."""
    path, err = safe_resolve(rel_path)
    if err:
        return _refused(err)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info(f"write_file: {rel_path} ({len(content)} chars)")
    return FileOpResult(True, f"Wrote {len(content)} chars", {"path": rel_path})


def save_upload(folder_rel: str, filename: str, content: bytes) -> FileOpResult:
    """An uploaded file into ``folder_rel``, under its own name; one there already is replaced.

    The name loses any directory part the client sent; only types the search index reads
    (``DOCUMENT_ALLOWED_EXTENSIONS``) are taken. ``metadata["rel_path"]`` is where it went."""
    from .config import DOCUMENT_ALLOWED_EXTENSIONS

    name = Path(filename).name
    if not _is_safe_name(name):
        return _refused(Invalid("invalid_file_name", name=filename))
    suffix = Path(name).suffix.lower()
    if suffix not in DOCUMENT_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(DOCUMENT_ALLOWED_EXTENSIONS))
        return _refused(Invalid("type_not_allowed", name=name, type=suffix or "(none)", allowed=allowed))
    rel_path = f"{folder_rel}/{name}".lstrip("/")
    path, err = safe_resolve(rel_path)
    if err:
        return _refused(err)
    assert path is not None
    if not path.parent.is_dir():
        return _refused(Invalid("folder_not_found", path=folder_rel))
    path.write_bytes(content)
    logger.info(f"save_upload: {rel_path} ({len(content)} bytes)")
    return FileOpResult(True, f"Saved {rel_path}", {"rel_path": rel_path})


def files_to_index(folder_rel: str) -> FileOpResult:
    """Every file below ``folder_rel`` (sub-folders too) that the search index has a parser for,
    as paths relative to the documents folder. ``metadata["files"]``."""
    from .document_store import PARSERS

    base, err = safe_resolve(folder_rel)
    if err:
        return _refused(err)
    assert base is not None
    if not base.is_dir():
        return _refused(Invalid("folder_not_found", path=folder_rel))
    root = DOCUMENTS_DIR.resolve()
    files = sorted(
        str(path.relative_to(root)) for path in base.rglob("*")
        if path.is_file() and path.suffix.lower() in PARSERS
    )
    return FileOpResult(True, f"{len(files)} files", {"files": files})


async def delete_file(
    folder_rel: str,
    name: str,
    *,
    from_disk: bool = True,
    from_index: bool = True,
) -> FileOpResult:
    """Delete a file. Either side (disk, index) can be skipped independently."""
    if not _is_safe_name(name):
        return _refused(Invalid("invalid_file_name", name=name))
    parent, err = safe_resolve(folder_rel) if folder_rel else (DOCUMENTS_DIR.resolve(), None)
    if err:
        return _refused(err)
    assert parent is not None
    target = parent / name
    if target.exists() and target.is_dir():
        return _refused(Invalid("file_is_a_folder", name=name))
    # A missing file on disk must NOT abort here: disk and index are
    # independent, so the index cleanup below still has to run. The
    # actions check at the end reports the "nothing to delete" case.

    rel_target = str(target.relative_to(DOCUMENTS_DIR))
    actions: list[str] = []
    chunks_removed = 0

    if from_index:
        from .document_store import get_document_store
        store = get_document_store()
        if store is None:
            return _refused(Unavailable("stays_index_unavailable", name=name))
        try:
            chunks_removed = await store.delete_document(rel_target, delete_file=False)
        except Exception as exc:  # noqa: BLE001 — the file stays: gone from disk, the search would keep finding it
            logger.warning(f"delete_file: index cleanup failed for {name}: {exc}")
            return _refused(Unavailable("stays_index_failed", name=name, reason=str(exc)))
        if chunks_removed:
            actions.append(f"index({chunks_removed} chunks)")

    if from_disk and target.exists():
        target.unlink()
        actions.append("disk")

    if not actions:
        return _refused(Invalid("nothing_to_delete_file", name=name))

    detail = f"Deleted {name}: {', '.join(actions)}"
    logger.info(f"delete_file: {rel_target} ({', '.join(actions)})")
    return FileOpResult(True, detail, {"chunks_removed": chunks_removed, "actions": actions})


def _sync_index_path(old_path: Path, new_path: Path) -> int:
    """Take the index along after a file or folder changed its path, so what is indexed
    stays searchable under the new one. Returns the number of chunks moved (0 when
    nothing is indexed there or the store is down)."""
    chunks_updated = 0
    try:
        from .document_store import get_document_store
        store = get_document_store()
        if store:
            old_rel = str(old_path.relative_to(DOCUMENTS_DIR))
            new_rel = str(new_path.relative_to(DOCUMENTS_DIR))
            names = [old_rel]
            if new_path.is_dir():
                names = [
                    document["filename"] for document in store.list_documents()
                    if document["filename"].startswith(f"{old_rel}/")
                ]
            for name in names:
                chunks_updated += store.rename_document(name, new_rel + name.removeprefix(old_rel))
    except Exception as exc:  # noqa: BLE001 — the rename stands, only the index is behind
        logger.warning(f"index path sync failed for {old_path.name}: {exc}")
    return chunks_updated


def copy_file(src_rel: str, dest_rel: str, *, overwrite: bool = False) -> FileOpResult:
    """Copy a file (binary-safe) inside the documents tree.

    ``dest_rel`` may be an existing folder — the file keeps its name then.
    Refuses to overwrite unless ``overwrite`` is set. Files only (no
    recursive folder copies).
    """
    import shutil
    src, err = safe_resolve(src_rel)
    if err:
        return _refused(err)
    assert src is not None
    if not src.exists():
        return _refused(Invalid("source_not_found", path=src_rel))
    if src.is_dir():
        return _refused(Invalid("copies_files_only"))

    dest, err = safe_resolve(dest_rel)
    if err:
        return _refused(err)
    assert dest is not None
    if dest.is_dir():
        dest = dest / src.name
    if dest == src:
        return _refused(Invalid("same_source_and_target"))
    if dest.exists() and not overwrite:
        return _refused(Invalid("target_exists_overwrite", path=dest_rel))

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    size = dest.stat().st_size
    dest_out = str(dest.relative_to(DOCUMENTS_DIR))
    logger.info(f"copy_file: {src_rel} → {dest_out} ({size} bytes)")
    return FileOpResult(True, f"Copied {size} bytes", {"path": dest_out, "bytes": size})


def move_file(src_rel: str, dest_rel: str, *, overwrite: bool = False) -> FileOpResult:
    """Move a file or folder inside the documents tree (rename across
    folders). Indexed files keep their searchability — chunk metadata is
    updated like in :func:`rename`. For moved FOLDERS the contained files'
    index paths become stale (use list_orphaned/index_document to repair).
    """
    import shutil
    src, err = safe_resolve(src_rel)
    if err:
        return _refused(err)
    assert src is not None
    if not src.exists():
        return _refused(Invalid("source_not_found", path=src_rel))

    dest, err = safe_resolve(dest_rel)
    if err:
        return _refused(err)
    assert dest is not None
    if dest.is_dir():
        dest = dest / src.name
    if dest == src:
        return _refused(Invalid("same_source_and_target"))
    if dest.exists() and not overwrite:
        return _refused(Invalid("target_exists_overwrite", path=dest_rel))

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    chunks_updated = _sync_index_path(src, dest)
    dest_out = str(dest.relative_to(DOCUMENTS_DIR))
    detail = f"Moved {src_rel} → {dest_out}"
    if chunks_updated:
        detail += f" (index updated, {chunks_updated} chunks)"
    logger.info(detail)
    return FileOpResult(True, detail, {"path": dest_out, "chunks_updated": chunks_updated})


def rename(
    folder_rel: str,
    old_name: str,
    new_name: str,
    *,
    sync_index: bool = True,
) -> FileOpResult:
    """Rename a file or folder. With ``sync_index=True`` the index follows, so
    what is indexed stays searchable under the new path.
    """
    if not _is_safe_name(new_name):
        return _refused(Invalid("invalid_new_name", name=new_name))
    if old_name == new_name:
        return _refused(Invalid("same_name"))

    parent, err = safe_resolve(folder_rel) if folder_rel else (DOCUMENTS_DIR.resolve(), None)
    if err:
        return _refused(err)
    assert parent is not None
    old_path = parent / old_name
    new_path = parent / new_name

    if not old_path.exists():
        return _refused(Invalid("source_not_found", path=old_name))
    if new_path.exists():
        return _refused(Invalid("target_exists", path=new_name))

    old_path.rename(new_path)

    chunks_updated = _sync_index_path(old_path, new_path) if sync_index else 0

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
        return _refused(Unavailable("document_store_unavailable"))

    path, err = safe_resolve(rel_path)
    if err:
        return _refused(err)
    assert path is not None
    if not path.exists():
        return _refused(Invalid("file_not_found", path=rel_path))
    if not path.is_file():
        return _refused(Invalid("not_a_file", path=rel_path))

    chunks = await store.index_document(path, rel_path)
    detail = f"Indexed {rel_path}: {chunks} chunks"
    logger.info(detail)
    return FileOpResult(True, detail, {"chunks": chunks, "rel_path": rel_path})


async def _index_temp_copy(chunks: AsyncIterator[bytes], name: str, source_key: str, file_id: str) -> FileOpResult:
    """Index a file that stays where it is: only a temporary copy is parsed, the
    index keeps the text. The result reports a failure instead of raising, a bad
    file must not end a run over a folder."""
    from .document_sources import source_index_name
    from .document_store import PARSERS, get_document_store
    store = get_document_store()
    if not store:
        return _refused(Unavailable("document_store_unavailable"))
    if Path(name).suffix.lower() not in PARSERS:
        return _refused(Invalid("unsupported_file_type", name=name))

    try:
        with tempfile.TemporaryDirectory(prefix="aifred-index-") as folder:
            copy = Path(folder) / Path(name).name
            with copy.open("wb") as target:
                async for chunk in chunks:
                    target.write(chunk)
            indexed = await store.index_document(copy, source_index_name(source_key, file_id), source=source_key)
    except Exception as exc:  # the user sees why the file was not indexed; logged
        logger.warning(f"index {source_key} {name}: {exc}", exc_info=True)
        return FileOpResult(False, f"{name}: {exc}")
    detail = f"Indexed {name} from {source_key}: {indexed} chunks"
    logger.info(detail)
    return FileOpResult(True, detail, {"chunks": indexed})


async def index_source_file(source_key: str, file_id: str, name: str) -> FileOpResult:
    """Index a file of a further document source."""
    from .document_sources import get_document_source

    async def download() -> AsyncIterator[bytes]:
        source_file = await get_document_source(source_key).download(file_id)
        async for chunk in source_file.chunks:
            yield chunk

    return await _index_temp_copy(download(), name, source_key, file_id)


async def index_received_file(data: bytes, name: str, source_key: str, file_id: str) -> FileOpResult:
    """Index a file that arrived from outside, such as a mail attachment. The index
    marks its text as external, like everything from a source."""
    async def content() -> AsyncIterator[bytes]:
        yield data

    return await _index_temp_copy(content(), name, source_key, file_id)


async def deindex_file(rel_path: str) -> FileOpResult:
    """Remove a file from ChromaDB index without touching disk."""
    from .document_store import get_document_store
    store = get_document_store()
    if not store:
        return _refused(Unavailable("document_store_unavailable"))

    chunks = await store.delete_document(rel_path, delete_file=False)
    detail = f"Deindexed {rel_path}: {chunks} chunks"
    logger.info(detail)
    return FileOpResult(True, detail, {"chunks_removed": chunks})


async def search_index(
    query: str,
    n_results: int = 5,
    folder: str | None = None,
    page: int = 1,
    without_corpora: bool = False,
) -> FileOpResult:
    """Search indexed documents by meaning and by words, over every chunk.

    Args:
        query: Search query.
        n_results: Page size — number of ranked hits per page. Each hit may
                   bring along ±DOCUMENT_SEARCH_NEIGHBOR_WINDOW neighbor
                   chunks so the model sees the full surrounding context.
        folder: If set, restrict to this folder. Recursively includes
                sub-folders via prefix match — ``"bibel"`` covers
                ``"bibel/Schlachter"`` AND ``"bibel/GuteNachricht"``;
                ``"bibel/Schlachter"`` stays narrow. ``None`` = whole store.
        page: 1-based page number for pagination. ``page=2`` skips the
              first ``n_results`` hits — same query, deeper into the ranking.
        without_corpora: Leave out the separate corpora when no folder is named.
    """
    from .document_store import get_document_store
    store = get_document_store()
    if not store:
        return _refused(Unavailable("document_store_unavailable"), {"results": []})

    hits, has_more, total = await store.search(
        query, n_results=n_results, folder=folder, page=page, without_corpora=without_corpora
    )
    return FileOpResult(
        True,
        f"{len(hits)} hits (page {page})",
        {"results": hits, "has_more": has_more, "page": page, "total": total},
    )


def list_indexed() -> FileOpResult:
    """Return all indexed documents (filename + chunk count + timestamp)."""
    from .document_store import get_document_store
    store = get_document_store()
    if not store:
        return _refused(Unavailable("document_store_unavailable"), {"documents": []})
    docs = store.list_documents()
    return FileOpResult(True, f"{len(docs)} indexed documents", {"documents": docs})


def list_orphaned() -> FileOpResult:
    """Return indexed documents whose source file is missing on disk.

    The result groups by filename (one entry per orphaned document, not per
    chunk) so the caller can review and bulk-delete entire documents
    without scrolling through individual chunks. Files of a further document
    source never lie on disk, so they are no orphans.
    """
    from .document_store import get_document_store
    store = get_document_store()
    if not store:
        return _refused(Unavailable("document_store_unavailable"), {"orphans": []})

    docs = store.list_documents()
    docs_root = DOCUMENTS_DIR.resolve()
    orphans = [
        d for d in docs
        if not d["source"] and not (docs_root / d["filename"]).exists()
    ]
    return FileOpResult(
        True,
        f"{len(orphans)} orphaned documents (out of {len(docs)} indexed)",
        {"orphans": orphans, "total_indexed": len(docs)},
    )
