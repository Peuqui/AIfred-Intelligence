"""Documents for the Vue app's documents page: the local folder (data/documents/, lib.file_manager) and
the further sources the enabled plugins offer (lib.document_sources, e.g. Nextcloud): browsing,
uploading, indexing (lib.document_indexing, a server job the page asks about) and downloading.

An upload into the local folder is indexed by itself; one into a source is not: a source's files sit
behind a higher tier than the search, so indexing one stays the user's decision per file or folder.

The document manager links to a source's file here (e.g. Google Drive); it streams through as it
comes. Login cookie required like every API path (app middleware).
"""
import asyncio
from datetime import datetime
from typing import Literal
from urllib.parse import quote

from fastapi import Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import document_indexing
from .. import file_manager as fm
from ..document_sources import DocumentSource, DocumentSourceError, get_document_source
from ..formatting import format_stamp
from ..logging_utils import log_exception, log_message
from ..prompt_loader import get_language
from ..upload_files import DOCUMENTS_URL
from ..user_error import Invalid, Missing, Unavailable
from .app import api_app


@api_app.get("/documents/source/{source_key}/file/{file_id:path}", tags=["Documents"])
async def document_source_file(source_key: str, file_id: str) -> StreamingResponse:
    """Download one file of a document source (404 unknown source, 502 source failed). The id may hold
    slashes: Nextcloud's is the file's path."""
    download = await get_document_source(source_key).download(file_id)
    log_message(f"📄 Document download from {source_key}: {download.name}")
    # RFC 5987: names with umlauts arrive intact.
    disposition = f"attachment; filename*=UTF-8''{quote(download.name)}"
    return StreamingResponse(
        download.chunks, media_type=download.mime, headers={"Content-Disposition": disposition},
    )


# ── The local folder ─────────────────────────────────────────────────────


class DocumentRow(BaseModel):
    name: str
    type: Literal["file", "folder"]
    size: str
    created: str
    modified: str
    indexed: bool
    chunks: int = Field(description="Sections in the search index")
    indexable: bool = Field(description="A file the search index has a parser for (false for a folder)")
    file_count: int = Field(description="Files below a folder (0 for a file)")


class DocumentFolder(BaseModel):
    folder: str = Field(description='Relative to the documents folder, "" its root')
    items: list[DocumentRow]
    download_prefix: str = Field(description="A file's URL: this prefix and the file's name")


class DocumentNewFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent: str = ""
    name: str


class DocumentRenaming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str = ""
    old: str
    new: str


class DocumentDeletion(BaseModel):
    """Files and folders of one folder, from the disk and/or from the search index."""

    model_config = ConfigDict(extra="forbid")
    folder: str = ""
    files: list[str] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=list, description="Deleted with everything below")
    from_disk: bool = True
    from_index: bool = True


class DocumentDeletionFailed(BaseModel):
    name: str
    reason: str


class DocumentsDeleted(BaseModel):
    deleted: int
    chunks_removed: int
    failed: list[DocumentDeletionFailed]
    listing: DocumentFolder


class DocumentPreview(BaseModel):
    name: str
    text: str


def _done(result: fm.FileOpResult) -> fm.FileOpResult:
    """A failed operation answers with its refusal (a path outside the folder, a name, not there, no index)."""
    if not result.success:
        assert result.problem is not None, result.detail   # every refusal of these operations names its reason
        raise result.problem
    return result


async def _listing(folder: str) -> DocumentFolder:
    result = _done(await asyncio.to_thread(fm.list_directory, folder))
    return DocumentFolder(
        folder=folder,
        items=[DocumentRow(**item) for item in result.metadata["items"]],
        download_prefix=f"{DOCUMENTS_URL}/{folder}/" if folder else f"{DOCUMENTS_URL}/",
    )


@api_app.get("/documents/local", response_model=DocumentFolder, tags=["Documents"])
async def documents_local(folder: str = "") -> DocumentFolder:
    """Files and subfolders of ``folder`` with their place in the search index."""
    return await _listing(folder)


@api_app.post("/documents/local/folders", response_model=DocumentFolder, tags=["Documents"])
async def documents_new_folder(new: DocumentNewFolder) -> DocumentFolder:
    """A new subfolder of ``parent``; answers with the parent's listing."""
    _done(await asyncio.to_thread(fm.create_folder, new.parent, new.name))
    return await _listing(new.parent)


@api_app.post("/documents/local/rename", response_model=DocumentFolder, tags=["Documents"])
async def documents_rename(renaming: DocumentRenaming) -> DocumentFolder:
    """A file or folder renamed; the search index follows (what is indexed stays findable)."""
    _done(await asyncio.to_thread(fm.rename, renaming.folder, renaming.old, renaming.new, sync_index=True))
    return await _listing(renaming.folder)


@api_app.post("/documents/local/delete", response_model=DocumentsDeleted, tags=["Documents"])
async def documents_delete(deletion: DocumentDeletion) -> DocumentsDeleted:
    """Every named file and folder of ``folder``, one by one: one that fails is reported and the rest
    still goes. Answers with the folder's listing."""
    failed: list[DocumentDeletionFailed] = []
    chunks = 0
    for name, is_folder in [(name, False) for name in deletion.files] + [(name, True) for name in deletion.folders]:
        if is_folder:
            result = await fm.delete_folder(
                deletion.folder, name, recursive=True, from_disk=deletion.from_disk, from_index=deletion.from_index,
            )
        else:
            result = await fm.delete_file(deletion.folder, name, from_disk=deletion.from_disk, from_index=deletion.from_index)
        chunks += int(result.metadata.get("chunks_removed", 0))
        if not result.success:
            failed.append(DocumentDeletionFailed(name=name, reason=result.said(get_language())))
    total = len(deletion.files) + len(deletion.folders)
    log_message(f"🗑️ Documents: {total - len(failed)}/{total} deleted in {deletion.folder or '/'} ({chunks} sections from the index)")
    return DocumentsDeleted(deleted=total - len(failed), chunks_removed=chunks, failed=failed, listing=await _listing(deletion.folder))


@api_app.get("/documents/local/preview", response_model=DocumentPreview, tags=["Documents"])
async def documents_preview(path: str) -> DocumentPreview:
    """The text of a document as its parser reads it (the one the search index uses)."""
    from ..document_store import PARSERS

    file, problem = fm.safe_resolve(path)
    if problem:
        raise problem
    assert file is not None
    if not file.is_file():
        raise Missing("file_not_found", path=path)
    parser = PARSERS.get(file.suffix.lower())
    if parser is None:
        raise Invalid("no_parser", ext=file.suffix.lower())
    try:
        text = await asyncio.to_thread(parser, file)
    except Exception as error:  # a damaged file of any kind: the page shows why it has no preview
        log_exception(f"Preview of {path} failed", error)
        raise Invalid("preview_failed", reason=str(error)) from error
    return DocumentPreview(name=file.name, text=text)


# ── Uploading and indexing ───────────────────────────────────────────────


class DocumentIndexFailure(BaseModel):
    path: str
    reason: str


class DocumentIndexProgress(BaseModel):
    """The indexing run of the server: one file after the other, the rest goes on past a failure."""

    running: bool
    current: str = Field(description="The file being indexed, relative to the documents folder")
    done: int = Field(description="Files of the run finished, indexed or failed")
    total: int = Field(description="Files of the run; grows when more are queued while it runs")
    indexed: int
    chunks: int = Field(description="Sections the run put into the search index")
    failed: list[DocumentIndexFailure]


class DocumentUploadRejected(BaseModel):
    name: str
    reason: str


class DocumentsUploaded(BaseModel):
    saved: list[str] = Field(description="Paths of the saved files; they are queued for indexing")
    rejected: list[DocumentUploadRejected]
    listing: DocumentFolder
    indexing: DocumentIndexProgress


class DocumentIndexing(BaseModel):
    """Files of one folder to index, and folders indexed with every readable file below them."""

    model_config = ConfigDict(extra="forbid")
    folder: str = ""
    files: list[str] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=list)


def _index_progress() -> DocumentIndexProgress:
    progress = document_indexing.progress()
    return DocumentIndexProgress(
        running=progress.running, current=progress.current, done=progress.done, total=progress.total,
        indexed=progress.indexed, chunks=progress.chunks,
        failed=[DocumentIndexFailure(path=failure.path, reason=failure.reason) for failure in progress.failed],
    )


@api_app.post("/documents/local/upload", response_model=DocumentsUploaded, tags=["Documents"])
async def documents_upload(files: list[UploadFile], folder: str = Form("")) -> DocumentsUploaded:
    """Files into ``folder`` (one there under the same name is replaced). Every saved file is queued
    for indexing, so the search finds it; one that is refused (type, name) does not stop the rest."""
    saved: list[str] = []
    rejected: list[DocumentUploadRejected] = []
    for file in files:
        name = file.filename or ""
        result = await asyncio.to_thread(fm.save_upload, folder, name, await file.read())
        if result.success:
            saved.append(result.metadata["rel_path"])
        else:
            rejected.append(DocumentUploadRejected(name=name, reason=result.said(get_language())))
    log_message(f"📄 Documents: {len(saved)} uploaded to {folder or '/'}, {len(rejected)} refused")
    if saved:
        document_indexing.queue_files([document_indexing.local_item(path) for path in saved])
    return DocumentsUploaded(saved=saved, rejected=rejected, listing=await _listing(folder), indexing=_index_progress())


@api_app.post("/documents/local/index", response_model=DocumentIndexProgress, tags=["Documents"])
async def documents_index(indexing: DocumentIndexing) -> DocumentIndexProgress:
    """Queue files and folders for indexing; one already indexed is indexed anew (its sections are
    replaced). Answers with the run's progress, ``GET /documents/index`` follows it."""
    paths = [f"{indexing.folder}/{name}".lstrip("/") for name in indexing.files]
    for name in indexing.folders:
        below = _done(await asyncio.to_thread(fm.files_to_index, f"{indexing.folder}/{name}".lstrip("/")))
        paths.extend(below.metadata["files"])
    if paths:
        document_indexing.queue_files([document_indexing.local_item(path) for path in paths])
    return _index_progress()


@api_app.get("/documents/index", response_model=DocumentIndexProgress, tags=["Documents"])
async def documents_index_progress() -> DocumentIndexProgress:
    """How far the indexing run is (or how the last one ended)."""
    return _index_progress()


# ── Index care ───────────────────────────────────────────────────────────


class IndexOrphan(BaseModel):
    filename: str
    chunks: int
    indexed: str = Field(description="When it was indexed, as the lists show a point in time")


class IndexOverview(BaseModel):
    """The search index of the documents: how much is in it, and what of it has lost its file."""

    documents: int
    chunks: int
    orphans: list[IndexOrphan] = Field(description="Indexed files of the local folder that are gone from the disk")


class OrphansRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filenames: list[str]


class IndexCleared(BaseModel):
    chunks_removed: int


def _index_overview() -> IndexOverview:
    listed = _done(fm.list_indexed()).metadata["documents"]
    orphans = _done(fm.list_orphaned()).metadata["orphans"]
    return IndexOverview(
        documents=len(listed), chunks=sum(int(entry["total_chunks"]) for entry in listed),
        orphans=[IndexOrphan(filename=entry["filename"], chunks=int(entry["total_chunks"]),
                             indexed=format_stamp(datetime.fromisoformat(entry["upload_date"]).timestamp()))
                 for entry in orphans],
    )


@api_app.get("/documents/index/overview", response_model=IndexOverview, tags=["Documents"])
async def documents_index_overview() -> IndexOverview:
    """Documents and sections in the search index, and the entries whose file is gone."""
    return await asyncio.to_thread(_index_overview)


@api_app.post("/documents/index/orphans/remove", response_model=IndexOverview, tags=["Documents"])
async def documents_index_orphans_remove(removal: OrphansRemoval) -> IndexOverview:
    """Take orphaned entries out of the index. Only entries that are orphans right now: a file still on
    disk is taken out of the index through the delete of the folder listing, not here."""
    orphans = {entry["filename"] for entry in _done(await asyncio.to_thread(fm.list_orphaned)).metadata["orphans"]}
    for filename in removal.filenames:
        if filename in orphans:
            await fm.deindex_file(filename)
    log_message(f"🧹 Index: {len(set(removal.filenames) & orphans)} orphaned entries removed")
    return await asyncio.to_thread(_index_overview)


@api_app.post("/documents/index/clear", response_model=IndexCleared, tags=["Documents"])
async def documents_index_clear() -> IndexCleared:
    """Take every section of every document out of the search index; the files stay where they are."""
    from ..document_store import get_document_store

    store = get_document_store()
    if store is None:
        raise Unavailable("document_store_unavailable")
    removed = await asyncio.to_thread(store.clear)
    log_message(f"🧹 Index cleared: {removed} sections")
    return IndexCleared(chunks_removed=removed)


# ── Further sources ──────────────────────────────────────────────────────


class DocumentSourceInfo(BaseModel):
    key: str = Field(description='"local" for the documents folder, else the source of a plugin')
    label: str
    writable: bool = Field(description="Takes uploads")


class SourceRow(BaseModel):
    id: str
    name: str
    type: Literal["file", "folder"]
    size: str
    modified: str
    indexable: bool = Field(description="A file the search index has a parser for (false for a folder)")
    indexed: bool
    chunks: int
    download_url: str


class SourceListing(BaseModel):
    source: str
    folder_id: str = Field(description='The listed folder, "" the source\'s root')
    query: str = Field(description="The search these are the hits of, empty for a folder listing")
    writable: bool
    items: list[SourceRow]


class SourceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str


class SourceIndexing(BaseModel):
    """Files of a source to index, and folders indexed with every readable file below them."""

    model_config = ConfigDict(extra="forbid")
    files: list[SourceFile] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=list, description="Folder ids")


class SourceDeindexing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_ids: list[str]


class SourceDeindexed(BaseModel):
    chunks_removed: int


class SourceUploaded(BaseModel):
    saved: list[str] = Field(description="Names of the files the source took")
    rejected: list[DocumentUploadRejected]


def _source(key: str) -> DocumentSource:
    """The source of an enabled plugin; 404 when none offers it (disabled or not connected)."""
    return get_document_source(key)


@api_app.get("/documents/sources", response_model=list[DocumentSourceInfo], tags=["Documents"])
async def documents_sources() -> list[DocumentSourceInfo]:
    """The local documents folder first, then the sources the enabled, connected plugins offer."""
    from ..document_sources import document_sources
    from ..i18n import t
    from ..prompt_loader import get_language

    lang = get_language()
    return [DocumentSourceInfo(key="local", label=t("doc_source_local", lang=lang), writable=True)] + [
        DocumentSourceInfo(key=key, label=source.label(lang), writable=source.writable)
        for key, source in document_sources().items()
    ]


@api_app.get("/documents/source/{source_key}", response_model=SourceListing, tags=["Documents"])
async def documents_source_listing(source_key: str, folder_id: str = "", query: str = "") -> SourceListing:
    """A folder of a source (``folder_id`` "" = its root), or the hits of a search over the whole source."""
    from pathlib import Path

    from ..document_sources import source_index_name
    from ..document_store import PARSERS

    source = _source(source_key)
    entries = await (source.search(query) if query else source.list_folder(folder_id))
    indexed = await asyncio.to_thread(fm.chunks_per_document)
    rows = []
    for entry in entries:
        chunks = indexed.get(source_index_name(source_key, entry.id), 0)
        rows.append(SourceRow(
            id=entry.id, name=entry.name, type="folder" if entry.is_folder else "file",
            size=fm.format_size(entry.size_bytes) if entry.size_bytes is not None else "",
            modified=format_stamp(datetime.fromisoformat(entry.modified).timestamp()) if entry.modified else "",
            indexable=not entry.is_folder and Path(entry.name).suffix.lower() in PARSERS,
            indexed=chunks > 0, chunks=chunks,
            download_url=f"/api/documents/source/{source_key}/file/{quote(entry.id, safe='/')}",
        ))
    return SourceListing(source=source_key, folder_id=folder_id, query=query, writable=source.writable, items=rows)


@api_app.post("/documents/source/{source_key}/upload", response_model=SourceUploaded, tags=["Documents"])
async def documents_source_upload(source_key: str, files: list[UploadFile], folder_id: str = Form("")) -> SourceUploaded:
    """Files into a folder of a writable source (422 for one that is not). Nothing is indexed: that is
    the user's decision per file. Only the types the search index reads are taken, as locally."""
    import mimetypes
    from pathlib import Path

    from ..config import DOCUMENT_ALLOWED_EXTENSIONS

    source = _source(source_key)
    if not source.writable:
        raise Invalid("source_takes_no_uploads", source=source_key)
    saved: list[str] = []
    rejected: list[DocumentUploadRejected] = []
    for file in files:
        name = Path(file.filename or "").name
        suffix = Path(name).suffix.lower()
        if suffix not in DOCUMENT_ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(DOCUMENT_ALLOWED_EXTENSIONS))
            refused = Invalid("type_not_allowed", name=name, type=suffix or "(none)", allowed=allowed)
            rejected.append(DocumentUploadRejected(name=name, reason=refused.message(get_language())))
            continue
        data = await file.read()
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        try:
            await source.upload(folder_id, name, data, mime)
        except DocumentSourceError as failed:
            rejected.append(DocumentUploadRejected(name=name, reason=failed.message(get_language())))
            continue
        saved.append(name)
    log_message(f"📄 Documents: {len(saved)} uploaded to {source_key}, {len(rejected)} refused")
    return SourceUploaded(saved=saved, rejected=rejected)


@api_app.post("/documents/source/{source_key}/index", response_model=DocumentIndexProgress, tags=["Documents"])
async def documents_source_index(source_key: str, indexing: SourceIndexing) -> DocumentIndexProgress:
    """Queue files and folders of a source for indexing (folders with every file below them the index
    can read). The files stay at the source; the index keeps their text."""
    from pathlib import Path

    from ..document_sources import files_below
    from ..document_store import PARSERS

    items = [document_indexing.source_item(source_key, file.id, file.name) for file in indexing.files]
    for folder_id in indexing.folders:
        below = await files_below(_source(source_key), folder_id=folder_id)
        items.extend(
            document_indexing.source_item(source_key, entry.id, entry.name)
            for entry in below if Path(entry.name).suffix.lower() in PARSERS
        )
    if items:
        document_indexing.queue_files(items)
    return _index_progress()


@api_app.post("/documents/source/{source_key}/deindex", response_model=SourceDeindexed, tags=["Documents"])
async def documents_source_deindex(source_key: str, deindexing: SourceDeindexing) -> SourceDeindexed:
    """Files of a source out of the search index; they stay at the source."""
    from ..document_sources import source_index_name

    _source(source_key)
    removed = 0
    for file_id in deindexing.file_ids:
        result = await fm.deindex_file(source_index_name(source_key, file_id))
        removed += int(result.metadata.get("chunks_removed", 0))
    log_message(f"📤 Documents: {len(deindexing.file_ids)} files of {source_key} out of the index ({removed} sections)")
    return SourceDeindexed(chunks_removed=removed)
