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
# Search
# ─────────────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: str = Field(default="semantic", pattern=r"^(semantic|literal)$")
    folder: Optional[str] = None
    n_results: int = Field(default=10, ge=1, le=200)
    neighbor: int = Field(default=1, ge=0, le=5)


@app.post("/api/search")
def search(req: SearchRequest) -> dict[str, Any]:
    s = _store()

    if req.mode == "semantic":
        hits = asyncio.run(s.search(
            query=req.query,
            n_results=req.n_results,
            folder=req.folder,
            neighbor_window=req.neighbor,
        ))
        return {"mode": "semantic", "query": req.query, "folder": req.folder,
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

    hits: list[dict[str, Any]] = []
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
