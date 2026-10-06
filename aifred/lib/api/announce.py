"""Announce endpoints: a spoken announcement to FreeEcho.2 pucks, no LLM.

For callers that already have the text (e.g. Agent-Orc reading out an agent's
answer). The text goes through the same delivery path as the proactive alerts
(announce_to_channel → TTS of the FreeEcho.2 plugin → alert queue), so a
running announcement is not interrupted: items of one room play one after
the other. Token-guarded like inject/webhook, with ``Authorization: Bearer``.
"""

from typing import Dict, List

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_service_token
from ..config import ANNOUNCE_MAX_CHARS
from ..logging_utils import log_message
from ..message_processor import announce_to_channel, resolve_announce_targets
from .app import api_app

# Audio type of the puck chime: always the gentle one, "alarm" is not offered here
ANNOUNCE_AUDIO_TYPE = "notification"


class AnnounceRequest(BaseModel):
    """Spoken announcement request"""
    room: str = Field(
        ..., min_length=1,
        description="Room name from GET /audio/announce/rooms, '@group' for a group, '*' for all connected rooms",
    )
    text: str = Field(..., min_length=1, description="Text to read aloud")


class AnnounceRoomsResponse(BaseModel):
    """Rooms that currently accept announcements"""
    rooms: List[str]


class AnnounceResponse(BaseModel):
    """Announcement response"""
    success: bool
    rooms: List[str]


def _require_announce_token(authorization: str | None) -> None:
    """Check the ``Authorization: Bearer <token>`` header against ANNOUNCE_API_TOKEN."""
    scheme, _, token = (authorization or "").partition(" ")
    require_service_token("announce", token if scheme.lower() == "bearer" else None)


@api_app.get("/audio/announce/rooms", response_model=AnnounceRoomsResponse, tags=["Audio"])
async def announce_rooms(authorization: str | None = Header(None)) -> Dict[str, List[str]]:
    """The FreeEcho.2 rooms connected right now — the valid values of ``room``."""
    _require_announce_token(authorization)
    return {"rooms": resolve_announce_targets("freeecho2", "*")}


@api_app.post("/audio/announce", response_model=AnnounceResponse, tags=["Audio"])
async def announce(request: AnnounceRequest, authorization: str | None = Header(None)) -> Dict[str, object]:
    """Read ``text`` aloud in ``room`` (404 for an unknown or not connected room,
    413 for a text longer than ``ANNOUNCE_MAX_CHARS``)."""
    _require_announce_token(authorization)
    if len(request.text) > ANNOUNCE_MAX_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"text has {len(request.text)} characters, the limit is {ANNOUNCE_MAX_CHARS}",
        )
    rooms = resolve_announce_targets("freeecho2", request.room)
    if not rooms:
        raise HTTPException(status_code=404, detail=f"no connected FreeEcho.2 room '{request.room}'")

    metadata = {"audio_type": ANNOUNCE_AUDIO_TYPE, "proactive": True, "end_tone": True}
    reached = [
        room for room in rooms
        if await announce_to_channel("freeecho2", room, request.text, session_id=None, metadata=metadata)
    ]
    if not reached:
        raise HTTPException(status_code=502, detail="the announcement could not be delivered")
    log_message(f"Announce API: {len(request.text)} characters to {reached}")
    return {"success": True, "rooms": reached}
