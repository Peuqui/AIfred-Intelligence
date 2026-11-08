"""The file requester's server (VueFinder's RemoteDriver contract, OpenAPI of vuefinder 4.x): one base per area,
``/api/files/{area}``, the paths as ``<area>://<relative path>``. The area (lib/file_areas.py) gives root and
allowed actions; the work is lib/file_browser.py. ``/symlink`` is AIfred's own (VueFinder has none).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Form, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from ..user_error import Invalid
from .app import api_app

_SIZES = {"small": (0, 1 << 20), "medium": (1 << 20, 10 << 20), "large": (10 << 20, None)}   # VueFinder's search filter


class DirEntry(BaseModel):
    dir: str = Field(description="The folder it is in, as <area>://<path>")
    basename: str
    extension: str
    path: str
    storage: str
    type: str = Field(description="file or dir")
    file_size: int | None
    last_modified: int | None = Field(description="Unix seconds")
    mime_type: str | None
    read_only: bool
    visibility: str = "public"


class FsData(BaseModel):
    storages: list[str]
    dirname: str
    read_only: bool
    files: list[DirEntry]


class DeleteResult(FsData):
    deleted: list[DirEntry]


class InFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    name: str


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    item: str
    name: str


class Item(BaseModel):
    path: str
    type: str


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    items: list[Item]


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[str]
    destination: str
    path: str | None = None


class SymlinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="The folder the link goes into")
    name: str
    target: str = Field(description="The folder it points to, as <area>://<path>")


class SearchResult(BaseModel):
    storages: list[str]
    dirname: str
    files: list[DirEntry]


def _rel(area: str, path: str | None) -> str:
    """The path below the root of a VueFinder path; one of another storage leaves the area."""
    if not path:
        return ""
    prefix = f"{area}://"
    if not path.startswith(prefix):
        raise Invalid("leads_outside_sandbox", path=path)
    return path[len(prefix):].strip("/")


def _vf(area: str, rel: str) -> str:
    return f"{area}://{rel}"


def _dir_entry(area: str, entry: object) -> DirEntry:
    from ..file_browser import Entry, mime_type

    assert isinstance(entry, Entry)
    parent = entry.rel_path.rpartition("/")[0]
    return DirEntry(
        dir=_vf(area, parent), basename=entry.name, extension="" if entry.is_dir else Path(entry.name).suffix.lstrip("."),
        path=_vf(area, entry.rel_path), storage=area, type="dir" if entry.is_dir else "file", file_size=entry.size,
        last_modified=entry.mtime, mime_type=None if entry.is_dir else mime_type(Path(entry.name)),
        read_only=not entry.writable,
    )


def _listing(area: str, rel: str) -> FsData:
    from ..file_areas import file_area
    from ..file_browser import list_folder

    entries, writable = list_folder(file_area(area).root(), rel)
    return FsData(storages=[area], dirname=_vf(area, rel), read_only=not writable, files=[_dir_entry(area, e) for e in entries])


def _root(area: str, action: str | None = None) -> str:
    from ..file_areas import file_area

    chosen = file_area(area)
    if action:
        chosen.require(action)
    return chosen.root()


class FileAreaInfo(BaseModel):
    root: str = Field(description="The folder on the server the area's paths start at")
    actions: list[str] = Field(description="What the requester may do there (lib/file_areas.ACTIONS)")


@api_app.get("/files/{area}/info", response_model=FileAreaInfo, tags=["Files"])
async def files_info(area: str) -> FileAreaInfo:
    """Root and allowed actions of an area: the requester offers only these."""
    from ..file_areas import file_area

    chosen = file_area(area)
    return FileAreaInfo(root=chosen.root(), actions=sorted(chosen.actions))


@api_app.get("/files/{area}", response_model=FsData, tags=["Files"])
async def files_list(area: str, path: str = Query("")) -> FsData:
    """A folder of the area (its root without ``path``)."""
    return await asyncio.to_thread(_listing, area, _rel(area, path))


@api_app.post("/files/{area}/create-folder", response_model=FsData, tags=["Files"])
async def files_create_folder(area: str, request: InFolder) -> FsData:
    from ..file_browser import create_folder

    rel = _rel(area, request.path)
    await asyncio.to_thread(create_folder, _root(area, "newfolder"), rel, request.name)
    log_message(f"📁 Files {area}: folder {request.name!r} in {rel or '/'}")
    return await asyncio.to_thread(_listing, area, rel)


@api_app.post("/files/{area}/rename", response_model=FsData, tags=["Files"])
async def files_rename(area: str, request: RenameRequest) -> FsData:
    from ..file_browser import rename

    await asyncio.to_thread(rename, _root(area, "rename"), _rel(area, request.item), request.name)
    log_message(f"📁 Files {area}: {request.item!r} renamed to {request.name!r}")
    return await asyncio.to_thread(_listing, area, _rel(area, request.path))


@api_app.post("/files/{area}/delete", response_model=DeleteResult, tags=["Files"])
async def files_delete(area: str, request: DeleteRequest) -> DeleteResult:
    """Files, links (never their targets) and folders with their content."""
    from ..file_browser import delete, list_folder

    root = _root(area, "delete")
    rel = _rel(area, request.path)
    targets = {_rel(area, item.path) for item in request.items}
    before, _writable = await asyncio.to_thread(list_folder, root, rel)
    for target in targets:
        await asyncio.to_thread(delete, root, target)
    log_message(f"🗑️ Files {area}: deleted {sorted(targets)}")
    listing = await asyncio.to_thread(_listing, area, rel)
    deleted = [_dir_entry(area, entry) for entry in before if entry.rel_path in targets]
    return DeleteResult(**listing.model_dump(), deleted=deleted)


async def _transfer(area: str, request: TransferRequest, *, keep: bool) -> FsData:
    from ..file_browser import transfer

    root = _root(area, "copy" if keep else "move")
    sources = [_rel(area, source) for source in request.sources]
    destination = _rel(area, request.destination)
    await asyncio.to_thread(transfer, root, sources, destination, keep=keep)
    log_message(f"📁 Files {area}: {'copied' if keep else 'moved'} {sources} → {destination or '/'}")
    return await asyncio.to_thread(_listing, area, _rel(area, request.path) if request.path else destination)


@api_app.post("/files/{area}/copy", response_model=FsData, tags=["Files"])
async def files_copy(area: str, request: TransferRequest) -> FsData:
    return await _transfer(area, request, keep=True)


@api_app.post("/files/{area}/move", response_model=FsData, tags=["Files"])
async def files_move(area: str, request: TransferRequest) -> FsData:
    return await _transfer(area, request, keep=False)


@api_app.post("/files/{area}/upload", tags=["Files"])
async def files_upload(area: str, file: UploadFile, path: str = Form(""), name: str = Form("")) -> dict:
    """One file (Uppy sends each on its own); a name in use is refused, nothing is overwritten."""
    from ..file_browser import write_file

    rel = _rel(area, path)
    filename = name or file.filename or ""
    data = await file.read()
    await asyncio.to_thread(write_file, _root(area, "upload"), rel, filename, data)
    log_message(f"⬆️ Files {area}: {filename!r} into {rel or '/'} ({len(data)} bytes)")
    return {}


def _file(area: str, action: str, path: str) -> Path:
    from ..file_browser import resolve

    target = resolve(_root(area, action), _rel(area, path))
    if not target.is_file():
        raise Invalid("not_a_file", path=path)
    return target


@api_app.get("/files/{area}/preview", tags=["Files"])
async def files_preview(area: str, path: str = Query(...)) -> FileResponse:
    from ..file_browser import mime_type

    target = await asyncio.to_thread(_file, area, "preview", path)
    return FileResponse(target, media_type=mime_type(target) or "application/octet-stream", filename=target.name,
                        content_disposition_type="inline")


@api_app.get("/files/{area}/download", tags=["Files"])
async def files_download(area: str, path: str = Query(...)) -> FileResponse:
    target = await asyncio.to_thread(_file, area, "download", path)
    return FileResponse(target, filename=target.name)


@api_app.get("/files/{area}/search", response_model=SearchResult, tags=["Files"])
async def files_search(area: str, path: str = Query(""), filter: str = Query(""), deep: str = Query(""),
                       size: str = Query("all")) -> SearchResult:
    """Names containing ``filter``; ``deep`` also below the folder; ``size`` small/medium/large as VueFinder offers."""
    from ..file_browser import search

    if size != "all" and size not in _SIZES:
        raise Invalid("unknown_size_filter", size=size)
    rel = _rel(area, path)
    found = await asyncio.to_thread(search, _root(area, "search"), rel, filter, deep=bool(deep))
    if size != "all":
        low, high = _SIZES[size]
        found = [entry for entry in found if entry.size is not None and entry.size >= low and (high is None or entry.size < high)]
    return SearchResult(storages=[area], dirname=_vf(area, rel), files=[_dir_entry(area, entry) for entry in found])


@api_app.post("/files/{area}/symlink", response_model=FsData, tags=["Files"])
async def files_symlink(area: str, request: SymlinkRequest) -> FsData:
    """A link to a folder inside the area (VueFinder has none: the requester's own menu entry)."""
    from ..file_browser import create_symlink

    rel = _rel(area, request.path)
    await asyncio.to_thread(create_symlink, _root(area, "symlink"), rel, request.name, _rel(area, request.target))
    log_message(f"🔗 Files {area}: link {request.name!r} → {request.target!r} in {rel or '/'}")
    return await asyncio.to_thread(_listing, area, rel)
