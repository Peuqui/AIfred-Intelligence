"""GPU release endpoints: what AIfred holds on the cards, and releasing it.

The API STT-Suite and TTS-Suite share (GPU-Orc's ``consumers/gpu_release.py`` calls it): ``GET /gpu/holders``
and ``POST /gpu/release``. AIfred's only item is the face worker (InsightFace in a process of its own,
``vision_filters/face_worker.py``); released means that process has ended and nvidia-smi no longer lists it.
The next face detection starts it again, on the card once GPU-Orc's traffic light is green.
Token-guarded with ``Authorization: Bearer <GPU_RELEASE_API_TOKEN>``.
"""

import asyncio
from dataclasses import asdict
from typing import Literal

from fastapi import Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from ..auth import require_bearer_token
from ..gpu_release import GpuReleaseResult
from ..logging_utils import log_message
from ..vision_filters import face_detect
from ..vision_filters.face_worker import ITEM as FACE_WORKER_ITEM
from .app import api_app

GPU_ITEMS = (FACE_WORKER_ITEM,)


class GpuHolder(BaseModel):
    """One thing AIfred holds on the cards"""
    item: str
    gpu_uuids: list[str]
    memory_mib: dict[str, int] | None = Field(description="MiB per card as nvidia-smi lists it, None = not listed yet")
    busy: bool = Field(description="True while a face detection runs on it")


class GpuHoldersResponse(BaseModel):
    holders: list[GpuHolder]


class GpuReleaseRequest(BaseModel):
    """Release request, as GPU-Orc sends it"""
    model_config = ConfigDict(extra="forbid")

    items: Literal["all"] | list[str] = Field(min_length=1, description=f'"all" or a list of {list(GPU_ITEMS)}')
    force: StrictBool = Field(False, description="End it even while a detection runs (that detection fails)")
    reason: str = Field(min_length=1, pattern=r"\S", description="Why, for the log")

    @field_validator("items")
    @classmethod
    def _known_items(cls, items: Literal["all"] | list[str]) -> Literal["all"] | list[str]:
        if items != "all" and (len(set(items)) != len(items) or set(items) - set(GPU_ITEMS)):
            raise ValueError(f'"all" or a list of distinct items of {list(GPU_ITEMS)}')
        return items


class GpuReleaseOutcome(BaseModel):
    item: str
    outcome: Literal["released", "not_running", "busy", "failed"]
    detail: str


class GpuReleaseResponse(BaseModel):
    results: list[GpuReleaseOutcome]


@api_app.get("/gpu/holders", response_model=GpuHoldersResponse, tags=["GPU"])
async def gpu_holders(authorization: str | None = Header(None)) -> dict[str, list[dict[str, object]]]:
    """What AIfred holds on the cards right now: the face worker, when it runs on a card."""
    require_bearer_token("gpu_release", authorization)
    detector = face_detect.running_detector()
    holders = await asyncio.to_thread(detector.worker.holders) if detector is not None else []
    return {"holders": holders}


@api_app.post("/gpu/release", response_model=GpuReleaseResponse, tags=["GPU"])
async def gpu_release(body: GpuReleaseRequest, authorization: str | None = Header(None)) -> JSONResponse:
    """Release ``items``: one result each. HTTP 200 when everything is released or was not running, 409 when an
    item is busy (left alone without force), 500 when an end could not be proven."""
    require_bearer_token("gpu_release", authorization)
    items = list(GPU_ITEMS) if body.items == "all" else body.items
    log_message(f"🎯 GPU release asked for {', '.join(items)} (force={body.force}): {body.reason}")
    detector = face_detect.running_detector()
    if detector is None:
        results = [GpuReleaseResult(item, "not_running", "nothing runs") for item in items]
    else:  # the face worker is the only item
        results = [await asyncio.to_thread(detector.worker.release, force=body.force, reason=body.reason)]
    for result in results:
        log_message(f"🎯 GPU release {result.item}: {result.outcome} ({result.detail})")
    outcomes = {result.outcome for result in results}
    status = 500 if "failed" in outcomes else 409 if "busy" in outcomes else 200
    payload = GpuReleaseResponse(results=[GpuReleaseOutcome(**asdict(result)) for result in results])
    return JSONResponse(payload.model_dump(), status_code=status)
