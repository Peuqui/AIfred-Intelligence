"""TTS escalation list for callers outside AIfred, no LLM.

For the service control page: it shows the list of the TTS service (order, place,
status) and starts or stops an entry's container through the service, which chooses
the card and controls the remote hosts. A start runs in the background (model loads
take up to a minute or more); the list shows it as "starting", then running
or "start failed" with the reason. Token-guarded
with ``Authorization: Bearer <TTS_CONTROL_API_TOKEN>``.
"""

import asyncio
from typing import Dict, List

from fastapi import Header, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import require_service_token
from ..logging_utils import log_message
from ..tts_client import EngineInfo, TTSSuiteError, TTSSuiteUnreachable
from ..tts_service import entries, start_entry, status_key, stop_entry
from .app import api_app
from .schemas import CallerName


class TTSEntry(BaseModel):
    """One entry of the escalation list, as the list editor shows it"""
    engine: str
    host: str | None = Field(description="Host name, None = this machine")
    label: str = Field(description="Engine · place, e.g. 'Qwen3-TTS · Aragon'")
    enabled: bool
    status: str = Field(description="Status key: running, sleeping, no_image, unreachable, …")
    status_text: str
    controllable: bool = Field(description="The TTS service can start/stop its container")
    container: str | None = Field(description="Always None: the TTS service does not report container names")
    detail: str | None = Field(description="Why the last start failed (status start_failed), None otherwise")


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
    """The TTS service's escalation list in speaking order, with the live status of each entry."""
    from ..i18n import t
    from ..tts_service import entries, entry_label, status_key

    _require_tts_token(authorization)
    try:
        infos = await asyncio.to_thread(entries, fresh=True)
    except TTSSuiteUnreachable as unreachable:
        raise HTTPException(status_code=503, detail=str(unreachable)) from unreachable
    rows: List[Dict[str, object]] = []
    for info in infos:
        status = status_key(info)
        rows.append({
            "engine": info.engine,
            "host": info.host,
            "label": entry_label(info, lang),
            "enabled": info.enabled,
            "status": status,
            "status_text": t(f"tts_status_{status}", lang=lang),
            "controllable": info.needs_gpu,
            "container": None,  # the service does not report container names
            "detail": info.start_hint if status == "start_failed" else None,
        })
    return rows


# Starts run in the background (a model load takes up to a minute or more, longer than a
# web request should wait); the list shows "starting", then running or "start failed"
# with the service's start hint.
_BACKGROUND_STARTS: set["asyncio.Task[None]"] = set()


async def _background_start(info: EngineInfo, caller: str) -> None:
    try:
        await asyncio.to_thread(start_entry, info.engine, info.host)
    except TTSSuiteError as failed:
        log_message(f"TTS API ({caller}): start {info.engine} failed — {failed}")
    else:
        log_message(f"TTS API ({caller}): {info.engine} serving")


def _find_entry(engine: str, host: str | None) -> EngineInfo:
    for info in entries():
        if info.engine == engine and info.host == host:
            return info
    raise HTTPException(status_code=404, detail=f"no TTS list entry {engine}@{host or 'local'}")


async def _act(request: TTSEntryRequest, verb: str) -> Dict[str, object]:
    try:
        info = await asyncio.to_thread(_find_entry, request.engine, request.host)
    except TTSSuiteUnreachable as unreachable:
        raise HTTPException(status_code=503, detail=str(unreachable)) from unreachable
    name = info.label_short["en"]
    if not info.needs_gpu:
        raise HTTPException(status_code=409, detail=f"{name}: has no container the TTS service controls")
    try:
        if verb == "start":
            if status_key(info) == "starting":
                message = "already starting"
            else:
                task = asyncio.get_running_loop().create_task(_background_start(info, request.caller))
                _BACKGROUND_STARTS.add(task)
                task.add_done_callback(_BACKGROUND_STARTS.discard)
                message = "start requested"
        else:
            await asyncio.to_thread(stop_entry, info.engine, info.host)
            message = "stopped"
    except TTSSuiteError as failed:
        log_message(f"TTS API ({request.caller}): {verb} {info.engine} failed — {failed}")
        raise HTTPException(status_code=409, detail=f"{name}: {failed}") from failed
    log_message(f"TTS API ({request.caller}): {info.engine} — {message}")
    return {"success": True, "message": f"{name}: {message}"}


@api_app.post("/tts/start", response_model=TTSActionResponse, tags=["TTS"])
async def tts_start(request: TTSEntryRequest, authorization: str | None = Header(None)) -> Dict[str, object]:
    """Start the entry's container in the background: locally on the card AIfred
    picks, on a host over SSH; /tts/entries shows the progress (404 no such
    entry, 409 not controllable)."""
    _require_tts_token(authorization)
    return await _act(request, "start")


@api_app.post("/tts/stop", response_model=TTSActionResponse, tags=["TTS"])
async def tts_stop(request: TTSEntryRequest, authorization: str | None = Header(None)) -> Dict[str, object]:
    """Stop the entry's container, VRAM free (404 no such entry, 409 failed)."""
    _require_tts_token(authorization)
    return await _act(request, "stop")
