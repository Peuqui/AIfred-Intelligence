"""Documents: the local folder (data/documents/, lib.file_manager) for the Vue app's documents page,
and the download of a file of a further source (lib.document_sources).

The document manager links to a source's file here (e.g. Google Drive); it streams through as it
comes, never through Reflex state. Login cookie required like every API path (app middleware).
"""
import asyncio
from typing import Literal
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import file_manager as fm
from ..document_sources import DocumentSourceError, get_document_source
from ..logging_utils import log_exception, log_message
from ..upload_files import DOCUMENTS_URL
from .app import api_app


@api_app.get("/documents/source/{source_key}/file/{file_id}", tags=["Documents"])
async def document_source_file(source_key: str, file_id: str) -> StreamingResponse:
    """Download one file of a document source (404 unknown source, 502 source failed)."""
    try:
        source = get_document_source(source_key)
    except LookupError as missing:
        raise HTTPException(status_code=404, detail=str(missing)) from missing
    try:
        download = await source.download(file_id)
    except DocumentSourceError as failed:
        raise HTTPException(status_code=502, detail=str(failed)) from failed
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
    """A failed operation answers 422 with its reason (a path outside the folder, a name, not there)."""
    if not result.success:
        raise HTTPException(status_code=422, detail=result.detail)
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
            failed.append(DocumentDeletionFailed(name=name, reason=result.detail))
    total = len(deletion.files) + len(deletion.folders)
    log_message(f"🗑️ Documents: {total - len(failed)}/{total} deleted in {deletion.folder or '/'} ({chunks} sections from the index)")
    return DocumentsDeleted(deleted=total - len(failed), chunks_removed=chunks, failed=failed, listing=await _listing(deletion.folder))


@api_app.get("/documents/local/preview", response_model=DocumentPreview, tags=["Documents"])
async def documents_preview(path: str) -> DocumentPreview:
    """The text of a document as its parser reads it (the one the search index uses)."""
    from ..document_store import PARSERS

    file, problem = fm.safe_resolve(path)
    if problem or file is None:
        raise HTTPException(status_code=422, detail=problem or "Invalid path")
    if not file.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    parser = PARSERS.get(file.suffix.lower())
    if parser is None:
        raise HTTPException(status_code=422, detail=f"No parser for {file.suffix.lower()}")
    try:
        text = await asyncio.to_thread(parser, file)
    except Exception as error:  # a damaged file of any kind: the page shows why it has no preview
        log_exception(f"Preview of {path} failed", error)
        raise HTTPException(status_code=422, detail=str(error)) from error
    return DocumentPreview(name=file.name, text=text)
