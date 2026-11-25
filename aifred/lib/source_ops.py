"""What the file requester's actions do in an area that is a document source (lib/file_areas.py ``source-<key>``,
Nextcloud, Google Drive …): AreaOps over lib/document_sources.py. The id of a source's item is its path in the area
(Nextcloud's id is the path; Drive's is an opaque id, which works the same). Reading, searching and uploading are
the base protocol; changing (folder, rename, move, copy, delete) only a source that is an ``EditableDocumentSource``.
Every change follows the search index: rename and move carry the indexed sections along, delete takes them away, copy
leaves them (the copy is a new file, indexed when the user asks).
"""

from __future__ import annotations

import asyncio
import mimetypes
from datetime import datetime
from typing import TYPE_CHECKING

from . import file_browser, file_manager
from .document_sources import (
    DocumentSource,
    EditableDocumentSource,
    SourceEntry,
    source_index_name,
)
from .file_browser import Entry
from .user_error import Refused

if TYPE_CHECKING:
    from fastapi.responses import Response

    from .api.file_preview import DocumentPreview


def source_actions(source: DocumentSource) -> frozenset[str]:
    """What the requester offers for a source, from what the source says it can: looking, downloading and searching
    always, an upload when it is writable, changes when it also is an EditableDocumentSource. Never links."""
    actions = {"preview", "download", "search"}
    if source.writable:
        actions.add("upload")
        if isinstance(source, EditableDocumentSource):
            actions |= {"newfolder", "rename", "delete", "copy", "move"}
    return frozenset(actions)


def _mtime(modified: str) -> int | None:
    return int(datetime.fromisoformat(modified).timestamp()) if modified else None


class SourceOps:
    def __init__(self, source: DocumentSource) -> None:
        self.source = source

    def _editable(self) -> EditableDocumentSource:
        """The source for a change; the area allows the change only for such a source, this is the type's proof."""
        assert isinstance(self.source, EditableDocumentSource)
        return self.source

    # ── reading ───────────────────────────────────────────────────

    async def _listed(self, found: list[SourceEntry]) -> list[Entry]:
        indexed = await asyncio.to_thread(file_manager.chunks_per_document)
        entries = []
        for item in found:
            state = file_manager.source_index_state(self.source.key, item, indexed)
            entries.append(Entry(
                name=item.name, rel_path=item.id, is_dir=item.is_folder, is_symlink=False, size=item.size_bytes,
                mtime=_mtime(item.modified), writable=self.source.writable,
                indexable=state.indexable, index_chunks=state.chunks,
            ))
        return entries

    async def entries(self, rel: str) -> tuple[list[Entry], bool]:
        return await self._listed(await self.source.list_folder(rel)), self.source.writable

    async def search(self, rel: str, text: str, *, deep: bool) -> list[Entry]:
        """The hits over the whole source: a source searches its whole store, whatever folder is open or ``deep`` says."""
        return await self._listed(await self.source.search(text))

    async def view(self, rel: str) -> Response:
        from .api.file_download import source_response

        return await source_response(await self.source.download(rel), inline=True)

    async def download(self, rel: str) -> Response:
        from .api.file_download import source_response

        return await source_response(await self.source.download(rel), inline=False)

    async def text(self, rel: str) -> DocumentPreview:
        from .api.file_preview import preview_of_download

        return await preview_of_download(await self.source.download(rel))

    # ── writing ───────────────────────────────────────────────────

    async def upload(self, rel: str, name: str, data: bytes) -> None:
        """Any file, as into a folder of the user's own (not only the types the search index reads: the store is the
        user's, not the index's); nothing is indexed, the user decides."""
        name = file_browser.check_name(name)
        await self.source.upload(rel, name, data, mimetypes.guess_type(name)[0] or "application/octet-stream")

    async def create_folder(self, rel: str, name: str) -> None:
        await self._editable().create_folder(rel, file_browser.check_name(name))

    async def rename(self, rel: str, name: str) -> None:
        new_id = await self._editable().rename(rel, file_browser.check_name(name))
        await self._index_follows(rel, new_id)

    async def transfer(self, sources: list[str], destination: str, *, keep: bool) -> None:
        editable = self._editable()
        for source_id in sources:
            if keep:
                await editable.copy(source_id, destination)
            else:
                await self._index_follows(source_id, await editable.move(source_id, destination))

    async def delete(self, rel: str) -> None:
        await self._editable().delete(rel)
        for name in await self._indexed_names(rel):
            await file_manager.deindex_file(name)

    async def symlink(self, rel: str, name: str, target_rel: str) -> None:
        raise Refused("file_action_not_allowed", action="symlink")   # never offered (source_actions); no links in a store

    # ── the search index ──────────────────────────────────────────

    async def _indexed_names(self, source_id: str) -> list[str]:
        """The index entries of the file ``source_id`` or, for a folder, of every file below it."""
        exact = source_index_name(self.source.key, source_id)
        below = source_index_name(self.source.key, f"{source_id}/")
        indexed = await asyncio.to_thread(file_manager.chunks_per_document)
        return [name for name in indexed if name == exact or name.startswith(below)]

    async def _index_follows(self, old_id: str, new_id: str) -> None:
        """What is indexed under the old id (the file, or the files of a folder) stays searchable under the new one."""
        names = await self._indexed_names(old_id)
        if not names:
            return
        from .document_store import get_document_store

        store = get_document_store()
        assert store is not None   # entries in the index were just listed from it
        old_name = source_index_name(self.source.key, old_id)
        new_name = source_index_name(self.source.key, new_id)
        for name in names:
            await asyncio.to_thread(store.rename_document, name, new_name + name.removeprefix(old_name))
