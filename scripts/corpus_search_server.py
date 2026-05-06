#!/usr/bin/env python3
"""HTTP service for the AIfred corpus: search + admin (browse, delete, reindex).

Serves a FastAPI app on 127.0.0.1:8005 by default. Designed to be
reverse-proxied by the Narnia nginx under /corpus/api/. The static
HTML UI lives separately under /corpus/ (handled by nginx).

Endpoints:
- GET  /api/health                      health check
- GET  /api/folders                     list folder values + chunk counts
- GET  /api/documents                   list all indexed documents
- GET  /api/documents/{filename}/chunks paginated chunk list of one document
- DELETE /api/documents/{filename}      remove document from index (and optionally disk)
- POST /api/reindex                     re-index one file by filename
- POST /api/search                      semantic OR literal search (mode=...)

Run manually:
    venv/bin/python scripts/corpus_search_server.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aifred.lib.document_store import get_document_store  # noqa: E402
from aifred.lib.config import DOCUMENTS_DIR  # noqa: E402

app = FastAPI(title="AIfred Corpus Search & Admin", version="1.0")

# Local-only by design (reverse-proxied by nginx). CORS is permissive so the
# UI served from the same origin can call the API without preflight pain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _store():
    s = get_document_store()
    if s is None:
        raise HTTPException(503, "DocumentStore unavailable — is ChromaDB running?")
    return s


# ─────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict[str, Any]:
    s = get_document_store()
    if s is None:
        return {"ok": False, "reason": "DocumentStore not connected"}
    return {"ok": True, "total_chunks": s.collection.count()}


# ─────────────────────────────────────────────────────────────────
# Folders / Documents browse
# ─────────────────────────────────────────────────────────────────


@app.get("/api/folders")
def folders() -> dict[str, Any]:
    """Return every folder value seen in metadata, with chunk count."""
    s = _store()
    counts: dict[str, int] = {}
    page = 5000
    offset = 0
    while True:
        data = s.collection.get(include=["metadatas"], limit=page, offset=offset)
        metas = data.get("metadatas") or []
        if not metas:
            break
        for m in metas:
            if isinstance(m, dict):
                f = str(m.get("folder", ""))
                counts[f] = counts.get(f, 0) + 1
        if len(metas) < page:
            break
        offset += page
    items = [{"folder": f, "chunks": c} for f, c in sorted(counts.items())]
    total = sum(c for _, c in counts.items())
    return {"total_chunks": total, "folders": items}


@app.get("/api/documents")
def documents() -> dict[str, Any]:
    """List every indexed document (filename + chunk count + folder)."""
    s = _store()
    docs: dict[str, dict[str, Any]] = {}
    page = 5000
    offset = 0
    while True:
        data = s.collection.get(include=["metadatas"], limit=page, offset=offset)
        metas = data.get("metadatas") or []
        if not metas:
            break
        for m in metas:
            if not isinstance(m, dict):
                continue
            fname = str(m.get("filename", ""))
            if not fname:
                continue
            entry = docs.setdefault(fname, {
                "filename": fname,
                "folder": str(m.get("folder", "")),
                "total_chunks": int(m.get("total_chunks", 0) or 0),
                "upload_date": str(m.get("upload_date", "")),
                "chunks_indexed": 0,
            })
            entry["chunks_indexed"] += 1
        if len(metas) < page:
            break
        offset += page
    items = sorted(docs.values(), key=lambda d: d["filename"])
    return {"count": len(items), "documents": items}


@app.get("/api/documents/{filename:path}/chunks")
def chunks(
    filename: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    """Return chunks of a single document, ordered by chunk_index."""
    s = _store()
    fname = unquote(filename)
    data = s.collection.get(
        where={"filename": fname},
        include=["documents", "metadatas"],
    )
    if not data.get("ids"):
        raise HTTPException(404, f"No chunks for filename {fname!r}")

    rows: list[dict[str, Any]] = []
    docs = data.get("documents") or []
    metas = data.get("metadatas") or []
    for d, m in zip(docs, metas):
        meta = m or {}
        rows.append({
            "chunk_index": int(meta.get("chunk_index", 0)),
            "total_chunks": int(meta.get("total_chunks", 0)),
            "content": d if isinstance(d, str) else "",
        })
    rows.sort(key=lambda r: r["chunk_index"])
    page = rows[offset:offset + limit]
    return {
        "filename": fname,
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "chunks": page,
    }


# ─────────────────────────────────────────────────────────────────
# Mutations: delete, reindex
# ─────────────────────────────────────────────────────────────────


class DeleteRequest(BaseModel):
    delete_file: bool = Field(
        False,
        description="Also delete the source file from disk (default: keep file, "
                    "only drop the chunks).",
    )


@app.delete("/api/documents/{filename:path}")
def delete_document(filename: str, delete_file: bool = False) -> dict[str, Any]:
    """Remove a document's chunks from the vector DB.

    Pass ?delete_file=true to also remove the source .txt/.pdf from disk.
    """
    s = _store()
    fname = unquote(filename)
    chunks_removed = asyncio.run(s.delete_document(fname, delete_file=delete_file))
    return {
        "filename": fname,
        "chunks_removed": chunks_removed,
        "file_deleted": delete_file,
    }


class ReindexRequest(BaseModel):
    filename: str = Field(
        description="Relative path under data/documents/, e.g. "
                    "'judaica/talmud/berakhot.txt'.",
    )


@app.post("/api/reindex")
def reindex(req: ReindexRequest) -> dict[str, Any]:
    """Re-index a single file. Uses delete-before-upsert per filename."""
    s = _store()
    fname = req.filename.strip().lstrip("/")
    file_path = DOCUMENTS_DIR / fname
    if not file_path.exists():
        raise HTTPException(404, f"File not found on disk: {fname}")
    if not file_path.is_file():
        raise HTTPException(400, f"Not a file: {fname}")

    chunks = asyncio.run(s.index_document(file_path, fname))
    return {"filename": fname, "chunks_indexed": chunks}


# ─────────────────────────────────────────────────────────────────
# Upload + Folder operations
# ─────────────────────────────────────────────────────────────────

# Supported parser suffixes (matches PARSERS in document_store.py).
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".csv", ".docx", ".xlsx",
                      ".pptx", ".odt", ".ods", ".odp"}


def _safe_relpath(folder: str, name: str) -> Path:
    """Resolve folder/name into DOCUMENTS_DIR; reject traversal."""
    rel = Path(folder.strip("/")) / name if folder else Path(name)
    target = (DOCUMENTS_DIR / rel).resolve()
    if not str(target).startswith(str(DOCUMENTS_DIR.resolve()) + str(Path("/"))):
        # Allow exact match too (file directly in DOCUMENTS_DIR root)
        if target != DOCUMENTS_DIR.resolve():
            raise HTTPException(400, "Path traversal not allowed")
    return target


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    folder: str = Form(default=""),
    auto_index: bool = Form(default=True),
) -> dict[str, Any]:
    """Upload a document into data/documents/<folder>/<filename>.

    Pass auto_index=false to only place the file without indexing.
    """
    s = _store()
    if not file.filename:
        raise HTTPException(400, "Missing filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            400,
            f"Unsupported file type: {suffix}. "
            f"Allowed: {sorted(SUPPORTED_SUFFIXES)}",
        )

    target = _safe_relpath(folder, file.filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    target.write_bytes(content)
    rel = target.relative_to(DOCUMENTS_DIR).as_posix()

    chunks_indexed = 0
    if auto_index:
        chunks_indexed = await s.index_document(target, rel)

    return {
        "filename": rel,
        "size_bytes": len(content),
        "indexed": auto_index,
        "chunks_indexed": chunks_indexed,
    }


@app.delete("/api/folders/{folder:path}")
def delete_folder(folder: str, delete_files: bool = False) -> dict[str, Any]:
    """Delete every chunk whose folder == folder (exact match, no prefix).

    Pass ?delete_files=true to also remove the source files from disk.
    """
    s = _store()
    target_folder = unquote(folder).strip("/")
    data = s.collection.get(
        where={"folder": target_folder},
        include=["metadatas"],
    )
    if not data.get("ids"):
        return {"folder": target_folder, "chunks_removed": 0, "files_deleted": 0}

    files = sorted({(m or {}).get("filename", "") for m in data["metadatas"]})
    files = [f for f in files if f]

    chunks_total = 0
    files_deleted = 0
    for fname in files:
        chunks_total += asyncio.run(
            s.delete_document(fname, delete_file=delete_files)
        )
        if delete_files:
            files_deleted += 1
    return {
        "folder": target_folder,
        "files_affected": len(files),
        "chunks_removed": chunks_total,
        "files_deleted": files_deleted,
    }


@app.post("/api/reindex-folder")
def reindex_folder(folder: str = Form(...)) -> dict[str, Any]:
    """Walk every file under data/documents/<folder>/ on disk and re-index it.

    Folder is treated as a path under DOCUMENTS_DIR. Existing chunks for
    each filename are replaced via delete-before-upsert.
    """
    s = _store()
    target = (DOCUMENTS_DIR / folder.strip("/")).resolve()
    base = DOCUMENTS_DIR.resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(400, "Path traversal not allowed")
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, f"Folder not found: {folder}")

    files = sorted(
        p for p in target.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    indexed = 0
    chunks_total = 0
    failures: list[dict[str, str]] = []
    for path in files:
        rel = path.relative_to(DOCUMENTS_DIR).as_posix()
        try:
            chunks = asyncio.run(s.index_document(path, rel))
            chunks_total += chunks
            indexed += 1
        except Exception as exc:
            failures.append({"filename": rel, "error": str(exc)})
    return {
        "folder": folder.strip("/"),
        "files_indexed": indexed,
        "chunks_total": chunks_total,
        "failures": failures,
    }


# ─────────────────────────────────────────────────────────────────
# Inventory — joined view of disk + index
# ─────────────────────────────────────────────────────────────────


def _scan_disk_files() -> dict[str, dict[str, Any]]:
    """Walk DOCUMENTS_DIR and return {relpath: {filename, folder, size_bytes}}.

    Skips unsupported suffixes and hidden files. Used to detect:
      - disk-only files (on disk, not in index)
      - orphans (in index, missing on disk)
    """
    out: dict[str, dict[str, Any]] = {}
    base = DOCUMENTS_DIR.resolve()
    if not base.exists():
        return out
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        rel = path.relative_to(base).as_posix()
        folder = str(Path(rel).parent.as_posix()) if "/" in rel else ""
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        out[rel] = {
            "filename": rel,
            "folder": folder,
            "size_bytes": size,
        }
    return out


def _scan_index_files() -> dict[str, dict[str, Any]]:
    """Aggregate index metadata per filename: folder, indexed_chunks."""
    s = _store()
    docs: dict[str, dict[str, Any]] = {}
    page = 5000
    offset = 0
    while True:
        data = s.collection.get(include=["metadatas"], limit=page, offset=offset)
        metas = data.get("metadatas") or []
        if not metas:
            break
        for m in metas:
            if not isinstance(m, dict):
                continue
            fname = str(m.get("filename", ""))
            if not fname:
                continue
            entry = docs.setdefault(fname, {
                "filename": fname,
                "folder": str(m.get("folder", "")),
                "indexed_chunks": 0,
                "upload_date": str(m.get("upload_date", "")),
            })
            entry["indexed_chunks"] += 1
        if len(metas) < page:
            break
        offset += page
    return docs


@app.get("/api/inventory")
def inventory() -> dict[str, Any]:
    """Joined view: every file on disk + every file in index, with status.

    Per file status:
      - "indexed"   → on disk AND in index
      - "disk_only" → on disk but NOT indexed
      - "orphan"    → in index but the source file is gone

    Result is grouped by folder for the UI tree. Each folder lists the
    union of disk-files and index-only entries, plus aggregate counts.
    """
    disk = _scan_disk_files()
    index = _scan_index_files()

    all_filenames = set(disk.keys()) | set(index.keys())
    files: list[dict[str, Any]] = []
    for fname in sorted(all_filenames):
        on_disk = fname in disk
        in_index = fname in index
        if on_disk and in_index:
            status = "indexed"
        elif on_disk:
            status = "disk_only"
        else:
            status = "orphan"
        # Folder: prefer index value (authoritative), fall back to disk path
        folder = ""
        if in_index:
            folder = index[fname]["folder"]
        elif on_disk:
            folder = disk[fname]["folder"]
        files.append({
            "filename": fname,
            "folder": folder,
            "status": status,
            "indexed_chunks": index[fname]["indexed_chunks"] if in_index else 0,
            "size_bytes": disk[fname]["size_bytes"] if on_disk else 0,
        })

    # Group by folder for the tree view
    by_folder: dict[str, dict[str, Any]] = {}
    for f in files:
        slot = by_folder.setdefault(f["folder"], {
            "folder": f["folder"],
            "indexed_chunks": 0,
            "files": [],
            "counts": {"indexed": 0, "disk_only": 0, "orphan": 0},
        })
        slot["indexed_chunks"] += f["indexed_chunks"]
        slot["counts"][f["status"]] += 1
        slot["files"].append(f)

    folders = sorted(by_folder.values(), key=lambda x: x["folder"])
    totals = {
        "files": len(files),
        "indexed": sum(1 for f in files if f["status"] == "indexed"),
        "disk_only": sum(1 for f in files if f["status"] == "disk_only"),
        "orphan": sum(1 for f in files if f["status"] == "orphan"),
        "indexed_chunks": sum(f["indexed_chunks"] for f in files),
    }
    return {"totals": totals, "folders": folders}


@app.post("/api/folders/{folder:path}/index-new")
def folder_index_new(folder: str) -> dict[str, Any]:
    """Index every disk_only file in this exact folder (no sub-folders).

    Skips files that are already in the index. Use ``/api/reindex-folder``
    instead when you want a delete-before-upsert refresh of all files.
    """
    s = _store()
    target_folder = unquote(folder).strip("/")
    target_path = (DOCUMENTS_DIR / target_folder).resolve()
    base = DOCUMENTS_DIR.resolve()
    if not str(target_path).startswith(str(base)):
        raise HTTPException(400, "Path traversal not allowed")
    if not target_path.exists() or not target_path.is_dir():
        raise HTTPException(404, f"Folder not found: {target_folder}")

    indexed_filenames = set(_scan_index_files().keys())
    candidates = [
        p for p in target_path.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    indexed = 0
    chunks_total = 0
    failures: list[dict[str, str]] = []
    for path in candidates:
        rel = path.relative_to(DOCUMENTS_DIR).as_posix()
        if rel in indexed_filenames:
            continue
        try:
            chunks = asyncio.run(s.index_document(path, rel))
            chunks_total += chunks
            indexed += 1
        except Exception as exc:
            failures.append({"filename": rel, "error": str(exc)})
    return {
        "folder": target_folder,
        "files_indexed": indexed,
        "chunks_total": chunks_total,
        "failures": failures,
    }


@app.post("/api/folders/{folder:path}/cleanup-orphans")
def folder_cleanup_orphans(folder: str) -> dict[str, Any]:
    """Remove index entries in this folder whose source file is gone.

    Folder match is exact (no sub-folders), like delete_folder.
    """
    s = _store()
    target_folder = unquote(folder).strip("/")
    disk = _scan_disk_files()
    on_disk_in_folder = {
        fname for fname, info in disk.items() if info["folder"] == target_folder
    }
    data = s.collection.get(
        where={"folder": target_folder},
        include=["metadatas"],
    )
    if not data.get("ids"):
        return {"folder": target_folder, "orphans_removed": 0, "files": []}

    orphan_ids: list[str] = []
    orphan_files: set[str] = set()
    for cid, meta in zip(data["ids"], data.get("metadatas") or []):
        if not isinstance(meta, dict):
            continue
        fname = str(meta.get("filename", ""))
        if fname and fname not in on_disk_in_folder:
            orphan_ids.append(cid)
            orphan_files.add(fname)

    if orphan_ids:
        s.collection.delete(ids=orphan_ids)
        s._invalidate_folder_cache()

    return {
        "folder": target_folder,
        "orphans_removed": len(orphan_ids),
        "files": sorted(orphan_files),
    }


# ─────────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: str = Field(default="semantic", pattern=r"^(semantic|literal|phrase)$")
    folder: Optional[str] = None
    # n_results = Anzeige-Limit (semantic) bzw. Hard-Cap (literal/phrase). Bei
    # phrase-mode iteriert der Server ueber alle Chunks und liefert alle
    # Phrase-Treffer (bis zu n_results, default hoch genug fuer Volltextsuche).
    n_results: int = Field(default=10, ge=1, le=2000)
    neighbor: int = Field(default=1, ge=0, le=5)


def _build_phrase_regex(query: str) -> Optional[re.Pattern[str]]:
    """Build a case-insensitive phrase regex with German-stem tolerance.

    Mirror of the frontend stem heuristic so frontend Highlight and backend
    filter agree on what "matches the phrase". Per word ≥6 chars: strip the
    last two characters as a heuristic stem, append ``\\w*``. Words ≥2 chars
    are joined by ``\\s+``. Returns None if the query is empty after filtering.

    Example: "heiliger geist" → /heilig\\w*\\s+geist\\w*/i — matches
    "Heiliger Geist", "heiligen Geistes", "heilige Geist", etc.
    """
    words = [w for w in query.strip().split() if len(w) >= 2]
    if not words:
        return None
    parts = []
    for w in words:
        stem = w[:-2] if len(w) >= 6 else w
        parts.append(re.escape(stem) + r"\w*")
    return re.compile(r"\s+".join(parts), re.IGNORECASE)


@app.post("/api/search")
def search(req: SearchRequest) -> dict[str, Any]:
    s = _store()

    if req.mode == "semantic":
        hits, _has_more = asyncio.run(s.search(
            query=req.query,
            n_results=req.n_results,
            folder=req.folder,
            neighbor_window=req.neighbor,
        ))
        return {"mode": "semantic", "query": req.query, "folder": req.folder,
                "total": len(hits), "results": hits}

    if req.mode == "phrase":
        # Volltext-Phrase-Suche mit Stem-Toleranz ueber das ganze (gefilterte)
        # Korpus. Kein Embedding-Cap — wenn der User eine Phrase sucht, will
        # er ALLE Vorkommen, nicht nur die top-N nach Distance.
        regex = _build_phrase_regex(req.query)
        if regex is None:
            return {"mode": "phrase", "query": req.query, "folder": req.folder,
                    "total": 0, "results": []}

        phrase_where: dict[str, Any] | None = None
        if req.folder is not None:
            matching = s._expand_folder_prefix(req.folder)
            if matching:
                phrase_where = (
                    {"folder": matching[0]} if len(matching) == 1
                    else {"folder": {"$in": matching}}
                )
            else:
                phrase_where = {"folder": req.folder}

        hits = []
        page_size = 5000
        offset = 0
        while True:
            phrase_kwargs: dict[str, Any] = {
                "include": ["documents", "metadatas"],
                "limit": page_size, "offset": offset,
            }
            if phrase_where is not None:
                phrase_kwargs["where"] = phrase_where
            data = s.collection.get(**phrase_kwargs)
            docs = data.get("documents") or []
            metas = data.get("metadatas") or []
            if not docs:
                break
            for d, m in zip(docs, metas):
                if not isinstance(d, str):
                    continue
                if not regex.search(d):
                    continue
                meta = m or {}
                hits.append({
                    "filename": str(meta.get("filename", "")),
                    "folder": str(meta.get("folder", "")),
                    "chunk_index": int(meta.get("chunk_index", 0)),
                    "total_chunks": int(meta.get("total_chunks", 0)),
                    "content": d,
                    "_neighbor": False,
                    "distance": None,
                })
                if len(hits) >= req.n_results:
                    return {"mode": "phrase", "query": req.query, "folder": req.folder,
                            "total": len(hits), "results": hits}
            if len(docs) < page_size:
                break
            offset += page_size
        hits.sort(key=lambda h: (h["filename"], h["chunk_index"]))
        return {"mode": "phrase", "query": req.query, "folder": req.folder,
                "total": len(hits), "results": hits}

    # literal — paginated string match, whitespace-normalised
    needle_norm = " ".join(req.query.lower().split())
    where: dict[str, Any] | None = None
    if req.folder is not None:
        matching = s._expand_folder_prefix(req.folder)
        if matching:
            where = (
                {"folder": matching[0]} if len(matching) == 1
                else {"folder": {"$in": matching}}
            )
        else:
            where = {"folder": req.folder}

    hits = []
    page = 5000
    offset = 0
    while True:
        kwargs: dict[str, Any] = {
            "include": ["documents", "metadatas"],
            "limit": page, "offset": offset,
        }
        if where is not None:
            kwargs["where"] = where
        data = s.collection.get(**kwargs)
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        if not docs:
            break
        for d, m in zip(docs, metas):
            if not isinstance(d, str):
                continue
            if needle_norm not in " ".join(d.lower().split()):
                continue
            meta = m or {}
            hits.append({
                "filename": str(meta.get("filename", "")),
                "folder": str(meta.get("folder", "")),
                "chunk_index": int(meta.get("chunk_index", 0)),
                "total_chunks": int(meta.get("total_chunks", 0)),
                "content": d,
                "_neighbor": False,
                "distance": None,
            })
            if len(hits) >= req.n_results:
                return {"mode": "literal", "query": req.query, "folder": req.folder,
                        "total": len(hits), "results": hits}
        if len(docs) < page:
            break
        offset += page
    return {"mode": "literal", "query": req.query, "folder": req.folder,
            "total": len(hits), "results": hits}


# ═════════════════════════════════════════════════════════════════════
# Generic Collection Explorer — funktioniert über alle ChromaDB-Collections
# ═════════════════════════════════════════════════════════════════════
#
# Die Endpoints oben sind speziell auf ``aifred_documents`` zugeschnitten
# (Folder-Tree, Reindex-from-Disk, etc.). Für ``research_cache`` und
# ``agent_memory_*`` haben wir ein anderes Schema und brauchen einen
# generischen Browser. Die Endpoints hier sind additiv — sie ersetzen
# nichts oben, das alte UI funktioniert weiter.

# Bekannte Schemas — bestimmt, wie die UI Felder rendert.
_SCHEMA_AIFRED = "aifred_documents"
_SCHEMA_RESEARCH = "research_cache"
_SCHEMA_MEMORY = "agent_memory"
_SCHEMA_GENERIC = "generic"


def _detect_schema(collection_name: str, sample_meta: dict[str, Any] | None) -> str:
    """Schema-Inferenz aus Collection-Name + Sample-Metadaten."""
    if collection_name == "aifred_documents":
        return _SCHEMA_AIFRED
    if collection_name == "research_cache":
        return _SCHEMA_RESEARCH
    if collection_name.startswith("agent_memory_"):
        return _SCHEMA_MEMORY
    # Heuristik via Metadata-Keys, falls Custom-Collection
    if sample_meta:
        keys = set(sample_meta.keys())
        if {"filename", "folder", "chunk_index"} <= keys:
            return _SCHEMA_AIFRED
        if {"query", "answer", "volatility"} <= keys or "sources_json" in keys:
            return _SCHEMA_RESEARCH
        if {"agent_id", "type", "summary"} <= keys:
            return _SCHEMA_MEMORY
    return _SCHEMA_GENERIC


def _chroma_client():
    """Direkter ChromaDB HttpClient — bypassed DocumentStore-Singleton."""
    import chromadb
    from chromadb.config import Settings
    return chromadb.HttpClient(
        host="localhost", port=8000,
        settings=Settings(anonymized_telemetry=False),
    )


def _get_collection(name: str):
    """Lookup einer Collection by name. 404 wenn nicht da."""
    from chromadb.errors import NotFoundError
    try:
        # Embedding-Function = None bedeutet: keine Auto-Embeddings beim
        # add/upsert. Search-Endpoints unten reichen Embeddings explizit
        # via query_embeddings rein.
        return _chroma_client().get_collection(name=name)
    except NotFoundError:
        raise HTTPException(404, f"Collection {name!r} not found")


@app.get("/api/collections")
def list_collections() -> dict[str, Any]:
    """Liste aller ChromaDB-Collections mit Count + Schema-Hint."""
    client = _chroma_client()
    out: list[dict[str, Any]] = []
    for col in client.list_collections():
        try:
            count = col.count()
            sample = col.get(limit=1, include=["metadatas"])
            metas = sample.get("metadatas") or []
            sample_meta = metas[0] if metas and isinstance(metas[0], dict) else None
            schema = _detect_schema(col.name, sample_meta)
        except Exception:
            count = -1
            schema = _SCHEMA_GENERIC
            sample_meta = None
        out.append({
            "name": col.name,
            "count": count,
            "schema": schema,
            "metadata_keys": sorted(sample_meta.keys()) if sample_meta else [],
        })
    return {"collections": sorted(out, key=lambda c: c["name"])}


@app.get("/api/collections/{name}/items")
def collection_items(
    name: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Items einer Collection paginiert. Liefert Document + Metadata + ID."""
    col = _get_collection(name)
    total = col.count()
    data = col.get(
        include=["documents", "metadatas"],
        limit=limit, offset=offset,
    )
    ids = data.get("ids") or []
    docs = data.get("documents") or []
    metas = data.get("metadatas") or []
    sample_meta = metas[0] if metas and isinstance(metas[0], dict) else None
    schema = _detect_schema(name, sample_meta)

    items = []
    for i, item_id in enumerate(ids):
        items.append({
            "id": item_id,
            "document": docs[i] if i < len(docs) and isinstance(docs[i], str) else "",
            "metadata": metas[i] if i < len(metas) and isinstance(metas[i], dict) else {},
        })
    return {
        "collection": name,
        "schema": schema,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }


@app.get("/api/collections/{name}/items/{item_id}")
def collection_item(name: str, item_id: str) -> dict[str, Any]:
    """Einzel-Item by ID."""
    col = _get_collection(name)
    data = col.get(ids=[item_id], include=["documents", "metadatas"])
    ids = data.get("ids") or []
    if not ids:
        raise HTTPException(404, f"Item {item_id!r} not found in {name}")
    docs = data.get("documents") or []
    metas = data.get("metadatas") or []
    return {
        "id": ids[0],
        "document": docs[0] if docs and isinstance(docs[0], str) else "",
        "metadata": metas[0] if metas and isinstance(metas[0], dict) else {},
    }


@app.delete("/api/collections/{name}/items/{item_id}")
def delete_collection_item(name: str, item_id: str) -> dict[str, Any]:
    """Einzel-Item by ID löschen — DB-only, kein File-System-Touch."""
    col = _get_collection(name)
    col.delete(ids=[item_id])
    return {"collection": name, "id": item_id, "deleted": True}


@app.post("/api/collections/{name}/clear")
def clear_collection(name: str) -> dict[str, Any]:
    """Wipe all items from a collection. DB-only, no file-system touch.

    Refused for ``aifred_documents`` — that's the user corpus and clearing
    it would silently destroy the indexed bibel/judaica/kommentare data.
    Use folder-/file-level deletes for the documents collection instead.
    """
    if name == "aifred_documents":
        raise HTTPException(
            400,
            "Refusing to clear aifred_documents (user corpus). "
            "Use folder/file deletion instead.",
        )
    col = _get_collection(name)
    data = col.get(include=[])
    ids = data.get("ids") or []
    if ids:
        col.delete(ids=ids)
    return {"collection": name, "items_removed": len(ids)}


class GenericSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: str = Field(default="semantic", pattern=r"^(semantic|literal)$")
    n_results: int = Field(default=10, ge=1, le=200)


@app.post("/api/collections/{name}/search")
def collection_search(name: str, req: GenericSearchRequest) -> dict[str, Any]:
    """Semantic/Literal-Search innerhalb einer Collection.

    Semantic: bge-m3-Embedding (gleicher Embedder wie Index → kompatibel
    über alle AIfred-Collections).
    Literal: paginiertes substring-Match auf documents.
    """
    col = _get_collection(name)

    if req.mode == "semantic":
        # Embedding über die DocumentStore-eigene Query-Function holen
        # (CPU-bge-m3, kein VRAM-Konflikt mit aktivem LLM).
        s = _store()
        embed = s._embed_query
        emb = embed([req.query])  # type: ignore[operator]
        hits_raw = col.query(
            query_embeddings=emb,
            n_results=req.n_results,
            include=["documents", "metadatas", "distances"],
        )
        ids = (hits_raw.get("ids") or [[]])[0]
        docs = (hits_raw.get("documents") or [[]])[0]
        metas = (hits_raw.get("metadatas") or [[]])[0]
        dists = (hits_raw.get("distances") or [[]])[0]
        results = [
            {
                "id": ids[i],
                "document": docs[i] if i < len(docs) and isinstance(docs[i], str) else "",
                "metadata": metas[i] if i < len(metas) and isinstance(metas[i], dict) else {},
                "distance": float(dists[i]) if i < len(dists) else None,
            }
            for i in range(len(ids))
        ]
        return {"mode": "semantic", "query": req.query, "collection": name,
                "total": len(results), "results": results}

    # literal
    needle_norm = " ".join(req.query.lower().split())
    hits: list[dict[str, Any]] = []
    page = 5000
    offset = 0
    while True:
        data = col.get(
            include=["documents", "metadatas"],
            limit=page, offset=offset,
        )
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        if not ids:
            break
        for i, doc_id in enumerate(ids):
            d = docs[i] if i < len(docs) else ""
            if not isinstance(d, str):
                continue
            if needle_norm not in " ".join(d.lower().split()):
                continue
            hits.append({
                "id": doc_id,
                "document": d,
                "metadata": metas[i] if i < len(metas) and isinstance(metas[i], dict) else {},
                "distance": None,
            })
            if len(hits) >= req.n_results:
                return {"mode": "literal", "query": req.query, "collection": name,
                        "total": len(hits), "results": hits}
        if len(ids) < page:
            break
        offset += page
    return {"mode": "literal", "query": req.query, "collection": name,
            "total": len(hits), "results": hits}


# ─────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────


def main() -> int:
    import uvicorn
    uvicorn.run(
        "scripts.corpus_search_server:app",
        host="127.0.0.1",
        port=8005,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
