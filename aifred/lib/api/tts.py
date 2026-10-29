"""TTS escalation list for callers outside AIfred, no LLM.

Read only: it shows AIfred's list (order, place, status from the service's catalog). Starting
and stopping engines is not AIfred's business: the service does it itself, and by hand in the
service's own web interface. Token-guarded with ``Authorization: Bearer <TTS_CONTROL_API_TOKEN>``.
"""

import asyncio

from fastapi import Header, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import require_service_token
from ..tts_client import TTSSuiteUnreachable
from .app import api_app


class TTSEntry(BaseModel):
    """One entry of the escalation list, as the list editor shows it"""
    engine: str
    host: str | None = Field(description="Host name, None = this machine")
    label: str = Field(description="Engine · place, e.g. 'Qwen3-TTS · Aragon'")
    enabled: bool
    status: str = Field(description="Status key: running, sleeping, no_image, unreachable, …")
    status_text: str
    detail: str | None = Field(description="Why the last start failed (status start_failed), None otherwise")


def _require_tts_token(authorization: str | None) -> None:
    scheme, _, token = (authorization or "").partition(" ")
    require_service_token("tts_control", token if scheme.lower() == "bearer" else None)


@api_app.get("/tts/entries", response_model=list[TTSEntry], tags=["TTS"])
async def tts_entries(
    lang: str = Query(..., pattern="^(de|en)$"), authorization: str | None = Header(None),
) -> list[dict[str, object]]:
    """AIfred's list in speaking order, with the live status of each entry."""
    from ..i18n import t
    from ..tts_service import entries, entry_label, status_key

    _require_tts_token(authorization)
    try:
        listed = await asyncio.to_thread(entries, fresh=True)
    except TTSSuiteUnreachable as unreachable:
        raise HTTPException(status_code=503, detail=str(unreachable)) from unreachable
    rows: list[dict[str, object]] = []
    for entry in listed:
        status = status_key(entry)
        rows.append({
            "engine": entry.engine,
            "host": entry.host,
            "label": entry_label(entry, lang),
            "enabled": entry.enabled,
            "status": status,
            "status_text": t(f"tts_status_{status}", lang=lang),
            "detail": entry.info.start_hint if entry.info is not None and status == "start_failed" else None,
        })
    return rows
