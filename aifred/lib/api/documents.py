"""Documents for the Vue app's documents page: what the search index does with the files the file browser shows
(the local documents folder, data/documents/, and the further sources the enabled plugins offer, lib.document_sources,
e.g. Nextcloud). Browsing, uploading, previewing and downloading are the file API's (``/files/{area}``, api/files.py):
this module has the list of the sources, indexing (lib.document_indexing, a server job the page asks about),
taking files out of the index, and the care of the index.

Files indexed from a source stay at the source: they sit behind a higher tier than the search, so indexing one is the
user's decision per file or folder. Login cookie required like every API path (app middleware).
"""
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .. import document_indexing
from .. import file_manager as fm
from ..document_sources import DocumentSource, get_document_source
from ..formatting import format_stamp
from ..logging_utils import log_message
from ..user_error import Unavailable
from .app import api_app


def _done(result: fm.FileOpResult) -> fm.FileOpResult:
    """A failed operation answers with its refusal (a path outside the folder, a name, not there, no index)."""
    if not result.success:
        assert result.problem is not None, result.detail   # every refusal of these operations names its reason
        raise result.problem
    return result


# ── Indexing ─────────────────────────────────────────────────────────


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


class DocumentIndexing(BaseModel):
    """Files of one folder to index, and folders indexed with every readable file below them."""

    model_config = ConfigDict(extra="forbid")
    folder: str = ""
    files: list[str] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=list)


class LocalDeindexing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[str] = Field(description="Paths from the root of the documents folder")


class Deindexed(BaseModel):
    chunks_removed: int


def _index_progress() -> DocumentIndexProgress:
    progress = document_indexing.progress()
    return DocumentIndexProgress(
        running=progress.running, current=progress.current, done=progress.done, total=progress.total,
        indexed=progress.indexed, chunks=progress.chunks,
        failed=[DocumentIndexFailure(path=failure.path, reason=failure.reason) for failure in progress.failed],
    )


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


@api_app.post("/documents/local/deindex", response_model=Deindexed, tags=["Documents"])
async def documents_local_deindex(deindexing: LocalDeindexing) -> Deindexed:
    """Files of the documents folder out of the search index; they stay on the disk."""
    removed = 0
    for path in deindexing.files:
        result = _done(await fm.deindex_file(path))
        removed += int(result.metadata.get("chunks_removed", 0))
    log_message(f"📤 Documents: {len(deindexing.files)} local files out of the index ({removed} sections)")
    return Deindexed(chunks_removed=removed)


@api_app.get("/documents/index", response_model=DocumentIndexProgress, tags=["Documents"])
async def documents_index_progress() -> DocumentIndexProgress:
    """How far the indexing run is (or how the last one ended)."""
    return _index_progress()


# ── Index care ───────────────────────────────────────────────────────────


class IndexOrphan(BaseModel):
    filename: str
    chunks: int
    indexed: str = Field(description="When it was indexed, as the lists show a point in time")


class IndexSource(BaseModel):
    key: str
    plugin: str = Field(description="Name of the plugin the source belongs to, as the user reads it")
    plugin_on: bool
    documents: int
    chunks: int


class IndexOverview(BaseModel):
    """The search index of the documents: how much is in it, and what of it has lost its file."""

    documents: int
    chunks: int
    orphans: list[IndexOrphan] = Field(description="Indexed files of the local folder that are gone from the disk")
    sources: list[IndexSource] = Field(description="Document sources with entries in the index, plugin on or off")


class OrphansRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filenames: list[str]


class SourceRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str


class IndexCleared(BaseModel):
    chunks_removed: int


def _source_entries(listed: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """The index entries of one source: the index names them ``<key>:/<id>`` (``source_index_name``)."""
    return [entry for entry in listed if entry["filename"].startswith(f"{key}:/")]


def _index_sources(listed: list[dict[str, Any]]) -> list[IndexSource]:
    from ..plugin_registry import (
        document_source_owners,
        get_tool_plugin_on_or_off,
        is_plugin_enabled,
        plugin_label,
    )

    sources = []
    for key, owner in document_source_owners().items():
        entries = _source_entries(listed, key)
        plugin = get_tool_plugin_on_or_off(owner)
        assert plugin is not None   # the owner was just found as a plugin
        if entries:
            sources.append(IndexSource(
                key=key, plugin=plugin_label(plugin), plugin_on=is_plugin_enabled(owner),
                documents=len(entries), chunks=sum(int(entry["total_chunks"]) for entry in entries),
            ))
    return sources


def _index_overview() -> IndexOverview:
    listed = _done(fm.list_indexed()).metadata["documents"]
    orphans = _done(fm.list_orphaned()).metadata["orphans"]
    return IndexOverview(
        sources=_index_sources(listed),
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
    disk is taken out of the index through its area's deindex, not here."""
    orphans = {entry["filename"] for entry in _done(await asyncio.to_thread(fm.list_orphaned)).metadata["orphans"]}
    for filename in removal.filenames:
        if filename in orphans:
            await fm.deindex_file(filename)
    log_message(f"🧹 Index: {len(set(removal.filenames) & orphans)} orphaned entries removed")
    return await asyncio.to_thread(_index_overview)


@api_app.post("/documents/index/sources/remove", response_model=IndexOverview, tags=["Documents"])
async def documents_index_source_remove(removal: SourceRemoval) -> IndexOverview:
    """Take every entry of one document source out of the index, whether its plugin is on or off (the files stay
    at the source). 404 for a key no plugin has."""
    from ..document_sources import require_known_document_source

    require_known_document_source(removal.key)
    listed = _done(await asyncio.to_thread(fm.list_indexed)).metadata["documents"]
    entries = _source_entries(listed, removal.key)
    for entry in entries:
        await fm.deindex_file(entry["filename"])
    log_message(f"🧹 Index: {len(entries)} entries of {removal.key} removed")
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


def _source(key: str) -> DocumentSource:
    """The source of an enabled plugin; 409 when its plugin is off, 404 when none offers it (unknown or not connected)."""
    return get_document_source(key)


@api_app.get("/documents/sources", response_model=list[DocumentSourceInfo], tags=["Documents"])
async def documents_sources() -> list[DocumentSourceInfo]:
    """The local documents folder first, then the sources the enabled, connected plugins offer."""
    from ..document_sources import document_sources
    from ..i18n import t
    from ..prompt_loader import get_language

    lang = get_language()
    return [DocumentSourceInfo(key="local", label=t("doc_source_local", lang=lang))] + [
        DocumentSourceInfo(key=key, label=source.label(lang))
        for key, source in document_sources().items()
    ]


@api_app.post("/documents/source/{source_key}/index", response_model=DocumentIndexProgress, tags=["Documents"])
async def documents_source_index(source_key: str, indexing: SourceIndexing) -> DocumentIndexProgress:
    """Queue files and folders of a source for indexing (folders with every file below them the index
    can read). The files stay at the source; the index keeps their text."""
    from ..document_sources import files_below
    from ..document_store import PARSERS

    source = _source(source_key)   # 409/404 at once, not as a failure of the queued run
    items = [document_indexing.source_item(source_key, file.id, file.name) for file in indexing.files]
    for folder_id in indexing.folders:
        below = await files_below(source, folder_id=folder_id)
        items.extend(
            document_indexing.source_item(source_key, entry.id, entry.name)
            for entry in below if Path(entry.name).suffix.lower() in PARSERS
        )
    if items:
        document_indexing.queue_files(items)
    return _index_progress()


@api_app.post("/documents/source/{source_key}/deindex", response_model=Deindexed, tags=["Documents"])
async def documents_source_deindex(source_key: str, deindexing: SourceDeindexing) -> Deindexed:
    """Files of a source out of the search index; they stay at the source. Works with the plugin off too."""
    from ..document_sources import require_known_document_source, source_index_name

    require_known_document_source(source_key)   # the index entries are core: no source, no plugin needed
    removed = 0
    for file_id in deindexing.file_ids:
        result = await fm.deindex_file(source_index_name(source_key, file_id))
        removed += int(result.metadata.get("chunks_removed", 0))
    log_message(f"📤 Documents: {len(deindexing.file_ids)} files of {source_key} out of the index ({removed} sections)")
    return Deindexed(chunks_removed=removed)
