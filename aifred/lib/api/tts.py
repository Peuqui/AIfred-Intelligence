"""TTS escalation list for callers outside AIfred, no LLM.

For the service control page: it shows AIfred's list (order, place, status)
and starts or stops an entry's container through AIfred, so the card choice
(``place_local_gpu_engine``) and the remote hosts (SSH) follow the same
rules as AIfred's own speech. A start returns once the container is up; the
model loads afterwards (the caller polls ``/tts/entries``). Token-guarded
with ``Authorization: Bearer <TTS_CONTROL_API_TOKEN>``.
"""

import asyncio
from typing import Dict, List

from fastapi import Header, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import require_service_token
from ..logging_utils import log_message
from .app import api_app
from .schemas import CallerName


class TTSEntry(BaseModel):
    """One entry of the escalation list, as the list editor shows it"""
    engine: str
    host: str | None = Field(description="Host name, None = this machine")
    label: str = Field(description="Engine · place, e.g. 'Qwen3-TTS · Aragon'")
    enabled: bool
    status: str = Field(description="Status key: running, sleeping, no_image, host_off, …")
    status_text: str
    controllable: bool = Field(description="AIfred can start/stop its container")


class TTSEntryRequest(BaseModel):
    """Which entry to start or stop"""
    engine: str = Field(..., min_length=1, description="Engine key, e.g. 'qwen3local', 'xtts'")
    host: str | None = Field(None, description="Host name from /tts/entries, None = this machine")
    caller: CallerName


class TTSActionResponse(BaseModel):
    success: bool
    message: str


def _require_tts_token(authorization: str | None) -> None:
    scheme, _, token = (authorization or "").partition(" ")
    require_service_token("tts_control", token if scheme.lower() == "bearer" else None)


@api_app.get("/tts/entries", response_model=List[TTSEntry], tags=["TTS"])
async def tts_entries(
    lang: str = Query(..., pattern="^(de|en)$"), authorization: str | None = Header(None),
) -> List[Dict[str, object]]:
    """AIfred's TTS escalation list in speaking order, with the live status of each entry."""
    from ..i18n import t
    from ..tts_escalation import entry_status, escalation_entries, is_controllable, location_label

    _require_tts_token(authorization)
    entries = escalation_entries()
    statuses = await asyncio.gather(*(asyncio.to_thread(entry_status, entry) for entry in entries))
    return [
        {
            "engine": entry.engine.key,
            "host": entry.host.name if entry.host else None,
            "label": f"{entry.engine.label_short} · {location_label(entry, lang)}",
            "enabled": entry.enabled,
            "status": status,
            "status_text": t(f"tts_status_{status}", lang=lang),
            "controllable": is_controllable(entry),
        }
        for entry, status in zip(entries, statuses)
    ]


async def _act(request: TTSEntryRequest, verb: str) -> Dict[str, object]:
    from ..tts_escalation import PlacementRefused, find_entry, halt_entry, launch_entry

    try:
        entry = find_entry(request.engine, request.host)
    except LookupError as missing:
        raise HTTPException(status_code=404, detail=str(missing)) from missing
    try:
        message = await (launch_entry(entry) if verb == "start" else halt_entry(entry))
    except PlacementRefused as refused:
        log_message(f"TTS API ({request.caller}): {verb} {entry.label} refused — {refused}")
        raise HTTPException(status_code=409, detail=f"{entry.engine.label_short}: {refused}") from refused
    except RuntimeError as failed:
        log_message(f"TTS API ({request.caller}): {verb} {entry.label} failed — {failed}")
        raise HTTPException(status_code=409, detail=str(failed)) from failed
    log_message(f"TTS API ({request.caller}): {entry.label} {message}")
    return {"success": True, "message": f"{entry.engine.label_short} {message}"}


@api_app.post("/tts/start", response_model=TTSActionResponse, tags=["TTS"])
async def tts_start(request: TTSEntryRequest, authorization: str | None = Header(None)) -> Dict[str, object]:
    """Start the entry's container: locally on the card AIfred picks, on a host
    over SSH (404 no such entry, 409 no card fits / not controllable / failed)."""
    _require_tts_token(authorization)
    return await _act(request, "start")


@api_app.post("/tts/stop", response_model=TTSActionResponse, tags=["TTS"])
async def tts_stop(request: TTSEntryRequest, authorization: str | None = Header(None)) -> Dict[str, object]:
    """Stop the entry's container, VRAM free (404 no such entry, 409 failed)."""
    _require_tts_token(authorization)
    return await _act(request, "stop")
