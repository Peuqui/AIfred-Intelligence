"""Google Drive as a document source for the document manager (live, no mirror).

Satisfies ``lib.document_sources.DocumentSource``: browse folders, search,
download as a stream, upload. Separate from the Drive tools, which serve the
model (text only, capped): here files go to and from the user as they are.
Google's own formats (Docs, Sheets, Slides, Drawings) have no file to fetch
and are exported — to the Office formats, so they stay editable.
"""
from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

import httpx

from .....lib.document_sources import DocumentSourceError, SourceDownload, SourceEntry
from .._common import _get_token, _google_request
from .tools import DRIVE_API, UPLOAD_API, _escape_drive_term

FOLDER_MIME = "application/vnd.google-apps.folder"

# Google-native type → (export type, file extension) for downloads.
_EXPORT_FOR_DOWNLOAD: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}

_FIELDS = "id,name,mimeType,modifiedTime,size"
_PAGE_SIZE = 200
_SEARCH_LIMIT = 100
_DOWNLOAD_CHUNK = 256 * 1024
_REQUEST_TIMEOUT_S = 30.0


def _entry(item: dict) -> SourceEntry:
    return SourceEntry(
        id=item["id"],
        name=item["name"],
        is_folder=item["mimeType"] == FOLDER_MIME,
        size_bytes=int(item["size"]) if "size" in item else None,
        modified=item.get("modifiedTime", ""),
        mime=item["mimeType"],
    )


def _source_error(exc: Exception, action: str) -> DocumentSourceError:
    """One readable message per failure; the cause stays chained for the log."""
    if isinstance(exc, httpx.HTTPStatusError):
        return DocumentSourceError(f"Google Drive: {action} failed (HTTP {exc.response.status_code})")
    return DocumentSourceError(f"Google Drive: {action} failed — {exc}")


class DriveDocumentSource:
    key = "google_drive"

    def label(self, lang: str) -> str:
        from pathlib import Path

        from .....lib.plugin_base import plugin_i18n_text
        return plugin_i18n_text(Path(__file__).resolve().parent.parent, "doc_source_google_drive", lang)

    async def _files(self, query: str, limit: int | None, order_by: str) -> list[SourceEntry]:
        entries: list[SourceEntry] = []
        page_token = ""
        while True:
            params = {"q": query, "fields": f"nextPageToken,files({_FIELDS})", "pageSize": _PAGE_SIZE}
            if order_by:
                params["orderBy"] = order_by
            if page_token:
                params["pageToken"] = page_token
            data = (await _google_request("GET", f"{DRIVE_API}/files", params=params)).json()
            entries += [_entry(item) for item in data.get("files", [])]
            page_token = data.get("nextPageToken", "")
            if not page_token or (limit is not None and len(entries) >= limit):
                return entries[:limit] if limit is not None else entries

    async def list_folder(self, folder_id: str) -> list[SourceEntry]:
        parent = _escape_drive_term(folder_id or "root")
        try:
            return await self._files(f"'{parent}' in parents and trashed=false", None, "folder,name_natural")
        except Exception as exc:  # noqa: BLE001 — every failure reaches the user as one message
            raise _source_error(exc, "listing the folder") from exc

    async def search(self, query: str) -> list[SourceEntry]:
        term = _escape_drive_term(query.strip())
        try:
            # Drive refuses orderBy on full-text queries: hits come by relevance.
            return await self._files(
                f"(name contains '{term}' or fullText contains '{term}') and trashed=false", _SEARCH_LIMIT, "",
            )
        except Exception as exc:  # noqa: BLE001
            raise _source_error(exc, "searching") from exc

    async def download(self, file_id: str) -> SourceDownload:
        try:
            meta = (await _google_request(
                "GET", f"{DRIVE_API}/files/{file_id}", params={"fields": "id,name,mimeType"},
            )).json()
        except Exception as exc:  # noqa: BLE001
            raise _source_error(exc, "opening the file") from exc
        mime = meta["mimeType"]
        name = meta["name"]
        if mime == FOLDER_MIME:
            raise DocumentSourceError("Google Drive: a folder cannot be downloaded")
        if mime.startswith("application/vnd.google-apps."):
            if mime not in _EXPORT_FOR_DOWNLOAD:
                raise DocumentSourceError(f"Google Drive: {name} has no downloadable format ({mime})")
            mime, extension = _EXPORT_FOR_DOWNLOAD[mime]
            url, params = f"{DRIVE_API}/files/{file_id}/export", {"mimeType": mime}
            if not name.lower().endswith(extension):
                name += extension
        else:
            url, params = f"{DRIVE_API}/files/{file_id}", {"alt": "media"}
        # Opened here, so an error shows before the first byte goes out; the
        # token is fetched right before the request (long downloads).
        client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S)
        try:
            token = await _get_token()
            request = client.build_request("GET", url, params=params, headers={"Authorization": f"Bearer {token}"})
            response = await client.send(request, stream=True)
        except Exception as exc:  # noqa: BLE001
            await client.aclose()
            raise _source_error(exc, f"downloading {name}") from exc
        if response.is_error:
            await response.aclose()
            await client.aclose()
            raise DocumentSourceError(f"Google Drive: downloading {name} failed (HTTP {response.status_code})")
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
        boundary = f"aifred-{uuid.uuid4().hex}"
        metadata = {"name": name, "parents": [folder_id or "root"]}
        body = b"".join([
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
            json.dumps(metadata).encode(),
            f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode(),
            data,
            f"\r\n--{boundary}--".encode(),
        ])
        try:
            token = await _get_token()
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                response = await client.post(
                    f"{UPLOAD_API}/files", params={"uploadType": "multipart", "fields": _FIELDS},
                    headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={boundary}"},
                    content=body,
                )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise _source_error(exc, f"uploading {name}") from exc
        return _entry(response.json())
