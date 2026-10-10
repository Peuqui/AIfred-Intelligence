"""Document sources beyond the local folder (lib.document_sources): download.

The document manager links here for files of a further source (e.g. Google
Drive); the file streams through as it comes, never through Reflex state.
Login cookie required like every API path (app middleware).
"""
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from ..document_sources import DocumentSourceError, get_document_source
from ..logging_utils import log_message
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
