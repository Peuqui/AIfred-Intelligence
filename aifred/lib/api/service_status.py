"""Status of model, speech recognition and speech output for the page's status line.

The page gets changes pushed over the browser bus (kind ``service_status``) and asks this
endpoint for the current state when it opens and when the user presses the refresh button
(``refresh=true`` reads STT and TTS from their services first).

The buttons behind the lines unload and load (``lib/service_control.py``). A load runs in the
background and the endpoint answers at once: a language model takes minutes, the status line shows
it loading through llama-swap's events, a failure lands in the debug console. A refusal (busy, the
describer does not fit) comes back as 409 with the message for the page.
"""

import asyncio
import json
from typing import Literal

from fastapi import Query
from pydantic import BaseModel

from ...backends.base import BackendError
from .. import session_events
from ..browser_push import browser_push
from ..logging_utils import log_message
from ..remote_describers import HostStatus
from ..service_control import (
    ServiceControlRefused,
    describer_to_load,
    load_llamaswap_model,
    main_model_to_load,
    release_stt_gpu,
    release_tts_engine,
    unload_llamaswap_model,
)
from ..service_status import ServiceSnapshot, get_monitor
from ..user_error import Conflict
from .app import api_app

SERVICE_STATUS_KIND = "service_status"


def _push_to_connected_pages(snapshot: ServiceSnapshot) -> None:
    payload = json.dumps(snapshot.as_json())
    for session_id in session_events.sessions_with_subscribers():
        browser_push(session_id, SERVICE_STATUS_KIND, payload)


get_monitor().add_listener(_push_to_connected_pages)


@api_app.get("/service-status", response_model=ServiceSnapshot, tags=["System"])
async def service_status(refresh: bool = Query(False)) -> ServiceSnapshot:
    """Model, STT and TTS state with the seconds left until each unloads itself (``refresh``: STT and TTS
    read anew first); the same snapshot comes over the session stream as ``service_status`` on every change."""
    monitor = get_monitor()
    if refresh:
        await monitor.refresh("stt")
        await monitor.refresh("tts")
    return monitor.snapshot()


class UnloadRequest(BaseModel):
    service: Literal["llm", "describer", "stt", "tts"]
    item: str  # the model (llm, describer) or the engine (tts); STT has one GPU worker, the item is empty


class LoadRequest(BaseModel):
    service: Literal["llm", "describer"]


class HoldRequest(BaseModel):
    host: str  # a machine of VISION_REMOTE_HOSTS
    hold: bool  # True = keep its card free for somebody (clears it), False = release it


@api_app.get("/service-status/hosts", response_model=list[HostStatus], tags=["System"])
async def service_status_hosts() -> list[HostStatus]:
    """The machines with describers: does their GPU-Orc answer, are their cards held, what is on them."""
    from ..remote_describers import host_status
    return await asyncio.to_thread(host_status)


@api_app.post("/service-status/hold", tags=["System"])
async def service_status_hold(request: HoldRequest) -> dict[str, str]:
    """Keep a machine's card free (its GPU-Orc clears it and nothing new goes on it) or release it."""
    from ..config import VISION_REMOTE_HOLD_REASON
    from ..gpu_orc_client import GpuOrcError
    from ..remote_describers import RemoteHostError, set_hold
    try:
        await asyncio.to_thread(set_hold, request.host, request.hold, VISION_REMOTE_HOLD_REASON)
    except (RemoteHostError, GpuOrcError) as error:
        refused = Conflict("hold_failed", host=request.host, error=str(error))
        log_message(f"⚠️ Status line: {refused}", "warning")
        raise refused from error
    log_message(f"{'⛔' if request.hold else '✅'} Status line: the card of {request.host} is "
                f"{'held free' if request.hold else 'released'}")
    return {"status": "held" if request.hold else "released"}


@api_app.post("/service-status/unload", tags=["System"])
async def service_status_unload(request: UnloadRequest) -> dict[str, str]:
    """Unload a model, the STT GPU worker or a TTS engine; refused while it is working."""
    try:
        match request.service:
            case "llm" | "describer":
                # Only what the status line lists: the name goes into llama-swap's URL path.
                listed = {model.name: model for model in get_monitor().snapshot().models}
                if request.item not in listed:
                    raise ServiceControlRefused("service_control_not_loaded", name=request.item)
                if listed[request.item].busy:
                    raise ServiceControlRefused("service_control_busy", name=request.item)
                await asyncio.to_thread(unload_llamaswap_model, request.item)
            case "stt":
                await release_stt_gpu()
            case "tts":
                await release_tts_engine(request.item)
    except ServiceControlRefused as refused:  # the log keeps it too; the page gets it in its language
        log_message(f"⚠️ Status line: {refused}", "warning")
        raise
    log_message(f"⏏️ Status line: {f'{request.service} {request.item}'.strip()} unloaded")
    return {"status": "unloaded"}


# Loads in flight: the event loop keeps only weak references to tasks.
_loads: set[asyncio.Task[None]] = set()


@api_app.post("/service-status/load", tags=["System"])
async def service_status_load(request: LoadRequest) -> dict[str, str]:
    """Start loading AIfred's main model or the describer that fits next to what is loaded."""
    choose = main_model_to_load if request.service == "llm" else describer_to_load
    try:
        backend_type, model = await asyncio.to_thread(choose)
    except ServiceControlRefused as refused:  # the log keeps it too; the page gets it in its language
        log_message(f"⚠️ Status line: {refused}", "warning")
        raise
    task = asyncio.create_task(_load(backend_type, model))
    _loads.add(task)
    task.add_done_callback(_loads.discard)
    return {"status": "loading", "model": model}


async def _load(backend_type: str, model: str) -> None:
    """The load itself logs its start and its time (``_load_model``); a failure is logged here."""
    try:
        await load_llamaswap_model(backend_type, model)
    except BackendError as error:
        log_message(f"⚠️ Status line: loading '{model}' failed: {error}", "warning")
