"""What the file requester's actions do in an area (lib/api/files.py): a plain folder (``FolderOps``, the work of
lib/file_browser.py) or the documents folder (``DocumentOps``), where every change goes through lib/file_manager.py
so the search index follows (rename and move carry the indexed sections along, delete takes them away, an upload is
queued for indexing). Reading — listing, search, a file for preview or download — is the same for both.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol

from . import file_browser
from .file_browser import Entry
from .user_error import Invalid

if TYPE_CHECKING:
    from fastapi.responses import Response

    from .api.file_preview import DocumentPreview


class AreaOps(Protocol):
    """What the requester's API (lib/api/files.py) asks of an area, wherever its files live: every method async, a
    path always relative to the area's root. The three reading methods answer as the API sends them."""

    async def entries(self, rel: str) -> tuple[list[Entry], bool]: ...
    async def search(self, rel: str, text: str, *, deep: bool) -> list[Entry]: ...
    async def create_folder(self, rel: str, name: str) -> None: ...
    async def rename(self, rel: str, name: str) -> None: ...
    async def delete(self, rel: str) -> None: ...
    async def transfer(self, sources: list[str], destination: str, *, keep: bool) -> None: ...
    async def upload(self, rel: str, name: str, data: bytes) -> None: ...
    async def symlink(self, rel: str, name: str, target_rel: str) -> None: ...
    async def view(self, rel: str) -> Response: ...
    async def download(self, rel: str) -> Response: ...
    async def text(self, rel: str) -> DocumentPreview: ...


class FolderOps:
    """An area that is a folder on this machine; the blocking work runs in a thread."""

    def __init__(self, root: str) -> None:
        self.root = root

    async def entries(self, rel: str) -> tuple[list[Entry], bool]:
        return await asyncio.to_thread(file_browser.list_folder, self.root, rel)

    async def search(self, rel: str, text: str, *, deep: bool) -> list[Entry]:
        return await asyncio.to_thread(file_browser.search, self.root, rel, text, deep=deep)

    def _file(self, rel: str) -> Path:
        target = file_browser.resolve(self.root, rel)
        if not target.is_file():
            raise Invalid("not_a_file", path=rel)
        return target

    async def view(self, rel: str) -> Response:
        from .api.file_download import view_response

        return await asyncio.to_thread(lambda: view_response(self._file(rel)))

    async def download(self, rel: str) -> Response:
        from .api.file_download import download_response

        return await asyncio.to_thread(lambda: download_response(self._file(rel)))

    async def text(self, rel: str) -> DocumentPreview:
        from .api.file_preview import preview_of_file

        return await preview_of_file(await asyncio.to_thread(self._file, rel))

    async def create_folder(self, rel: str, name: str) -> None:
        await asyncio.to_thread(file_browser.create_folder, self.root, rel, name)

    async def rename(self, rel: str, name: str) -> None:
        await asyncio.to_thread(file_browser.rename, self.root, rel, name)

    async def delete(self, rel: str) -> None:
        await asyncio.to_thread(file_browser.delete, self.root, rel)

    async def transfer(self, sources: list[str], destination: str, *, keep: bool) -> None:
        await asyncio.to_thread(file_browser.transfer, self.root, sources, destination, keep=keep)

    async def upload(self, rel: str, name: str, data: bytes) -> None:
        await asyncio.to_thread(file_browser.write_file, self.root, rel, name, data)

    async def symlink(self, rel: str, name: str, target_rel: str) -> None:
        await asyncio.to_thread(file_browser.create_symlink, self.root, rel, name, target_rel)


def _parts(rel: str) -> tuple[str, str]:
    path = PurePosixPath(rel)
    return ("" if str(path.parent) == "." else str(path.parent)), path.name


def _done(result: object) -> None:
    """A refused file_manager operation as its UserError (the page words it in its language)."""
    from .file_manager import FileOpResult
    from .user_error import NotDone

    assert isinstance(result, FileOpResult)
    if not result.success:
        raise result.problem or NotDone("file_operation_failed", work="documents", reason=result.detail)


class DocumentOps(FolderOps):
    """The documents folder (data/documents/): its rules are the documents page's (an upload replaces a file of the
    same name and is indexed; copying takes files only), file_manager keeps the index in step. Its entries carry
    the file's place in the search index, from the same numbers as the documents page (file_manager.index_state)."""

    def _with_index(self, entries: list[Entry]) -> list[Entry]:
        from . import file_manager

        indexed = file_manager.chunks_per_document()
        states = (file_manager.index_state(entry.rel_path, indexed) for entry in entries)
        return [
            entry if entry.is_dir else dataclasses.replace(entry, indexable=state.indexable, index_chunks=state.chunks)
            for entry, state in zip(entries, states, strict=True)
        ]

    async def entries(self, rel: str) -> tuple[list[Entry], bool]:
        entries, writable = await super().entries(rel)
        return await asyncio.to_thread(self._with_index, entries), writable

    async def search(self, rel: str, text: str, *, deep: bool) -> list[Entry]:
        return await asyncio.to_thread(self._with_index, await super().search(rel, text, deep=deep))

    async def create_folder(self, rel: str, name: str) -> None:
        from . import file_manager

        _done(await asyncio.to_thread(file_manager.create_folder, rel, name))

    async def rename(self, rel: str, name: str) -> None:
        from . import file_manager

        parent, old = _parts(rel)
        _done(await asyncio.to_thread(file_manager.rename, parent, old, name))

    async def delete(self, rel: str) -> None:
        from . import file_manager

        parent, name = _parts(rel)
        if await asyncio.to_thread(lambda: file_browser.resolve(self.root, rel).is_dir()):
            _done(await file_manager.delete_folder(parent, name, recursive=True))
        else:
            _done(await file_manager.delete_file(parent, name))

    async def transfer(self, sources: list[str], destination: str, *, keep: bool) -> None:
        from . import file_manager

        for source in sources:
            operation = file_manager.copy_file if keep else file_manager.move_file
            _done(await asyncio.to_thread(operation, source, destination))

    async def upload(self, rel: str, name: str, data: bytes) -> None:
        from . import document_indexing, file_manager

        result = await asyncio.to_thread(file_manager.save_upload, rel, name, data)
        _done(result)
        document_indexing.queue_files([document_indexing.local_item(result.metadata["rel_path"])])
