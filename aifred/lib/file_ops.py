"""What the file requester's actions do in an area (lib/api/files.py): a plain folder (``FolderOps``, the work of
lib/file_browser.py) or the documents folder (``DocumentOps``), where every change goes through lib/file_manager.py
so the search index follows (rename and move carry the indexed sections along, delete takes them away, an upload is
queued for indexing). Reading — listing, search, a file for preview or download — is the same for both.
"""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath

from . import file_browser
from .file_browser import Entry


class FolderOps:
    def __init__(self, root: str) -> None:
        self.root = root

    def entries(self, rel: str) -> tuple[list[Entry], bool]:
        return file_browser.list_folder(self.root, rel)

    def search(self, rel: str, text: str, *, deep: bool) -> list[Entry]:
        return file_browser.search(self.root, rel, text, deep=deep)

    def file(self, rel: str) -> Path:
        return file_browser.resolve(self.root, rel)

    def create_folder(self, rel: str, name: str) -> None:
        file_browser.create_folder(self.root, rel, name)

    def rename(self, rel: str, name: str) -> None:
        file_browser.rename(self.root, rel, name)

    async def delete(self, rel: str) -> None:
        await asyncio.to_thread(file_browser.delete, self.root, rel)

    def transfer(self, sources: list[str], destination: str, *, keep: bool) -> None:
        file_browser.transfer(self.root, sources, destination, keep=keep)

    async def upload(self, rel: str, name: str, data: bytes) -> None:
        await asyncio.to_thread(file_browser.write_file, self.root, rel, name, data)

    def symlink(self, rel: str, name: str, target_rel: str) -> None:
        file_browser.create_symlink(self.root, rel, name, target_rel)


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
    same name and is indexed; copying takes files only), file_manager keeps the index in step."""

    def create_folder(self, rel: str, name: str) -> None:
        from . import file_manager

        _done(file_manager.create_folder(rel, name))

    def rename(self, rel: str, name: str) -> None:
        from . import file_manager

        parent, old = _parts(rel)
        _done(file_manager.rename(parent, old, name))

    async def delete(self, rel: str) -> None:
        from . import file_manager

        parent, name = _parts(rel)
        if self.file(rel).is_dir():
            _done(await file_manager.delete_folder(parent, name, recursive=True))
        else:
            _done(await file_manager.delete_file(parent, name))

    def transfer(self, sources: list[str], destination: str, *, keep: bool) -> None:
        from . import file_manager

        for source in sources:
            _done(file_manager.copy_file(source, destination) if keep else file_manager.move_file(source, destination))

    async def upload(self, rel: str, name: str, data: bytes) -> None:
        from . import document_indexing, file_manager

        result = await asyncio.to_thread(file_manager.save_upload, rel, name, data)
        _done(result)
        document_indexing.queue_files([document_indexing.local_item(result.metadata["rel_path"])])
