"""Nextcloud files as a document source for the document manager (live, no mirror).

Satisfies ``lib.document_sources.DocumentSource``: browse folders, search,
download as a stream, upload. Separate from the file tools, which serve the
model (text only, only the agent folder): here the user sees the whole account
of the plugin's user, and files go to and from the user as they are.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from ....lib.document_sources import DocumentSourceError, SourceDownload, SourceEntry
from ....lib.plugin_base import plugin_i18n_text
from .dav import DavClient, DavConflict, DavError, DavFile
from .errors import NextcloudRefused
from .files import clean_path

_SEARCH_LIMIT = 100
_DOWNLOAD_CHUNK = 256 * 1024
_NOT_FOUND = 404
_OCTET_STREAM = "application/octet-stream"


def _entry(item: DavFile) -> SourceEntry:
    return SourceEntry(id=item.path, name=item.name, is_folder=item.is_folder, size_bytes=item.size,
                       modified=item.modified, mime=item.mime)


def _source_error(exc: Exception, action: str) -> DocumentSourceError:
    """One readable message per failure; the cause stays chained for the log."""
    if isinstance(exc, DavError):
        reason = "not found" if exc.status == _NOT_FOUND else f"HTTP {exc.status}"
        return DocumentSourceError(f"Nextcloud: {action} failed ({reason})")
    return DocumentSourceError(f"Nextcloud: {action} failed — {exc}")


class NextcloudDocumentSource:
    key = "nextcloud_files"

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
            raise _source_error(exc, "listing the folder") from exc
        return [_entry(item) for item in sorted(entries, key=lambda e: (not e.is_folder, e.name.casefold()))]

    async def search(self, query: str) -> list[SourceEntry]:
        try:
            entries = await asyncio.to_thread(self._client.search_files, "", query.strip(), _SEARCH_LIMIT)
        except (DavError, httpx.HTTPError) as exc:
            raise _source_error(exc, "searching") from exc
        return [_entry(item) for item in entries]

    async def download(self, file_id: str) -> SourceDownload:
        name = file_id.rsplit("/", 1)[-1]
        try:
            client, response = await self._client.open_download(clean_path(file_id))
        except (DavError, NextcloudRefused, httpx.HTTPError) as exc:
            raise _source_error(exc, f"downloading {name}") from exc
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
        if not self.writable:
            raise DocumentSourceError("Nextcloud files are read-only in the plugin settings")
        try:
            path = clean_path(f"{folder_id}/{name}")
            etag = await asyncio.to_thread(self._client.write_file, path, data, mime, None)
        except DavConflict as exc:
            raise DocumentSourceError(f"Nextcloud: {name} exists already") from exc
        except (DavError, NextcloudRefused, httpx.HTTPError) as exc:
            raise _source_error(exc, f"uploading {name}") from exc
        return _entry(DavFile(path=path, is_folder=False, size=len(data), modified="", mime=mime, etag=etag))
