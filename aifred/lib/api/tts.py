"""TTS container control: start a local GPU engine on the card AIfred picks, no LLM.

For callers outside AIfred that bring a TTS container up by hand (the
service control page). The card comes from the same rule the escalation
list uses (``place_local_gpu_engine``): side-channel card first, else the
fitting card with the most free memory; nothing fits → refused, nothing
started. The call returns once ``docker compose up -d`` is through; the
model loads afterwards (the caller polls the engine's /health).
Token-guarded with ``Authorization: Bearer <TTS_CONTROL_API_TOKEN>``.
"""

import asyncio
from typing import Dict

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_service_token
from ..logging_utils import log_message
from .app import api_app
from .schemas import CallerName


class TTSStartRequest(BaseModel):
    """Start request for one local GPU engine"""
    engine: str = Field(..., min_length=1, description="Engine key, e.g. 'qwen3local', 'xtts'")
    caller: CallerName


class TTSStartResponse(BaseModel):
    """Start response: where the engine went (``gpu`` is None when it was already running)"""
    success: bool
    engine: str
    gpu: int | None
    message: str


@api_app.post("/tts/start", response_model=TTSStartResponse, tags=["TTS"])
async def tts_start(request: TTSStartRequest, authorization: str | None = Header(None)) -> Dict[str, object]:
    """Start the engine's container on the card AIfred picks (404 unknown engine,
    422 not a local GPU engine, 409 no image built or no card fits, 502 start failed)."""
    from ..tts_engines import TTS_ENGINES
    from ..tts_escalation import PlacementRefused, place_local_gpu_engine

    scheme, _, token = (authorization or "").partition(" ")
    require_service_token("tts_control", token if scheme.lower() == "bearer" else None)

    engine = TTS_ENGINES.get(request.engine)
    if engine is None:
        raise HTTPException(status_code=404, detail=f"unknown TTS engine '{request.engine}'")
    if not (engine.needs_gpu and engine.runs_in_container):
        raise HTTPException(status_code=422, detail=f"'{request.engine}' is no local GPU engine")
    if not await asyncio.to_thread(engine.is_installed):
        raise HTTPException(status_code=409, detail=f"no image built for '{request.engine}'")
    if await asyncio.to_thread(engine.is_running):
        return {"success": True, "engine": engine.key, "gpu": None,
                "message": f"{engine.label_short} already running"}

    try:
        placement = await place_local_gpu_engine(engine)
    except PlacementRefused as refused:
        log_message(f"TTS start API ({request.caller}): {engine.key} refused — {refused}")
        raise HTTPException(status_code=409, detail=f"{engine.label_short}: {refused}") from refused
    ok, message = await asyncio.to_thread(placement.engine.start)
    if not ok:
        raise HTTPException(status_code=502, detail=message)
    log_message(f"TTS start API ({request.caller}): {engine.key} started on {placement.describe()}")
    return {"success": True, "engine": engine.key, "gpu": placement.gpu,
            "message": f"{engine.label_short} started on GPU {placement.gpu}"}
