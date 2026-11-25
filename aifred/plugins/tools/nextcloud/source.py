"""Nextcloud files as a document source for the document manager (live, no mirror).

Satisfies ``lib.document_sources.EditableDocumentSource``: browse folders, search,
download as a stream, upload, and change what is there (new folder, rename, move, copy, delete). Separate from the file tools, which serve the
model (text only, only the agent folder): here the user sees the whole account
of the plugin's user, and files go to and from the user as they are.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx

from ....lib.document_sources import DocumentSourceError, SourceDownload, SourceEntry
from ....lib.plugin_base import plugin_i18n_text
from ....lib.user_error import Conflict, Refused
from . import DOCUMENT_SOURCE_KEY
from .dav import DavClient, DavConflict, DavError, DavFile
from .errors import NextcloudRefused
from .files import clean_path

_DOWNLOAD_CHUNK = 256 * 1024
_OCTET_STREAM = "application/octet-stream"


def _entry(item: DavFile) -> SourceEntry:
    return SourceEntry(id=item.path, name=item.name, is_folder=item.is_folder, size_bytes=item.size,
                       modified=item.modified, mime=item.mime)


SOURCE = "Nextcloud"


def _reason(exc: Exception) -> str:
    """One readable reason per failure; the cause stays chained for the log."""
    return f"HTTP {exc.status}" if isinstance(exc, DavError) else str(exc)


class NextcloudDocumentSource:
    key = DOCUMENT_SOURCE_KEY

    def __init__(self, client: DavClient, writable: bool) -> None:
        self._client = client
        # Follows the plugin's files level; Nextcloud's own rights come on top.
        self.writable = writable

    def label(self, lang: str) -> str:
        return plugin_i18n_text(Path(__file__).resolve().parent, "doc_source_nextcloud", lang)

    async def list_folder(self, folder_id: str) -> list[SourceEntry]:
        try:
            entries = await asyncio.to_thread(self._client.list_files, clean_path(folder_id))
        except (DavError, NextcloudRefused, httpx.HTTPError) as exc:
            raise DocumentSourceError("source_list_failed", source=SOURCE, reason=_reason(exc)) from exc
        return [_entry(item) for item in sorted(entries, key=lambda e: (not e.is_folder, e.name.casefold()))]

    async def search(self, query: str) -> list[SourceEntry]:
        try:
            entries = await asyncio.to_thread(self._client.search_files, "", query.strip())
        except (DavError, httpx.HTTPError) as exc:
            raise DocumentSourceError("source_search_failed", source=SOURCE, reason=_reason(exc)) from exc
        return [_entry(item) for item in entries]

    async def download(self, file_id: str) -> SourceDownload:
        name = file_id.rsplit("/", 1)[-1]
        try:
            client, response = await self._client.open_download(clean_path(file_id))
        except (DavError, NextcloudRefused, httpx.HTTPError) as exc:
            raise DocumentSourceError("source_download_failed", source=SOURCE, name=name, reason=_reason(exc)) from exc
        mime = response.headers.get("Content-Type", _OCTET_STREAM).split(";", 1)[0]
        return SourceDownload(name=name, mime=mime, chunks=self._chunks(client, response))

    @staticmethod
    async def _chunks(client: httpx.AsyncClient, response: httpx.Response) -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes(_DOWNLOAD_CHUNK):
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    async def upload(self, folder_id: str, name: str, data: bytes, mime: str) -> SourceEntry:
        self._writable()
        try:
            path = clean_path(f"{folder_id}/{name}")
            etag = await asyncio.to_thread(self._client.write_file, path, data, mime, None)
        except DavConflict as exc:
            raise Conflict("source_name_exists", source=SOURCE, name=name) from exc
        except (DavError, NextcloudRefused, httpx.HTTPError) as exc:
            raise DocumentSourceError("source_upload_failed", source=SOURCE, name=name, reason=_reason(exc)) from exc
        return _entry(DavFile(path=path, is_folder=False, size=len(data), modified="", mime=mime, etag=etag))

    def _writable(self) -> None:
        """A name in use is a conflict (409) and a read-only account a refusal (403), not a failure of Nextcloud (502)."""
        if not self.writable:
            raise Refused("source_read_only", source=SOURCE)

    async def _change(self, work: Callable[[], object], name: str, failed: Callable[[str], DocumentSourceError]) -> None:
        """One change of the account in a worker thread; an existing target is its own message, any other failure
        the caller's (``failed`` words it with the reason)."""
        try:
            await asyncio.to_thread(work)
        except DavConflict as exc:
            raise Conflict("source_name_exists", source=SOURCE, name=name) from exc
        except (DavError, NextcloudRefused, httpx.HTTPError) as exc:
            raise failed(_reason(exc)) from exc

    async def create_folder(self, folder_id: str, name: str) -> str:
        self._writable()
        path = clean_path(f"{folder_id}/{name}")
        await self._change(lambda: self._client.make_folder(path, exist_ok=False), name, lambda reason: DocumentSourceError(
            "source_create_folder_failed", source=SOURCE, name=name, reason=reason))
        return path

    async def rename(self, file_id: str, name: str) -> str:
        self._writable()
        source = clean_path(file_id)
        leaf = source.rsplit("/", 1)[-1]
        target = clean_path(f"{source.rpartition('/')[0]}/{name}")
        await self._change(lambda: self._client.move_file(source, target), name, lambda reason: DocumentSourceError(
            "source_rename_failed", source=SOURCE, name=leaf, reason=reason))
        return target

    async def move(self, file_id: str, target_folder_id: str) -> str:
        self._writable()
        source = clean_path(file_id)
        leaf = source.rsplit("/", 1)[-1]
        target = clean_path(f"{target_folder_id}/{leaf}")
        await self._change(lambda: self._client.move_file(source, target), leaf, lambda reason: DocumentSourceError(
            "source_move_failed", source=SOURCE, name=leaf, reason=reason))
        return target

    async def copy(self, file_id: str, target_folder_id: str) -> str:
        self._writable()
        source = clean_path(file_id)
        leaf = source.rsplit("/", 1)[-1]
        target = clean_path(f"{target_folder_id}/{leaf}")
        await self._change(lambda: self._client.copy_file(source, target), leaf, lambda reason: DocumentSourceError(
            "source_copy_failed", source=SOURCE, name=leaf, reason=reason))
        return target

    async def delete(self, file_id: str) -> None:
        """Nextcloud keeps it in the user's trash bin."""
        self._writable()
        source = clean_path(file_id)
        leaf = source.rsplit("/", 1)[-1]
        await self._change(lambda: self._client.delete_file(source), leaf, lambda reason: DocumentSourceError(
            "source_delete_failed", source=SOURCE, name=leaf, reason=reason))
